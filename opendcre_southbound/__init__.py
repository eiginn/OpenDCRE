#!/usr/bin/env python
""" OpenDCRE Southbound API Endpoint

    Author:  andrew
    Date:    4/8/2015
    Update:  06/11/2015 - Support power control commands/responses. Minor bug
                          fixes and sensor/device renaming. (ABC)
             06/19/2015 - Add locking to prevent multiple simultaneous requests
                          from stomping all over the bus.
             07/20/2015 - v0.7.0 add node information (stub for now)
             07/28/2015 - v0.7.1 convert to python package
             12/05/2015 - Line noise robustness (retries)
             02/23/2016 - Reorganize code to move IPMI and Location capabilities to other modules.
             09/20/2016 - Reorganize code to move device-specific implementations for command
                          handling to the 'devicebus' module.
             09/25/2016 - Break out endpoint definitions from this file into blueprints.

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
import logging
import datetime
import threading
from flask import Flask, jsonify
from itertools import count

import constants as const
from errors import OpenDCREException

from utils import ThreadPool, cache_registration_dependencies

from opendcre_southbound.devicebus.devices.plc import *
from opendcre_southbound.devicebus.devices.ipmi import *
from opendcre_southbound.devicebus.devices.redfish.redfish_device import *

from opendcre_southbound.blueprints import core
from opendcre_southbound.devicebus.command_factory import CommandFactory

from vapor_common.util import setup_json_errors
from vapor_common.vapor_config import ConfigManager
from vapor_common.vapor_logging import setup_logging, get_startup_logger

from version import __api_version__  # major.minor API version
from version import __version__      # full OpenDCRE version


cfg = ConfigManager(
    default='/opendcre/default/default.json',
    override='/opendcre/override'
)

logger = logging.getLogger(__name__)

# noinspection PyUnresolvedReferences
DEVICES = cfg.devices                       # devicebus interfaces configured for the OpenDCRE instance
# noinspection PyUnresolvedReferences
SCAN_CACHE_FILE = cfg.scan_cache_file       # file which the scan cache will be stored in
# noinspection PyUnresolvedReferences
CACHE_TIMEOUT = cfg.cache_timeout           # the time it takes for the cache to expire
# noinspection PyUnresolvedReferences
CACHE_THRESHOLD = cfg.cache_threshold       # the max number of items the cache can store

app = Flask(__name__)
setup_json_errors(app)

# add the api_version to the prefix
PREFIX = const.endpoint_prefix + __api_version__


# Define a mapping of device registrars.
# The key here is the same as the key in the json configuration file for opendcre.
# The value is a device class that can register the device.
device_registrars = {
    'plc': PLCDevice,
    'ipmi': IPMIDevice,
    'redfish': RedfishDevice
}

################################################################################


def _count(start=0x00, step=0x01):
    """ Generator whose next() method returns consecutive values until it reaches
    0xff, then wraps back to 0x00.

    Args:
        start (int): the value at which to start the count
        step (int): the amount to increment the count

    Returns:
        int: the next count value.
    """
    n = start
    while True:
        yield n
        n += step
        n %= 0xff


################################################################################
# DEBUG METHODS
################################################################################

@app.route(PREFIX + '/test', methods=['GET', 'POST'])
def test_routine():
    """ Test routine to verify the endpoint is running and ok, without
    relying on the serial bus layer.
    """
    return jsonify({'status': 'ok'})


@app.route(const.endpoint_prefix + 'version', methods=['GET', 'POST'])
def opendcre_version():
    """ Get the API version used by OpenDCRE CORE. This can be used in formulating
    subsequent requests against the OpenDCRE REST API.
    """
    return jsonify({'version': __api_version__})


@app.route(PREFIX + '/plc_config', methods=['GET', 'POST'])
def plc_config():
    """ Test routine to return the PLC modem configuration parameters on the endpoint.
    """
    raise OpenDCREException('Unsupported hardware type in use. Unable to retrieve modem configuration.')


def register_app_devices(app):
    """ Register all devicebus interfaces specified in the OpenDCRE config
    file with the Flask application.

    Args:
        app (Flask): the Flask application to register the devices to.
    """
    # before registering any devices, make sure all dependencies are cached
    # globally -- this is needed for any threaded registration.
    cache_registration_dependencies()

    _devices = {}
    _single_board_devices = {}
    _range_devices = []

    app_cache = (_devices, _single_board_devices, _range_devices)

    _failed_registration = False

    for device_interface, device_config in DEVICES.iteritems():
        device_interface = device_interface.lower()

        _registrar = device_registrars.get(device_interface)

        if not _registrar:
            raise ValueError(
                'Unsupported device interface "{}" found during registration.'.format(device_interface)
            )

        try:
            _registrar.register(device_config, app.config, app_cache)
        except Exception as e:
            logger.error('Failed to register {} device: {}'.format(device_interface, device_config))
            logger.exception(e)
            _failed_registration = True

    if _failed_registration:
        raise ValueError(
            'Failed to register all configured devices -- check that the device configuration files are correct.'
        )

    app.config['DEVICES'] = _devices
    app.config['SINGLE_BOARD_DEVICES'] = _single_board_devices
    app.config['RANGE_DEVICES'] = _range_devices


def main(serial_port=None, hardware=None):
    """ Main method to run the flask server.

    Args:
        serial_port (str): specify the serial port to use; the default is fine
            for production, but for testing it is necessary to pass in an emulator
            port here.
        hardware (int): the type of hardware we are working with - see devicebus.py
            for values -> by default we use the emulator, but may use VEC or RPI HAT
            which dictates what type of configuration we do on startup and throughout.
    """
    setup_logging(default_path='logging_opendcre.json')

    logger.info('=====================================')
    logger.info('Starting OpenDCRE Southbound Endpoint')
    logger.info('[{}]'.format(datetime.datetime.utcnow()))
    logger.info('=====================================')

    # get an error logger to log out anything that could cause app startup failure
    startup_logger = get_startup_logger()

    try:
        # FIXME - using app.config here isn't 'wrong', but when OpenDCRE changes to be
        #         anything more than single proc/thread, we will want to change this.
        #         flask context objects are not a 'good' solution, so we will need some
        #         thin db/caching layer (likely redis)

        app.config['SERIAL_OVERRIDE'] = serial_port
        app.config['HARDWARE_OVERRIDE'] = int(hardware) if hardware is not None else hardware

        app.config['COUNTER'] = _count(start=0x01, step=0x01)
        app.config['ENDPOINT_PREFIX'] = PREFIX
        app.config['SCAN_CACHE'] = SCAN_CACHE_FILE

        # define board offsets -- e.g. the offset within the board_id space to add to the
        # board_id. this should increase monotonically for each board for each device interface
        # so that each board has a unique id whether registered upfront or at runtime
        app.config['IPMI_BOARD_OFFSET'] = count()
        app.config['PLC_BOARD_OFFSET'] = count()
        app.config['REDFISH_BOARD_OFFSET'] = count()

        # add a command factory to the app context
        app.config['CMD_FACTORY'] = CommandFactory(app.config['COUNTER'])

        # register the configured devicebus interfaces with the app. no failure handing here
        # so that if this stage fails, we know about it immediately.
        app.config['DEVICES'] = {}

        # single board devices can be accessed by board_id to get device instance
        app.config['SINGLE_BOARD_DEVICES'] = {}

        # range-devices must be iterated through to determine if a board_id belongs to one of them
        app.config['RANGE_DEVICES'] = []

        app.register_blueprint(core)
        register_app_devices(app)

        logger.info('Registered {} Device(s)'.format(len(app.config['DEVICES'])))
        for v in app.config['DEVICES'].values():
            logger.info('... {}'.format(v))

        logger.info('Endpoint Setup and Registration Complete')
        logger.info('----------------------------------------')

    except Exception as e:
        startup_logger.error('Failed to start up OpenDCRE endpoint!')
        startup_logger.exception(e)
        raise

    if __name__ == '__main__':
        app.run(host='0.0.0.0')
