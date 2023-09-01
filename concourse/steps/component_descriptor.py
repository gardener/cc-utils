# Copyright (c) 2019-2020 SAP SE or an SAP affiliate company. All rights reserved. This file is
# licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging

import git
import yaml

import ci.util
import cnudie.util
import cnudie.retrieve
import cnudie.migrate
import version

import gci.componentmodel as cm

logger = logging.getLogger('step.component_descriptor')


def dump_component_descriptor_v2(component_descriptor_v2: cm.ComponentDescriptor):
    import gci.componentmodel as cm
    import dataclasses
    import yaml
    return yaml.dump(
        data=dataclasses.asdict(component_descriptor_v2),
        Dumper=cm.EnumValueYamlDumper,
    )


def base_component_descriptor_v2(
    component_name_v2: str,
    component_labels: list[cm.Label],
    effective_version: str,
    source_labels: tuple,
    ctx_repository_base_url: str,
    commit: str,
):
    import datetime
    import cnudie.migrate
    import gci.componentmodel as cm
    import version as version_util
    parsed_version = version_util.parse_to_semver(effective_version)
    if parsed_version.finalize_version() == parsed_version:
        # "final" version --> there will be a tag, later (XXX hardcoded hack)
        src_ref = f'refs/tags/{effective_version}'
    else:
        # let's hope the version contains something committish
        if parsed_version.build:
            src_ref = f'{parsed_version.prerelease}{parsed_version.build}'
        else:
            src_ref = f'{parsed_version.prerelease}'

    # logical names must not contain slashes or dots
    logical_name = component_name_v2.replace('/', '_').replace('.', '_')

    if cnudie.migrate.emit_compatible_to_cnudie_v2:
        provider = 'internal'
    else:
        provider = {
            'name': 'SAP SE',
        }

    component_labels = list(component_labels)
    component_labels.append(
        cm.Label(
            name='cloud.gardener/ocm/creation-date',
            value=datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
        ),
    )

    base_descriptor_v2 = cm.ComponentDescriptor(
      meta=cm.Metadata(schemaVersion=cm.SchemaVersion.V2),
      component=cm.Component(
        name=component_name_v2,
        version=effective_version,
        repositoryContexts=[
          cm.OciRepositoryContext(
            baseUrl=ctx_repository_base_url,
            type=cm.AccessType.OCI_REGISTRY,
          )
        ],
        provider=provider,
        sources=[
          cm.ComponentSource(
            name=logical_name,
            type=cm.SourceType.GIT,
            access=cm.GithubAccess(
              type=cm.AccessType.GITHUB,
              repoUrl=component_name_v2,
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
      ),
    )

    return base_descriptor_v2


def component_diff_since_last_release(
    component_descriptor: cm.ComponentDescriptor,
    mapping_config: cnudie.util.OcmLookupMappingConfig,
):
    component: cm.Component = ci.util.not_none(
        component_descriptor.component,
    )

    version_lookup = cnudie.retrieve.version_lookup(
        ocm_repository_lookup=mapping_config,
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

    component_descriptor_lookup = cnudie.retrieve.create_default_component_descriptor_lookup(
        ocm_repository_lookup=mapping_config,
        default_absent_ok=False,
    )

    greatest_released_cd = component_descriptor_lookup(cm.ComponentIdentity(
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
