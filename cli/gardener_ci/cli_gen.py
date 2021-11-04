#!/usr/bin/env python3

# Copyright (c) 2019-2020 SAP SE or an SAP affiliate company. All rights reserved. This file is
# licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

import argparse
import enum
import functools
import inspect
import itertools
import os
import pkgutil
import sys

import ci.log
# to overwrite cli.py log level, call
# "configure_default_logging(force=True, stdout_level=logging.DEBUG)" in specific module cli
ci.log.configure_default_logging(force=True)

try:
    import ci.util
except ModuleNotFoundError:
    repo_dir = os.path.abspath(
        os.path.join(
            os.path.dirname(__name__),
            os.pardir,
            os.pardir,
        )
    )
    sys.path.insert(1, repo_dir)
    import ci.util
import ctx  # noqa: E402

import_errs = []


def print_import_errs():
    for ie in import_errs:
        ci.util.verbose(ie)


if ctx.Config.TERMINAL.value.output_columns() is not None:
    column_width = ctx.Config.TERMINAL.value.output_columns()
    # Create a custom width formatter by fixing two arguments for the default formatter class,
    # namely 'width' (defaults to 80 - 2) and 'max_help_position' (defaults to 24)
    FORMATTER_CLASS = functools.partial(
        argparse.RawDescriptionHelpFormatter,
        max_help_position=24,
        width=column_width
    )
else:
    FORMATTER_CLASS = argparse.RawDescriptionHelpFormatter


def main():
    '''
    Creates a command line parser (using argparse) for each python module found in this
    directory (except for _this_ module). For each module, a sub-command named as the
    module name is added. Each function defined in a given module is again added as a
    sub-sub-command. Based on the function signature, optional arguments are added.
    This parser is then used to parse the given ARGV. Provided that parsing succeeds,
    the thus specified function is executed.
    '''

    parser = argparse.ArgumentParser(formatter_class=FORMATTER_CLASS)
    add_global_args(parser)
    sub_command_parsers = parser.add_subparsers()
    cli_module_dir = os.path.dirname(os.path.abspath(os.path.realpath(__file__)))
    sys.path.insert(0, cli_module_dir)
    for _, module_name, _ in pkgutil.iter_modules([cli_module_dir]):
    # skip own module name
        if module_name == os.path.splitext(os.path.basename(__file__))[0]:
            continue
        if module_name == 'setup': # skip setup.py
            continue
        add_module(module_name, sub_command_parsers)
    if len(sys.argv) == 1:
        parser.print_usage()
        print_import_errs()
        sys.exit(1)
    parsed = parser.parse_args()
    # write parsed args to global ctx module so called module functions may
    # retrieve if (see ci.util.ctx)
    ctx.args = parsed

    config_from_args = ctx.load_config_from_args()
    ctx.add_config_source(config_from_args)

    # mark 'cli' mode
    ci.util._set_cli(True)
    if hasattr(parsed, 'module'):
        parsed.module.args = parsed
        parsed.func(parsed)
    print_import_errs()


def add_global_args(parser):
    parser.add_argument('--quiet', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--cfg-dir', default=None)


def add_module(module_name, parser):
    try:
        module = __import__(module_name)
    except ImportError as ie:
        if ie.name in (
            'containerregistry',
            'kubernetes',
            'protecode',
        ) or module_name in (
            'checkmarx_cli',
            'protecode_cli',
            'whitesource_cli',
        ):
            # (checkmarx|protecode|whitesource)_cli.py have different, additional
            # requirements, as they belong to the "gardener-cicd-dso" package.
            # "gardener-cicd-libs" might be present while "gardener-cicd-dso" is not,
            # therefore their requirements might be missing.
            # To still let the cli.py work as intended, ImportErrors caused by these
            # modules are ignored.
            return # XXX HACK: ignore these particular import errors for now
        raise ie

    # skip if module defines a symbol 'main'
    if hasattr(module, 'main'):
        return
    if hasattr(module, '__cmd_name__'):
        cmd_name = module.__cmd_name__
    else:
        cmd_name = module_name

    module_parser = parser.add_parser(cmd_name, formatter_class=FORMATTER_CLASS)
    module_parser.set_defaults(
      func=display_usage_function(module_parser),
      module=module
    )
    # add module-specific arguments
    if hasattr(module, '__add_module_command_args'):
        getattr(module, '__add_module_command_args')(module_parser)

    function_parsers = module_parser.add_subparsers()

    for fname, function in inspect.getmembers(module, predicate=inspect.isfunction):
        if fname.startswith('_'):
            continue # skip "private" functions
        function_docstring = inspect.getdoc(function)
        function_parser = function_parsers.add_parser(
            fname,
            description=function_docstring,
            formatter_class=FORMATTER_CLASS,
        )
        fspec = inspect.getfullargspec(function)
        function_parser.set_defaults(func=run_function(function))

        action = None
        # defaults are filled "from the end", so reverse both argnames and defaults
        for argname, default in reversed(list(
            itertools.zip_longest(
              reversed(fspec.args),
              reversed(fspec.defaults or []),
              fillvalue=NotImplemented # workaround to be able to discriminate from None
            )
          )):
            cl_arg = '--' + argname.replace('_', '-')
            annotation = fspec.annotations.get(argname, None)
            argtype = None
            action = None
            kwargs = {}
            if annotation:
                from ci.util import CliHint
                # special case: CliHint
                if type(annotation) == CliHint:
                    typehint = annotation.typehint
                    kwargs.update(annotation.argparse_args)
                else:
                    typehint = annotation
                # handle type-specific actions (lists, booleans, ..)
                if type(typehint) == type: # primitives (str, bool, int, ..)
                    argtype = typehint
                    if typehint == bool:
                        action = 'store_true'
                        argtype = None # type must not be set for store_true/store_false actions
                elif type(typehint) == list:
                    action = 'append'
                elif callable(typehint):
                    argtype = typehint
                    if inspect.isclass(typehint) and issubclass(typehint, enum.Enum):
                        # XXX: improve online-help
                        kwargs['choices'] = [
                            e for e in typehint
                        ]

            if default != NotImplemented:
                required = False
            else:
                required = True
                default = None # set back to None to not have argparser behave strangely :-)

            # add_argument does not allow 'type' as a parameter in some cases;
            # workaround this by omitting it in all cases where it is None anyway
            if argtype is not None and 'type' not in kwargs:
                kwargs['type'] = argtype

            if action:
                kwargs['action'] = action

            if default:
                help_text = kwargs.get('help', '')
                help_text += '(default: %(default)s)'
                kwargs['help'] = help_text

            function_parser.add_argument(
              cl_arg,
              required=required,
              default=default,
              **kwargs
            )

            if annotation == bool and not argname.startswith('no'):
                kwargs['help'] = '(default: False)'
                cl_arg = '--no-' + argname.replace('_', '-')
                kwargs['action'] = 'store_false'
                function_parser.add_argument(
                  cl_arg,
                  required=False,
                  dest=argname.replace('-', '_'),
                  **kwargs
                )


def run_function(function):
    def function_runner(args):
        fspec = inspect.getfullargspec(function)
        function_args = []
        for argname in fspec.args:
            function_args.append(getattr(args, argname))
        function(*function_args)
    return function_runner


def display_usage_function(parser):
    def display_usage(_):
        parser.print_usage()
    return display_usage


if __name__ == '__main__':
    main()
