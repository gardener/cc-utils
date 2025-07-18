import collections.abc
import logging
import os
import subprocess
import tempfile
import traceback

import ocm
import ocm.gardener
import ocm.util
import github3.exceptions
import github3.repos.repo

import ci.util
import concourse.model.traits.update_component_deps as ucd
import concourse.steps.component_descriptor_util as cdu
import dockerutil
import github.pullrequest
import gitutil
import model.container_registry as cr
import release_notes.ocm
import version

UpgradePullRequest = github.pullrequest.UpgradePullRequest

logger = logging.getLogger('step.update_component_deps')


def current_product_descriptor():
    component_descriptor_file_path = cdu.component_descriptor_path(
        schema_version=ocm.SchemaVersion.V2,
    )

    # cd is supplied via component-descriptor file. Parse and return
    if os.path.isfile(component_descriptor_file_path):
        return ocm.ComponentDescriptor.from_dict(
            component_descriptor_dict=ci.util.parse_yaml_file(component_descriptor_file_path,)
        )
    else:
        raise RuntimeError(f'did not find component-descriptor at {component_descriptor_file_path=}')


def current_component():
    return current_product_descriptor().component


def close_obsolete_pull_requests(
    upgrade_pull_requests,
    reference_component: ocm.Component,
):
    obsolete_upgrade_requests = [
        pr for pr in upgrade_pull_requests
        if pr.pull_request.state == 'open' and pr.is_obsolete(
            reference_component=reference_component,
        )
    ]

    for obsolete_request in obsolete_upgrade_requests:
        obsolete_request.purge()


def upgrade_pr_exists(
    component_reference: ocm.ComponentReference,
    component_version: str,
    upgrade_requests: collections.abc.Iterable[UpgradePullRequest],
    request_filter: collections.abc.Callable[[UpgradePullRequest], bool] = lambda rq: True,
) -> UpgradePullRequest | None:
    if any(
        (matching_rq := upgrade_rq).target_matches(
            reference=component_reference,
            reference_version=component_version,
        ) and request_filter(upgrade_rq)
        for upgrade_rq in upgrade_requests
    ):
        return matching_rq
    return None


def greatest_component_version(
    component_name,
    version_lookup,
    ignore_prerelease_versions,
) -> str | None:
    versions = version_lookup(component_name)
    if not versions:
        return None

    greatest_version = version.greatest_version(
        versions=versions,
        ignore_prerelease_versions=ignore_prerelease_versions,
    )

    if not greatest_version:
        return None

    return greatest_version


def greatest_component_version_with_matching_minor(
    component_name,
    version_lookup,
    reference_version,
    ignore_prerelease_versions,
) -> str | None:
    versions = version_lookup(component_name)
    if not versions:
        return None

    greatest_version = version.greatest_version_with_matching_minor(
        reference_version=reference_version,
        versions=versions,
        ignore_prerelease_versions=ignore_prerelease_versions,
    )

    if not greatest_version:
        return None

    return greatest_version


def latest_component_version_from_upstream(
    component_name: str,
    upstream_component_name: str,
    ocm_lookup,
    version_lookup,
    ignore_prerelease_versions: bool=False,
):
    upstream_component_version = greatest_component_version(
        component_name=upstream_component_name,
        version_lookup=version_lookup,
        ignore_prerelease_versions=ignore_prerelease_versions,
    )

    if not upstream_component_version:
        raise RuntimeError(
            f'did not find any versions for {upstream_component_name=}'
        )

    upstream_component_descriptor = ocm_lookup(
        ocm.ComponentIdentity(
            name=upstream_component_name,
            version=upstream_component_version,
        ),
    )

    upstream_component = upstream_component_descriptor.component
    for component_ref in upstream_component.componentReferences:
        # TODO: Validate that component_name is unique
        if component_ref.componentName == component_name:
            return component_ref.version


def determine_reference_versions(
    component_name: str,
    reference_version: str,
    version_lookup,
    ocm_lookup,
    upstream_component_name: str,
    upstream_update_policy: ucd.UpstreamUpdatePolicy=ucd.UpstreamUpdatePolicy.STRICTLY_FOLLOW,
    ignore_prerelease_versions: bool=False,
) -> collections.abc.Sequence[str]:
    version_candidate = latest_component_version_from_upstream(
        component_name=component_name,
        upstream_component_name=upstream_component_name,
        version_lookup=version_lookup,
        ocm_lookup=ocm_lookup,
        ignore_prerelease_versions=ignore_prerelease_versions,
    )

    if upstream_update_policy is ucd.UpstreamUpdatePolicy.STRICTLY_FOLLOW:
        return (version_candidate,)

    elif upstream_update_policy is ucd.UpstreamUpdatePolicy.ACCEPT_HOTFIXES:
        hotfix_candidate = greatest_component_version_with_matching_minor(
            component_name=component_name,
            version_lookup=version_lookup,
            reference_version=reference_version,
            ignore_prerelease_versions=ignore_prerelease_versions,
        )
        if hotfix_candidate == version_candidate:
            return (version_candidate,)
        else:
            return (hotfix_candidate, version_candidate)

    else:
        raise NotImplementedError


def determine_upgrade_vector(
    component_reference: ocm.ComponentReference,
    upstream_component_name: str|None,
    upstream_update_policy: ucd.UpstreamUpdatePolicy,
    upgrade_pull_requests: collections.abc.Iterable[UpgradePullRequest],
    version_lookup,
    ocm_lookup,
    ignore_prerelease_versions=False,
) -> ocm.gardener.UpgradeVector | None:
    if not upstream_component_name:
        return ocm.gardener.find_upgrade_vector(
            component_id=component_reference.component_id,
            version_lookup=version_lookup,
            ignore_prerelease_versions=ignore_prerelease_versions,
            ignore_invalid_semver_versions=True,
        )

    # below this line, we handle following upstream-component
    versions_to_consider = determine_reference_versions(
        component_name=component_reference.componentName,
        reference_version=component_reference.version,
        upstream_component_name=upstream_component_name,
        upstream_update_policy=upstream_update_policy,
        version_lookup=version_lookup,
        ocm_lookup=ocm_lookup,
        ignore_prerelease_versions=ignore_prerelease_versions,
    )
    if versions_to_consider:
        logger.info(
            f"Found possible version(s) to up- or downgrade to: '{versions_to_consider}' for "
            f'{component_reference.componentName=}'
        )
    else:
        logger.warning(
            f'No component versions found for {component_reference.componentName=}'
        )
    for candidate_version in versions_to_consider:
        # we might have found 'None' as version to consider.
        if not candidate_version:
            continue

        candidate_version_semver = version.parse_to_semver(candidate_version)
        reference_version_semver = version.parse_to_semver(component_reference.version)

        logger.info(f'{candidate_version=}, ours: {component_reference}')

        if candidate_version_semver <= reference_version_semver:
            downgrade_pr = True
            # downgrades are permitted iff the version is tracking a _dependency_ of another
            # component and we are to follow strictly
            if (
                candidate_version_semver == reference_version_semver or
                not upstream_component_name
                or upstream_update_policy is not ucd.UpstreamUpdatePolicy.STRICTLY_FOLLOW
            ):
                logger.info(
                    f'skipping (outdated) {component_reference=}; '
                    f'our {component_reference.version=}, '
                    f'found: {candidate_version=}'
                )
                continue
        else:
            downgrade_pr = False

        if not downgrade_pr and (matching_pr := upgrade_pr_exists(
            component_reference=component_reference,
            component_version=candidate_version,
            upgrade_requests=upgrade_pull_requests,
            request_filter=lambda rq: not rq.is_downgrade,
        )):
            logger.info(
                'skipping upgrade (PR already exists): '
                f'{component_reference=} '
                f'to {candidate_version=} ({matching_pr.pull_request.html_url})'
            )
            continue
        elif downgrade_pr and (matching_pr := upgrade_pr_exists(
            component_reference=component_reference,
            component_version=candidate_version,
            upgrade_requests=upgrade_pull_requests,
            request_filter=lambda rq: rq.is_downgrade,
        )):
            logger.info(
                'skipping downgrade (PR already exists): '
                f'{component_reference=} '
                f'to {candidate_version=} ({matching_pr.pull_request.html_url})'
            )
            continue
        else:
            return ocm.gardener.UpgradeVector(
                whence=ocm.ComponentIdentity(
                    name=component_reference.componentName,
                    version=component_reference.version,
                ),
                whither=ocm.ComponentIdentity(
                    name=component_reference.componentName,
                    version=candidate_version,
                ),
            )


def create_upgrade_commit_diff(
    repo_dir: str,
    container_image,
    upgrade_script_path,
    upgrade_script_relpath,
    cmd_env: dict[str, str],
):
    if container_image:
        cmd_env['REPO_DIR'] = (repo_dir_in_container := '/mnt/main_repo')

    if not container_image:
        # create upgrade diff
        subprocess.run(
            [str(upgrade_script_path)],
            check=True,
            env=cmd_env
        )
    else:
        # run check-script in container
        oci_registry_cfg = cr.find_config(image_reference=container_image)
        if oci_registry_cfg:
            docker_cfg_dir = tempfile.TemporaryDirectory()
            dockerutil.mk_docker_cfg_dir(
                cfg={'auths': oci_registry_cfg.as_docker_auths()},
                cfg_dir=docker_cfg_dir.name,
                exist_ok=True,
            )
        else:
            docker_cfg_dir = None

        upgrade_script_path_in_container = os.path.join(
            repo_dir_in_container,
            upgrade_script_relpath,
        )

        docker_argv = dockerutil.docker_run_argv(
            image_reference=container_image,
            argv=(
                upgrade_script_path_in_container,
            ),
            env=cmd_env,
            mounts={
                repo_dir: repo_dir_in_container,
            },
            cfg_dir=docker_cfg_dir.name,
        )

        logger.info(f'will run: ${docker_argv=}')

        try:
            subprocess.run(
                docker_argv,
                check=True,
            )
        finally:
            if docker_cfg_dir:
                docker_cfg_dir.cleanup()


def create_upgrade_pr(
    upgrade_vector: ocm.gardener.UpgradeVector,
    repository: github3.repos.Repository,
    upgrade_script_path,
    upgrade_script_relpath,
    branch: str,
    repo_dir,
    git_helper: gitutil.GitHelper,
    github_cfg_name,
    merge_policy: ucd.MergePolicy,
    merge_method: ucd.MergeMethod,
    version_lookup,
    component_descriptor_lookup,
    oci_client,
    delivery_dashboard_url: str=None,
    after_merge_callback=None,
    container_image:str=None,
    pullrequest_body_suffix: str=None,
    include_bom_diff: bool=True,
) -> github.pullrequest.UpgradePullRequest:
    if container_image:
        dockerutil.launch_dockerd_if_not_running()

    from_component_descriptor = component_descriptor_lookup(
        upgrade_vector.whence,
        absent_ok=False,
    )
    from_component = from_component_descriptor.component

    to_component_descriptor = component_descriptor_lookup(
        upgrade_vector.whither,
    )

    to_component = to_component_descriptor.component

    bom_diff_markdown = None
    if include_bom_diff:
        bom_diff_markdown = github.pullrequest.bom_diff(
            delivery_dashboard_url=delivery_dashboard_url,
            from_component=from_component,
            to_component=to_component,
            component_descriptor_lookup=component_descriptor_lookup,
        )

    cmd_env = github.pullrequest.set_dependency_cmd_env(
        upgrade_vector=upgrade_vector,
        repo_dir=repo_dir,
        github_cfg_name=github_cfg_name,
    )

    create_upgrade_commit_diff(
        repo_dir=repo_dir,
        container_image=container_image,
        upgrade_script_path=upgrade_script_path,
        upgrade_script_relpath=upgrade_script_relpath,
        cmd_env=cmd_env,
    )

    from_version = upgrade_vector.whence.version
    to_version = upgrade_vector.whither.version
    cname = upgrade_vector.component_name
    commit_message = f'Upgrade {cname}\n\nfrom {from_version} to {to_version}'

    try:
        release_notes = fetch_release_notes(
            from_component=from_component,
            to_version=to_version,
            version_lookup=version_lookup,
            component_descriptor_lookup=component_descriptor_lookup,
            oci_client=oci_client,
        )
    except Exception:
        logger.warning('failed to retrieve release-notes')
        traceback.print_exc()
        release_notes = 'failed to retrieve release-notes'

    pr_body, additional_notes = github.pullrequest.upgrade_pullrequest_body(
        release_notes=release_notes,
        bom_diff_markdown=bom_diff_markdown,
    )

    if pullrequest_body_suffix:
        pr_body += f'\n{pullrequest_body_suffix}'

    if merge_policy is ucd.MergePolicy.MANUAL:
        delete_on_exit = False
    else:
        delete_on_exit = True

    with github.pullrequest.commit_and_push_to_tmp_branch(
        repository=repository,
        git_helper=git_helper,
        commit_message=commit_message,
        target_branch=branch,
        delete_on_exit=delete_on_exit,
    ) as upgrade_branch_name:
        try:
            pull_request = repository.create_pull(
                title=github.pullrequest.upgrade_pullrequest_title(
                    upgrade_vector=upgrade_vector,
                ),
                base=branch,
                head=upgrade_branch_name,
                body=pr_body.strip(),
            )

            for release_note_part in additional_notes:
                pull_request.create_comment(body=release_note_part)
        except github3.exceptions.UnprocessableEntity as e:
            logger.info(f'Intercepted UnprocessableEntity exception. Listed errors: {e.errors}')
            raise

        if merge_policy is ucd.MergePolicy.MANUAL:
            return github.pullrequest.as_upgrade_pullrequest(pull_request)

        logger.info(
            f"Merging upgrade-pr #{pull_request.number} ({merge_method=!s}) on branch "
            f"'{upgrade_branch_name}' into branch '{branch}'."
        )

        pull_request.merge(
            merge_method=str(merge_method),
        )

    if after_merge_callback:
        subprocess.run(
            [os.path.join(repo_dir, after_merge_callback)],
            check=True,
            env=cmd_env
        )

    return github.pullrequest.as_upgrade_pullrequest(pull_request)


def fetch_release_notes(
    from_component: ocm.Component,
    to_version: str,
    component_descriptor_lookup,
    version_lookup,
    oci_client,
):
    version_vector = ocm.gardener.UpgradeVector(
        whence=from_component,
        whither=ocm.ComponentIdentity(
            name=from_component.name,
            version=to_version,
        ),
    )

    release_notes_md = ''
    try:
        release_notes_md = '\n'.join((
            release_notes.ocm.release_notes_markdown_with_heading(cid, rn)
            for cid, rn in release_notes.ocm.release_notes_range_recursive(
                version_vector=version_vector,
                component_descriptor_lookup=component_descriptor_lookup,
                version_lookup=version_lookup,
                oci_client=oci_client,
                version_filter=version.is_final,
            )
        )) or ''
    except:
        logger.warning('an error occurred during release notes processing (ignoring)')
        import traceback
        logger.warning(traceback.format_exc())

    if release_notes_md:
        return f'**Release Notes**:\n{release_notes_md}'
