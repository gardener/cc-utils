# SPDX-FileCopyrightText: 2021 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import ctt.process_dependencies as process_dependencies


def test_processor_instantiation(tmpdir):
    tmpfile = tmpdir.join('a_file')
    tmpfile.write('')  # touch

    cfg = {
        'filter': {
            'type': 'ImageFilter',
            'kwargs': {
                'include_image_refs': ['^aaa'],
            },
        },
        'processor': {
            'type': 'FileFilter',
            'kwargs': {
                'filter_files': [tmpfile],
            },
        },
        'upload': {
            'type': 'PrefixUploader',
            'kwargs': {
                'prefix': 'a/prefix',
            },
        },
    }

    _ = process_dependencies.processing_pipeline(cfg)

    # test shared processor
    shared_proc = {'shared_p': cfg['processor']}
    cfg['processor'] = 'shared_p'

    _ = process_dependencies.processing_pipeline(cfg, shared_processors=shared_proc)

    # revert
    cfg['processor'] = shared_proc['shared_p']

    # test shared uploader
    shared_upld = {'shared_u': cfg['upload']}
    cfg['upload'] = 'shared_u'

    _ = process_dependencies.processing_pipeline(cfg, shared_uploaders=shared_upld)
