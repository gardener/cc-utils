# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import os

import mako.template
import mako.lookup

import makoutil

steps_dir = os.path.abspath(os.path.dirname(__file__))
template_lookup = mako.lookup.TemplateLookup(directories=(steps_dir,))


def step_template(name):
    with makoutil.template_lock:
        return template_lookup.get_template(f'/{name}.mako')


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
