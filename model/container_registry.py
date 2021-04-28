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

import base64
import json

import ci.util
import oci.util
import oci.auth as oa

from model.base import (
    BasicCredentials,
    NamedModelElement,
    ModelDefaultsMixin,
)

from ci.util import check_type


class ContainerRegistryConfig(NamedModelElement, ModelDefaultsMixin):
    '''
    Not intended to be instantiated by users of this module
    '''
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_defaults(self.raw)

    def _defaults_dict(self):
        return {
            'privileges': oa.Privileges.READONLY.value,
        }

    def _optional_attributes(self):
        return {
            'image_reference_prefixes',
            'host',
            'email',
            'api_base_url',
        }

    def _required_attributes(self):
        return {
            'username',
            'password',
        }

    def api_base_url(self):
        return self.raw.get('api_base_url')

    def credentials(self):
        # XXX handle different container registry types
        return GcrCredentials(self.raw)

    def has_service_account_credentials(self):
        return GcrCredentials(self.raw).has_service_account_credentials()

    def as_docker_auths(self):
        '''
        returns a representation of the credentials from this registry-cfg as "docker-auths",
        which can be used to populate a docker-cfg file ($HOME/.docker/config.json) below the
        `auths` attr
        '''
        auth_str = f'{self.credentials().username()}:{self.credentials().passwd()}'
        auth_str = base64.b64encode(auth_str.encode('utf-8')).decode('utf-8')

        auths = {
            host: {'auth': auth_str} for host in self.image_reference_prefixes()
        }

        return auths

    def privileges(self) -> oa.Privileges:
        return oa.Privileges(self.raw['privileges'])

    def image_reference_prefixes(self):
        prefixes = self.raw.get('image_reference_prefixes', ())
        if isinstance(prefixes, str):
            return [prefixes]
        return prefixes

    def image_ref_matches(
        self,
        image_reference: str,
        privileges: oa.Privileges=None,
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
        if privileges:
            # if privileges were specified, ours must be "great enough" (greater means more privs)
            if self.privileges() < privileges:
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


def find_config(
    image_reference: str,
    privileges:oa.Privileges=None,
    _normalised_image_reference=False,
) -> ContainerRegistryConfig:
    ci.util.check_type(image_reference, str)
    cfg_factory = ci.util.ctx().cfg_factory()

    matching_cfgs = sorted((
        cfg for cfg in
        cfg_factory._cfg_elements('container_registry')
        if cfg.image_ref_matches(image_reference, privileges=privileges)
        ),
        key=lambda c:c.privileges(),
    )

    if not matching_cfgs:
        # finally give up - did not match anything, even after normalisation
        if _normalised_image_reference:
            return None
        else:
            return find_config(
                image_reference=oci.util.normalise_image_reference(image_reference=image_reference),
                privileges=privileges,
                _normalised_image_reference=True,
            )

    # return first match (because they are sorted, this will be the one with least privileges)
    return matching_cfgs[0]
