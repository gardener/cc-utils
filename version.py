# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import collections
import dataclasses
import enum
import logging
import semver

from typing import (
    Iterable,
    Set,
    Union,
)
import typing

import ci.util

logger = logging.getLogger(__name__)

Version = semver.VersionInfo | str

NOOP = 'noop'
SET_PRERELEASE = 'set_prerelease'
APPEND_PRERELEASE = 'append_prerelease'
SET_BUILD_METADATA = 'set_build_metadata'
SET_PRERELEASE_AND_BUILD = 'set_prerelease_and_build'
SET_VERBATIM = 'set_verbatim'


class VersionRestriction(enum.Enum):
    SAME_MINOR = 'same-minor'
    NONE = 'none'


class VersionType(enum.Enum):
    SNAPSHOT = 'snapshots'
    RELEASE = 'releases'
    ANY = 'any'


@dataclasses.dataclass(frozen=True)
class VersionRetentionPolicy:
    name: str = None
    keep: typing.Union[str, int] = 'all'
    match: VersionType = VersionType.ANY
    restrict: VersionRestriction = VersionRestriction.NONE
    recursive: bool = False

    def matches_version_restriction(self, version, ref_version) -> bool:
        version = parse_to_semver(version)
        final = is_final(version)

        if self.match is VersionType.ANY:
            pass
        if self.match is VersionType.SNAPSHOT and final:
            return False
        if self.match is VersionType.RELEASE and not final:
            return False

        # if this line is reached, version-type matches

        if self.restrict is VersionRestriction.NONE:
            return True
        elif self.restrict is VersionRestriction.SAME_MINOR:
            ref_version = version.parse_to_semver(ref_version)
            return ref_version.minor == version.minor
        else:
            raise RuntimeError(f'not implemented: {self.restrict}')

    @property
    def keep_all(self) -> bool:
        return self.keep == 'all'


@dataclasses.dataclass
class VersionRetentionPolicies:
    name: str
    rules: list[VersionRetentionPolicy]
    dry_run: bool = True


T = typing.TypeVar('T')


def is_final(
    version: Version,
    converter: typing.Callable[[T], Version]=None,
) -> bool:
    if converter:
        version = converter(version)
    version = parse_to_semver(version=version)
    return not version.build and not version.prerelease


def versions_to_purge(
    versions: typing.Iterable[T],
    reference_version: Version,
    policy: VersionRetentionPolicies,
    converter: typing.Callable[[T], Version]=None,
) -> typing.Generator[T, None, None]:
    versions_by_policy = collections.defaultdict(list)

    def to_version(v: T):
        if converter:
            v = converter(v)
        return v

    for v in versions:
        converted_version = to_version(v)
        for rule in policy.rules:
            rule: VersionRetentionPolicy
            if rule.matches_version_restriction(
                version=converted_version,
                ref_version=reference_version,
            ):
                versions_by_policy[rule].append(v)
                break # first rule matches
            else:
                continue
        else:
            logger.info(f'no rule matched {converted_version}')

    for policy, versions in versions_by_policy.items():
        policy: VersionRetentionPolicy
        if policy.keep_all:
            continue

        yield from smallest_versions(
            versions=versions,
            keep=policy.keep,
            converter=converter,
        )


def parse_to_semver(
    version,
    invalid_semver_ok: bool=False,
) -> semver.VersionInfo:
    '''
    parses the given version into a semver.VersionInfo object.

    Different from strict semver, the given version is preprocessed, if required, to
    convert the version into a valid semver version, if possible.

    The following preprocessings are done:

    - strip away `v` prefix
    - append patch-level `.0` for two-digit versions
    - rm leading zeroes

    @param version: either a str, or an object with a `version` attr
    '''
    if isinstance(version, str):
        version_str = version
    elif isinstance(version, semver.VersionInfo):
        return version
    else:
        if hasattr(version, 'version'):
            if callable(version.version):
                version_str = version.version()
            else:
                version_str = str(version.version)
        elif version is None:
            raise ValueError('version must not be None')
        else:
            ci.util.warning(f'unexpected type for version: {type(version)}')
            version_str = str(version) # fallback

    try:
        semver_version_info, _ = _parse_to_semver_and_prefix(version_str)
    except ValueError:
        if invalid_semver_ok:
            return None

        raise

    return semver_version_info


def _parse_to_semver_and_prefix(version: str) -> tuple[semver.VersionInfo, str | None]:
    def raise_invalid():
        raise ValueError(f'not a valid (semver) version: `{version}`')

    if not version:
        raise_invalid()

    semver_version = version
    prefix = None

    # strip leading `v`
    if version[0] == 'v':
        semver_version = version[1:]
        prefix = 'v'

    # in most cases, we should be fine now
    try:
        return semver.VersionInfo.parse(semver_version), prefix
    except ValueError:
        pass # try extending `.0` as patch-level

    # blindly append patch-level
    if '-' in version:
        sep = '-'
    else:
        sep = '+'

    numeric, sep, suffix = semver_version.partition(sep)
    if len(tuple(c for c in version if c == '.')) == 1:
        numeric += '.0'

    try:
        return semver.VersionInfo.parse(numeric + sep + suffix), prefix
    except ValueError:
        pass # last try: strip leading zeroes

    try:
        major, minor, patch = numeric.split('.')
    except ValueError:
        raise_invalid()

    numeric = '.'.join((
        str(int(major)),
        str(int(minor)),
        str(int(patch)),
    ))

    try:
        return semver.VersionInfo.parse(numeric + sep + suffix), prefix
    except ValueError:
        # re-raise with original version str
        raise_invalid()


def _sort_versions(
    versions
):
    '''
    sorts the given versions (which may be a sequence containing any combination of
    str, semver.VersionInfo, or model element bearing a `version` attr) on a best-effort
    base.
    Firstly, it is checked whether all versions are semver-parsable, using this module's
    `parse_to_semver` (which allows some deviations from strict semver-v2). If all versions
    are parsable, str representations of the originally given versions are returned, ordered
    according to semver artithmetics.
    Otherwise, sorting falls back to alphabetical sorting as implemented by python's str.

    Note that there is _no_ validation of any kind w.r.t. to the sanity of the passed values.

    This function is not intended to be used by external users, and is planned to be removed
    again.
    '''
    if not versions:
        return

    def to_ver(version_obj):
        if hasattr(version_obj, 'version'):
            if callable(version_obj.version):
                return version_obj.version()
            else:
                return version_obj.version
        else:
            return str(version_obj)

    try:
        # try if all versions are semver-compatible
        for version_str in map(to_ver, versions):
            parse_to_semver(version_str)

        return sorted(
            versions,
            key=lambda vo: parse_to_semver(to_ver(vo)),
        )
    except ValueError:
        pass # ignore and fall-back to str-sorting

    return sorted(
        versions,
        key=to_ver,
    )


def process_version(
    version_str: str,
    operation: str,
    prerelease: str=None,
    build_metadata: str=None,
    # Limit the length of the build-metadata suffix.
    # By default we use 12 chars, following the advice given in
    # https://blog.cuviper.com/2013/11/10/how-short-can-git-abbreviate/
    # as we usually use git commit hashes
    build_metadata_length: int=12,
    verbatim_version: str=None,
    skip_patchlevel_zero=False,
):
    if operation in [SET_PRERELEASE,SET_PRERELEASE_AND_BUILD,APPEND_PRERELEASE] and not prerelease:
        raise ValueError('Prerelease must be given when replacing or appending.')
    if operation in [SET_BUILD_METADATA,SET_PRERELEASE_AND_BUILD]:
        if not build_metadata:
            raise ValueError('Build metadata must be given when replacing.')
        if build_metadata_length < 0:
            raise ValueError('Build metadata must not be empty')
    if operation == SET_VERBATIM and (not verbatim_version or prerelease or build_metadata):
        raise ValueError('Exactly verbatim-version must be given when using operation set_verbatim')

    parsed_version, prefix = _parse_to_semver_and_prefix(version_str)
    version_str = str(parsed_version)

    if operation == APPEND_PRERELEASE and not parsed_version.prerelease:
        raise ValueError('Given SemVer must have prerelease-version to append to.')

    if hasattr(semver, operation):
        function = getattr(parsed_version, operation)
        processed_version = str(function())
    elif operation == NOOP:
        processed_version = version_str
    elif operation == SET_VERBATIM:
        processed_version = str(verbatim_version)
    elif operation == APPEND_PRERELEASE:
        parsed_version = parsed_version.replace(
            prerelease='-'.join((parsed_version.prerelease, prerelease))
        )
        processed_version = str(parsed_version)
    else:
        parsed_version = parsed_version.replace(prerelease=None, build=None)
        if operation in [SET_PRERELEASE, SET_PRERELEASE_AND_BUILD]:
            parsed_version = parsed_version.replace(prerelease=prerelease)
        if operation in [SET_BUILD_METADATA, SET_PRERELEASE_AND_BUILD]:
            parsed_version = parsed_version.replace(build=build_metadata[:build_metadata_length])
        processed_version = str(parsed_version)

    if skip_patchlevel_zero:
        parsed_version = parse_to_semver(processed_version)
        if parsed_version.patch == 0:
            processed_version = str(parsed_version.replace(patch=1))

    if prefix:
        return prefix + processed_version

    return processed_version


T = typing.TypeVar('T', semver.VersionInfo, str)


def greatest_version(
    versions: Iterable[T],
    ignore_prerelease_versions: bool=False,
    invalid_semver_ok: bool=False,
    min_version: semver.VersionInfo | str=None,
) -> T | None:
    '''
    returns the greatest version from the passed versions. versions are parsed as semver versions
    using gardener's relaxed semver (which allows a `v` prefix, as well as omitting the patchlevel).
    if `ignore_prerelease_versions` is set to True, only final release versions will be considered.
    if `invalid_semver_ok` is set to True, versions that are not valid (relaxed) semver versions
    are silently ignored (will raise otherwise).

    If `min_version` is given, only versions greater than min_version will be returned. If the
    greatest passed-in version is smaller than or equal to min_version, None will be returned.
    '''
    greatest_candidate = None
    greatest_candidate_semver = None

    for candidate in versions:
        if isinstance(candidate, str):
            candidate_semver = parse_to_semver(
                version=candidate,
                invalid_semver_ok=invalid_semver_ok,
            )

            if not candidate_semver:
                continue
        else:
            candidate_semver = candidate

        if ignore_prerelease_versions and candidate_semver.prerelease:
            continue

        if not greatest_candidate_semver:
            greatest_candidate_semver = candidate_semver
            greatest_candidate = candidate
            continue

        if candidate_semver > greatest_candidate_semver:
            greatest_candidate_semver = candidate_semver
            greatest_candidate = candidate

    if min_version and greatest_candidate_semver:
        min_version = parse_to_semver(min_version)

        if greatest_candidate_semver <= min_version:
            return None

    return greatest_candidate


def greatest_version_with_matching_major(
    reference_version: Union[semver.VersionInfo, str],
    versions: Iterable[T],
    ignore_prerelease_versions: bool=False,
) -> T | None:
    '''
    returns the greatest version with matching major version compared to `reference_version`
    if no matching version is found, returns `None`

    the returned object (if matching version is found) is guaranteed to be identical to passed-in
    from `versions`.
    `versions` (if passed as str) must be parsable into semver using version.parse_to_semver
    '''
    latest_candidate_semver = None
    latest_candidate = None

    if isinstance(reference_version, str):
        reference_version = parse_to_semver(reference_version)

    for candidate in versions:
        if isinstance(candidate, str):
            candidate_semver = parse_to_semver(candidate)
        else:
            candidate_semver = candidate

        # skip if major version does not match
        if candidate_semver.major != reference_version.major:
            continue

        if ignore_prerelease_versions and candidate_semver.prerelease:
            continue

        if candidate_semver > reference_version:
            if not latest_candidate_semver or latest_candidate_semver < candidate_semver:
                latest_candidate_semver = candidate_semver
                latest_candidate = candidate

    return latest_candidate


def greatest_version_with_matching_minor(
    reference_version: T,
    versions: Iterable[T],
    ignore_prerelease_versions: bool=False,
) -> T | None:
    latest_candidate_semver = None
    latest_candidate = None

    if isinstance(reference_version, str):
        reference_version = parse_to_semver(reference_version)

    for candidate in versions:
        if isinstance(candidate, str):
            candidate_semver = parse_to_semver(candidate)
        else:
            candidate_semver = candidate

        # skip if major version does not match
        if candidate_semver.major != reference_version.major:
            continue
        # skip if minor version does not match
        if candidate_semver.minor != reference_version.minor:
            continue

        if ignore_prerelease_versions and candidate_semver.prerelease:
            continue

        if candidate_semver >= reference_version:
            if not latest_candidate_semver or latest_candidate_semver < candidate_semver:
                latest_candidate_semver = candidate_semver
                latest_candidate = candidate

    return latest_candidate


def find_smallest_version_with_matching_minor(
    reference_version: Union[semver.VersionInfo, str],
    versions: Iterable[Union[semver.VersionInfo, str]],
    ignore_prerelease_versions: bool=False,
) -> str | None:
    latest_candidate_semver = None
    latest_candidate_str = None

    if isinstance(reference_version, str):
        reference_version = parse_to_semver(reference_version)

    # Note: The sorting is done this way to preserve non-semver prefixes we allow, usually 'v'
    for candidate in versions:
        if isinstance(candidate, str):
            candidate_semver = parse_to_semver(candidate)
        else:
            candidate_semver = candidate

        # skip if major version does not match
        if candidate_semver.major != reference_version.major:
            continue
        # skip if minor version does not match
        if candidate_semver.minor != reference_version.minor:
            continue

        if ignore_prerelease_versions and candidate_semver.prerelease:
            continue

        if candidate_semver <= reference_version or not latest_candidate_semver:
            if not latest_candidate_semver or latest_candidate_semver > candidate_semver:
                latest_candidate_semver = candidate_semver
                latest_candidate_str = candidate

    return latest_candidate_str


def greatest_version_before(
    reference_version: Union[semver.VersionInfo, str],
    versions: Iterable[T],
    ignore_prerelease_versions: bool=False,
) -> T | None:
    latest_candidate_semver = None
    latest_candidate = None

    if isinstance(reference_version, str):
        reference_version = parse_to_semver(reference_version)

    for candidate in versions:
        if isinstance(candidate, str):
            candidate_semver = parse_to_semver(candidate)
        else:
            candidate_semver = candidate

        if ignore_prerelease_versions and candidate_semver.prerelease:
            continue

        if candidate_semver < reference_version:
            if not latest_candidate_semver or candidate_semver > latest_candidate_semver:
                latest_candidate_semver = candidate_semver
                latest_candidate = candidate

    return latest_candidate


def partition_by_major_and_minor(
    versions: Iterable[semver.VersionInfo],
) -> Iterable[Set[semver.VersionInfo]]:
    '''partition an iterable of semver VersionInfos by their joined major and minor version
    '''
    partitions = collections.defaultdict(set)
    for version_info in versions:
        partitions[(version_info.major,version_info.minor)].add(version_info)
    yield from [
        sorted(partition, reverse=True)
        for partition in partitions.values()
    ]


def is_semver_parseable(version_string: str):
    try:
        parse_to_semver(version_string)
    except ValueError:
        logger.debug(f"Could not parse '{version_string}' as semver version")
        return False
    return True


def smallest_versions(
    versions: typing.Sequence[T],
    keep: int,
    converter: typing.Callable[[T], str]=None,
) -> list[T]:
    '''
    find smallest versions from given sequence of versions, excluding the given amount of
    greatest versions (keep parameter). This is useful, e.g. to cleanup old versions.

    `keep`:      specifies how many versions to keep
    `converter`: optional value-conversion-callback (for convenience)
    '''
    if (versions_count := len(versions)) <= keep:
        return []

    def _parse_version(version: T):
        if converter:
            version = converter(version)
        return parse_to_semver(version)

    versions = sorted(
        versions,
        key=_parse_version,
    ) # smallest versions come first

    purge_idx = versions_count - keep

    return versions[:purge_idx]


def iter_upgrade_path(
    whence: Version,
    whither: Version,
    versions: collections.abc.Iterable[Version],
) -> collections.abc.Iterable[semver.VersionInfo]:
    '''
    returns an iterable of versions marking the upgrade-path between whence and whither versions.

    The upgrade-path is determined heuristically, by assuming semver-semantics. The upgrade-path
    is considered to be useful to collect release-notes, assuming that any releases of less
    significance (e.g. patch-level-releases) contain changes that were either downported (i.e.
    also included in greater versions, including release-ntoes), or not relevant for upstream (in
    which case it is not of interest to collect release-notes).

    Firstly, the left-most differing version-part is determined.

    If major-version is different, then upgrade-path will consist of the sequence of smallest
    versions of each major-version until (including) `whither`-version. Any additional versions
    with major-version matching `whither`-version will be yielded (including whither-version),
    in ascending order.

    If major-version is equal, but minor-version is different, then all smallest versions with
    matching minor- and major-version will be yielded (in ascending order), followed by all versions
    with matching major and minor-version to `whither`-version (in ascending order), including
    `whither`-version.

    If major, and minor-versions are equal, versions with patch-levels between `whence` and
    `whither` will be yielded, in ascending order, including `whither`-version.
    '''
    whence = parse_to_semver(whence)
    whither = parse_to_semver(whither)

    if not whence < whither:
        raise ValueError(f'{whence=} must be smaller than {whither=}')

    major_eq = whence.major == whither.major

    versions = [ # <parsed, original>
        (pv, v) for v in versions
        if (pv := parse_to_semver(v)) > whence and pv <= whither
    ]
    versions = sorted(
        versions,
        key=lambda x: x[0],
    )

    last = whence
    if not major_eq:
        # major-versions differ - yield smallest versions for each major-version until whither
        # (as versions are already sorted, it is sufficient to keep last yielded)
        for version, orig_version in versions:
            if version.major == whither.major:
                yield orig_version
            elif version.major > last.major:
                last = version
                yield orig_version
        return

    minor_eq = whence.minor == whither.minor
    last = whence
    if not minor_eq:
        # major versions are equal, minor versions differ. yield smallest version for each
        # minor-version until whither
        for version, orig_version in versions:
            if version.minor == whither.minor:
                yield orig_version
            elif version.minor > last.minor:
                last = version
                yield orig_version
        return

    # major and minor versions are equal - yield all
    for _, orig_version in versions:
        yield orig_version


def find_predecessor(
    version: Version,
    versions: collections.abc.Iterable[Version],
) -> Version | None:
    '''
    finds the predecessor version for the given version, using semver semantics.

    Which version is considered a predecessor depends on the rightmost non-zero version part.
    If patchlevel is greater than zero, the closest version with the same major and minor and
    smaller patch-level is deemed to be predecessor. If minor version is greater than zero, the
    closest smaller minor-version with matching major-version is deemed predecessor.
    '''
    version = parse_to_semver(version)
    major = version.major
    minor = version.minor

    versions = [ # <parsed, original>
        (pv, v) for v in versions
        if (pv := parse_to_semver(v)) < version
    ]

    if not versions:
        return

    versions = sorted(
        versions,
        key=lambda x: x[0],
        reverse=True, # greatest first
    )

    for idx, (candidate, candidate_orig) in enumerate(versions):
        if candidate.minor == minor and candidate.major == major:
            # take closest candidate with equal major/minor
            return candidate_orig

        # if this is reached, there was no candidate with matching major/minor
        # -> fixate current minor, and take smallest patchlevel
        for better_candidate, better_candidate_orig in versions[idx:]:
            if better_candidate.minor == candidate.minor:
                candidate = better_candidate
                candidate_orig = better_candidate_orig
            else:
                # we went past the smallest version w/ matching minor version
                return candidate_orig

    return candidate_orig
