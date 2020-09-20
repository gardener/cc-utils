# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import os

import mako.template

import ci.util


steps_dir = os.path.abspath(os.path.dirname(__file__))


def step_template(name):
    step_file = ci.util.existing_file(os.path.join(steps_dir, name + '.mako'))

    return mako.template.Template(filename=step_file)


def step_def(name):
    template = step_template(name)

    return template.get_def(name + '_step').render


def step_lib_def(name):
    template = step_template(name)

    return template.get_def(name + '_step_lib').render


def step_lib(name):
    module_file = os.path.join(steps_dir, name + '.py')
    with open(module_file) as f:
        return f.read()
