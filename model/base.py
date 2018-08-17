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
from util import not_none


class ModelValidationError(ValueError):
    '''
    exception to be raised upon model validation errors
    '''
    pass


class ConfigElementNotFoundError(ValueError):
    pass


class ModelBase(object):
    '''
    Base class for 'dict-based' configuration classes (i.e. classes that expose contents
    from a dict through a set of 'getter' methods.

    Extenders _may_ overwrite `_required_attributes(self)` and return an iterable of attribute
    identifiers. If such an iterable is returned, the ctor ensures that all specified attributes be
    contained in the given dictionary (ModelValidationError is raised on absent attribs).
    '''

    def __init__(self, raw_dict):
        self.raw = not_none(raw_dict)
        self._validate_dict()

    def _required_attributes(self):
        return []

    def _validate_dict(self):
        required_attribs = self._required_attributes()
        missing_keys = [k for k in required_attribs if k not in self.raw]
        if len(list(missing_keys)) > 0:
            raise ModelValidationError('missing required attribute(s): {a}'.format(
                a=', '.join(missing_keys))
            )

    def __str__(self):
        return '{c} {a}'.format(
            c=self.__class__.__name__,
            a=str(self.raw),
        )


class NamedModelElement(ModelBase):
    def __init__(self, name, raw_dict, *args, **kwargs):
        self._name = not_none(name)
        super().__init__(raw_dict=raw_dict, *args, **kwargs)

    def name(self):
        return self._name

    def __str__(self):
        return '{n}: {d}'.format(n=self.name(), d=self.raw)
