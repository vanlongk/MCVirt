# Copyright (c) 2016 - I.T. Dev Ltd
#
# This file is part of MCVirt.
#
# MCVirt is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
#
# MCVirt is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MCVirt.  If not, see <http://www.gnu.org/licenses/>

import Pyro4
from texttable import Texttable
from os.path import exists as os_path_exists
from os import makedirs
from enum import Enum
import time

from mcvirt.virtual_machine.virtual_machine import VirtualMachine
from mcvirt.virtual_machine.virtual_machine_config import VirtualMachineConfig
from mcvirt.mcvirt_config import MCVirtConfig
from mcvirt.auth.permissions import PERMISSIONS
from mcvirt.exceptions import (InvalidNodesException, DrbdNotEnabledOnNode,
                               InvalidVirtualMachineNameException, VmAlreadyExistsException,
                               ClusterNotInitialisedException, NodeDoesNotExistException,
                               VmDirectoryAlreadyExistsException, InvalidGraphicsDriverException,
                               MCVirtTypeError)
from mcvirt.rpc.pyro_object import PyroObject
from mcvirt.rpc.expose_method import Expose
from mcvirt.utils import get_hostname
from mcvirt.argument_validator import ArgumentValidator
from mcvirt.virtual_machine.hard_drive.base import Driver as HardDriveDriver
from mcvirt.constants import AutoStartStates
from mcvirt.syslogger import Syslogger


class GraphicsDriver(Enum):
    """Enums for specifying the graphics driver type"""
    VGA = 'vga'
    CIRRUS = 'cirrus'
    VMVGA = 'vmvga'
    XEN = 'xen'
    VBOX = 'vbox'
    QXL = 'qxl'


class Factory(PyroObject):
    """Class for obtaining virtual machine objects"""

    OBJECT_TYPE = 'virtual machine'
    VIRTUAL_MACHINE_CLASS = VirtualMachine
    DEFAULT_GRAPHICS_DRIVER = GraphicsDriver.VMVGA.value
    CACHED_OBJECTS = {}

    def autostart(self, start_type=AutoStartStates.ON_POLL):
        """Autostart VMs"""
        Syslogger.logger().info('Starting autostart: %s' % start_type.name)
        for vm in self.getAllVirtualMachines():
            if (vm.isRegisteredLocally() and vm.is_stopped and
                    vm._get_autostart_state() in
                    [AutoStartStates.ON_POLL, AutoStartStates.ON_BOOT] and
                    (start_type == vm._get_autostart_state() or
                     start_type == AutoStartStates.ON_BOOT)):
                try:
                    Syslogger.logger().info('Autostarting: %s' % vm.get_name())
                    vm.start()
                    Syslogger.logger().info('Autostart successful: %s' % vm.get_name())
                except Exception, e:
                    Syslogger.logger().error('Failed to autostart: %s: %s' %
                                             (vm.get_name(), str(e)))
        Syslogger.logger().info('Finished autostsart: %s' % start_type.name)

    @Expose()
    def getVirtualMachineByName(self, vm_name):
        """Obtain a VM object, based on VM name"""
        ArgumentValidator.validate_hostname(vm_name)
        if vm_name not in Factory.CACHED_OBJECTS:
            vm_object = VirtualMachine(self, vm_name)
            self._register_object(vm_object)
            Factory.CACHED_OBJECTS[vm_name] = vm_object
        return Factory.CACHED_OBJECTS[vm_name]

    @Expose()
    def getAllVirtualMachines(self, node=None):
        """Return objects for all virtual machines"""
        return [self.getVirtualMachineByName(vm_name) for vm_name in self.getAllVmNames(node=node)]

    @Expose()
    def getAllVmNames(self, node=None):
        """Returns a list of all VMs within the cluster or those registered on a specific node"""
        if node is not None:
            ArgumentValidator.validate_hostname(node)
        # If no node was defined, check the local configuration for all VMs
        if (node is None):
            return MCVirtConfig().get_config()['virtual_machines']
        elif node == get_hostname():
            # Obtain array of all domains from libvirt
            all_domains = self._get_registered_object(
                'libvirt_connector').get_connection().listAllDomains()
            return [vm.name() for vm in all_domains]
        else:
            # Return list of VMs registered on remote node
            cluster = self._get_registered_object('cluster')

            def remote_command(node_connection):
                virtual_machine_factory = node_connection.get_connection('virtual_machine_factory')
                return virtual_machine_factory.getAllVmNames(node=node)
            return cluster.run_remote_command(callback_method=remote_command, nodes=[node])[node]

    @Expose()
    def listVms(self, include_ram=False, include_cpu=False, include_disk=False):
        """Lists the VMs that are currently on the host"""
        table = Texttable()
        table.set_deco(Texttable.HEADER | Texttable.VLINES)
        headers = ['VM Name', 'State', 'Node']
        if include_ram:
            headers.append('RAM Allocation')
        if include_cpu:
            headers.append('CPU')
        if include_disk:
            headers.append('Total disk size (MiB)')

        table.header(tuple(headers))

        for vm_object in sorted(self.getAllVirtualMachines(), key=lambda vm: vm.name):
            vm_row = [vm_object.get_name(), vm_object._getPowerState().name,
                      vm_object.getNode() or 'Unregistered']
            if include_ram:
                vm_row.append(str(int(vm_object.getRAM()) / 1024) + 'MB')
            if include_cpu:
                vm_row.append(vm_object.getCPU())
            if include_disk:
                hard_drive_size = 0
                for disk_object in vm_object.getHardDriveObjects():
                    hard_drive_size += disk_object.getSize()
                vm_row.append(hard_drive_size)
            table.add_row(vm_row)
        table_output = table.draw()
        return table_output

    @Expose()
    def check_exists(self, vm_name):
        """Determines if a VM exists, given a name"""
        try:
            ArgumentValidator.validate_hostname(vm_name)
        except (MCVirtTypeError, InvalidVirtualMachineNameException):
            return False

        return (vm_name in self.getAllVmNames())

    @Expose()
    def checkName(self, name, ignore_exists=False):
        try:
            ArgumentValidator.validate_hostname(name)
        except MCVirtTypeError:
            raise InvalidVirtualMachineNameException(
                'Error: Invalid VM Name - VM Name can only contain 0-9 a-Z and dashes'
            )

        if len(name) < 3:
            raise InvalidVirtualMachineNameException('VM Name must be at least 3 characters long')

        if self.check_exists(name) and not ignore_exists:
            raise VmAlreadyExistsException('VM already exists')

        return True

    def check_graphics_driver(self, driver):
        """Check that the provided graphics driver name is valid"""
        if driver not in [i.value for i in list(GraphicsDriver)]:
            raise InvalidGraphicsDriverException('Invalid graphics driver \'%s\'' % driver)

    @Expose(locking=True, instance_method=True)
    def create(self, *args, **kwargs):
        """Exposed method for creating a VM, that performs a permission check"""
        self._get_registered_object('auth').assert_permission(PERMISSIONS.CREATE_VM)
        return self._create(*args, **kwargs)

    def _create(self, name, cpu_cores, memory_allocation, hard_drives=None,
                network_interfaces=None, node=None, available_nodes=None, storage_type=None,
                hard_drive_driver=None, graphics_driver=None, modification_flags=None):
        """Create a VM and returns the virtual_machine object for it"""
        network_interfaces = [] if network_interfaces is None else network_interfaces
        hard_drives = [] if hard_drives is None else hard_drives
        available_nodes = [] if available_nodes is None else available_nodes
        modification_flags = [] if modification_flags is None else modification_flags

        self.checkName(name)
        ArgumentValidator.validate_positive_integer(cpu_cores)
        ArgumentValidator.validate_positive_integer(memory_allocation)
        for hard_drive in hard_drives:
            ArgumentValidator.validate_positive_integer(hard_drive)
        if network_interfaces:
            for network_interface in network_interfaces:
                ArgumentValidator.validate_network_name(network_interface)
        if node is not None:
            ArgumentValidator.validate_hostname(node)
        for available_node in available_nodes:
            ArgumentValidator.validate_hostname(available_node)
        assert storage_type in [None] + [
            storage_type_itx.__name__ for storage_type_itx in self._get_registered_object(
                'hard_drive_factory').STORAGE_TYPES
        ]
        if hard_drive_driver is not None:
            HardDriveDriver[hard_drive_driver]

        # If no graphics driver has been specified, set it to the default
        if graphics_driver is None:
            graphics_driver = self.DEFAULT_GRAPHICS_DRIVER

        # Check the driver name is valid
        self.check_graphics_driver(graphics_driver)

        # Ensure the cluster has not been ignored, as VMs cannot be created with MCVirt running
        # in this state
        if self._cluster_disabled:
            raise ClusterNotInitialisedException('VM cannot be created whilst the cluster' +
                                                 ' is not initialised')

        # Determine if VM already exists
        if self.check_exists(name):
            raise VmAlreadyExistsException('Error: VM already exists')

        # If a node has not been specified, assume the local node
        if node is None:
            node = get_hostname()

        # If Drbd has been chosen as a storage type, ensure it is enabled on the node
        node_drbd = self._get_registered_object('node_drbd')
        if storage_type == 'Drbd' and not node_drbd.is_enabled():
            raise DrbdNotEnabledOnNode('Drbd is not enabled on this node')

        # Create directory for VM on the local and remote nodes
        if os_path_exists(VirtualMachine._get_vm_dir(name)):
            raise VmDirectoryAlreadyExistsException('Error: VM directory already exists')

        # If available nodes has not been passed, assume the local machine is the only
        # available node if local storage is being used. Use the machines in the cluster
        # if Drbd is being used
        cluster_object = self._get_registered_object('cluster')
        all_nodes = cluster_object.get_nodes(return_all=True)
        all_nodes.append(get_hostname())

        if len(available_nodes) == 0:
            if storage_type == 'Drbd':
                # If the available nodes are not specified, use the
                # nodes in the cluster
                available_nodes = all_nodes
            else:
                # For local VMs, only use the local node as the available nodes
                available_nodes = [get_hostname()]

        # If there are more than the maximum number of Drbd machines in the cluster,
        # add an option that forces the user to specify the nodes for the Drbd VM
        # to be added to
        if storage_type == 'Drbd' and len(available_nodes) != node_drbd.CLUSTER_SIZE:
            raise InvalidNodesException('Exactly %i nodes must be specified'
                                        % node_drbd.CLUSTER_SIZE)

        for check_node in available_nodes:
            if check_node not in all_nodes:
                raise NodeDoesNotExistException('Node \'%s\' does not exist' % check_node)

        if get_hostname() not in available_nodes and self._is_cluster_master:
            raise InvalidNodesException('One of the nodes must be the local node')

        # Check whether the hard drives can be created.
        if self._is_cluster_master:
            hard_drive_factory = self._get_registered_object('hard_drive_factory')
            for hard_drive_size in hard_drives:
                hard_drive_factory.ensure_hdd_valid(
                    hard_drive_size, storage_type,
                    [node_itx for node_itx in available_nodes if node_itx != get_hostname()]
                )

        # Create directory for VM
        makedirs(VirtualMachine._get_vm_dir(name))

        # Add VM to MCVirt configuration
        def updateMCVirtConfig(config):
            config['virtual_machines'].append(name)
        MCVirtConfig().update_config(
            updateMCVirtConfig,
            'Adding new VM \'%s\' to global MCVirt configuration' %
            name)

        # Create VM configuration file
        VirtualMachineConfig.create(name, available_nodes, cpu_cores, memory_allocation,
                                    graphics_driver)

        # Add VM to remote nodes
        if self._is_cluster_master:
            def remote_command(remote_connection):
                virtual_machine_factory = remote_connection.get_connection(
                    'virtual_machine_factory'
                )
                virtual_machine_factory.create(
                    name=name, memory_allocation=memory_allocation, cpu_cores=cpu_cores,
                    node=node, available_nodes=available_nodes,
                    modification_flags=modification_flags
                )
            cluster_object.run_remote_command(callback_method=remote_command)

        # Obtain an object for the new VM, to use to create disks/network interfaces
        vm_object = self.getVirtualMachineByName(name)
        vm_object.get_config_object().gitAdd('Created VM \'%s\'' % vm_object.get_name())

        if node == get_hostname():
            # Register VM with LibVirt. If MCVirt has not been initialised on this node,
            # do not set the node in the VM configuration, as the change can't be
            # replicated to remote nodes
            vm_object._register(set_node=self._is_cluster_master)
        elif self._is_cluster_master:
            # If MCVirt has been initialised on this node and the local machine is
            # not the node that the VM will be registered on, set the node on the VM
            vm_object._setNode(node)

        if self._is_cluster_master:
            # Create disk images
            hard_drive_factory = self._get_registered_object('hard_drive_factory')
            for hard_drive_size in hard_drives:
                hard_drive_factory.create(vm_object=vm_object, size=hard_drive_size,
                                          storage_type=storage_type, driver=hard_drive_driver)

            # If any have been specified, add a network configuration for each of the
            # network interfaces to the domain XML
            network_adapter_factory = self._get_registered_object('network_adapter_factory')
            network_factory = self._get_registered_object('network_factory')
            if network_interfaces is not None:
                for network in network_interfaces:
                    network_object = network_factory.get_network_by_name(network)
                    network_adapter_factory.create(vm_object, network_object)

            # Add modification flags
            vm_object._update_modification_flags(add_flags=modification_flags)

        return vm_object
