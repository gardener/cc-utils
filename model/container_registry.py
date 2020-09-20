# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import enum

import json

import ci.util

from model.base import (
    BasicCredentials,
    NamedModelElement,
    ModelDefaultsMixin,
)

from ci.util import check_type


class Privileges(enum.Enum):
    READ_ONLY = 'readonly'
    READ_WRITE = 'readwrite'


class ContainerRegistryConfig(NamedModelElement, ModelDefaultsMixin):
    '''
    Not intended to be instantiated by users of this module
    '''
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_defaults(self.raw)

    def _defaults_dict(self):
        return {
            'privileges': Privileges.READ_ONLY.value,
        }

    def _optional_attributes(self):
        return {
            'image_reference_prefixes',
            'host',
            'email',
        }

    def _required_attributes(self):
        return {
            'username',
            'password',
        }

    def credentials(self):
        # XXX handle different container registry types
        return GcrCredentials(self.raw)

    def has_service_account_credentials(self):
        return GcrCredentials(self.raw).has_service_account_credentials()

    def privileges(self) -> Privileges:
        return Privileges(self.raw['privileges'])

    def image_reference_prefixes(self):
        prefixes = self.raw.get('image_reference_prefixes', ())
        if isinstance(prefixes, str):
            return [prefixes]
        return prefixes

    def image_ref_matches(
        self,
        image_reference: str,
        privileges: Privileges=None,
    ):
        '''
        returns a boolean indicating whether a given container image reference matches any
        configured image reference prefixes (thus indicating this cfg might be adequate for
        retrieving or deploying the given container image using this cfg).

        If no image reference prefixes are configured, `False` is returned.
        '''
        check_type(image_reference, str)

        prefixes = self.image_reference_prefixes()
        if not prefixes:
            return False
        if privileges and self.privileges() != privileges:
            return False

        for prefix in prefixes:
            if image_reference.startswith(prefix):
                return True
        return False


class GcrCredentials(BasicCredentials):
    '''
    Not intended to be instantiated by users of this module
    '''
    def _optional_attributes(self):
        return {'image_reference_prefixes', 'host', 'email'}

    def host(self):
        return self.raw.get('host')

    def email(self):
        return self.raw.get('email')

    def has_service_account_credentials(self):
        '''
        heuristically (aka HACKY!!!) guesses whether the configured passwd _could_ be a
        GCP Service Account document
        '''
        try:
            json.loads(self.passwd())
            return True
        except json.decoder.JSONDecodeError:
            return False

    def service_account_credentials(self): # -> 'google.oauth2.service_account.Credentials':
        import google.oauth2.service_account
        return google.oauth2.service_account.Credentials.from_service_account_info(
            json.loads(self.passwd())
        )


def find_config(image_reference: str, privileges:Privileges=None) -> 'GcrCredentials':
    ci.util.check_type(image_reference, str)
    cfg_factory = ci.util.ctx().cfg_factory()

    matching_cfgs = [
        cfg for cfg in
        cfg_factory._cfg_elements('container_registry')
        if cfg.image_ref_matches(image_reference, privileges=privileges)
    ]

    if not matching_cfgs:
        return None

    # return first match
    return matching_cfgs[0]
