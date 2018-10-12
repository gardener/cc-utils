# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed
# under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

import sys
import os

import inspect

import concourse.model.base as model_base
import concourse.model.traits as traits

# add repository root to pythonpath
sys.path.append(os.path.abspath('../..'))


def trait_module(trait_name: str):
    qualified_module_name = f'{traits.__name__}.{trait_name}'
    module = __import__(qualified_module_name)
    for submodule_name in qualified_module_name.split('.')[1:]:
        module = getattr(module, submodule_name)
    return module


def trait_class(trait_name: str):
    module = trait_module(trait_name=trait_name)
    for _, t in inspect.getmembers(module, predicate=inspect.isclass):
        if t == model_base.Trait:
            continue # skip import
        if issubclass(t, model_base.Trait):
            return t
    raise RuntimeError('failed to find trait class in module ' + self.trait_name)


def trait_instance(trait_name: str):
    ctor = trait_class(trait_name=trait_name)
    return ctor(name=trait_name, variant_name='dummy', raw_dict={})
