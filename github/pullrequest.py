import collections.abc
import contextlib
import dataclasses
import logging
import os
import re

import github3.pulls

import ci.util
import cnudie.retrieve
import cnudie.util
import github.limits
import gitutil
import ocm
import ocm.gardener
import version

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class UpgradePullRequest:
    pull_request: github3.pulls.PullRequest
    upgrade_vector: ocm.gardener.UpgradeVector
    component_reference_name: str | None = None

    @property
    def component_name(self) -> str:
        return self.upgrade_vector.component_name

    @property
    def is_downgrade(self) -> bool:
        whence_version = version.parse_to_semver(self.upgrade_vector.whence.version)
        whiter_version = version.parse_to_semver(self.upgrade_vector.whither.version)
        return whence_version > whiter_version

    def matching_component_references(
        self,
        reference_component: ocm.Component,
    ) -> collections.abc.Iterable[ocm.ComponentReference]:
        '''
        Yields those component references of `reference_component` which match the component name
        of this upgrade vector and, if `component_reference_name` is set, also have a matching name.
        '''
        for component_reference in reference_component.componentReferences:
            if component_reference.componentName != self.upgrade_vector.component_name:
                continue
            if (
                self.component_reference_name
                and self.component_reference_name != component_reference.name
            ):
                continue

            yield component_reference

    def is_obsolete(
        self,
        reference_component: ocm.Component,
    ):
        '''returns a boolean indicating whether or not this Upgrade PR is "obsolete"

        An Upgrade is considered to be obsolete, iff the following conditions hold true:
        - the reference product contains a component reference with the same component name and
          reference name
        - the destination version is greater than the greatest reference component version
        '''
        # find matching component versions
        if not isinstance(reference_component, ocm.Component):
            raise ValueError(reference_component)

        reference_refs = sorted(
            self.matching_component_references(reference_component=reference_component),
            key=lambda r: version.parse_to_semver(r.version),
        )

        if not reference_refs:
            return False # special case: we have a new reference

        greatest_reference_version = version.parse_to_semver(reference_refs[-1].version)
        whence_version = self.upgrade_vector.whence_version

        # PR is obsolete if greater component version is already configured in reference
        return greatest_reference_version > whence_version

    def purge(self):
        self.pull_request.close()
        head_ref = f'heads/{self.pull_request.head.ref}'
        self.pull_request.repository.ref(head_ref).delete()


def get_component_name_from_reference_name(
    reference_name: str,
    reference_component: ocm.Component,
) -> str:
    for component_reference in reference_component.componentReferences:
        if component_reference.name == reference_name:
            return component_reference.componentName
    raise ValueError(f'No component reference found {reference_name=}.')


def parse_pullrequest_title(
    title: str,
    invalid_ok=False,
    title_regex_pattern: str | None=None,
    reference_component: ocm.Component | None=None,
) -> ocm.gardener.UpgradeVector | tuple[ocm.gardener.UpgradeVector, str]:
    if not title_regex_pattern:
        title_regex_pattern = r'^\[ci:(\S*):(\S*):(\S*)->(\S*)\]$'

    title_pattern = re.compile(title_regex_pattern)
    if not title_pattern.fullmatch(title):
        if invalid_ok:
            return None
        raise ValueError(f'{title=} is not a valid upgrade-pullrequest-title')

    title = title.removeprefix('[ci:').removesuffix(']')

    kind, component_name_or_reference_name, version_vector = title.split(":")
    version_whence, version_whither = version_vector.split("->")

    reference_name = None
    if kind == "name":
        reference_name = component_name_or_reference_name
        if not reference_component:
            raise ValueError(
                'reference_component must be given when parsing pullrequest title with kind=name'
            )
        component_name = get_component_name_from_reference_name(reference_name, reference_component)
    elif kind == "component":
        component_name = component_name_or_reference_name
    else:
        raise ValueError(f'upgrade-target-type {kind=} not implemented')

    upgrade_vector = ocm.gardener.UpgradeVector(
        whence=ocm.ComponentIdentity(
            name=component_name,
            version=version_whence,
        ),
        whither=ocm.ComponentIdentity(
            name=component_name,
            version=version_whither,
        )
    )

    if reference_name:
        return upgrade_vector, reference_name

    return upgrade_vector


def as_upgrade_pullrequest(pull_request: github3.pulls.PullRequest) -> UpgradePullRequest:
    upgrade_vector = parse_pullrequest_title(
        title=pull_request.title,
    )

    return UpgradePullRequest(
        pull_request=pull_request,
        upgrade_vector=upgrade_vector,
    )


def upgrade_pullrequest_title(
    upgrade_vector: ocm.gardener.UpgradeVector,
    reference_name: str | None=None,
) -> str:
    if reference_name:
        type_name = 'name'
        component_name_or_reference_name = reference_name
    else:
        type_name = 'component'
        component_name_or_reference_name = upgrade_vector.component_name

    from_version = upgrade_vector.whence.version
    to_version = upgrade_vector.whither.version

    return f'[ci:{type_name}:{component_name_or_reference_name}:{from_version}->{to_version}]'


def iter_upgrade_pullrequests(
    repository: github3.repos.Repository,
    state: str='all',
    title_regex_pattern: str | None=None,
    reference_component: ocm.Component | None=None,
) -> collections.abc.Generator[UpgradePullRequest, None, None]:
    def has_upgrade_pr_title(pull_request):
        return parse_pullrequest_title(
            title=pull_request.title,
            invalid_ok=True,
            title_regex_pattern=title_regex_pattern,
            reference_component=reference_component,
        ) is not None

    for pull_request in repository.pull_requests(
        state=state,
        number=128, # avoid issueing more than one github-api-request
    ):
        pull_request.title = pull_request.title.strip()
        if not has_upgrade_pr_title(pull_request):
            continue

        yield as_upgrade_pullrequest(
            pull_request=pull_request,
        )


def iter_obsolete_upgrade_pull_requests(
    upgrade_pull_requests: collections.abc.Iterable[UpgradePullRequest],
    pr_naming_pattern: str='component-name',
    keep_hotfix_versions: bool=True,
    reference_component: ocm.Component | None=None,
) -> collections.abc.Generator[UpgradePullRequest, None, None]:
    grouped_upgrade_pull_requests = collections.defaultdict(list)

    def group_name(upgrade_pull_request: UpgradePullRequest, pr_naming_pattern: str) -> str:
        '''
        calculate groupname, depending on whether or not we should keep hotfix_versions and on the pr
        naming pattern; for each upgrade-pr-group, we keep only exactly one version (the greatest
        tgt-version); therefore, to prevent hotfix-upgrades from being removed, collect hotfixes in a
        separate group.
        '''

        key = upgrade_pull_request.component_name
        if pr_naming_pattern == 'component-reference-name':
            key += f':{upgrade_pull_request.component_reference_name}'

        if not keep_hotfix_versions:
            return key

        from_version = version.parse_to_semver(upgrade_pull_request.upgrade_vector.whence.version)
        to_version = version.parse_to_semver(upgrade_pull_request.upgrade_vector.whither.version)

        if from_version.major != to_version.major:
            return key # not a hotfix
        if from_version.minor != to_version.minor:
            return key # not a hotfix (hardcode hotfixes differ at patchlevel, always)

        # we have a hotfix version (patchlevel differs)
        return f'{key}:{from_version.major}.{from_version.minor}'

    for upgrade_pull_request in upgrade_pull_requests:
        if upgrade_pull_request.pull_request.state != 'open':
            continue

        if reference_component and upgrade_pull_request.is_obsolete(
            reference_component=reference_component,
        ):
            # whence version is already behind current component reference version
            yield upgrade_pull_request
            continue

        name = group_name(upgrade_pull_request,pr_naming_pattern)
        grouped_upgrade_pull_requests[name].append(upgrade_pull_request)

    for upgrade_pull_request_group in grouped_upgrade_pull_requests.values():
        if len(upgrade_pull_request_group) < 2:
            continue

        # greatest version will be sorted as last element
        ordered_by_version = sorted(
            upgrade_pull_request_group,
            key=lambda upr: version.parse_to_semver(upr.upgrade_vector.whither.version),
        )

        greatest_version = version.parse_to_semver(
            ordered_by_version[-1].upgrade_vector.whither.version
        )
        for upgrade_pr in ordered_by_version:
            if version.parse_to_semver(upgrade_pr.upgrade_vector.whither.version) < greatest_version:
                yield upgrade_pr


def bom_diff(
    delivery_dashboard_url: str,
    from_component: ocm.Component,
    to_component: ocm.Component,
    component_descriptor_lookup,
) -> str:
    if delivery_dashboard_url:
        delivery_dashboard_url_view_diff = (
            f'{delivery_dashboard_url}/#/component?name={to_component.name}&view=diff'
            f'&componentDiff={from_component.name}:{from_component.version}'
            f':{to_component.name}:{to_component.version}'
        )
    else:
        delivery_dashboard_url_view_diff = None

    bom_diff = cnudie.retrieve.component_diff(
        left_component=from_component,
        right_component=to_component,
        component_descriptor_lookup=component_descriptor_lookup,
    )

    formatted_diff = cnudie.util.format_component_diff(
        component_diff=bom_diff,
        delivery_dashboard_url_view_diff=delivery_dashboard_url_view_diff,
        delivery_dashboard_url=delivery_dashboard_url
    )

    return formatted_diff


def split_into_chunks_if_too_long(
    string: str,
    split_hint: str,
    max_leng: int,
    max_chunk_leng: int,
) -> tuple[str, tuple[str]]:
    '''
    split passed string into chunks, if needed, adding an optional splitting-hint to first part.

    If string is shorter than allowed max_leng, the string will be returned unchanged, along with
    an empty tuple of extra-chunks.

    Otherwise, a shortened string (shortened to max_leng minus the length of the given split_hint)
    is returned. Remainder of string is returned as a tuple of strings where each string is
    at most as long as max_chunk_leng.

    This function is useful for creating (potentially long) pullrequest-bodies, where, in case of
    body being too long, remainder can be posted as a sequence of comments.
    '''
    if len(string) <= max_leng:
        return string, ()

    # string is too long
    split_idx = max_leng - len(split_hint)
    first = f'{string[0: split_idx]}{split_hint}'
    string = string[split_idx:]

    chunks = tuple(
        string[start:start + max_chunk_leng]
        for start in range(0, len(string), max_chunk_leng)
    )

    return first, chunks


def upgrade_pullrequest_body(
    release_notes: str | None,
    bom_diff_markdown: str | None,
    pullrequest_body_suffix: str | None=None,
) -> tuple[str, list[str]]:
    pr_body = ''

    if bom_diff_markdown:
        total_length = len(bom_diff_markdown)
        if release_notes:
            total_length += len(release_notes)
        if pullrequest_body_suffix:
            total_length += len(pullrequest_body_suffix)
        include_bom_diff = total_length <= github.limits.issue_body
    else:
        include_bom_diff = False

    if release_notes:
        pr_body = release_notes

    if include_bom_diff:
        pr_body = f'{pr_body}\n\n{bom_diff_markdown}'

    if pullrequest_body_suffix:
        pr_body = f'{pr_body}\n\n{pullrequest_body_suffix}'

    return split_into_chunks_if_too_long(
        string=pr_body,
        split_hint='release-notes were too long (remainder will be appended as comments)',
        max_leng=github.limits.issue_body,
        max_chunk_leng=github.limits.comment_body,
    )


def set_dependency_cmd_env(
    upgrade_vector: ocm.gardener.UpgradeVector,
    repo_dir: str,
    github_cfg_name: str=None,
    whither_component_descriptor_path: str=None,
    component_reference_name: str | None=None,
) -> dict[str, str]:
    '''
    returns a cmd-env-block (in form of a dict) to pass to `set_depedency_version` callbacks.

    I.e. callbacks as defined in Gardener-CICD that shall, depending on passed env-vars, leave a
    diff that sets the target-version to the given upgrade-vector's `whither`-version.
    '''
    cmd_env = os.environ.copy()
    cmd_env['DEPENDENCY_TYPE'] = 'component'
    if component_reference_name:
        cmd_env['DEPENDENCY_TYPE'] = 'component-reference-name'
        cmd_env['DEPENDENCY_REFERENCE_NAME'] = component_reference_name
    cmd_env['DEPENDENCY_NAME'] = upgrade_vector.component_name
    cmd_env['DEPENDENCY_VERSION'] = upgrade_vector.whither.version
    cmd_env['REPO_DIR'] = repo_dir

    if whither_component_descriptor_path: # github-actions only
        cmd_env['WHITHER_COMPONENT_DESCRIPTOR'] = whither_component_descriptor_path

    if github_cfg_name: # concourse-only
        cmd_env['GITHUB_CFG_NAME'] = github_cfg_name

    return cmd_env


@contextlib.contextmanager
def commit_and_push_to_tmp_branch(
    repository: github3.repos.repo.Repository,
    git_helper: gitutil.GitHelper,
    commit_message: str,
    target_branch: str,
    delete_on_exit: bool=False,
):
    '''
    creates a commit from existing diff and pushes it to a temporary branch w/ random name. The
    temporary branch-name is yielded.

    In case of exceptions, the branch will be purged.
    '''
    commit = git_helper.index_to_commit(message=commit_message)
    logger.info(f'commit for upgrade-PR: {commit.hexsha=}')
    new_branch_name = ci.util.random_str(prefix='ci-', length=12)
    head_sha = repository.ref(f'heads/{target_branch}').object.sha
    repository.create_ref(f'refs/heads/{new_branch_name}', head_sha)

    try:
        git_helper.push(from_ref=commit.hexsha, to_ref=f'refs/heads/{new_branch_name}')
    except:
        logger.warning('an error occurred - removing now useless pr-branch')
        repository.ref(f'heads/{new_branch_name}').delete()
        raise

    git_helper.repo.git.checkout('.')

    try:
        yield new_branch_name
    except:
        repository.ref(f'heads/{new_branch_name}').delete()
        raise
    finally:
        if delete_on_exit:
            try:
                repository.ref(f'heads/{new_branch_name}').delete()
            except github3.exceptions.NotFoundError:
                pass
