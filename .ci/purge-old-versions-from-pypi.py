#!/usr/bin/env python

'''
helper-script for mass-purging releases from pypi

usage: authenticate against pypi using webbrowser, extract session-id from browser and
copy-paste into this script.
'''

import argparse
import concurrent.futures
import subprocess
import sys

import requests

import ctx
import version


def releases_url(package: str):
    return f'https://pypi.org/manage/project/{package}/releases/'


def release_version_url(package: str, version: str):
    return f'https://pypi.org/manage/project/{package}/release/{version}/'


def parse_csrf_token(body: str):
    # hack: search for csrf-token
    token_idx = body.index('csrf_token')
    if not token_idx:
        print('error: did not find csrf-token in response')
        exit(1)
    body = body[token_idx:]
    # we are now within <input name="csrf_token ...> - now search for value
    value_idx = body.index('value="')
    body = body[value_idx:].removeprefix('value="')
    # csrf-token is value up to closing `"`
    value_end_idx = body.index('"')
    csrf_token = body[:value_end_idx]

    return csrf_token


_csrf_token_cache = dict() # package: token


def csrf_token_for_package_post_operation(
    sess,
    package: str,
):
    if (token := _csrf_token_cache.get(package)):
        return token

    res = sess.get(
        url=releases_url(package=package),
    )

    new_csrf_token = parse_csrf_token(body=res.text)

    _csrf_token_cache[package] = new_csrf_token

    return new_csrf_token


def rm_version(
    version,
    package,
    sess,
):
    res = sess.post(
        url=release_version_url(package=package, version=version),
        data={
            'csrf_token': csrf_token_for_package_post_operation(
                sess=sess,
                package=package,
            ),
            'confirm_delete_version': version,
        },
        headers={
            'authority': 'pypi.org',
            'referer': releases_url(package=package),
        },
    )

    return version, res


def login(
    username: str,
    password: str,
    sess: requests.Session,
):
    res = sess.get(
        url='https://pypi.org/account/login/',
    )
    csrf_token = parse_csrf_token(body=res.text)

    res = sess.post(
        url='https://pypi.org/account/login/',
        headers={
            'authority': 'pypi.org',
            'referer': 'https://pypi.org/account/login/',
        },
        data={
            'username': username,
            'password': password,
            'csrf_token': csrf_token,
        },
    )

    res.raise_for_status()

    return csrf_token


def iter_package_versions(
    packages,
    keep: int,
    keep_versions: list[str],
):
    for package in packages:
        versions = subprocess.run( # nosec B603
            (sys.executable, '-m', 'pip', 'index', 'versions', package),
            capture_output=True,
            check=True,
        ).stdout.decode('utf-8').split('\n')

        # hardcode expected output (versions are printed on second line)
        versions = versions[1]
        if not versions.startswith('Available versions:'):
            print('did not find expected output. actual:')
            print(versions)
            exit(1)

        versions = versions.removeprefix('Available versions: ')
        versions = versions.split(', ')

        versions = sorted(
            versions,
            key=version.parse_to_semver,
        ) # smallest versions come first

        if len(versions) <= keep:
            continue # not enough versions present

        remove_idx = len(versions) - keep - 1

        versions = versions[:remove_idx]
        for keep_version in keep_versions:
            if keep_version in versions:
                versions.remove(keep_version)

        yield package, versions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--keep',
        type=int,
        default=256,
    )
    parser.add_argument(
        '--keep-version',
        action='append',
        dest='keep_versions',
        default=['1.1630.0'], # hardcode: consumed by gardener-robot
    )
    parser.add_argument(
        '--package',
        action='append',
        dest='packages',
    )
    parser.add_argument(
        '--cfg-name',
    )

    parsed = parser.parse_args()

    packages = parsed.packages

    if not packages:
        print('did not specify packages - nothing to do')
        exit(1)

    if not (cfg_name := parsed.cfg_name):
        print('must pass --cfg-name (cc-config/pypi)')
        exit(1)

    cfg_factory = ctx.cfg_factory()
    pypi_cfg = cfg_factory.pypi(cfg_name)
    creds = pypi_cfg.credentials()

    sess = requests.session()

    print(f'logging in using {creds.username()=}')
    login(
        username=creds.username(),
        password=creds.passwd(),
        sess=sess,
    )

    pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=16,
    )

    def iter_jobs():
        for package, versions in iter_package_versions(
            packages=packages,
            keep=parsed.keep,
            keep_versions=parsed.keep_versions,
        ):
            print(package)
            for v in versions:
                print(f' {v}')
                yield pool.submit(
                    rm_version,
                    sess=sess,
                    version=v,
                    package=package,
                )

    for res in concurrent.futures.as_completed(iter_jobs()):
        version, http_res = res.result()
        print(f'{version}: {http_res.status_code}')

        if not http_res.ok:
            print(f'warning: {version}: {http_res.text}')


if __name__ == '__main__':
    main()
