#!/usr/bin/env python
""" Command object which models an OpenDCRE command to be handled by a
devicebus implementation. Each OpenDCRE endpoint will generate a Command
object and will pass it along to the appropriate devicebus interface. It
is left to the interface to handle (or refuse to handle) the command.

    Author: Erick Daniszewski
    Date:   09/15/2016
    
    \\//
     \/apor IO

-------------------------------
Copyright (C) 2015-17  Vapor IO

This file is part of OpenDCRE.

OpenDCRE is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 2 of the License, or
(at your option) any later version.

OpenDCRE is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with OpenDCRE.  If not, see <http://www.gnu.org/licenses/>.
"""
from response import Response


class Command(object):
    """ Model for a generic OpenDCRE command.
    """

    def __init__(self, cmd_id, data, sequence):
        """ Initialize a new Command instance.

        Args:
            cmd_id (int): the integer id which specifies the command type.
            data (dict): the data associated with the command.
            sequence (int): the sequence number of the command.

        Returns:
            Command: a new instance of Command.
        """
        self.cmd_id = cmd_id
        self.data = data
        self.sequence = sequence

    def make_response(self, data):
        """ Make a Response object which contains a devicebus implementation's
        response to the given Command.

        Args:
            data (dict): a dictionary containing the response data.

        Returns:
            Response: a Response object wrapping the given data.
        """
        return Response(self, data)
