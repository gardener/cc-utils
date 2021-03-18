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

from contextlib import contextmanager
from io import StringIO
import sys
import typing


@contextmanager
def capture_out():
    new_stdout, new_stderr = StringIO(), StringIO()
    old_stdout, old_stderr = sys.stdout, sys.stderr
    try:
        sys.stdout, sys.stderr = new_stdout, new_stderr
        yield sys.stdout, sys.stderr
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr


@contextmanager
def replace_modules(modules:dict):
    '''
    ctx manager that will replace the given modules in sys.modules and restore them
    afterwards
    '''
    original_modules = {
        name: sys.modules[name] for name in modules.keys()
    }
    try:
        for name, module in modules.items():
            sys.modules[name] = module
        yield None
    finally:
        for name, module in original_modules.items():
            sys.modules[name] = module


class AssertMixin:
    def assertEmpty(self, iterable, msg=None):
        if issubclass(type(iterable), typing.Sequence):
            leng = len(iterable)
            if leng == 0:
                return
            raise self.failureException('iterable was not empty')
        try:
            next(iterable)
            raise self.failureException('iterable was not empty')
        except StopIteration:
            return # ok - iterable was empty
        except Exception as e:
            raise self.failureException(str(e))
