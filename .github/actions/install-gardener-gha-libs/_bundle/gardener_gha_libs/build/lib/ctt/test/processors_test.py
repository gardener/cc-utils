# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import pytest

import ctt.model
import ctt.processors as processors


@pytest.fixture
def replication_resource_element():
    return ctt.model.ReplicationResourceElement(
        source=None,
        target=None,
        component_id=None,
        src_ocm_repo=None,
    )


def test_noop_processor(replication_resource_element):
    examinee = processors.NoOpProcessor()

    result = examinee.process(replication_resource_element)

    assert result is replication_resource_element


def test_filefilter_processor(replication_resource_element, tmpdir):
    filter_file = tmpdir.join('filters')
    filter_file.write('remove/me')

    examinee = processors.FileFilter(
        filter_files=[filter_file],
    )

    result = examinee.process(replication_resource_element)
    remove_files = result.remove_files

    assert remove_files == ['remove/me',]


def test_manifestfilter_processor(replication_resource_element):
    examinee = processors.ManifestFilterProcessor(
        strip_manifest_attributes=['manifests[].platform.features'],
    )

    result = examinee.process(replication_resource_element)

    assert result.strip_manifest_attributes == ['manifests[].platform.features']


def test_manifestfilter_processor_validates_paths():
    with pytest.raises(ValueError):
        processors.ManifestFilterProcessor(
            strip_manifest_attributes=['manifests[]'],
        )


def test_appendfiles_processor_defaults_to_replace_removed(replication_resource_element, tmp_path):
    src = tmp_path / 'curated.pem'
    src.write_bytes(b'payload')

    examinee = processors.AppendFiles(
        src_path=str(src),
        tgt_path='/etc/ssl/certs/ca-certificates.crt',
    )

    result = examinee.process(replication_resource_element)

    assert result.append_files == [
        ctt.model.AppendFilesEntry(
            src_path=str(src),
            tgt_path='etc/ssl/certs/ca-certificates.crt',
        ),
    ]
    assert result.append_mode is ctt.model.AppendMode.REPLACE_REMOVED
    assert result.append_layer_idx is None


def test_appendfiles_processor_insert_into_layer(replication_resource_element, tmp_path):
    src = tmp_path / 'curated.pem'
    src.write_bytes(b'payload')

    examinee = processors.AppendFiles(
        src_path=str(src),
        tgt_path='/etc/ssl/certs/ca-certificates.crt',
        mode='insert_into_layer',
        layer_idx=-1,
    )

    result = examinee.process(replication_resource_element)

    assert result.append_mode is ctt.model.AppendMode.INSERT_INTO_LAYER
    assert result.append_layer_idx == -1


def test_appendfiles_processor_rejects_missing_src(tmp_path):
    with pytest.raises(ValueError, match='does not exist'):
        processors.AppendFiles(
            src_path=str(tmp_path / 'not-there'),
            tgt_path='/etc/ssl/certs/ca-certificates.crt',
        )


def test_appendfiles_processor_rejects_invalid_tgt(tmp_path):
    src = tmp_path / 'curated.pem'
    src.write_bytes(b'payload')

    with pytest.raises(ValueError):
        processors.AppendFiles(
            src_path=str(src),
            tgt_path='',
        )

    with pytest.raises(ValueError):
        processors.AppendFiles(
            src_path=str(src),
            tgt_path='/../outside',
        )


def test_appendfiles_processor_rejects_invalid_mode(tmp_path):
    src = tmp_path / 'curated.pem'
    src.write_bytes(b'payload')

    with pytest.raises(ValueError, match='unsupported'):
        processors.AppendFiles(
            src_path=str(src),
            tgt_path='/x',
            mode='bogus-mode',
        )


def test_appendfiles_processor_requires_layer_idx_for_insert(tmp_path):
    src = tmp_path / 'curated.pem'
    src.write_bytes(b'payload')

    with pytest.raises(ValueError, match='layer_idx is required'):
        processors.AppendFiles(
            src_path=str(src),
            tgt_path='/x',
            mode='insert_into_layer',
        )

    with pytest.raises(ValueError, match='only supported'):
        processors.AppendFiles(
            src_path=str(src),
            tgt_path='/x',
            mode='new_layer',
            layer_idx=-1,
        )


def test_chained_processor_applies_in_order(replication_resource_element, tmp_path):
    filter_file = tmp_path / 'rm.txt'
    filter_file.write_text('remove/me')
    payload = tmp_path / 'curated.pem'
    payload.write_bytes(b'payload')

    chained = processors.ChainedProcessor(
        processors=[
            processors.FileFilter(filter_files=[str(filter_file)]),
            processors.AppendFiles(
                src_path=str(payload),
                tgt_path='/etc/ssl/certs/ca-certificates.crt',
            ),
            processors.AppendFiles(
                src_path=str(payload),
                tgt_path='/etc/pki/tls/certs/ca-bundle.crt',
            ),
        ],
    )

    result = chained.process(replication_resource_element)

    assert result.remove_files == ['remove/me']
    assert result.append_files == [
        ctt.model.AppendFilesEntry(
            src_path=str(payload),
            tgt_path='etc/ssl/certs/ca-certificates.crt',
        ),
        ctt.model.AppendFilesEntry(
            src_path=str(payload),
            tgt_path='etc/pki/tls/certs/ca-bundle.crt',
        ),
    ]
    assert result.append_mode is ctt.model.AppendMode.REPLACE_REMOVED


def test_chained_processor_rejects_empty_list():
    with pytest.raises(ValueError):
        processors.ChainedProcessor(processors=[])


def test_appendfiles_multi_entry_conflict_detection(replication_resource_element, tmp_path):
    src = tmp_path / 'curated.pem'
    src.write_bytes(b'payload')

    first = processors.AppendFiles(
        src_path=str(src),
        tgt_path='/etc/ssl/certs/ca-certificates.crt',
        mode='new_layer',
    )
    conflicting = processors.AppendFiles(
        src_path=str(src),
        tgt_path='/x',
        mode='insert_into_layer',
        layer_idx=-1,
    )

    replication_resource_element = first.process(replication_resource_element)
    with pytest.raises(ValueError, match='conflicting'):
        conflicting.process(replication_resource_element)


def test_appendfiles_dedup_and_append(replication_resource_element, tmp_path):
    src = tmp_path / 'curated.pem'
    src.write_bytes(b'payload')

    first = processors.AppendFiles(src_path=str(src), tgt_path='/x')
    second_same = processors.AppendFiles(src_path=str(src), tgt_path='/x')
    third_different_tgt = processors.AppendFiles(src_path=str(src), tgt_path='/y')

    rre = first.process(replication_resource_element)
    rre = second_same.process(rre)
    rre = third_different_tgt.process(rre)

    assert rre.append_files == [
        ctt.model.AppendFilesEntry(src_path=str(src), tgt_path='x'),
        ctt.model.AppendFilesEntry(src_path=str(src), tgt_path='y'),
    ]


def test_filefilter_processor_with_globs(replication_resource_element, tmpdir):
    filter_file = tmpdir.join('filters')
    filter_file.write('remove/me\n')

    examinee = processors.FileFilter(
        filter_files=[filter_file],
        filter_globs=['/etc/ssl/certs/*.pem', '/etc/ssl/certs/*.0'],
    )

    result = examinee.process(replication_resource_element)

    assert result.remove_files == ['remove/me']
    assert result.remove_file_globs == ['etc/ssl/certs/*.pem', 'etc/ssl/certs/*.0']


def test_filefilter_processor_globs_only(replication_resource_element):
    examinee = processors.FileFilter(
        filter_globs=['/usr/share/ca-certificates/mozilla/*.crt'],
    )

    result = examinee.process(replication_resource_element)

    assert result.remove_files == []
    assert result.remove_file_globs == ['usr/share/ca-certificates/mozilla/*.crt']


def test_filefilter_processor_no_args(replication_resource_element):
    examinee = processors.FileFilter()

    result = examinee.process(replication_resource_element)

    assert result.remove_files == []
    assert result.remove_file_globs == []
