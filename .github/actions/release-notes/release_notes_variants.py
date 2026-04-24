#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


def normalise_variant_cfg(raw: list[dict]) -> list[dict]:
    '''
    Normalises a list of release-notes-variant config dicts as provided via action input:
    - splits 'audiences' and 'categories' comma-separated strings into lists
    - removes absent optional keys (so dataclass defaults apply downstream)
    '''
    for variant in raw:
        for key in ('audiences', 'categories'):
            if (value := variant.get(key, None)):
                variant[key] = [v.strip() for v in value.split(',')]
            elif key in variant:
                del variant[key]
    return raw
