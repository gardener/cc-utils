# Copyright (c) 2022 SAP SE or an SAP affiliate company. All rights reserved. This file is
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

import dataclasses
import json

import oci.model as om


COSIGN_SIGNATURE_TYPE = "gardener.vnd/oci/cosign-signature"


@dataclasses.dataclass
class Payload:
    '''
    Class which can be used for generating the unsigned payload of a
    cosign signature for a specific container image.
    '''
    image_ref: om.OciImageReference
    annotations: dict

    def __init__(self, image_ref: str, annotations: dict = None):
        self.image_ref = om.OciImageReference.to_image_ref(image_ref)
        if not self.image_ref.has_digest_tag:
            raise ValueError('only images that are referenced via a digest are allowed')

        self.annotations = annotations

    def normalised_json(self):
        '''
        return the normalised (ordered keys, no whitespace) json representation.
        the returned payload can then be hashed, signed, and used as a cosign signature.
        '''
        data = {
            "critical": {
                "identity": {
                    "docker-reference": self.image_ref.ref_without_tag,
                },
                "image": {
                    "docker-manifest-digest": self.image_ref.tag,
                },
                "type": COSIGN_SIGNATURE_TYPE,
            },
            "optional": self.annotations,
        }

        return json.dumps(
            obj=data,
            separators=(',', ':'),
            sort_keys=True,
        )
