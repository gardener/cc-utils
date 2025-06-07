import collections.abc
import dataclasses
import re

import github3.pulls

import cnudie.retrieve
import cnudie.util
import ocm
import ocm.gardener
import version


@dataclasses.dataclass
class UpgradePullRequest:
    pull_request: github3.pulls.PullRequest
    upgrade_vector: ocm.gardener.UpgradeVector

    @property
    def component_name(self) -> str:
        return self.upgrade_vector.component_name

    @property
    def is_downgrade(self) -> bool:
        whence_version = version.parse_to_semver(self.upgrade_vector.whence.version)
        whiter_version = version.parse_to_semver(self.upgrade_vector.whither.version)
        return whence_version > whiter_version

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
            raise ValueError(reference_component)

        reference_refs = sorted(
            [
                rc for rc in reference_component.componentReferences
                if rc.componentName == self.upgrade_vector.component_name
            ],
            key=lambda r: version.parse_to_semver(r.version)
        )

        if not reference_refs:
            return False # special case: we have a new reference

        greatest_reference_version = version.parse_to_semver(reference_refs[-1].version)
        wither_version = version.parse_to_semver(self.upgrade_vector.whither.version)

        # PR is obsolete if same or newer component version is already configured in reference
        return greatest_reference_version >= wither_version

    def purge(self):
        self.pull_request.close()
        head_ref = f'heads/{self.pull_request.head.ref}'
        self.pull_request.repository.ref(head_ref).delete()

    def target_matches(
        self,
        reference: ocm.ComponentReference,
        reference_version: str,
    ):
        if not isinstance(reference, ocm.ComponentReference):
            return False
        if reference.componentName != self.component_name:
            return False

        reference_version = reference_version or reference.version
        if reference_version != self.upgrade_vector.whither.version:
            return False

        return True


def parse_pullrequest_title(
    title: str,
    invalid_ok=False,
    title_regex_pattern: str | None=None,
) -> ocm.gardener.UpgradeVector:
    if not title_regex_pattern:
        title_regex_pattern = r'^\[ci:(\S*):(\S*):(\S*)->(\S*)\]$'

    title_pattern = re.compile(title_regex_pattern)
    if not title_pattern.fullmatch(title):
        if invalid_ok:
            return None
        raise ValueError(f'{title=} is not a valid upgrade-pullrequest-title')

    title = title.removeprefix('[ci:').removesuffix(']')

    kind, component_name, version_vector = title.split(':')

    if kind != 'component':
        raise ValueError(f'upgrade-target-type {kind=} not implemented')

    version_whence, version_whiter = version_vector.split('->')

    return ocm.gardener.UpgradeVector(
        whence=ocm.ComponentIdentity(
            name=component_name,
            version=version_whence,
        ),
        whither=ocm.ComponentIdentity(
            name=component_name,
            version=version_whiter,
        )
    )


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
) -> str:
    type_name = 'component'
    cname = upgrade_vector.component_name
    from_version = upgrade_vector.whence.version
    to_version = upgrade_vector.whither.version

    return f'[ci:{type_name}:{cname}:{from_version}->{to_version}]'


def iter_upgrade_pullrequests(
    repository: github3.repos.Repository,
    state: str='all',
    title_regex_pattern: str | None=None,
) -> collections.abc.Generator[UpgradePullRequest, None, None]:
    def has_upgrade_pr_title(pull_request):
        return parse_pullrequest_title(
            title=pull_request.title,
            invalid_ok=True,
            title_regex_pattern=title_regex_pattern,
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
    keep_hotfix_versions: bool=True,
) -> collections.abc.Generator[UpgradePullRequest, None, None]:
    grouped_upgrade_pull_requests = collections.defaultdict(list)

    def group_name(upgrade_pull_request: UpgradePullRequest):
        '''
        calculate groupname, depending on whether or not we should keep hotfix_versions;
        for each upgrade-pr-group, we keep only exactly one version (the greatest tgt-version);
        therefore, to prevent hotfix-upgrades from being removed, collect hotfixes in a separate
        group.
        '''
        cname = upgrade_pull_request.component_name

        if not keep_hotfix_versions:
            return cname

        from_version = version.parse_to_semver(upgrade_pull_request.upgrade_vector.whence.version)
        to_version = version.parse_to_semver(upgrade_pull_request.upgrade_vector.whither.version)

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
