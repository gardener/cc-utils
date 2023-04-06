#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2021 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import dataclasses
import datetime
import enum
import json
import requests
import typing

import gci.componentmodel as cm


# adds the defined label to a list of labels. won't overwrite existing labels with the same key
def add_label(
    src_labels: typing.Sequence[cm.Label],
    label: cm.Label,
) -> typing.Sequence[cm.Label]:
    label_exists = [src_label for src_label in src_labels if src_label.name == label.name]
    if label_exists:
        # label exists --> do not overwrite it
        return src_labels
    else:
        # label doesn't exist --> append it
        return src_labels + [
            label,
        ]


class EnumJSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, enum.Enum):
            return o.value
        elif isinstance(o, datetime.datetime):
            return o.isoformat()
        return super().default(o)


@dataclasses.dataclass
class PEMBlock:
    type = ''
    headers = {}
    content = ''


def parse_pem(
    pem_str: str,
) -> typing.Sequence[PEMBlock]:
    pem_blocks = []

    pem_block = None
    for line in pem_str.splitlines():
        if line.startswith('-----BEGIN '):
            pem_block = PEMBlock()
            splitted = line.strip('-').split(' ', maxsplit=1)
            pem_block.type = splitted[1]
        elif line.startswith('-----END '):
            pem_blocks.append(pem_block)
            pem_block = None
        elif ':' in line:
            if not pem_block:
                raise ValueError()
            splitted = line.split(':', maxsplit=1)
            key = splitted[0].strip()
            val = splitted[1].strip()
            pem_block.headers[key] = val
        else:
            if not pem_block:
                raise ValueError()
            if line.strip() != "":
                pem_block.content = pem_block.content + line

    return pem_blocks


def sign_with_signing_server(
    server_url: str,
    content: bytes,
    root_ca_cert_path: str = None,
) -> str:
    headers = {
        'Accept': 'application/x-pem-file',
    }
    response = requests.post(
        url=f'{server_url}/sign/rsassa-pkcs1-v1_5?hashAlgorithm=sha256',
        headers=headers,
        data=content,
        verify=root_ca_cert_path,
    )
    response.raise_for_status()

    pem_blocks = parse_pem(response.content.decode())

    signature_pem = [b for b in pem_blocks if b.type.lower() == 'signature']
    if len(signature_pem) != 1:
        raise RuntimeError('signing server response doesn\'t contain signature pem block')

    return signature_pem[0].content
