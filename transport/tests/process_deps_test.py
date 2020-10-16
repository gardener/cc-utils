import processing.processing as processing


def test_processor_instantiation(tmpdir):
    tmpfile = tmpdir.join('a_file')
    tmpfile.write('') # touch

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
                'context_url': 'registry.local/context',
                'prefix': 'a/prefix',
            },
        },
    }

    processing.processing_pipeline(cfg)

    # test shared processor
    shared_proc = {'shared_p': cfg['processor']}
    cfg['processor'] = 'shared_p'

    processing.processing_pipeline(cfg, shared_processors=shared_proc)

    # revert
    cfg['processor'] = shared_proc['shared_p']

    # test shared uploader
    shared_upld = {'shared_u': cfg['upload']}
    cfg['upload'] = 'shared_u'

    processing.processing_pipeline(cfg, shared_uploaders=shared_upld)
