# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import sys
import os

import inspect

import concourse.model.base as model_base
import concourse.model.traits as traits

# add repository root to pythonpath
sys.path.append(os.path.abspath('../..'))


def module(qualified_module_name: str):
    module = __import__(qualified_module_name)
    for submodule_name in qualified_module_name.split('.')[1:]:
        module = getattr(module, submodule_name)
    return module


def trait_module(trait_name: str):
    qualified_module_name = f'{traits.__name__}.{trait_name}'
    return module(qualified_module_name)


def trait_class(trait_name: str):
    module = trait_module(trait_name=trait_name)
    for _, t in inspect.getmembers(module, predicate=inspect.isclass):
        if t == model_base.Trait:
            continue # skip import
        if issubclass(t, model_base.Trait):
            return t
    raise RuntimeError('failed to find trait class in module ' + trait_name)


def trait_instance(trait_name: str):
    ctor = trait_class(trait_name=trait_name)
    return ctor(name=trait_name, variant_name='dummy', raw_dict={})


def model_element_type(qualified_type_name: str):
    module_name, class_name = qualified_type_name.rsplit('.', 1)

    mod = module(module_name)
    return getattr(mod, class_name)
