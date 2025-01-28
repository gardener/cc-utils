# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import collections
import enum
import logging
import re
import textwrap

import typing
from typing import Iterable, Tuple

import github3
import github3.issues
from github3.exceptions import NotFoundError
from github3.github import GitHub
from github3.orgs import Team
from github3.pulls import PullRequest
from github3.repos.release import Release

import ci.util
import ocm
import version

logger = logging.getLogger(__name__)

'''
The maximum allowed length of github-release-bodies.
This limit is not documented explicitly in the GitHub docs. To see it, the error returned by
GitHub when creating a release with more then the allowed number of characters must be
looked at.
as (inofficial) alternative, see: https://github.com/dead-claudia/github-limits
'''
MAXIMUM_GITHUB_RELEASE_BODY_LENGTH = 25000


class RepositoryHelperBase:
    GITHUB_TIMESTAMP_UTC_FORMAT = '%Y-%m-%dT%H:%M:%SZ'

    def __init__(
        self,
        owner: str,
        name: str,
        github_api: GitHub=None,
        default_branch: str='master',
    ):
        '''
        Args:
            owner (str):    repository owner (also called organisation in GitHub)
            name (str):     repository name
            default_branch (str): branch to use for operations when not specified
            github_api (GitHub): github api to use
        '''
        if not github_api:
            raise ValueError('must pass github_api')

        self.github = github_api

        self.repository = self._create_repository(
            owner=owner,
            name=name
        )
        self.owner = owner
        self.repository_name = name

        self.default_branch = default_branch

    def _create_repository(self, owner: str, name: str):
        try:
            repository = self.github.repository(
                    owner=owner,
                    repository=name
            )
            return repository
        except NotFoundError as nfe:
            try:
                gh_user = self.github.me().name
            except:
                gh_user = 'failed to determine current user'

            raise RuntimeError(
                f'failed to retrieve repository {owner}/{name} {gh_user=}',
                nfe,
            )


class UpgradePullRequest:
    def __init__(
        self,
        pull_request: PullRequest,
        from_ref: typing.Union[ocm.Resource, ocm.ComponentReference],
        to_ref: typing.Union[ocm.Resource, ocm.ComponentReference],
    ):
        self.pull_request = ci.util.not_none(pull_request)

        if from_ref.name != to_ref.name:
            raise ValueError(f'reference name mismatch {from_ref.name=} {to_ref.name=}')
        if (isinstance(from_ref, ocm.Resource) and isinstance(to_ref, ocm.Resource) and
            from_ref.type != to_ref.type
            ) or \
            type(from_ref) != type(to_ref):
            raise ValueError(f'reference types do not match: {from_ref=} {to_ref=}')

        self.ref_name = from_ref.name

        self.from_ref = from_ref
        self.to_ref = to_ref
        if isinstance(from_ref, ocm.Resource):
            if isinstance(from_ref.type, enum.Enum):
                self.reference_type_name = from_ref.type.value
            elif isinstance(from_ref.type, str):
                self.reference_type_name = from_ref.type
            else:
                raise ValueError(from_ref.type)
        elif isinstance(from_ref, ocm.ComponentReference):
            self.reference_type_name = 'component'
        else:
            raise NotImplementedError(from_ref.type)

    def is_downgrade(self) -> bool:
        from_ver = version.parse_to_semver(self.from_ref.version)
        to_ver = version.parse_to_semver(self.to_ref.version)
        return from_ver > to_ver

    def is_obsolete(
        self,
        reference_component: ocm.Component,
    ):
        '''returns a boolean indicating whether or not this Upgrade PR is "obsolete"

        A Upgrade is considered to be obsolete, iff the following conditions hold true:
        - the reference product contains a component reference with the same name
        - the destination version is greater than the greatest reference component version
        '''
        # find matching component versions
        if not isinstance(reference_component, ocm.Component):
            raise TypeError(reference_component)

        if self.reference_type_name == 'component':
            reference_refs = sorted(
                [
                    rc for rc in reference_component.componentReferences
                    if rc.componentName == self.ref_name
                ],
                key=lambda r: version.parse_to_semver(r.version)
            )

            if not reference_refs:
                return False # special case: we have a new reference

            greatest_reference_version = version.parse_to_semver(reference_refs[-1].version)

        else:
            raise NotImplementedError

        # PR is obsolete if same or newer component version is already configured in reference
        return greatest_reference_version >= version.parse_to_semver(self.to_ref.version)

    def target_matches(
        self,
        reference: typing.Tuple[ocm.ComponentReference, ocm.Resource],
        reference_version: str = None,
    ):
        if not isinstance(reference, ocm.ComponentReference) and not \
                isinstance(reference, ocm.Resource):
            raise TypeError(reference)

        if isinstance(reference, ocm.ComponentReference):
            if self.reference_type_name != 'component':
                return False
            if reference.componentName != self.ref_name:
                return False
        else: # ocm.Resource, already checked above
            if reference.name != self.ref_name:
                return False
            if isinstance(reference.type, enum.Enum):
                reference_type = reference.type.value
            elif isinstance(reference.type, str):
                reference_type = reference.type
            else:
                raise ValueError(reference.type)
            if reference_type != self.reference_type_name:
                return False

        reference_version = reference_version or reference.version
        if reference_version != self.to_ref.version:
            return False

        return True

    def purge(self):
        self.pull_request.close()
        head_ref = 'heads/' + self.pull_request.head.ref
        self.pull_request.repository.ref(head_ref).delete()


def iter_obsolete_upgrade_pull_requests(
    upgrade_pull_requests: typing.Iterable[UpgradePullRequest],
    keep_hotfix_versions: bool=True,
) -> typing.Generator[UpgradePullRequest, None, None]:
    grouped_upgrade_pull_requests = collections.defaultdict(list)

    def group_name(upgrade_pull_request: UpgradePullRequest):
        '''
        calculate groupname, depending on whether or not we should keep hotfix_versions;
        for each upgrade-pr-group, we keep only exactly one version (the greatest tgt-version);
        therefore, to prevent hotfix-upgrades from being removed, collect hotfixes in a separate
        group.
        '''
        cname = upgrade_pull_request.to_ref.componentName

        if not keep_hotfix_versions:
            return cname

        from_version = version.parse_to_semver(upgrade_pull_request.from_ref.version)
        to_version = version.parse_to_semver(upgrade_pull_request.to_ref.version)

        if from_version.major != to_version.major:
            return cname # not a hotfix
        if from_version.minor != to_version.minor:
            return cname # not a hotfix (hardcode hotfixes differ at patchlevel, always)

        # we have a hotfix version (patchlevel differs)
        return f'{cname}:{from_version.major}.{from_version.minor}'

    for upgrade_pull_request in upgrade_pull_requests:
        if upgrade_pull_request.pull_request.state != 'open':
            continue
        name = group_name(upgrade_pull_request)
        grouped_upgrade_pull_requests[name].append(upgrade_pull_request)

    for upgrade_pull_request_group in grouped_upgrade_pull_requests.values():
        if len(upgrade_pull_request_group) < 2:
            continue

        # greatest version will be sorted as last element
        ordered_by_version = sorted(
            upgrade_pull_request_group,
            key=lambda upr: version.parse_to_semver(upr.to_ref.version),
        )

        greatest_version = version.parse_to_semver(ordered_by_version[-1].to_ref.version)
        for upgrade_pr in ordered_by_version:
            if version.parse_to_semver(upgrade_pr.to_ref.version) < greatest_version:
                yield upgrade_pr


class PullRequestUtil(RepositoryHelperBase):
    _pr_title_pattern = re.compile(r'^\[ci:(\S*):(\S*):(\S*)->(\S*)\]$')

    @staticmethod
    def calculate_pr_title(
            reference: ocm.ComponentReference,
            from_version: str,
            to_version: str,
    ) -> str:
        if not isinstance(reference, ocm.ComponentReference):
            raise TypeError(reference)

        type_name = 'component'
        reference_name = reference.componentName

        return f'[ci:{type_name}:{reference_name}:{from_version}->{to_version}]'

    def _pr_to_upgrade_pull_request(
        self,
        pull_request,
        pattern: re.Pattern=None,
    ) -> UpgradePullRequest:
        ci.util.not_none(pull_request)

        if not pattern:
            pattern = self._pr_title_pattern

        match = pattern.fullmatch(pull_request.title)
        if match is None:
            raise ValueError("PR-title '{t}' did not match title-schema".format(
                t=pull_request.title)
            )

        reference_type_name = match.group(1)
        if not reference_type_name:
            # backwards compatibility hack
            reference_type_name = 'component'

        if not reference_type_name == 'component':
            raise NotImplementedError(reference_type_name) # todo: support all resources

        ref_name = match.group(2)
        from_version = match.group(3)
        to_version = match.group(4)

        from_ref = ocm.ComponentReference(
            name=ref_name,
            componentName=ref_name,
            version=from_version,
        )
        to_ref = ocm.ComponentReference(
            name=ref_name,
            componentName=ref_name,
            version=to_version,
        )

        return UpgradePullRequest(
            pull_request=pull_request,
            from_ref=from_ref,
            to_ref=to_ref,
        )

    def enumerate_upgrade_pull_requests(
        self,
        state: str='all',
        pattern: re.Pattern=None,
    ) -> typing.Generator[UpgradePullRequest, None, None]:
        def has_upgrade_pr_title(pull_request):
            return bool(pattern.fullmatch(pull_request.title))

        if not pattern:
            pattern = self._pr_title_pattern

        for pull_request in self.repository.pull_requests(
            state=state,
            number=128, # avoid issueing more than one github-api-request
        ):
            pull_request.title = pull_request.title.strip()
            if not has_upgrade_pr_title(pull_request):
                continue

            yield self._pr_to_upgrade_pull_request(
                pull_request=pull_request,
                pattern=pattern,
            )


class GitHubRepositoryHelper(RepositoryHelperBase):
    def _replacement_release_notes(
        self,
        asset_url: str,
    ):
        return textwrap.dedent(
            f'''\
            Release-Notes were too long (limit: {MAXIMUM_GITHUB_RELEASE_BODY_LENGTH} octets).
            They were instaed uploaded as [release-asset]({asset_url}).
            '''
        )

    def tag_exists(
        self,
        tag_name: str,
    ):
        ci.util.not_empty(tag_name)
        try:
            self.repository.ref('tags/' + tag_name)
            return True
        except NotFoundError:
            return False

    def add_labels_to_pull_request(self, pull_request_number, *labels):
        pull_request = self.repository.pull_request(pull_request_number)
        pull_request.issue().add_labels(*labels)

    def remove_label_from_pull_request(self, pull_request_number, label):
        pull_request = self.repository.pull_request(pull_request_number)
        pull_request.issue().remove_label(label)

    def add_comment_to_pr(self, pull_request_number, comment):
        pull_request = self.repository.pull_request(pull_request_number)
        pull_request.create_comment(comment)

    def is_org_member(self, organization_name, user_login):
        organization = self.github.organization(organization_name)
        return organization.is_member(user_login)

    def is_team_member(self, team_name, user_login) -> bool:
        '''Returns a bool indicating team-membership to the given team for the given user-login

        The team-name is expected in the format `<org-name>/<team-name>`.
        Note: If the team cannot be seen by the user used for the lookup or does not exist `False`
        will be returned.
        '''
        o, t = team_name.split('/')
        org = self.github.organization(o)
        try:
            team = org.team_by_name(t)
            team.membership_for(user_login)
        except github3.exceptions.NotFoundError:
            return False
        else:
            return True

    def delete_outdated_draft_releases(self) -> Iterable[Tuple[github3.repos.release.Release, bool]]:
        '''Find outdated draft releases and try to delete them

        Yields tuples containing a release and a boolean indicating whether its deletion was
        successful.

        A draft release is considered outdated iff:
        1: its version is smaller than the greatest release version (according to semver) AND
            2a: it is NOT a hotfix draft release AND
            2b: there are no hotfix draft releases with the same major and minor version
            OR
            3a: it is a hotfix draft release AND
            3b: there is a hotfix draft release of greater version (according to semver)
                with the same major and minor version
        '''

        releases = [release for release in self.repository.releases(number=20)]
        non_draft_releases = [release for release in releases if not release.draft]
        draft_releases = [release for release in releases if release.draft]
        greatest_release_version = find_greatest_github_release_version(non_draft_releases)

        if greatest_release_version is not None:
            draft_releases_to_delete = outdated_draft_releases(
                    draft_releases=draft_releases,
                    greatest_release_version=greatest_release_version,
            )
        else:
            draft_releases_to_delete = []

        for release in draft_releases_to_delete:
            yield release, release.delete()


def _retrieve_team_by_name_or_none(
    organization: github3.orgs.Organization,
    team_name: str
) -> Team:

    team_list = list(filter(lambda t: t.name == team_name, organization.teams()))
    return team_list[0] if team_list else None


def find_greatest_github_release_version(
    releases: list[Release],
    warn_for_unparseable_releases: bool = True,
    ignore_prerelease_versions: bool = False,
):
    # currently, non-draft-releases are not created with a name by us. Use the tag name as fallback
    release_versions = [
        release.name if release.name else release.tag_name
        for release in releases
    ]

    def filter_non_semver_parseable_releases(release_name):
        try:
            version.parse_to_semver(release_name)
            return True
        except ValueError:
            if warn_for_unparseable_releases:
                ci.util.warning(f'ignoring release {release_name=} (not semver)')
            return False

    release_versions = [
        name for name in filter(filter_non_semver_parseable_releases, release_versions)
    ]

    release_version_infos = [
        version.parse_to_semver(release_version)
        for release_version in release_versions
    ]
    latest_version = version.find_latest_version(
        versions=release_version_infos,
        ignore_prerelease_versions=ignore_prerelease_versions,
    )
    if latest_version:
        return str(latest_version)
    else:
        return None


def outdated_draft_releases(
    draft_releases: list[Release],
    greatest_release_version: str,
):
    '''Find outdated draft releases from a list of draft releases and return them. This is achieved
    by partitioning the release versions according to their joined major and minor version.
    Partitions are then checked:
        - if there is only a single release in a partition it is either a hotfix release
            (keep corresponding release) or it is not (delete if it is not the greatest release
            according to semver)
        - if there are multiple releases versions in a partition, keep only the release
            corresponding to greatest (according to semver)
    '''

    greatest_release_version_info = version.parse_to_semver(greatest_release_version)

    def _has_semver_draft_prerelease_label(release_name):
        version_info = version.parse_to_semver(release_name)
        if version_info.prerelease != 'draft':
            return False
        return True

    autogenerated_draft_releases = [
        release for release in draft_releases
        if release.name
        and version.is_semver_parseable(release.name)
        and _has_semver_draft_prerelease_label(release.name)
    ]

    draft_release_version_infos = [
        version.parse_to_semver(release.name)
        for release in autogenerated_draft_releases
    ]

    def _yield_outdated_version_infos_from_partition(partition):
        if len(partition) == 1:
            version_info = partition.pop()
            if version_info < greatest_release_version_info and version_info.patch == 0:
                yield version_info
        else:
            yield from [
                version_info
                for version_info in partition[1:]
            ]

    outdated_version_infos = list()
    for partition in version.partition_by_major_and_minor(draft_release_version_infos):
        outdated_version_infos.extend(_yield_outdated_version_infos_from_partition(partition))

    outdated_draft_releases = [
        release
        for release in autogenerated_draft_releases
        if version.parse_to_semver(release.name) in outdated_version_infos
    ]

    return outdated_draft_releases


def close_issue(
    issue: github3.issues.ShortIssue,
) -> bool:
    '''
    handle known corner-cases where regular issue close will fail
    comment on issue if closing still fails

    returns True if close was successful, False otherwise
    '''
    def try_close() -> bool:
        try:
            return issue.edit(
                state='closed',
            )

        except github3.exceptions.UnprocessableEntity:
            # likely that assignee was suspended from github

            if not issue.assignees:
                raise

            issue.remove_assignees(issue.assignees)
            return issue.edit(
                state='closed',
            )

    closed = try_close()
    if not closed:
        issue.create_comment('unable to close ticket')

    return closed
