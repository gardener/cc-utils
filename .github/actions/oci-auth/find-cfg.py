#!/usr/bin/env python

import argparse
import os
import pprint
import sys

import yaml

own_dir = os.path.dirname(__file__)
cfgs_path = os.path.join(own_dir, 'oidc-cfgs.yaml')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('oci_image_reference', nargs=1)
    parser.add_argument('--outfile', default='-')
    parser.add_argument('--server-url', default='https://github.com')
    parser.add_argument('--repository', default=None)

    parsed = parser.parse_args()
    oci_image_reference = parsed.oci_image_reference[0]

    labels = set()

    if parsed.server_url:
        if parsed.server_url.startswith('https://github.tools'):
            github_host = 'gh-tools'
        elif parsed.server_url.startswith('https://github.wdf'):
            github_host = 'gh-wdf'
        elif parsed.server_url == 'https://github.com':
            github_host = 'gh-com'
        else:
            raise ValueError(f'unsupported GitHub server {parsed.server_url}')

    if parsed.repository:
        org, _ = parsed.repository.split('/')

    with open(cfgs_path) as f:
        cfgs = yaml.safe_load(f)

    found = False
    prefixes = set()
    for cfg in cfgs:
        if cfg['github-host'] != github_host:
            continue

        for prefix in cfg['oci-repository-prefixes']:
            prefixes.add(prefix)

        if (github_orgs := cfg.get('github-orgs', None)):
            if org not in github_orgs:
                continue

        for prefix in cfg['oci-repository-prefixes']:
            if oci_image_reference.startswith(prefix):
                found = True
        if found:
            break
    else:
        print(f'did not find matching cfg for {oci_image_reference=}, {labels=}')
        print('known prefixes:')
        for p in prefixes:
            print(f'  {p}')
        exit(1)

    # output as outputs understood by google-github-actions/auth action
    if parsed.outfile == '-':
        f = sys.stdout
    else:
        f = open(parsed.outfile, 'a')

    print('found cfg:')
    pprint.pprint(cfg)

    project_name = cfg['gcp-project-name']
    project_id = cfg['gcp-project-id']
    service_account = cfg['service-account']
    identity_pool_name = cfg['identity-pool-name']
    identity_provider_name = cfg['identity-provider-name']

    workload_identity_provider = f'projects/{project_id}/locations/global/workloadIdentityPools/'
    workload_identity_provider += f'{identity_pool_name}/providers/{identity_provider_name}'

    f.write(f'project-id={project_name}\n')
    f.write(f'service-account={service_account}\n')
    f.write(f'workload-identity-provider={workload_identity_provider}\n')


if __name__ == '__main__':
    main()
