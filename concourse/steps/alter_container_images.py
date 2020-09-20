# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import ci.util
import container.util


def alter_image(
    src_ref: str,
    tgt_ref: str,
    filter_path_file: str,
):
    ci.util.not_none(src_ref)
    ci.util.not_none(tgt_ref)
    if src_ref == tgt_ref:
        raise ValueError(f'src and tgt must not be be equal: {src_ref} {tgt_ref}')

    with open(ci.util.existing_file(filter_path_file)) as f:
        rm_paths = [
            p.strip() for p in f.readlines()
            if p.strip() and not p.strip().startswith('#')
        ]

    container.util.filter_image(
        source_ref=src_ref,
        target_ref=tgt_ref,
        remove_files=rm_paths,
    )
