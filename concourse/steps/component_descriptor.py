# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import logging

import git
import yaml

import ci.util
import cnudie.util
import cnudie.retrieve
import version

import ocm

logger = logging.getLogger('step.component_descriptor')


def dump_component_descriptor_v2(component_descriptor_v2: ocm.ComponentDescriptor):
    import ocm
    import dataclasses
    import yaml
    return yaml.dump(
        data=dataclasses.asdict(component_descriptor_v2),
        Dumper=ocm.EnumValueYamlDumper,
    )


def base_component_descriptor_v2(
    component_name_v2: str,
    component_labels: list[ocm.Label],
    effective_version: str,
    source_labels: tuple,
    ocm_repository_url: str,
    commit: str,
    repo_url: str,
):
    import datetime
    import ocm
    import version as version_util
    parsed_version = version_util.parse_to_semver(effective_version)
    if parsed_version.finalize_version() == parsed_version:
        # "final" version --> there will be a tag, later (XXX hardcoded hack)
        src_ref = f'refs/tags/{effective_version}'
    elif parsed_version.prerelease.startswith('timestamp'):
        # prerelease is the build timestamp, not a "real" ref
        src_ref = None
    else:
        # let's hope the version contains something committish
        if parsed_version.build:
            src_ref = f'{parsed_version.prerelease}{parsed_version.build}'
        else:
            src_ref = f'{parsed_version.prerelease}'

    # logical names must not contain slashes or dots
    logical_name = component_name_v2.replace('/', '_').replace('.', '_')

    provider = 'SAP SE'

    component_labels = list(component_labels)
    component_labels.append(
        ocm.Label(
            name='cloud.gardener/ocm/creation-date',
            value=datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
        ),
    )

    base_descriptor_v2 = ocm.ComponentDescriptor(
      meta=ocm.Metadata(schemaVersion=ocm.SchemaVersion.V2),
      component=ocm.Component(
        name=component_name_v2,
        version=effective_version,
        repositoryContexts=[
          ocm.OciOcmRepository(
            baseUrl=ocm_repository_url,
            type=ocm.AccessType.OCI_REGISTRY,
          )
        ],
        provider=provider,
        sources=[
          ocm.Source(
            name=logical_name,
            type=ocm.ArtefactType.GIT,
            access=ocm.GithubAccess(
              type=ocm.AccessType.GITHUB,
              repoUrl=repo_url,
              ref=src_ref,
              commit=commit,
            ),
            version=effective_version,
            labels=source_labels,
          )
        ],
        componentReferences=[], # added later
        resources=[], # added later
        labels=component_labels,
        creationTime=datetime.datetime.now(datetime.timezone.utc).isoformat(),
      ),
    )

    return base_descriptor_v2


def component_diff_since_last_release(
    component_descriptor: ocm.ComponentDescriptor,
    component_descriptor_lookup,
    version_lookup,
):
    component: ocm.Component = ci.util.not_none(
        component_descriptor.component,
    )

    versions = version_lookup(
        component.identity()
    )

    greatest_release_version = version.greatest_version_before(
        reference_version=component.version,
        versions=versions,
        ignore_prerelease_versions=True,
    )

    if not greatest_release_version:
        logger.warning('could not determine last release version')
        return None

    greatest_release_version = str(greatest_release_version)
    logger.info('last released version: ' + str(greatest_release_version))

    greatest_released_cd = component_descriptor_lookup(ocm.ComponentIdentity(
        name=component.name,
        version=greatest_release_version,
    ))
    greatest_released_component = greatest_released_cd.component

    if not greatest_released_component:
        ci.util.fail(f'could not find {component=}')

    return cnudie.retrieve.component_diff(
        left_component=component,
        right_component=greatest_released_component,
        ignore_component_names=(component.name,),
        component_descriptor_lookup=component_descriptor_lookup,
    )


def write_component_diff(component_diff, out_path):
    # let us write only a subset for now, namely component names with changed versions
    diff_dict = {
        'component_names_with_version_changes': list(component_diff.names_version_changed),
    }

    with open(out_path, 'w') as f:
        yaml.dump(diff_dict, f)


def head_commit_hexsha(repo_path):
    git_repo = git.Repo(repo_path)
    if not git_repo.head.is_valid():
        commit_hash = None
    else:
        try:
            commit_hash = git_repo.head.commit.hexsha
        except:
            commit_hash = None

    return commit_hash
