#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2021 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import datetime
import enum
import json
import typing

import ocm


# adds the defined label to a list of labels. won't overwrite existing labels with the same key
def add_label(
    src_labels: typing.Sequence[ocm.Label],
    label: ocm.Label,
) -> typing.Sequence[ocm.Label]:
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
