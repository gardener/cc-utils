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


import ci.util


class ModelValidationError(ValueError):
    '''
    exception to be raised upon model validation errors
    '''
    pass


class ConfigElementNotFoundError(ValueError):
    pass


class ModelValidationMixin(object):
    def _required_attributes(self):
        return ()

    def _optional_attributes(self):
        return ()

    def _known_attributes(self):
        return set(self._required_attributes()) | \
                set(self._optional_attributes()) | \
                set(self._defaults_dict().keys())

    def validate(self):
        self._validate_required_attributes()
        self._validate_known_attributes()

    def _validate_required_attributes(self):
        missing_attributes = [a for a in self._required_attributes() if a not in self.raw]
        if missing_attributes:
            raise ModelValidationError(
                'the following required attributes are absent: {m}'.format(
                    m=', '.join(missing_attributes),
                )
            )

    def _validate_known_attributes(self):
        unknown_attributes = [a for a in self.raw if a not in self._known_attributes()]
        if unknown_attributes:
            if hasattr(self, 'name'):
                if callable(self.name):
                    name = self.name()
                else:
                    name = str(self.name)
            else:
                name = '<unknown>'

            raise ModelValidationError(
                '{c}:{e}: the following attributes are unknown: {m}'.format(
                    c=type(self).__name__,
                    e=str(name),
                    m=', '.join(unknown_attributes)
                )
            )


class ModelDefaultsMixin(object):
    def _defaults_dict(self):
        return {}

    def _apply_defaults(self, raw_dict):
        self.raw = ci.util.merge_dicts(
            self._defaults_dict(),
            raw_dict,
        )


class ModelBase(ModelValidationMixin, ModelDefaultsMixin):
    '''
    Base class for 'dict-based' configuration classes (i.e. classes that expose contents
    from a dict through a set of 'getter' methods.

    Extenders _may_ overwrite `_required_attributes(self)` and return an iterable of attribute
    identifiers. If such an iterable is returned, the ctor ensures that all specified attributes be
    contained in the given dictionary (ModelValidationError is raised on absent attribs).
    '''

    def __init__(self, raw_dict):
        self.raw = ci.util.not_none(raw_dict)

    def __repr__(self):
        return '{c} {a}'.format(
            c=self.__class__.__name__,
            a=str(self.raw),
        )


class NamedModelElement(ModelBase):
    def __init__(self, name, raw_dict, *args, **kwargs):
        self._name = ci.util.not_none(name)
        super().__init__(raw_dict=raw_dict, *args, **kwargs)

    def _optional_attributes(self):
        # workaround: NamedModelElement allows any attribute; it would
        # obviously be a better way to disable this check (e.g. split into
        # separate mixin and not add it to NME
        return set(self.raw.keys())

    def name(self):
        return self._name

    def __repr__(self):
        return f'{self.__class__.__qualname__}: {self.name()}'

    def __str__(self):
        return '{n}: {d}'.format(n=self.name(), d=self.raw)


class BasicCredentials(ModelBase):
    '''
    Base class for configuration objects that contain basic authentication credentials
    (i.e. a username and a password)

    Not intended to be instantiated
    '''

    def username(self):
        return self.raw.get('username')

    def passwd(self):
        return self.raw.get('password')

    def as_tuple(self):
        return (self.username(), self.passwd())

    def _required_attributes(self):
        return ['username', 'password']
