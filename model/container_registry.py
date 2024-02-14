# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import base64
import json
import logging
import urllib.parse
import typing

import dacite

import ci.log
import ci.util
import oci.util
import oci.auth as oa
import oci.model as om

import model.base
from model.base import (
    BasicCredentials,
    NamedModelElement,
    ModelDefaultsMixin,
)

from ci.util import check_type


ci.log.configure_default_logging()
logger = logging.getLogger(__name__)


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
            'registry_type',
            'rotation_cfg',
        }

    def _required_attributes(self):
        return {
            'username',
            'password',
        }

    def validate(self):
        super().validate()
        if (
            self.rotation_cfg()
            and self.registry_type() not in (
                om.OciRegistryType.GAR,
                om.OciRegistryType.GCR,
            )
        ):
            raise model.base.ModelValidationError(
                f'rotation_cfg not allowed for {self.registry_type()=}'
            )

    def client_email(self) -> str:
        return json.loads(self.password())['client_email']

    def private_key_id(self) -> str:
        return json.loads(self.password())['private_key_id']

    def registry_type(self):
        try:
            return om.OciRegistryType(self.raw.get('registry_type'))
        except ValueError:
            return om.OciRegistryType.UNKNOWN

    def api_base_url(self):
        return self.raw.get('api_base_url')

    def rotation_cfg(self) -> model.base.CfgElementReference:
        '''
        used to specify cfg-element to use for cross-rotation
        '''
        raw = self.raw.get('rotation_cfg')
        if raw:
            return dacite.from_dict(
                data_class=model.base.CfgElementReference,
                data=raw,
            )

        return None

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

        hostnames = set()
        for image_prefix in self.image_reference_prefixes():
            if '://' not in image_prefix:
                image_prefix = f'x://{image_prefix}'
            hostnames.add(urllib.parse.urlparse(image_prefix).hostname)

        auths = {
            host: {'auth': auth_str} for host in hostnames
        }

        # ugly workaround for docker-hub, see:
        # https://github.com/GoogleContainerTools/kaniko/issues/1209
        # -> if there is a cfg for "docker-hub", implicitly patch-in the v1-flavoured cfg
        if not (docker_v1_prefix := 'https://index.docker.io/v1/') in auths:
            # try both w/ slash and w/o slash suffix
            def auth_for_prefix(prefix):
                if v := auths.get(prefix, None):
                    return v
                return auths.get(prefix + '/', None)

            for candidate in 'registry-1.docker.io', 'docker.io', 'index.docker.io':
                if auth_cfg := auth_for_prefix(prefix=candidate):
                    auths[docker_v1_prefix] = auth_cfg
                    break # use first (we have no way of knowing which one would be better..)

        logger.info(f'using cfg {self.name()=} for prefixes {list(auths.keys())}')

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
        return {
            'email',
            'host',
            'image_reference_prefixes',
        }

    def email(self):
        # used by KubernetesSecretsHelper's create_gcr_secret
        return self.raw.get('email')

    def host(self):
        # used in lss
        return self.raw.get('host')

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
    image_reference: typing.Union[str, om.OciImageReference],
    privileges:oa.Privileges=None,
    _normalised_image_reference=False,
    cfg_factory=None,
) -> typing.Optional[ContainerRegistryConfig]:
    image_reference = str(image_reference)
    if not cfg_factory:
        cfg_factory = ci.util.ctx().cfg_factory()

    if isinstance(image_reference, om.OciImageReference):
        image_reference = image_reference.normalised_image_reference()
        _normalised_image_reference = True

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
                cfg_factory=cfg_factory,
            )

    # return first match (because they are sorted, this will be the one with least privileges)
    registry_cfg = matching_cfgs[0]

    return registry_cfg
