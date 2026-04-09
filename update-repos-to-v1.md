This repository contains multiple actions and workflows, which are used by many repositories
in https://github.com/gardener organisation.

Up to now, those usages were done by referencing `@master` branch of cc-utils.

However, as of recently (see pin-actions-and-workflows.yaml in this here repository),
pinned workflows are available from `v1` branch.

I would like to raise pullrequests for all repositories below aforementioned github-organisation,
for all "active" (non-archived) repositories that have references to acitons or workflows from
cc-utils repository using @master, which will switch those references to `v1`.

The pullrequest should contain a short motivation (summary of doc/github_actions.rst - there is
section about the branching model). note that for links, rather link to github-pages at
https://gardener.github.io/cc-utils, than to source-file).

Furthermore, I would like to be able to re-run this activity, and only raise (new) pullrequests
for repositories that do not either already have such a pullrequest, or are archived, or do not
use workflows or actions from cc-utils using @master.

Lastly, I would like to have a means to generate a report listing (and grouping) of repositories,
where categories are:
- uses no actions/workflows from cc-utils (those should go last)
- uses actions/workflows from cc-utils using @master
- uses actions/worklfows from cc-utils using @v1
- uses actions/workflows from cc-utils using anything but master or v1

I suggest you generate a couple of scripts for that into this repository (but do not put it under
version-control). Let's say gha-pinning.d (create the directory).

The script creating pullrequest should have a --dry-run-mode.
