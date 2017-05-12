# Copyright (c) 2013-2017 Christian Geier et al.
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#

from click import confirm, prompt, UsageError
import xdg

from functools import partial
import json
from itertools import zip_longest
from os.path import (expanduser, expandvars, join, normpath, exists, isdir,
                     dirname)
from os import makedirs, environ
from subprocess import call

from datetime import date, datetime

from khal.log import logger
from .exceptions import FatalError
from .settings import settings


def compressuser(path):
    """Abbreviate home directory to '~', for presenting a path."""
    home = normpath(expanduser('~'))
    path = normpath(path)
    if path.startswith(home):
        path = '~' + path[len(home):]
    return path


def validate_int(input, min_value, max_value):
    try:
        number = int(input)
    except ValueError:
        raise UsageError('Input must be an integer')
    if min_value <= number <= max_value:
        return number
    else:
        raise UsageError('Input must be between {} and {}'.format(min_value, max_value))


DATE_FORMAT_INFO = [
    ('Year', ['%Y', '%y']),
    ('Month', ['%m', '%B', '%b']),
    ('Day', ['%d', '%a', '%A'])
]


def present_date_format_info(example_date):
    columns = []
    widths = []
    for title, formats in DATE_FORMAT_INFO:
        newcol = [title]
        for f in formats:
            newcol.append('{}={}'.format(f, example_date.strftime(f)))
        widths.append(max(len(s) for s in newcol) + 2)
        columns.append(newcol)

    print('Common fields for date formatting:')
    for row in zip_longest(*columns, fillvalue=''):
        print(''.join(s.ljust(w) for (s, w) in zip(row, widths)))

    print('More info: '
          'https://docs.python.org/3/library/datetime.html#strftime-and-strptime-behavior')


def choose_datetime_format():
    """query user for their date format of choice"""
    choices = [
        ('year-month-day', '%Y-%m-%d'),
        ('day/month/year', '%d/%m/%Y'),
        ('month/day/year', '%m/%d/%Y'),
    ]
    validate = partial(validate_int, min_value=0, max_value=3)
    today = date.today()
    print("What ordering of year, month, date do you want to use?")
    for num, (desc, fmt) in enumerate(choices):
        print('[{}] {} (today: {})'.format(num, desc, today.strftime(fmt)))
    print('[3] Custom')
    choice_no = prompt("Please choose one of the above options", value_proc=validate)
    if choice_no == 3:
        present_date_format_info(today)
        dateformat = prompt('Make your date format')
    else:
        dateformat = choices[choice_no][1]
    print("Date format: {} "
          "(today as an example: {})".format(dateformat, today.strftime(dateformat)))
    return dateformat


def choose_time_format():
    """query user for their time format of choice"""
    choices = ['%H:%M', '%I:%M %p']
    print("What timeformat do you want to use?")
    print("[0] 24 hour clock (recommended)\n[1] 12 hour clock")
    validate = partial(validate_int, min_value=0, max_value=1)
    prompt_text = "Please choose one of the above options"
    timeformat = choices[prompt(prompt_text, default=0, value_proc=validate)]
    now = datetime.now()
    print("Time format: {} "
          "(current time as an example: {})".format(timeformat, now.strftime(timeformat)))
    return timeformat


def get_vdirs_from_vdirsyncer_config():
    """trying to load vdirsyncer's config and read all vdirs from it"""
    try:
        from vdirsyncer.cli import config
        from vdirsyncer.exceptions import UserError
    except ImportError:
        print("Couldn't load vdirsyncer to discover its calendars.")
        return None
    try:
        vdir_config = config.load_config()
    except UserError as error:
        print("Sorry, trying to load vdirsyncer config failed with the following "
              "error message:")
        print(error)
        return None
    vdirs = list()
    for storage in vdir_config.storages.values():
        if storage['type'] == 'filesystem':
            # TODO detect type of storage properly
            vdirs.append((storage['instance_name'], storage['path'], 'discover'))
    if vdirs == list():
        print("No calendars found from vdirsyncer.")
        return None
    else:
        return vdirs


def find_vdir():
    """Use one or more existing vdirs on the system.

    Tries to get data from vdirsyncer if it's installed and configured, and
    asks user to confirm it. If not, prompt the user for the path to a single
    vdir.
    """
    print("The following collections were found:")
    synced_vdirs = get_vdirs_from_vdirsyncer_config()
    if synced_vdirs:
        print("Found {} calendars from vdirsyncer".format(len(synced_vdirs)))
        for name, path, _ in synced_vdirs:
            print('  {}: {}'.format(name, compressuser(path)))
        if confirm("Use these calendars for khal?", default=True):
            return synced_vdirs

    vdir_path = prompt("Enter the path to a vdir calendar")
    vdir_path = normpath(expanduser(expandvars(vdir_path)))
    return [('private', vdir_path, 'calendar')]


def create_vdir(names=[]):
    name = 'private'
    while True:
        path = join(xdg.BaseDirectory.xdg_data_home, 'khal', 'calendars', name)
        path = normpath(expanduser(expandvars(path)))
        if name not in names and not exists(path):
            break
        else:
            name += '1'
    try:
        makedirs(path)
    except OSError as error:
        print("Could not create directory {} because of {}. Exiting".format(path, error))
        raise
    print("Created new vdir at {}".format(path))
    return [(name, path, 'calendar')]


# Parsing and then dumping config naively could lose comments and formatting.
# Since we don't need to modify existing fields, we can simply append our new
# config to the end of the file.
VDS_CONFIG_START = """\
[general]
status_path = "~/.local/share/vdirsyncer/status/"
"""

VDS_CONFIG_TEMPLATE = """
[pair khal_pair_{pairno}]
a = "khal_pair_{pairno}_local"
b = "khal_pair_{pairno}_remote"
collections = ["from a", "from b"]

[storage khal_pair_{pairno}_local]
type = "filesystem"
path = {local_path}
fileext = ".ics"

[storage khal_pair_{pairno}_remote]
type = "caldav"
url = {url}
username = {username}
password = {password}
"""


def vdirsyncer_config_path():
    """Find where vdirsyncer will look for it's config.

    There may or may not already be a file at the returned path.
    """
    fname = environ.get('VDIRSYNCER_CONFIG', None)
    if fname is None:
        fname = normpath(expanduser('~/.vdirsyncer/config'))
        if not exists(fname):
            xdg_config_dir = environ.get('XDG_CONFIG_HOME',
                                         normpath(expanduser('~/.config/')))
            fname = join(xdg_config_dir, 'vdirsyncer/config')
    return fname


def get_available_pairno():
    """Find N so that 'khal_pair_N' is not already used in vdirsyncer config
    """
    try:
        from vdirsyncer.cli import config
    except ImportError:
        raise FatalError("vdirsyncer config exists, but couldn't import vdirsyncer.")
    vdir_config = config.load_config()
    pairno = 1
    while 'khal_pair_{}'.format(pairno) in vdir_config.pairs:
        pairno += 1
    return pairno


def create_synced_vdir():
    """Create a new vdir, and set up vdirsyncer to sync it.
    """
    name, path, _ = create_vdir()[0]

    caldav_url = prompt('CalDAV URL')
    username = prompt('Username')
    password = prompt('Password', hide_input=True)

    vds_config = vdirsyncer_config_path()
    if exists(vds_config):
        # We are adding a pair to vdirsyncer config
        mode = 'a'
        new_file = False
        pairno = get_available_pairno()
    else:
        # We're setting up vdirsyncer for the first time
        mode = 'w'
        new_file = True
        pairno = 1

    with open(vds_config, mode) as f:
        if new_file:
            f.write(VDS_CONFIG_START)

        f.write(VDS_CONFIG_TEMPLATE.format(
            local_path=json.dumps(dirname(path)),
            url=json.dumps(caldav_url),
            username=json.dumps(username),
            password=json.dumps(password),
            pairno=pairno,
        ))
    start_syncing()
    return [(name, path, 'calendar')]


def start_syncing():
    """Run vdirsyncer to sync the newly created vdir with the remote."""
    print("Syncing calendar...")
    try:
        exit_code = call(['vdirsyncer', 'discover'])
    except FileNotFoundError:
        print("Could not find vdirsyncer - please set it up manually")
    else:
        if exit_code == 0:
            exit_code = call(['vdirsyncer', 'sync'])
        if exit_code != 0:
            print("vdirsyncer failed - please set up sync manually")

    # Add code here to check platform and automatically set up cron or similar
    print("Please set up your system to run 'vdirsyncer sync' periodically, "
          "using cron or similar mechanisms.")


def choose_vdir_calendar():
    """query the user for their preferred calendar source"""
    choices = [
        ("Create a new calendar on this computer", create_vdir),
        ("Use a calendar already on this computer (vdir format)", find_vdir),
        ("Sync a calendar from the internet (CalDAV format, requires vdirsyncer)",
         create_synced_vdir),
    ]
    validate = partial(validate_int, min_value=0, max_value=2)
    for i, (desc, func) in enumerate(choices):
        print('[{}] {}'.format(i, desc))
    choice_no = prompt("Please choose one of the above options",
                       value_proc=validate)
    return choices[choice_no][1]()


def create_config(vdirs, dateformat, timeformat):
    config = ['[calendars]']
    for name, path, type_ in sorted(vdirs or ()):
        config.append('\n[[{name}]]'.format(name=name))
        config.append('path = {path}'.format(path=path))
        config.append('type = {type}'.format(type=type_))

    config.append('\n[locale]')
    config.append('timeformat = {timeformat}\n'
                  'dateformat = {dateformat}\n'
                  'longdateformat = {longdateformat}\n'
                  'datetimeformat = {dateformat} {timeformat}\n'
                  'longdatetimeformat = {longdateformat} {timeformat}\n'
                  .format(timeformat=timeformat,
                          dateformat=dateformat,
                          longdateformat=dateformat))

    config = '\n'.join(config)
    return config


def configwizard():
    config_file = settings.find_configuration_file()
    if config_file is not None:
        logger.fatal("Found an existing config file at {}.".format(compressuser(config_file)))
        logger.fatal(
            "If you want to create a new configuration file, "
            "please remove the old one first. Exiting.")
        raise FatalError()
    dateformat = choose_datetime_format()
    print()
    timeformat = choose_time_format()
    print()
    try:
        vdirs = choose_vdir_calendar()
    except OSError as error:
        raise FatalError(error)
    print()

    if not vdirs:
        print("\nWARNING: no vdir configured, khal will not be usable like this!\n")

    config = create_config(vdirs, dateformat=dateformat, timeformat=timeformat)
    config_path = join(xdg.BaseDirectory.xdg_config_home, 'khal', 'config')
    if not confirm(
            "Do you want to write the config to {}? "
            "(Choosing `No` will abort)".format(compressuser(config_path)), default=True):
        raise FatalError('User aborted...')
    config_dir = join(xdg.BaseDirectory.xdg_config_home, 'khal')
    if not exists(config_dir) and not isdir(config_dir):
        try:
            makedirs(config_dir)
        except OSError as error:
            print(
                "Could not write config file at {} because of {}. "
                "Aborting".format(compressuser(config_dir), error)
            )
            raise FatalError(error)
        else:
            print('created directory {}'.format(compressuser(config_dir)))
    with open(config_path, 'w') as config_file:
        config_file.write(config)
    print("Successfully wrote configuration to {}".format(compressuser(config_path)))
