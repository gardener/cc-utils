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
import sys
import tempfile
import uuid

import git as gitpython
import ruamel.yaml

try:
    import sign_pin_commit
except ImportError:
    # when not loaded from GITHUB_ACTION_PATH (e.g. local tests), make the sibling resolvable
    sys.path.insert(1, os.path.dirname(os.path.abspath(__file__)))
    import sign_pin_commit


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


def _create_signing_branch(
    *,
    repo: gitpython.Repo,
    parent: str,
) -> str:
    '''
    create-only push of a throwaway signing-branch at `parent` on origin, returns its name.
    Mirrors the release-action pattern: createCommitOnBranch moves an existing branch, so we
    give each run its own fresh branch to build the chain on.
    '''
    branch = f'pin-signing/{uuid.uuid4().hex}'
    repo.git.push('origin', f'{parent}:refs/heads/{branch}')
    logger.info('Created signing branch %s @ %s', branch, parent)
    return branch


def _rest_api_base(host: str | None) -> str:
    '''REST base-url for `host` (mirrors signed_commit._graphql_endpoint's host-switch).'''
    if not host or host == 'github.com':
        return 'https://api.github.com'
    return f'https://{host}/api/v3'


def _move_target_branch(
    *,
    token: str,
    repository_full_name: str,
    target_branch: str,
    tip: str,
    own_digest: str,
    host: str | None = None,
) -> None:
    '''
    point the target branch (e.g. v1) at `tip` and create the preservation ref
    refs/tags/fixated/<own-digest> (GC-protection for old pinned digests), both via the REST
    git-refs API.

    The whole pin-chain is authored server-side via createCommitOnBranch, so `tip` only exists on
    the remote - it was never fetched into the local repo. Moving the refs over REST (rather than
    `git push <tip>:<ref>`) therefore avoids a `bad object` failure without having to fetch the
    chain just to advance a pointer. The branch move is a deliberate force (a pin-run fully rewrites
    the branch, so a non-fast-forward update is expected); REST has no force-with-lease equivalent.
    '''
    owner, repo = repository_full_name.split('/', 1)
    session = sign_pin_commit.signed_commit.github.github_api(token=token).session
    base = _rest_api_base(host)

    response = session.patch(
        f'{base}/repos/{owner}/{repo}/git/refs/heads/{target_branch}',
        json={'sha': tip, 'force': True},
    )
    response.raise_for_status()
    logger.info('Moved %s/%s -> %s', repository_full_name, target_branch, tip)

    preservation_ref = f'refs/tags/fixated/{own_digest}'
    response = session.post(
        f'{base}/repos/{owner}/{repo}/git/refs',
        json={'ref': preservation_ref, 'sha': tip},
    )
    # a re-run with the same own-digest re-creates an existing preservation ref: POST /git/refs
    # returns 422 ("Reference already exists"). The old `git push` (no --force) rejected this too,
    # so tolerate it rather than fail the whole pin-run.
    if response.status_code == 422:
        logger.info('Preservation ref %s already exists - leaving as-is', preservation_ref)
    else:
        response.raise_for_status()
        logger.info('Created preservation ref %s -> %s', preservation_ref, tip)


def _delete_signing_branch(
    *,
    repo: gitpython.Repo,
    branch: str,
) -> None:
    try:
        repo.git.push('origin', '--delete', branch)
        logger.info('Deleted signing branch %s', branch)
    except Exception as e:
        logger.warning('failed to delete signing branch %s: %r', branch, e)


def _resolve_host(host: str | None) -> str:
    '''
    return `host` if given, otherwise derive it from the environment so createCommitOnBranch hits
    the right GitHub (incl. GHE mirrors — the whole point of this change; without it signed_commit
    defaults to api.github.com). github is reached via the action-path-loaded signed_commit module,
    not the gha-libs bundle.
    '''
    if host is not None:
        return host
    host, _, _ = sign_pin_commit.signed_commit.github.host_org_and_repo(repo_url=None)
    logger.info('Derived GraphQL host: %s', host)
    return host


def _sign_one(
    *,
    repo: gitpython.Repo,
    token: str,
    repository_full_name: str,
    signing_branch: str,
    parent: str,
    path: str,
    content: bytes,
    message: str,
    host: str,
) -> str:
    '''
    sign one file-commit onto `signing_branch` at `parent` and return the signed oid.
    '''
    oid = sign_pin_commit.sign_file_commit(
        token=token,
        repository_full_name=repository_full_name,
        branch=signing_branch,
        expected_head_oid=parent,
        path=path,
        content=content,
        message=message,
        host=host,
    )
    logger.info('Created signed pinning commit %s for %s', oid, path)
    return oid


def _sign_many(
    *,
    token: str,
    repository_full_name: str,
    signing_branch: str,
    parent: str,
    files: list[tuple[str, bytes]],
    message: str,
    host: str,
) -> str:
    '''
    sign a single commit containing all `(path, content)` pairs in `files` onto `signing_branch`
    at `parent`, and return the signed oid.
    '''
    oid = sign_pin_commit.sign_files_commit(
        token=token,
        repository_full_name=repository_full_name,
        branch=signing_branch,
        expected_head_oid=parent,
        files=files,
        message=message,
        host=host,
    )
    logger.info('Created signed bundle commit %s (%d files)', oid, len(files))
    return oid


def _iter_bundle_files(
    *,
    worktree_path: str,
    bundle_dir: str | None,
    bundle_action_prefix: str,
    bundle_rel: str,
):
    '''
    copy `bundle_dir` into <bundle_action_prefix>/<bundle_rel> inside the worktree and yield
    (worktree-relative-path, content-bytes) for each bundle file, in a deterministic order.
    Yields nothing when no bundle is given.
    '''
    if not bundle_dir:
        return
    target_bundle = os.path.join(worktree_path, bundle_action_prefix, bundle_rel)
    shutil.copytree(bundle_dir, target_bundle)
    for dirpath, _, fnames in os.walk(target_bundle):
        for fname in sorted(fnames):
            abspath = os.path.join(dirpath, fname)
            relpath = os.path.relpath(abspath, worktree_path)
            with open(abspath, 'rb') as f:
                yield relpath, f.read()


def _rewritten_content(
    *,
    blob: gitpython.Blob,
    own_org: str,
    own_repo: str,
    prefix: str,
    prefix_to_digest: dict[str, str],
    repo: gitpython.Repo,
    bundle_dir: str | None,
    bundle_action_prefix: str,
    worktree_path: str,
) -> bytes | None:
    '''
    rewrite `blob`'s `uses:` references to pinned digests (and apply the bundle rewrite for the
    install action) and return the new file content as bytes. Returns None when nothing changed.
    '''
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
        return None

    abs_path = os.path.join(worktree_path, blob.path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, 'w') as f:
        yaml.dump(parsed, f)
    with open(abs_path, 'rb') as f:
        return f.read()


def create_pinned_branch(
    *,
    repo_root: str,
    own_ref: str,
    target_branch: str,
    own_org: str | None,
    own_repo: str | None,
    token: str,
    host: str | None = None,
    bundle_dir: str | None = None,
    bundle_action_prefix: str = '.github/actions/install-gardener-gha-libs',
) -> None:
    '''
    Build GitHub-signed pinned commits via createCommitOnBranch and move the target branch to the
    resulting tip. The signed-commit chain is built on a throwaway signing-branch; the target
    branch (e.g. v1) is moved at the end.

    If bundle_dir is given, its contents are copied into <bundle_action_prefix>/_bundle/ and the
    action YAML at that prefix is rewritten to install from the bundle instead of cloning.
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

    # The commit target is the origin remote (where createCommitOnBranch authors and where we push
    # the target branch). This is the push target, NOT own_org/own_repo (which only identifies
    # whose `uses:` references get rewritten — usually the same repo, but not when pinning a
    # different repo's refs, e.g. a fork pinning upstream refs).
    push_org, push_repo = _org_and_repo_from_remote(repo)
    repository_full_name = f'{push_org}/{push_repo}'
    bundle_rel = '_bundle'

    # Real run: build the signed-commit chain on a throwaway signing-branch, then move the target
    # branch. signing_branch and host are resolved lazily on the first commit so an empty run stays
    # remote-free. The whole phase runs under one try/finally so a failure still deletes the branch.
    signing_branch: str | None = None
    resolved_host: str | None = None
    current = own_digest
    commit_count = 0

    try:
        with tempfile.TemporaryDirectory(prefix='pin-') as tmpdir:
            worktree_path = os.path.join(tmpdir, 'worktree')
            repo.git.worktree('add', '--detach', worktree_path, own_digest)
            try:
                prefix_to_digest: dict[str, str] = {}

                # --- bundling pass: the whole bundle becomes ONE signed commit (before rewrites).
                #     createCommitOnBranch carries all additions in a single mutation, so there is no
                #     need (and it would bloat the v1 chain) to sign one commit per bundle file.
                bundle_files = list(_iter_bundle_files(
                    worktree_path=worktree_path,
                    bundle_dir=bundle_dir,
                    bundle_action_prefix=bundle_action_prefix,
                    bundle_rel=bundle_rel,
                ))
                if bundle_files:
                    if signing_branch is None:
                        signing_branch = _create_signing_branch(repo=repo, parent=own_digest)
                    if resolved_host is None:
                        resolved_host = _resolve_host(host)
                    current = _sign_many(
                        token=token,
                        repository_full_name=repository_full_name,
                        signing_branch=signing_branch,
                        parent=current,
                        files=bundle_files,
                        message=(
                            f'pin: bundle own sources into '
                            f'{bundle_action_prefix}/{bundle_rel}\n\n'
                            f'own-commit: {own_digest}\nown-ref: {own_ref}'
                        ),
                        host=resolved_host,
                    )
                    commit_count += 1

                # --- rewrite pass: leaf-first, signing each changed file immediately so parents
                #     can pin against the child's real signed oid ---
                for prefix in ordered_prefixes:
                    blob = prefix_to_blob.get(prefix)
                    if blob is None:
                        logger.warning('No blob for prefix %s — skipping', prefix)
                        continue

                    content = _rewritten_content(
                        blob=blob,
                        own_org=own_org,
                        own_repo=own_repo,
                        prefix=prefix,
                        prefix_to_digest=prefix_to_digest,
                        repo=repo,
                        bundle_dir=bundle_dir,
                        bundle_action_prefix=bundle_action_prefix,
                        worktree_path=worktree_path,
                    )

                    if content is None:
                        # unchanged file is already in its final pinned state at the current chain
                        # tip; parents pin against that (mirrors original local-commit behaviour).
                        prefix_to_digest[prefix] = current
                        continue

                    if signing_branch is None:
                        signing_branch = _create_signing_branch(repo=repo, parent=own_digest)
                    if resolved_host is None:
                        resolved_host = _resolve_host(host)
                    current = _sign_one(
                        repo=repo,
                        token=token,
                        repository_full_name=repository_full_name,
                        signing_branch=signing_branch,
                        parent=current,
                        path=blob.path,
                        content=content,
                        message=(
                            f'pin: rewrite references in {blob.path}\n\n'
                            f'own-commit: {own_digest}\nown-ref: {own_ref}'
                        ),
                        host=resolved_host,
                    )
                    commit_count += 1
                    # parents pin against this child's real signed oid
                    prefix_to_digest[prefix] = current
            finally:
                repo.git.worktree('remove', '--force', worktree_path)

        if commit_count == 0:
            logger.info('No rewrites were necessary; target branch unchanged')
            return

        _move_target_branch(
            token=token,
            repository_full_name=repository_full_name,
            target_branch=target_branch,
            tip=current,
            own_digest=own_digest,
            host=resolved_host,
        )
        logger.info('Done. Pinned tip: %s', current)
    finally:
        if signing_branch is not None:
            _delete_signing_branch(repo=repo, branch=signing_branch)


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
    parser.add_argument(
        '--token',
        default=os.environ.get('GITHUB_TOKEN'),
        help='GitHub token used to author signed commits (default: $GITHUB_TOKEN)',
    )
    parser.add_argument(
        '--host',
        default=None,
        help='GitHub host for the GraphQL endpoint (default: derived from environment)',
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
        token=args.token,
        host=args.host,
        bundle_dir=args.bundle_dir,
    )


if __name__ == '__main__':
    main()
