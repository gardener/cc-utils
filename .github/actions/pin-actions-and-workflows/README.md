# pin-actions-and-workflows

<!-- LLM-CONTEXT-START
This document describes a GitHub Actions composite action and reusable workflow for
pinning cross-references between co-located actions and workflows in a single
repository by commit digest. Key files:
  - action.yaml         — composite action definition (inputs, steps)
  - pin.py              — implementation script (Python, GitPython, ruamel.yaml)
  - ../../../.github/workflows/pin-actions-and-workflows.yaml — reusable workflow

Terminology:
  - "own" org/repo/branch — the repository containing the actions/workflows being
    pinned (as opposed to third-party actions from other repositories)
  - "commit digest" — a full git commit SHA used as an immutable reference

Design decisions:
  - Uses ruamel.yaml for round-trip YAML parsing (preserves formatting/comments)
  - Uses GitPython for all git operations (no subprocess wrappers)
  - Uses graphlib.TopologicalSorter for leaf-to-root processing order
  - Pinned-reference detection uses repo.commit(ref) + branch/tag exclusion:
    branch and tag names resolve to commits but are mutable, so only direct
    SHAs (full or abbreviated) are considered pinned; stale SHAs return False
  - own-org and own-repo default to values derived from the origin remote URL,
    so callers typically do not need to specify them explicitly
  - Pinned commits are pushed to a separate target branch (default: v1) via
    force-push; previous tips are preserved via refs/tags/fixated/<own-commit-digest>
  - pin.py has a full CLI for local testing (--dry-run, --verbose)
LLM-CONTEXT-END -->

## Motivation

GitHub Actions and reusable workflows in a single repository typically reference
each other by branch name (e.g. `@master`). This is convenient but means there is
no way to pin to a specific, immutable version of a co-located action or workflow:
any commit that touches an action immediately affects all consumers, with no ability
to reference a known-good prior state.

Splitting each action into its own repository (the conventional solution) is
impractical at scale — it would produce a large number of repositories and
significantly increase maintenance overhead.

This action solves the problem by generating a snapshot of the repository on a
separate target branch (default: `v1`), where all cross-references between actions
and workflows are pinned by commit digest. Consumers can reference:

- `@v1` — a rolling pointer to the latest pinned snapshot, equivalent in
  convenience to `@master` but with consistent transitive pinning
- `@<commit-digest>` — a fully immutable reference; the entire transitive closure
  of actions and workflows is frozen at that digest

## How it works

After every push to the own branch (`master`), the
[`pin-actions-and-workflows` workflow](../../../.github/workflows/pin-actions-and-workflows.yaml)
runs `pin.py`, which:

1. Builds the dependency graph of all actions and workflows in the repository.
2. Processes files in topological order (leaves first), so that when a file is
   rewritten, all files it references have already been assigned a pinned digest.
3. For each file whose cross-references need rewriting, creates a minimal commit
   containing only that file's change. This covers both `uses:` references to
   own actions/workflows and `ref:` fields in `actions/checkout` steps that check
   out the own repository (e.g. `install-gardener-gha-libs`).
4. Force-pushes the resulting commit chain to the target branch (`v1`).
5. Creates a preservation ref at `refs/tags/fixated/<own-commit-digest>` to prevent the
   tip commit from being garbage-collected after future force-pushes.

## Usage

### As a caller workflow (triggering on push to master)

```yaml
jobs:
  pin:
    uses: gardener/cc-utils/.github/workflows/pin-actions-and-workflows.yaml@master
    secrets: inherit
    permissions:
      contents: write
```

`own-org` and `own-repo` default to the calling repository's organisation and name
and typically do not need to be specified.

### As a standalone action (in a custom workflow)

```yaml
- uses: gardener/cc-utils/.github/actions/pin-actions-and-workflows@master
  with:
    target-branch: v1      # default
    own-branch: master     # default
    # own-org and own-repo derived from origin remote by default
```

### Local testing

```bash
pip install gitpython ruamel.yaml

.github/actions/pin-actions-and-workflows/pin.py \
  --verbose
  # --own-org and --own-repo derived from origin remote by default
```

## Inputs

| Input | Default | Description |
|---|---|---|
| `own-branch` | `master` | Branch to read from |
| `target-branch` | `v1` | Branch to push pinned commits to (force-pushed) |
| `own-org` | *(from remote)* | GitHub organisation (e.g. `gardener`) |
| `own-repo` | *(from remote)* | GitHub repository name (e.g. `cc-utils`) |
| `git-user-name` | `github-actions[bot]` | Author name for pinning commits |
| `git-user-email` | `github-actions[bot]@...` | Author email for pinning commits |

## Backwards compatibility

Existing consumers referencing `@master` are unaffected — `master` continues to
work as before. Migration to `@v1` or a specific commit digest is purely opt-in.

## Garbage collection and preservation refs

Force-pushing `v1` makes previous tip commits unreachable from branch heads. To
ensure old commit digests remain resolvable (so consumers pinned to them are not
broken), a preservation ref is created at `refs/tags/fixated/<own-commit-digest>` for
every processed own commit. These refs live outside `refs/heads/` and do not appear
in the branch list, but prevent git GC from collecting the referenced objects.

Note that it is not possible to enumerate all external consumers of a given commit
digest, so preservation refs are kept indefinitely. Organisations with strict ref
hygiene may wish to implement a retention policy (e.g. delete refs older than one
year), accepting that very old pinned digests may eventually become unresolvable.
