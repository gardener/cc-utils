# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import shutil
import sys
import os
import pathlib
import yaml

class Failure(RuntimeError):
    pass

def _set_cli(is_cli: bool):
    ctx().args._cli = is_cli
    global Failure
    if is_cli:
        class Failure(SystemExit): pass
    else:
        class Failure(RuntimeError): pass


def ensure_file_exists(path):
    if isinstance(path, pathlib.Path):
        is_file = path.is_file()
    else:
        is_file = os.path.isfile(path)
    if not is_file:
        fail('not an existing file: ' + str(path))
    return path


def ensure_directory_exists(path: str):
    if isinstance(path, pathlib.Path):
        is_dir = path.is_dir()
    else:
        is_dir = os.path.isdir(path)
    if not is_dir:
        fail('not an existing directory: ' + str(path))
    return path

# export shorted aliases
existing_file = ensure_file_exists
existing_dir = ensure_directory_exists

class SimpleNamespaceDict(dict):
    def __getattr__(self, name):
        element = self.get(name)
        if isinstance(element, dict):
            return SimpleNamespaceDict(element)
        if isinstance(element, list):
            return map(SimpleNamespaceDict, element)
        return element
    def __getitem__(self, name):
        return self.__getattr__(name)


class CliHint(object):
    def __init__(self, typehint=str, *args, **kwargs):
        self.argparse_args = SimpleNamespaceDict(*args, **kwargs)
        self.typehint = typehint


class CliHints(object):
    '''
    predefined cli hint instances
    '''
    @staticmethod
    def existing_file(help_string:str='an existing file'):
        return CliHint(typehint=str, help=help_string, type=ensure_file_exists)

    @staticmethod
    def yaml_file(help_string:str='an existing YAML file'):
        return CliHint(typehint=str, help=help_string, type=parse_yaml_file)

    @staticmethod
    def existing_dir(help_string:str='an existing directory'):
        return CliHint(typehint=str, help=help_string, type=ensure_directory_exists)


def ctx():
    # late import because the ctx module is altered after all existing modules have
    # already been imported
    import ctx
    return ctx

def _quiet():
    return ctx().args and ctx().args.quiet


# pylint: disable=no-member
def _verbose():
    return ctx().args and ctx().args.verbose

def _cli():
    return bool(ctx().args and hasattr(ctx().args, '._cli') and ctx().args._cli)
# pylint: enable=no-member

KNOWN_FORMATS = {
    'bold red': '\033[91m\033[1m',
    'bold yellow': '\033[33m\033[1m',
}

# A collection of string-identifiers of terminals capable of rendering the defined formats
FORMATTING_COMPATIBLE_TERMINALS = ['xterm']

def ansi_format_string(character_string: str, format_name: str):
    '''Format a string using ANSI escape sequences.

    This function wraps a string in the appropriate ANSI escape sequence for the given format name
    after checking whether a compatible terminal is connected to sys.stdout. If the connected terminal is
    found to be incompatible (or there is no terminal connected), the input string is returned unaltered.

    Parameters
    ------
    character_string : str
        A string to be formatted.
    format_name : str
        A string alias denoting one of a few specific known formats to apply to the string.
        Known aliases: 'bold red' and 'bold yellow'

    Returns
    ------
    str
        The string wrapped in ANSI colour escape sequences if there is a compatible terminal connected
        to stdoud and a valid format name was given, the unmodified input string otherwise.
    '''
    FORMAT_END = '\033[0m'

    ensure_not_none(character_string)

    if format_name not in KNOWN_FORMATS:
        raise ValueError("Unknown format name: {n}".format(n=format_name))
    if not sys.stdout.isatty():
        return character_string

    if 'TERM' in os.environ and os.environ['TERM'] in FORMATTING_COMPATIBLE_TERMINALS:
        return KNOWN_FORMATS[format_name] + character_string + FORMAT_END


def fail(msg=None):
    if msg:
        print(ansi_format_string('ERROR: ', 'bold red') + msg)
    raise Failure(1)


def info(msg:str):
    if _quiet():
        return
    if msg:
        print('INFO: ' + msg)
        sys.stdout.flush()


def warning(msg:str):
    if _quiet():
        return
    if msg:
        print(ansi_format_string('WARNING: ', 'bold yellow') + msg)
        sys.stdout.flush()


def verbose(msg:str):
    if not _verbose():
        return
    if msg:
        print('VERBOSE: ' + msg)
        sys.stdout.flush()


def ensure_not_empty(value):
    if not value or len(value) == 0:
        fail('passed value must not be empty')
    return value


def ensure_not_none(value):
    if value is None:
        fail('passed value must not be None')
    return value

# export shorted aliases
not_none = ensure_not_none
not_empty = ensure_not_empty


def is_yaml_file(path: CliHints.existing_file()):
    with open(path) as f:
        try:
            if yaml.load(f):
                return True
        except:
            warning('an error occurred whilst trying to parse {f}'.format(f=path))
            raise
    return False


def parse_yaml_file(path: CliHints.existing_file(), as_snd=True):
    with open(path) as f:
        if as_snd:
            return SimpleNamespaceDict(yaml.load(f))
        else:
            return yaml.load(f)


def urljoin(*parts):
    if len(parts) == 1:
        return parts[0]
    first = parts[0]
    last = parts[-1]
    middle = parts[1:-1]

    first = first.rstrip('/')
    middle = list(map(lambda s: s.strip('/'), middle))
    last = last.lstrip('/')

    return '/'.join([first] + middle + [last])


def which(cmd_name: str) -> str:
    '''
    wrapper around shutil.which that calls util.fail if the requested executable is not
    found in the PATH.
    '''
    cmd_path = shutil.which(cmd_name)
    if not cmd_path:
        fail("{cmd} not found in PATH".format(cmd=cmd_name))
    return cmd_path


def merge_dicts(base: dict, other: dict, list_semantics='set_merge'):
    '''
    merges copies of the given dict instances and returns the merge result.
    The arguments remain unmodified. However, it must be possible to copy them
    using `copy.deepcopy`.

    Merging is done using the `deepmerge` module. In case of merge conflicts, values from
    `other` overwrite values from `base`.

    By default, different from the original implementation, a "set-merge" will be applied to
    lists. This results in deduplication and potential change of element order, which may be
    undesired. In this case, set `list_semantics` to 'None'

    '''
    ensure_not_none(base)
    ensure_not_none(other)

    from deepmerge import Merger

    if list_semantics == 'set_merge':
        # monkey-patch merge-strategy for lists
        list_merge_strategy = Merger.PROVIDED_TYPE_STRATEGIES[list]
        list_merge_strategy.strategy_merge = lambda c, p, base, other: list(set(base) | set(other))

        strategy_cfg = [(list, ['merge']), (dict, ['merge'])]
        merger = Merger(strategy_cfg, ['override'], ['override'])

    from copy import deepcopy
    # copy dicts, so they remain unmodified
    return merger.merge(deepcopy(base), deepcopy(other))

