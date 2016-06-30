"""Argument validators."""

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

import re


class ArgumentValidator(object):
    """Provide methods to validate argument values"""

    @staticmethod
    def validate_hostname(hostname):
        """Validate a hostname"""
        # Check length
        if len(hostname) > 255 or not len(hostname):
            raise TypeError

        disallowed = re.compile("[^A-Z\d-]", re.IGNORECASE)
        if disallowed.search(hostname):
            raise TypeError

        if hostname.startswith('-') or hostname.endswith('-'):
            raise TypeError

    @staticmethod
    def validate_integer(value):
        """Validate integer"""
        if str(int(value)) != str(value):
            raise TypeError

    @staticmethod
    def validate_positive_integer(value):
        """Validate that a given variable is a
        positive integer
        """
        ArgumentValidator.validate_integer(value)

        if int(value) < 1:
            raise TypeError

    @staticmethod
    def validate_boolean(variable):
        """Ensure variable is a boolean"""
        if type(variable) is not bool:
            raise TypeError

    @staticmethod
    def validate_drbd_resource(variable):
        """Validate DRBD resource name"""
        valid_name = re.compile('^mcvirt_vm-(.*)-disk-(\d+)$')
        result = valid_name.match(variable)
        if not result:
            raise TypeError

        # Validate the hostname in the DRBD resource
        ArgumentValidator.validate_hostname(result.groups(1))

        if int(result.group(2)) < 1 or int(result.group(2)) > 99:
            raise TypeError
