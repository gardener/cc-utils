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

import typing
from xml.etree import ElementTree as ET

import container.registry
import product.model


def _se_with_text(parent, tag, text, *args, **kwargs):
    se = ET.SubElement(parent, tag, *args, **kwargs)
    se.text = text
    return se


def container_image_refs_to_xml(
    container_images: typing.Iterable[product.model.ContainerImage],
    contact_name='dev',
    contact_email='dev@sap.com',
):
    root = ET.Element('context', xmlns='http://client.incr.tpip.bosap.com')

    contact = ET.SubElement(root, 'contact')
    _se_with_text(contact, 'name', contact_name)
    _se_with_text(contact, 'email', contact_email)

    for id, img_ref in enumerate([c.image_reference() for c in container_images]):
        di = ET.SubElement(root, 'Dockerimage', id=str(id))
        normalised_ref = container.registry.normalise_image_reference(img_ref)

        host, path_and_tag = normalised_ref.split('/', 1)
        path, tag = path_and_tag.rsplit(':', 1)

        _se_with_text(di, 'Host', host)
        _se_with_text(di, 'Repo-path', '/' + path)
        _se_with_text(di, 'Tag', tag)

    tree = ET.ElementTree(root)
    return tree
