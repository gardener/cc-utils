#!/usr/bin/env python3
'''
pin-actions-and-workflows: recursive action/workflow reference pinning tool

PURPOSE
-------
GitHub Actions workflows and composite actions within a single repository typically
reference each other using a mutable branch ref (e.g. @master). This means that any
commit touching an action or workflow immediately affects all in-flight and future
workflow runs that reference it — there is no way to pin to a specific, immutable
version of a co-located action or workflow.

This tool solves that by producing a *pinned* view of the repository on a separate
target branch (default: `v1`). On that branch, every `uses:` reference to an action
or workflow in this repository is rewritten from `@<branch>` to `@<sha>`, working
recursively from leaves to roots so that the entire dependency tree is consistently
frozen. The result is that any commit SHA ever pointed to by the target branch
represents a fully self-consistent, immutable snapshot of the action/workflow tree.

APPROACH
--------
After every push to the own branch (default: `master`), a pipeline runs this
tool, which:

  1. Resolves the full dependency graph of all actions and workflows.
  2. Rewrites references leaf-first (topological order), so that when a parent is
     rewritten, all files it references already point at pinned commits.
  3. For each file that needs rewriting, creates a minimal git commit containing
     only that file's change, recording the own commit digest in the commit message.
  4. Force-pushes the resulting chain of commits to the target branch.

Consumers may reference:
  - `@<target-branch>` — always tracks the latest pinned state (equivalent to
    currently tracking `@master`, but with consistent transitive pinning)
  - `@<commit-digest>` — fully immutable; the entire transitive closure is frozen

YAML PARSING
------------
This tool uses ruamel.yaml for round-trip parsing. This means:
  - Only genuine `uses:` step fields are modified — comments, anchors, and
    `uses:` occurrences inside strings or comments are never falsely matched.
  - Original formatting (indentation, comments, quoting style) is preserved
    for all lines that are not modified.

PINNED REFERENCE DETECTION
---------------------------
A reference is considered already pinned if and only if it resolves to an existing
commit object in the repository AND is not a branch or tag name. Branch and tag
names are mutable pointers and are never treated as pinned, even though they resolve
to commits. Only direct commit SHAs (full or abbreviated) count as pinned. Stale
SHAs (garbage-collected) correctly return False.

GARBAGE COLLECTION
------------------
Force-pushing the target branch makes previous tip commits unreachable. To prevent
git GC from collecting them (so that old pinned commit digests remain resolvable),
this tool creates a preservation ref at `refs/tags/fixated/<own-commit-digest>` for every
processed own commit. These refs are outside `refs/heads/` and do not clutter the
branch list, but keep the commit objects alive indefinitely.

CLI USAGE (for local testing)
------------------------------
  ./pin.py \\
      [--repo-root /path/to/repo]   # defaults to repo root relative to script
      [--own-branch master]         # defaults to master
      [--target-branch v1]          # defaults to v1
      [--own-org gardener]          # defaults to origin remote org
      [--own-repo cc-utils]         # defaults to origin remote repo name
      [--verbose / -v]              # extra logging
'''

import argparse
import graphlib
import logging
import os
import shutil
import tempfile

import git as gitpython
import ruamel.yaml


logger = logging.getLogger(__name__)

yaml = ruamel.yaml.YAML()
yaml.preserve_quotes = True
yaml.indent(mapping=2, sequence=4, offset=2)
yaml.width = 4096  # prevent ruamel from wrapping long lines


def _is_pinned(ref: str, repo: gitpython.Repo) -> bool:
    '''
    Return True if ref is an immutable commit reference (i.e. a SHA — full or
    abbreviated) that resolves to an existing commit in the repository.

    Branch names and tag names are NOT considered pinned even though they
    resolve to commits, because they are mutable pointers. Only direct commit
    references (SHAs) count as pinned.
    '''
    # Reject anything that matches a branch or tag name — those are mutable
    for ref_obj in (*repo.branches, *repo.tags):
        if ref_obj.name == ref:
            return False
    try:
        repo.commit(ref)
        return True
    except (gitpython.BadName, gitpython.BadObject, ValueError):
        return False


def _action_and_workflow_blobs(commit: gitpython.Commit):
    '''
    Yield (path, blob) for all action.yaml/yml and workflow .yaml/yml files
    in the given commit, via GitPython tree traversal.
    '''
    def _is_relevant(path: str) -> bool:
        if path.startswith('.github/actions/') and path.endswith(('action.yaml', 'action.yml')):
            return True
        if path.startswith('.github/workflows/') and path.endswith(('.yaml', '.yml')):
            return True
        return False

    for blob in commit.tree.traverse():
        if isinstance(blob, gitpython.Blob) and _is_relevant(blob.path):
            yield blob.path, blob


def _read_blob(blob: gitpython.Blob) -> str:
    return blob.data_stream.read().decode()


def _path_to_uses_prefix(path: str) -> str:
    '''
    Convert a file path to the prefix used in `uses:` references.

    .github/actions/foo/action.yaml  ->  .github/actions/foo
    .github/workflows/bar.yaml       ->  .github/workflows/bar.yaml
    '''
    if path.startswith('.github/actions/'):
        return '/'.join(path.split('/')[:-1])
    return path


def _iter_step_uses(parsed: dict):
    '''
    Yield (container_dict, 'uses') for every `uses:` reference that needs
    rewriting, covering:
      - composite actions: runs.steps[*].uses
      - workflow steps:    jobs.*.steps[*].uses
      - reusable workflow calls: jobs.*.uses  (job-level, no steps)
    '''
    runs = parsed.get('runs')
    if isinstance(runs, dict):
        for step in (runs.get('steps') or []):
            if isinstance(step, dict) and 'uses' in step:
                yield step, 'uses'

    jobs = parsed.get('jobs')
    if isinstance(jobs, dict):
        for job in jobs.values():
            if not isinstance(job, dict):
                continue
            # job-level uses: calls a reusable workflow directly
            if 'uses' in job:
                yield job, 'uses'
            for step in (job.get('steps') or []):
                if isinstance(step, dict) and 'uses' in step:
                    yield step, 'uses'


def _extract_internal_deps(
    parsed: dict,
    *,
    own_org: str,
    own_repo: str,
    known_prefixes: set[str],
    repo: gitpython.Repo,
):
    '''
    Yield uses-prefixes this file depends on internally (same own org/repo, not yet
    pinned to an existing commit).
    '''
    own_prefix = f'{own_org}/{own_repo}/'
    for step, key in _iter_step_uses(parsed):
        uses_value = step[key]
        if not isinstance(uses_value, str) or not uses_value.startswith(own_prefix):
            continue
        after_repo = uses_value[len(own_prefix):]
        if '@' not in after_repo:
            continue
        dep_prefix, ref = after_repo.rsplit('@', 1)
        if not _is_pinned(ref, repo) and dep_prefix in known_prefixes:
            yield dep_prefix


def _rewrite_for_bundle(
    parsed: dict,
    *,
    own_org: str,
    own_repo: str,
) -> bool:
    '''
    Remove dead clone/checkout steps from install-gardener-gha-libs on pinned
    branches.  The install-from-bundle step in the action auto-detects the
    bundle from github.action_path/_bundle; no run-script rewriting is needed.
    Returns True if any changes were made.
    '''
    runs = parsed.get('runs')
    if not isinstance(runs, dict):
        return False
    steps = runs.get('steps')
    if not isinstance(steps, list):
        return False

    own_repo_full = f'{own_org}/{own_repo}'
    clone_only_names = {
        'install-prerequisites-on-ghe',
        'checkout-fallback',
        'install-gardener-gha-libs',
    }

    def _is_own_checkout(step: dict) -> bool:
        uses = step.get('uses', '')
        if not isinstance(uses, str) or not uses.startswith('actions/checkout@'):
            return False
        with_block = step.get('with')
        return isinstance(with_block, dict) and with_block.get('repository') == own_repo_full

    def _is_clone_only(step: dict) -> bool:
        return step.get('name') in clone_only_names

    steps_to_remove = [s for s in steps if _is_own_checkout(s) or _is_clone_only(s)]
    if not steps_to_remove:
        return False  # nothing to rewrite — not the expected action shape

    for step in steps_to_remove:
        steps.remove(step)

    return True


def _build_dependency_graph(
    commit: gitpython.Commit,
    *,
    own_org: str,
    own_repo: str,
    repo: gitpython.Repo,
) -> tuple[dict[str, set[str]], dict[str, gitpython.Blob]]:
    '''
    Build a dependency graph suitable for graphlib.TopologicalSorter:
      { uses_prefix -> set of uses_prefixes it depends on }

    Also returns prefix_to_blob mapping for later rewriting.
    '''
    prefix_to_blob: dict[str, gitpython.Blob] = {}

    for path, blob in _action_and_workflow_blobs(commit):
        prefix_to_blob[_path_to_uses_prefix(path)] = blob

    known_prefixes = set(prefix_to_blob)
    graph: dict[str, set[str]] = {}

    for prefix, blob in prefix_to_blob.items():
        parsed = yaml.load(_read_blob(blob))
        graph[prefix] = set(_extract_internal_deps(
            parsed,
            own_org=own_org,
            own_repo=own_repo,
            known_prefixes=known_prefixes,
            repo=repo,
        ))

    return graph, prefix_to_blob


def _rewrite_parsed(
    parsed: dict,
    *,
    own_org: str,
    own_repo: str,
    prefix_to_digest: dict[str, str],
    repo: gitpython.Repo,
) -> bool:
    '''
    Rewrite all own `uses:` values in-place from @<branch> to @<commit-digest>.
    Returns True if any changes were made.
    '''
    own_prefix = f'{own_org}/{own_repo}/'
    changed = False

    for step, key in _iter_step_uses(parsed):
        uses_value = step[key]
        if not isinstance(uses_value, str) or not uses_value.startswith(own_prefix):
            continue
        after_repo = uses_value[len(own_prefix):]
        if '@' not in after_repo:
            continue
        dep_prefix, ref = after_repo.rsplit('@', 1)
        if _is_pinned(ref, repo):
            continue
        digest = prefix_to_digest.get(dep_prefix)
        if digest is None:
            logger.warning('No pinned digest found for %s — leaving unchanged', dep_prefix)
            continue
        step[key] = f'{own_prefix}{dep_prefix}@{digest}'
        changed = True

    return changed


def _org_and_repo_from_remote(repo: gitpython.Repo) -> tuple[str, str]:
    '''
    Derive org and repo name from the origin remote URL.
    Supports both https://github.com/org/repo and git@github.com:org/repo forms.
    '''
    url = repo.remotes.origin.url.removesuffix('.git')
    # normalise ssh form git@github.com:org/repo -> .../org/repo
    if ':' in url and not url.startswith('http'):
        url = url.split(':', 1)[1]
    parts = url.rstrip('/').split('/')
    return parts[-2], parts[-1]


def create_pinned_branch(
    *,
    repo_root: str,
    own_ref: str,
    target_branch: str,
    own_org: str | None,
    own_repo: str | None,
    bundle_dir: str | None = None,
    bundle_action_prefix: str = '.github/actions/install-gardener-gha-libs',
) -> None:
    '''
    Build pinned commits locally and update the target branch ref in the local
    repository. Pushing to the remote is intentionally left to the caller (action
    or workflow), so this function is safe to run locally without side-effects on
    the remote.

    If bundle_dir is given, its contents are copied into
    <bundle_action_prefix>/_bundle/ in the worktree, and the action YAML at
    that prefix is rewritten to install from the bundle instead of cloning.
    bundle_dir is expected to contain one subdirectory per package, each with
    source files and a setup.py (as produced by bundle-gardener-gha-libs).
    '''
    repo = gitpython.Repo(repo_root)

    if own_org is None or own_repo is None:
        derived_org, derived_repo = _org_and_repo_from_remote(repo)
        own_org = own_org or derived_org
        own_repo = own_repo or derived_repo
        logger.info('Derived own org/repo from remote: %s/%s', own_org, own_repo)

    own_commit = repo.commit(own_ref)
    own_digest = own_commit.hexsha
    logger.info('Own ref %s at commit digest %s', own_ref, own_digest)

    dep_graph, prefix_to_blob = _build_dependency_graph(
        own_commit,
        own_org=own_org,
        own_repo=own_repo,
        repo=repo,
    )
    logger.info('Found %d action/workflow files', len(prefix_to_blob))

    # TopologicalSorter yields leaves (no deps) first — exactly what we need
    ordered_prefixes = graphlib.TopologicalSorter(dep_graph).static_order()

    with tempfile.TemporaryDirectory(prefix='pin-') as tmpdir:
        worktree_path = os.path.join(tmpdir, 'worktree')
        repo.git.worktree('add', '--detach', worktree_path, own_digest)
        worktree_repo = gitpython.Repo(worktree_path)

        try:
            prefix_to_digest: dict[str, str] = {}
            current_digest = own_digest

            # --- bundling pass (before YAML rewriting) ---
            bundle_rel = '_bundle'
            if bundle_dir:
                target_bundle = os.path.join(worktree_path, bundle_action_prefix, bundle_rel)
                shutil.copytree(bundle_dir, target_bundle)
                bundle_files = [
                    os.path.relpath(os.path.join(dirpath, fname), worktree_path)
                    for dirpath, _, fnames in os.walk(target_bundle)
                    for fname in fnames
                ]
                worktree_repo.index.add(bundle_files)
                worktree_repo.index.commit(
                    f'pin: bundle own sources into {bundle_action_prefix}/{bundle_rel}\n\n'
                    f'own-commit: {own_digest}\n'
                    f'own-ref: {own_ref}'
                )
                current_digest = worktree_repo.head.commit.hexsha
                logger.info('Created bundle commit %s', current_digest)

            for prefix in ordered_prefixes:
                blob = prefix_to_blob.get(prefix)
                if blob is None:
                    logger.warning('No blob for prefix %s — skipping', prefix)
                    continue

                parsed = yaml.load(_read_blob(blob))
                changed = _rewrite_parsed(
                    parsed,
                    own_org=own_org,
                    own_repo=own_repo,
                    prefix_to_digest=prefix_to_digest,
                    repo=repo,
                )

                if bundle_dir and prefix == bundle_action_prefix:
                    changed |= _rewrite_for_bundle(
                        parsed,
                        own_org=own_org,
                        own_repo=own_repo,
                    )

                if not changed:
                    logger.debug('No changes needed for %s', blob.path)
                    prefix_to_digest[prefix] = current_digest
                    continue

                abs_path = os.path.join(worktree_path, blob.path)
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                with open(abs_path, 'w') as f:
                    yaml.dump(parsed, f)

                worktree_repo.index.add([blob.path])
                worktree_repo.index.commit(
                    f'pin: rewrite references in {blob.path}\n\n'
                    f'own-commit: {own_digest}\n'
                    f'own-ref: {own_ref}'
                )
                current_digest = worktree_repo.head.commit.hexsha
                prefix_to_digest[prefix] = current_digest
                logger.info('Created pinning commit %s for %s', current_digest, blob.path)

            tip_digest = worktree_repo.head.commit.hexsha

            if tip_digest == own_digest:
                logger.info('No rewrites were necessary; target branch tip unchanged')
                return

            # update local target branch ref — caller is responsible for pushing
            repo.git.update_ref(f'refs/heads/{target_branch}', tip_digest)
            logger.info('Updated local ref refs/heads/%s -> %s', target_branch, tip_digest)

            # preservation ref — keeps tip alive after future force-pushes to remote
            preservation_ref = f'refs/tags/fixated/{own_digest}'
            repo.git.update_ref(preservation_ref, tip_digest)
            logger.info('Created preservation ref %s -> %s', preservation_ref, tip_digest)

            logger.info('Done. Pinned tip: %s', tip_digest)

        finally:
            repo.git.worktree('remove', '--force', worktree_path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--repo-root',
        default=os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')),
        help='Path to git repository root (default: derived from script location)',
    )
    parser.add_argument(
        '--own-ref',
        default='master',
        help='Ref (branch name or commit digest) to read from (default: master)',
    )
    parser.add_argument(
        '--target-branch',
        default='v1',
        help='Branch to push pinned commits to (default: v1)',
    )
    parser.add_argument(
        '--own-org',
        default=None,
        help='GitHub organisation owning the repository (default: derived from origin remote)',
    )
    parser.add_argument(
        '--own-repo',
        default=None,
        help='GitHub repository name (default: derived from origin remote)',
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        default=False,
        help='Enable verbose (DEBUG) logging',
    )
    parser.add_argument(
        '--bundle-dir',
        default=None,
        help=(
            'Path to a pre-populated bundle directory as produced by '
            'bundle-gardener-gha-libs.  When given, the bundle is copied into '
            'the install-gardener-gha-libs action directory and the action YAML '
            'is rewritten to install from it instead of cloning at runtime.'
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(levelname)s %(message)s',
    )
    create_pinned_branch(
        repo_root=args.repo_root,
        own_ref=args.own_ref,
        target_branch=args.target_branch,
        own_org=args.own_org,
        own_repo=args.own_repo,
        bundle_dir=args.bundle_dir,
    )


if __name__ == '__main__':
    main()
