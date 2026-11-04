#!/usr/bin/env python3
'''
sync-mirror: verify a pinned target branch (as created by pin.py) on a source
repository, and push it (plus its preservation ref) to one or more mirrors.

Intended for mirrors (e.g. on GitHub Enterprise Server instances) which cannot run
the pin-workflow themselves, and which consumers reference instead of the source
repository. Replaces manually force-pushing the target branch to mirrors.

VERIFICATION
------------
Before pushing, the following is checked (refusing to push if any check fails):
  1. the source target branch's tip equals the source preservation ref
     `refs/tags/fixated/<own-commit>` (where own-commit is read from the tip's
     commit message)
  2. the own commit was reviewed (see pin.check_own_commit_reviewed), checked
     against the source repository's allowed base branch
  3. the commits between own commit and tip form a linear chain of pinning-commits:
     - each commit references the own commit in its message
     - the (optional) bundle commit only adds files below the bundle directory
     - all other commits only rewrite own `uses:` references to commits of the
       chain (or the own commit) — except for the install-gardener-gha-libs action,
       from which pin.py removes steps

Note that contents of the bundle (in particular third-party wheels) cannot be
verified against the own commit.

USAGE
-----
  ./sync_mirror.py \\
      --source https://github.com/gardener/cc-utils.git \\
      --mirror <mirror-remote-or-url> [--mirror ...] \\
      [--ssh-key ~/.ssh/cc-utils-mirror-deploy-key]  # if mirror requires deploy key
      [--backfill-fixated-tags]  # also push missing preservation refs
      [--dry-run]
'''

import argparse
import logging
import os
import re
import sys

import git as gitpython

import pin


logger = logging.getLogger(__name__)

_SOURCE_NS = 'refs/sync-mirror/source'
_OWN_COMMIT_RE = re.compile(r'^own-commit: ([0-9a-f]{40})$', re.MULTILINE)
_BUNDLE_ACTION_PREFIX = '.github/actions/install-gardener-gha-libs'


class VerificationError(Exception):
    pass


def _own_commit_from_message(commit: gitpython.Commit) -> str | None:
    if not (match := _OWN_COMMIT_RE.search(commit.message)):
        return None
    return match.group(1)


def _iter_diff_lines(diff_output: str):
    '''
    yield (path, line) for each added or removed line of a unified diff
    '''
    path = None
    for line in diff_output.splitlines():
        if line.startswith('--- '):
            continue
        if line.startswith('+++ '):
            path = line.removeprefix('+++ ').removeprefix('b/')
            continue
        if line.startswith('diff --git '):
            # path of removed files is only given in header
            path = line.rsplit(' b/', 1)[-1]
            continue
        if line.startswith(('+', '-')):
            yield path, line


def verify_pinned_chain(
    *,
    repo: gitpython.Repo,
    own_commit: gitpython.Commit,
    tip: gitpython.Commit,
    own_org: str,
    own_repo: str,
    bundle_action_prefix: str = _BUNDLE_ACTION_PREFIX,
) -> None:
    '''
    Raise VerificationError unless the commits from own_commit (exclusive) to tip
    (inclusive) form a linear chain of pinning-commits as created by pin.py.
    '''
    chain = []
    commit = tip
    while commit != own_commit:
        if len(commit.parents) != 1:
            raise VerificationError(
                f'{commit.hexsha} has {len(commit.parents)} parents (expected linear chain)'
            )
        chain.append(commit)
        commit = commit.parents[0]
        if len(chain) > 10000:
            raise VerificationError(f'{own_commit.hexsha} is not an ancestor of {tip.hexsha}')
    chain.reverse()

    # pinned references may only point to commits of the (verified) chain, or own commit
    allowed_digests = {own_commit.hexsha} | {c.hexsha for c in chain}
    own_uses_prefix = f'{own_org}/{own_repo}/'
    pinned_uses_re = re.compile(
        r'''^\+\s*(- )?uses:\s*['"]?'''
        + re.escape(own_uses_prefix)
        + r'''\S+@([0-9a-f]{40})['"]?\s*(#.*)?$'''
    )
    own_uses_re = re.compile(
        r'''^-\s*(- )?uses:\s*['"]?''' + re.escape(own_uses_prefix)
    )
    bundle_dir_prefix = f'{bundle_action_prefix}/_bundle/'
    bundle_action_files = {
        f'{bundle_action_prefix}/action.yaml',
        f'{bundle_action_prefix}/action.yml',
    }

    for commit in chain:
        if _own_commit_from_message(commit) != own_commit.hexsha:
            raise VerificationError(
                f'{commit.hexsha} does not reference own commit {own_commit.hexsha}'
            )

        diffs = commit.parents[0].diff(commit)
        paths = {p for d in diffs for p in (d.a_path, d.b_path) if p}
        if not paths:
            raise VerificationError(f'{commit.hexsha} is empty')

        if all(p.startswith(bundle_dir_prefix) for p in paths):
            if any(d.change_type != 'A' for d in diffs):
                raise VerificationError(
                    f'{commit.hexsha} changes existing files below {bundle_dir_prefix}'
                )
            logger.info('%s: bundle commit (%d files added)', commit.hexsha, len(diffs))
            continue

        if any(d.change_type != 'M' for d in diffs):
            raise VerificationError(f'{commit.hexsha} adds, removes, or renames files')

        diff_output = repo.git.diff(
            '--unified=0',
            '--no-color',
            '--no-ext-diff',
            '--no-renames',
            commit.parents[0].hexsha,
            commit.hexsha,
        )
        for path, line in _iter_diff_lines(diff_output):
            if line.startswith('+'):
                if not (match := pinned_uses_re.match(line)):
                    raise VerificationError(
                        f'{commit.hexsha} adds unexpected line to {path}: {line}'
                    )
                if (digest := match.group(2)) not in allowed_digests:
                    raise VerificationError(
                        f'{commit.hexsha} pins {path} to {digest}, which is not part of '
                        'the verified chain'
                    )
            elif path in bundle_action_files:
                continue  # pin.py removes clone-steps from this action
            elif not own_uses_re.match(line):
                raise VerificationError(
                    f'{commit.hexsha} removes unexpected line from {path}: {line}'
                )
        logger.info('%s: rewrites own references in %s', commit.hexsha, ', '.join(paths))


def verify_source(
    *,
    repo: gitpython.Repo,
    target_branch: str,
    allowed_base: str,
    own_org: str,
    own_repo: str,
) -> tuple[gitpython.Commit, str]:
    '''
    verify fetched source refs (see module docstring). Returns tip and own commit digest.
    '''
    tip = repo.commit(f'{_SOURCE_NS}/heads/{target_branch}')

    if not (own_digest := _own_commit_from_message(tip)):
        raise VerificationError(f'tip {tip.hexsha} does not reference an own commit')
    own_commit = repo.commit(own_digest)

    try:
        fixated = repo.commit(f'{_SOURCE_NS}/tags/fixated/{own_digest}')
    except (gitpython.BadName, ValueError):
        raise VerificationError(f'source has no preservation ref fixated/{own_digest}')
    if fixated != tip:
        raise VerificationError(
            f'source preservation ref fixated/{own_digest} points to {fixated.hexsha}, '
            f'but {target_branch} points to {tip.hexsha}'
        )

    try:
        pin.check_own_commit_reviewed(
            repo=repo,
            own_commit=own_commit,
            allowed_base=f'{_SOURCE_NS}/heads/{allowed_base}',
        )
    except pin.UnreviewedOwnCommit as e:
        raise VerificationError(str(e))

    verify_pinned_chain(
        repo=repo,
        own_commit=own_commit,
        tip=tip,
        own_org=own_org,
        own_repo=own_repo,
    )

    return tip, own_digest


def _ls_remote(repo: gitpython.Repo, remote: str, *patterns: str) -> dict[str, str]:
    refs = {}
    for line in repo.git.ls_remote(remote, *patterns).splitlines():
        digest, ref = line.split('\t', 1)
        refs[ref] = digest
    return refs


def sync_mirror(
    *,
    repo: gitpython.Repo,
    mirror: str,
    tip: gitpython.Commit,
    own_digest: str,
    target_branch: str,
    backfill_fixated_tags: bool,
    dry_run: bool,
) -> None:
    mirror_refs = _ls_remote(
        repo,
        mirror,
        f'refs/heads/{target_branch}',
        'refs/tags/fixated/*',
    )
    refspecs = []

    tag_ref = f'refs/tags/fixated/{own_digest}'
    if (mirror_tag := mirror_refs.get(tag_ref)) is None:
        refspecs.append(f'{tip.hexsha}:{tag_ref}')
    elif mirror_tag != tip.hexsha:
        raise VerificationError(
            f'{mirror}: {tag_ref} points to {mirror_tag} (expected {tip.hexsha})'
        )

    if backfill_fixated_tags:
        source_tags = {
            ref.path.removeprefix(f'{_SOURCE_NS}/'): ref.commit.hexsha
            for ref in repo.refs
            if ref.path.startswith(f'{_SOURCE_NS}/tags/fixated/')
        }
        for ref, digest in sorted(source_tags.items()):
            ref = f'refs/{ref}'
            if ref == tag_ref:
                continue
            if (mirror_digest := mirror_refs.get(ref)) is None:
                refspecs.append(f'{digest}:{ref}')
            elif mirror_digest != digest:
                logger.warning('%s: %s differs from source - not overwriting', mirror, ref)

    branch_ref = f'refs/heads/{target_branch}'
    mirror_tip = mirror_refs.get(branch_ref)
    lease = []
    if mirror_tip != tip.hexsha:
        # only overwrite the state we just observed (fails if mirror changed meanwhile)
        lease = [f'--force-with-lease={branch_ref}:{mirror_tip or ""}']
        refspecs.append(f'+{tip.hexsha}:{branch_ref}')

    if not refspecs:
        logger.info('%s: already up-to-date (%s -> %s)', mirror, branch_ref, tip.hexsha)
        return

    logger.info('%s: pushing (current %s: %s):', mirror, branch_ref, mirror_tip)
    for refspec in refspecs:
        logger.info('  %s', refspec)
    if dry_run:
        logger.info('%s: dry-run - not pushing', mirror)
        return

    repo.git.push('--atomic', *lease, mirror, *refspecs)
    logger.info('%s: done', mirror)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--repo-root',
        default=os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')),
        help='Path to a local clone used for fetching (default: derived from script location)',
    )
    parser.add_argument(
        '--source',
        required=True,
        help='URL (or remote name) of repository to read pinned target branch from',
    )
    parser.add_argument(
        '--mirror',
        dest='mirrors',
        action='append',
        required=True,
        help='URL (or remote name) of mirror to push to (repeatable)',
    )
    parser.add_argument('--target-branch', default='v1')
    parser.add_argument(
        '--allowed-base',
        default='master',
        help='Branch of source repository own commits must be reachable from (default: master)',
    )
    parser.add_argument('--own-org', default='gardener')
    parser.add_argument('--own-repo', default='cc-utils')
    parser.add_argument(
        '--ssh-key',
        default=None,
        help='SSH private key (deploy key) to push to mirrors with',
    )
    parser.add_argument(
        '--backfill-fixated-tags',
        action='store_true',
        default=False,
        help='Also push preservation refs present on source, but missing on mirrors',
    )
    parser.add_argument('--dry-run', action='store_true', default=False)
    parser.add_argument('--verbose', '-v', action='store_true', default=False)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(levelname)s %(message)s',
    )
    repo = gitpython.Repo(args.repo_root)

    if args.ssh_key:
        repo.git.update_environment(
            GIT_SSH_COMMAND=f'ssh -i {args.ssh_key} -o IdentitiesOnly=yes',
        )

    logger.info('Fetching %s', args.source)
    repo.git.fetch(
        '--no-tags',
        '--prune',
        args.source,
        f'+refs/heads/{args.target_branch}:{_SOURCE_NS}/heads/{args.target_branch}',
        f'+refs/heads/{args.allowed_base}:{_SOURCE_NS}/heads/{args.allowed_base}',
        f'+refs/tags/fixated/*:{_SOURCE_NS}/tags/fixated/*',
    )

    try:
        tip, own_digest = verify_source(
            repo=repo,
            target_branch=args.target_branch,
            allowed_base=args.allowed_base,
            own_org=args.own_org,
            own_repo=args.own_repo,
        )
        logger.info('Verified %s at %s (own commit %s)', args.target_branch, tip, own_digest)

        for mirror in args.mirrors:
            sync_mirror(
                repo=repo,
                mirror=mirror,
                tip=tip,
                own_digest=own_digest,
                target_branch=args.target_branch,
                backfill_fixated_tags=args.backfill_fixated_tags,
                dry_run=args.dry_run,
            )
    except VerificationError as e:
        logger.error('Refusing to sync: %s', e)
        sys.exit(1)


if __name__ == '__main__':
    main()
