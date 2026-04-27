# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import os
import sys

sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', '..', '..', '.github', 'actions', 'version')
    ),
)

import version_cli


# --- process_version ---

def test_process_version_noop():
    result = version_cli.process_version(
        version='1.2.3',
        operation=version_cli.VersionOperation.NOOP,
        prerelease='',
        commit_digest=None,
    )
    assert result == '1.2.3'


def test_process_version_noop_preserves_prerelease():
    result = version_cli.process_version(
        version='1.2.3-dev',
        operation=version_cli.VersionOperation.NOOP,
        prerelease='',
        commit_digest=None,
    )
    assert result == '1.2.3-dev'


def test_process_version_bump_patch():
    result = version_cli.process_version(
        version='1.2.3',
        operation=version_cli.VersionOperation.BUMP_PATCH,
        prerelease='',
        commit_digest=None,
    )
    assert result == '1.2.4'


def test_process_version_bump_minor():
    result = version_cli.process_version(
        version='1.2.3',
        operation=version_cli.VersionOperation.BUMP_MINOR,
        prerelease='',
        commit_digest=None,
    )
    assert result == '1.3.0'


def test_process_version_bump_major():
    result = version_cli.process_version(
        version='1.2.3',
        operation=version_cli.VersionOperation.BUMP_MAJOR,
        prerelease='',
        commit_digest=None,
    )
    assert result == '2.0.0'


def test_process_version_bump_patch_with_prerelease():
    result = version_cli.process_version(
        version='1.2.3',
        operation=version_cli.VersionOperation.BUMP_PATCH,
        prerelease='dev',
        commit_digest=None,
    )
    assert result == '1.2.4-dev'


def test_process_version_set_prerelease():
    result = version_cli.process_version(
        version='1.2.3',
        operation=version_cli.VersionOperation.SET_PRERELEASE,
        prerelease='rc.1',
        commit_digest=None,
    )
    assert result == '1.2.3-rc.1'


def test_process_version_set_prerelease_empty_finalises():
    result = version_cli.process_version(
        version='1.2.3-dev',
        operation=version_cli.VersionOperation.SET_PRERELEASE,
        prerelease='',
        commit_digest=None,
    )
    assert result == '1.2.3'


# --- read_version_from_file / write_version_to_file ---

def test_read_version_from_file(tmp_path):
    vf = tmp_path / 'VERSION'
    vf.write_text('1.2.3\n')
    assert version_cli.read_version_from_file(str(vf)) == '1.2.3'


def test_read_version_from_file_skips_comments(tmp_path):
    vf = tmp_path / 'VERSION'
    vf.write_text('# this is a comment\n1.2.3\n')
    assert version_cli.read_version_from_file(str(vf)) == '1.2.3'


def test_write_version_to_file(tmp_path):
    vf = tmp_path / 'VERSION'
    version_cli.write_version_to_file('2.0.0', str(vf))
    assert vf.read_text() == '2.0.0\n'


def test_write_then_read_roundtrip(tmp_path):
    vf = tmp_path / 'VERSION'
    version_cli.write_version_to_file('3.1.4', str(vf))
    assert version_cli.read_version_from_file(str(vf)) == '3.1.4'


# --- check_default_files ---

def test_check_default_files_finds_versionfile(tmp_path):
    vf = tmp_path / 'VERSION'
    vf.write_text('1.0.0\n')
    result = version_cli.check_default_files(str(tmp_path))
    assert result == str(vf)


def test_check_default_files_returns_none_when_nothing(tmp_path):
    result = version_cli.check_default_files(str(tmp_path))
    assert result is None


def test_check_default_files_callbacks_take_precedence(tmp_path):
    vf = tmp_path / 'VERSION'
    vf.write_text('1.0.0\n')
    ci = tmp_path / '.ci'
    ci.mkdir()
    rv = ci / 'read-version'
    wv = ci / 'write-version'
    rv.write_text('#!/bin/sh\necho 1.0.0\n')
    wv.write_text('#!/bin/sh\n')
    rv.chmod(0o755)
    wv.chmod(0o755)
    result = version_cli.check_default_files(str(tmp_path))
    assert isinstance(result, tuple)
    assert result == (str(rv), str(wv))


# --- parse_and_check_args ---

def test_parse_and_check_args_none_when_no_args():
    import argparse
    parsed = argparse.Namespace(
        versionfile=None,
        read_callback=None,
        write_callback=None,
        root_dir='/tmp',
    )
    result = version_cli.parse_and_check_args(parsed)
    assert result is None


def test_parse_and_check_args_versionfile(tmp_path):
    import argparse
    vf = tmp_path / 'VERSION'
    vf.write_text('1.0.0\n')
    parsed = argparse.Namespace(
        versionfile='VERSION',
        read_callback=None,
        write_callback=None,
        root_dir=str(tmp_path),
    )
    result = version_cli.parse_and_check_args(parsed)
    assert result == str(vf)


def test_parse_and_check_args_exits_on_conflict():
    import argparse
    parsed = argparse.Namespace(
        versionfile='VERSION',
        read_callback='/some/read',
        write_callback=None,
        root_dir='/tmp',
    )
    with __import__('pytest').raises(SystemExit):
        version_cli.parse_and_check_args(parsed)


def test_parse_and_check_args_exits_on_single_callback():
    import argparse
    parsed = argparse.Namespace(
        versionfile=None,
        read_callback='/some/read',
        write_callback=None,
        root_dir='/tmp',
    )
    with __import__('pytest').raises(SystemExit):
        version_cli.parse_and_check_args(parsed)
