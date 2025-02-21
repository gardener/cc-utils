#!/usr/bin/env python

import argparse
import enum
import logging
import os
import subprocess
import sys

try:
    import version as version_mod
except ImportError:
    # inject root-directory for easier local debugging/development
    sys.path.insert(
        1,
        os.path.join(
            os.path.dirname(__file__),
            '../../..',
        ),
    )
    import version as version_mod

logger = logging.getLogger('version-action')
logging.basicConfig(stream=sys.stderr, level=logging.INFO)


class VersionOperation(enum.StrEnum):
    NOOP = 'noop'
    SET_PRERELEASE = 'set-prerelease'
    BUMP_MAJOR = 'bump-major'
    BUMP_MINOR = 'bump-minor'
    BUMP_PATCH = 'bump-patch'


def file_exists_or_fail(*paths):
    for path in paths:
        if not os.path.exists(path):
            logger.error(f'Error: not an existing file: {path=}')
            exit(1)


def file_is_executable_or_fail(*paths):
    file_exists_or_fail(*paths)
    for path in paths:
        if not os.X_OK & os.stat(path).st_mode:
            logger.error(f'Error: not executable: {path=}')
            exit(1)


def read_version_from_file(path: str) -> str:
    if path == '-': # helpful for local testing
        return sys.stdin.read().strip()

    with open(path) as f:
        for line in f.readlines():
            if line.lstrip().startswith('#'):
                continue
            return line.strip()


def read_version_from_callback(path: str, root_dir: str) -> str:
    res = subprocess.run(
        args=(path,),
        capture_output=True,
        text=True,
        cwd=root_dir,
        check=True,
    )

    return res.stdout.strip()


def write_version_to_file(version: str, path: str):
    version = f'{version.strip()}\n'
    if path == '-': # helpful for local testing
        return sys.stdout.write(version)

    with open(path, 'w') as f:
        return f.write(version)


def write_version_to_callback(version, path: str, root_dir: str):
    subprocess.run(
        args=(path,),
        input=str(version),
        text=True,
        cwd=root_dir,
        check=True,
    )


VersionFileOrCallbacks = str | tuple[str, str]


def parse_args():
    parser = argparse.ArgumentParser()
    # cannot set defaults here, as we need to check actually passed arguments
    parser.add_argument('--versionfile', required=False, default=None)
    parser.add_argument('--read-callback', required=False, default=None)
    parser.add_argument('--write-callback', required=False, default=None)
    parser.add_argument('--root-dir', default=os.getcwd())
    parser.add_argument('--version', required=False, default=None)
    parser.add_argument('--operation', type=VersionOperation, default=VersionOperation.NOOP)
    parser.add_argument('--prerelease', default=None)
    parser.add_argument('--commit-digest', default=None)
    parser.add_argument('--extra-version-outfile', default=None)

    parsed = parser.parse_args()

    return parsed


def parse_and_check_args(parsed) -> VersionFileOrCallbacks | None:
    '''
    parse and validate passed args from ARGV. All arguments are optional.

    if arguments are invalid, exit(1). returns either:
    - None (no arguments)
    - str (versionfile)
    - str, str (read_callback, write_callback)

    returned strs are validated to be existing files (executable in case of callbacks)
    '''
    versionfile = parsed.versionfile
    read_callback = parsed.read_callback
    write_callback = parsed.write_callback

    if versionfile and (read_callback or write_callback):
        logger.error('either --versionfile, or both --read-callback, --write-callback must be set')
        exit(1)

    if bool(read_callback) ^ bool(write_callback):
        logger.error('--read-callback and --write-callback must both be set')
        exit(1)

    if versionfile:
        if versionfile == '-':
            return versionfile
        versionfile = os.path.join(parsed.root_dir, versionfile)
        file_exists_or_fail(versionfile)
        return versionfile

    if read_callback:
        # we already checked both read- and write-callbacks were set
        file_is_executable_or_fail(read_callback, write_callback)
        return read_callback, write_callback

    return None


def check_default_files(root_dir: str):
    versionfile = os.path.join(root_dir, 'VERSION')
    read_callback = os.path.join(root_dir, '.ci/read-version')
    write_callback = os.path.join(root_dir, '.ci/write-version')

    have_versionfile = os.path.isfile(versionfile)
    have_read_callback = os.path.isfile(read_callback)
    have_write_callback = os.path.isfile(write_callback)

    # read/write-callbacks have precedence over versionfile
    if have_read_callback and have_write_callback:
        file_is_executable_or_fail(
            read_callback,
            write_callback,
        )
        return read_callback, write_callback

    if have_read_callback ^ have_write_callback:
        logger.error(f'either none or both must exist: {read_callback=}, {write_callback=}')
        exit(1)

    if have_versionfile:
        return versionfile

    return None


def process_version(
    version: str,
    operation: VersionOperation,
    prerelease: str,
    commit_digest: str,
):
    if operation is VersionOperation.NOOP:
        return version
    parsed_version = version_mod.parse_to_semver(version)
    parsed_version.replace(
        prerelease=prerelease,
    )

    if operation is VersionOperation.SET_PRERELEASE:
        return str(parsed_version)

    if operation is VersionOperation.BUMP_MAJOR:
        bumped = parsed_version.bump_major()
        if prerelease:
            bumped = f'{bumped}-{prerelease}'
        return bumped
    if operation is VersionOperation.BUMP_MINOR:
        bumped = parsed_version.bump_minor()
        if prerelease:
            bumped = f'{bumped}-{prerelease}'
        return bumped
    if operation is VersionOperation.BUMP_PATCH:
        bumped = parsed_version.bump_patch()
        if prerelease:
            bumped = f'{bumped}-{prerelease}'
        return bumped

    raise ValueError('unexpected version-operation', operation)


def main():
    parsed = parse_args()
    args = parse_and_check_args(parsed)
    if args is None:
        logger.info('no versionfile/callbacks were specified - falling back to defaults')
        args = check_default_files(root_dir=parsed.root_dir)

    if args is None:
        logger.error('Error: could not find any versionfile or callbacks')
        exit(1)

    if isinstance(args, str):
        versionfile = args
        read_callback = None
        write_callback = None
    elif isinstance(args, tuple):
        versionfile = None
        read_callback, write_callback = args
    else:
        raise ValueError('unexpected type', args)

    if not parsed.version:
        if versionfile:
            version = read_version_from_file(versionfile)
        else:
            version = read_version_from_callback(
                path=read_callback,
                root_dir=parsed.root_dir,
            )
    else:
        logger.info('reading version from --version (ignoring versionfile/callbacks)')
        version = parsed.version

    logger.info(f'read {version=}')

    effective_version = process_version(
        version=version,
        operation=parsed.operation,
        prerelease=parsed.prerelease,
        commit_digest=parsed.commit_digest,
    )

    logger.info(f'{effective_version=}')

    if versionfile:
        write_version_to_file(
            version=effective_version,
            path=versionfile,
        )
        logger.info(f'wrote {effective_version=} to {versionfile=}')
    else:
        write_version_to_callback(
            version=effective_version,
            path=write_callback,
            root_dir=parsed.root_dir,
        )
        logger.info(f'sent {effective_version=} to {write_callback=}')

    if parsed.extra_version_outfile:
        if parsed.extra_version_outfile == '-':
            sys.stdout.write(f'{effective_version}\n')
        else:
            with open(parsed.extra_version_outfile, 'w') as f:
                f.write(f'{effective_version}\n')


if __name__ == '__main__':
    main()
