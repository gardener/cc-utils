import collections.abc
import dataclasses
import pytest

import ocm.branch_info
import version

import cleanup_branches


@dataclasses.dataclass
class DummyBranch:
    name: str


@pytest.fixture
def branches() -> list[DummyBranch]:
    return [
        DummyBranch('release-v1.0'),
        DummyBranch('release-v1.1'),
        DummyBranch('release-v2.0'),
        DummyBranch('release-v2.0.1'),
        DummyBranch('feature-x'),
    ]


def get_branch_info(
    significant_part: ocm.branch_info.VersionParts,
    release_branch_template: str='release-v$major.$minor',
) -> ocm.branch_info.BranchInfo:
    return ocm.branch_info.BranchInfo(
        release_branch_template=release_branch_template,
        branch_policy=ocm.branch_info.BranchPolicy(
            significant_part=significant_part,
            supported_versions_count=2,
        ),
    )


def test_stale_major_branches(
    branches: collections.abc.Iterable[DummyBranch],
):
    branch_info = get_branch_info(significant_part=ocm.branch_info.VersionParts.MAJOR)

    empty_stale_branches = tuple(cleanup_branches.iter_stale_release_branches(
        version_semver=version.parse_to_semver('v1.1.0'),
        branch_info=branch_info,
        branches=branches,
    ))

    assert not empty_stale_branches

    stale_branches = tuple(cleanup_branches.iter_stale_release_branches(
        version_semver=version.parse_to_semver('3.1.0'),
        branch_info=branch_info,
        branches=branches,
    ))

    assert 'release-v1.0' in stale_branches
    assert 'release-v1.1' in stale_branches
    assert len(stale_branches) == 2


def test_stale_minor_branches(
    branches: collections.abc.Iterable[DummyBranch],
):
    branch_info = get_branch_info(significant_part=ocm.branch_info.VersionParts.MINOR)

    empty_stale_branches = tuple(cleanup_branches.iter_stale_release_branches(
        version_semver=version.parse_to_semver('v2.1'),
        branch_info=branch_info,
        branches=branches,
    ))

    assert not empty_stale_branches

    stale_branches = tuple(cleanup_branches.iter_stale_release_branches(
        version_semver=version.parse_to_semver('v2.2'),
        branch_info=branch_info,
        branches=branches,
    ))

    assert 'release-v2.0' in stale_branches
    assert len(stale_branches) == 1


def test_stale_patch_branches(
    branches: collections.abc.Iterable[DummyBranch],
):
    branch_info = get_branch_info(significant_part=ocm.branch_info.VersionParts.PATCH)

    empty_stale_branches = tuple(cleanup_branches.iter_stale_release_branches(
        version_semver=version.parse_to_semver('v1.1.0'),
        branch_info=branch_info,
        branches=branches,
    ))

    assert not empty_stale_branches

    branch_info = get_branch_info(
        significant_part=ocm.branch_info.VersionParts.PATCH,
        release_branch_template='release-v$major.$minor.$patch',
    )

    empty_stale_branches = tuple(cleanup_branches.iter_stale_release_branches(
        version_semver=version.parse_to_semver('v2.0.1'),
        branch_info=branch_info,
        branches=branches,
    ))

    assert not empty_stale_branches

    stale_branches = tuple(cleanup_branches.iter_stale_release_branches(
        version_semver=version.parse_to_semver('v2.0.3'),
        branch_info=branch_info,
        branches=branches,
    ))

    assert 'release-v2.0.1' in stale_branches
    assert len(stale_branches) == 1
