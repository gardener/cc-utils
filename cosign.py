# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


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
