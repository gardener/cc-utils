==============
GitHub-Actions
==============

`GitHub-Actions <https://github.com/features/actions>`_ is a managed CI/CD offering integrated
into GitHub. For standardisation and re-use within Gardener-Project, we maintain some re-usable
Actions and Workflows (kept in
`cc-utils repository <https://github.com/gardener/cc-utils/tree/master/.github>`_). Those offer
functionality similar to Concourse-Pipeline-Template. This includes integration with
`OCM (Open Component Model) <https://ocm.software>`_, as well as release (notes) handling.

To improve security posture, we make use of trustbased-authentication, and avoid usage of static
credentials where possible.

Migration from Concourse-Pipelines
==================================

The default pipeline setup for Concourse-Pipelines consists of three pipelines:

* head-update (run for certain branches - typically default + release-branches)
* pull-request (run for pullrequests)
* release (manually triggered to publish new releases)

This is migrated to workflows of the following layout:

.. code-block::

    .github/workflows/build.yaml       # shared; called by other workflows
    .github/workflows/release.yaml     # for manually triggering releases
    .github/workflows/non-release.yaml # for triggering upon head-updates / pullrequests


Branch-Protection -> Rulesets
-----------------------------

Most repositories use branch-protections to forbid directly pushing to a branch. Some pipelines,
most prominently release-pipelines (which push release- and "bump"-commits), need to circumvent
those rules. As we want to avoid using static credentials (for Service-Accounts w/
owner-permissions), we use a `GitHub-App <https://github.com/apps/gardener-github-actions>`_
for granting such permissions more fine-granular.

However, as discussed `here <https://github.com/orgs/community/discussions/13836>`_, it is only
possible to grant exceptions (or "bypassers", as GitHub Rulesets call them) to branch protections
if using the more modern `Rulesets`. Hence, as part of migration to GitHub-Actions, it is necessary
to migrate branch-protections to rulesets.

.. note::
   It is important to no longer use "classical" branch protections after migration of
   release-pipeline to GitHub-Actions. Configure any protection-rules using the more modern
   `Rulesets`, instead. The latter are a superset to branch-protections w.r.t. configuration
   options.


Pull-Requests from forked Repositories
======================================

To mitigate harm from malicious Pull-Requests, GitHub-Actions-Workflow-Runs are run with restricted
privileges. If using `pull_request` as pipeline-trigger, corresponding workflow-runs will only have
readonly-access to target-repository. This prevents such runs to push build artefacts, such as
OCI-Images or Helmcharts (to OCI-Registries).

As an alternative, there is the `pull_request_target` trigger, which does not have this limitation.
However, by default, thus-triggered runs will be based on the pull-request's target repository,
i.e. the actual changes proposed by a Pull-Request will not be visible to the pipeline-run.

For the latter case, there is the
`trusted-checkout <https://github.com/gardener/cc-utils/tree/master/.github/actions/trusted-checkout>`_
action, which will circumvent this limitation, and explicitly checkout commits from trusted
pullrequests. Pullrequests are considered to be trusted, if

- the fork's owner is the same as the target-repository (i.e. a fork within the same organisation)
OR
- the pullrequest-author is either of:
  - `COLLABORATOR`
  - `CONTRIBUTOR`
  - `MEMBER` (org-member)
  - `OWNER` (repository-owner)
- the pullrequest has a certain label (default: `ok-to-test`) set

The preferred approach (because it will also work for first-time contributors) is using
"label-based trust".

.. warning::
   For pullrequests that are not considered to be trusted, the workflow-run will still be executed.
   However, re-usable workflows from cc-utils will not attempt to push build-results, nor will
   the run be based on the changes from the pullrequest, which may be unintuitive.

   In such cases, a warning is emitted into the pipeline-run's summary.


.. note::
   There are the following "autor-associations" a pullrequest author can have:

   ======================= ===============================================================
   association             explanation
   ======================= ===============================================================
   COLLABORATOR            Author has been invited to collaborate on the repository
   CONTRIBUTOR             Author has previously committed to the repository
   FIRST_TIMER             Author has not previously committed to GitHub
   FIRST_TIME_CONTRIBUTOR  Author has not previously committed to the repository
   MANNEQUIN               Author is a placeholder for an unclaimed user
   MEMBER                  Author is a member of the organization that owns the repository
   NONE                    Author has no association with the repository
   OWNER                   Author is the owner of the repository.
   ======================= ===============================================================

When to use what
----------------

If a workflow does not need to publish changes from pullrequests, use `on.pull_request`.
Otherwise, use `on.pull_request_target`. In this case, consistently use `trusted-checkout` instead
of `actions/checkout`.

.. warning::
   If using `pull_request_target`, special care needs to be done to catch malicious changes,
   especially such changes that are done in buildscripts.

Example configuration for label-based trust
-------------------------------------------

If privileged pipelines are needed, use the following event-trigger:

.. code-block:: yaml

   on:
      pull_request_target:
         types:
            - labeled

   jobs:
      example:
         # the left condition (!= labeled) is only needed, if different triggers (e.g. push) are
         # used.
         # it is important to add the explicit check for label's name to prevent accidental
         # triggering (e.g. from gardener-robot setting initial set of labels)
         if: ${{ github.event.action != 'labeled' || (github.event.label.name == vars.DEFAULT_LABEL_OK_TO_TEST && vars.DEFAULT_LABEL_OK_TO_TEST != '')}}
         permissions:
            pull-requests: write # needed so trusted-checkout can remove trusted-label
                                 # caveat: also needs to be set for all called workflows
                                 # that use trusted-checkout (action)
         ...

The following workflow can be added for convenience:

.. code-block:: yaml

   # pullrequest-trust-helper.yaml
   on:
      pull_request_target:
         types:
            - opened
            - edited
            - reopened
            - synchronize

   jobs:
      pullrequest-trusted-helper:
         permissions:
            pull-requests: write
         secrets: inherit # access to `GitHub-Actions`-App is needed to read teams
         uses: gardener/cc-utils/.github/workflows/pullrequest-trust-helper.yaml@master
         with:
            # members will be trusted (-> get okay-to-test-label automatically)
            trusted-teams: 'first-team,second-team'

Caveats
-------

Regardless which of `on.pull_request` or `on.pull_request_target` is used, workflow-runs will
always be based on target-repository's local workflow- and actions-definitions.

.. note::
   Be sure to grant `pull-requests: write`-permission to all workflows called from
   pull_request_target-event (this is needed so trusted-checkout action is able to remove
   trusted-label).

Release Branches
================

To allow an automatic management of release branches (i.e. branch creation/deletion, draft release
notes), it is necessary to provide general information on the release cycle in the special
`.ocm/branch-info.yaml` file (see
`model class <https://github.com/gardener/cc-utils/blob/master/ocm/branch_info.py>`_ for its
expected structure and default values). Example:

.. code-block:: yaml

   # .ocm/branch-info.yaml
   release-branch-template: release-v$major.$minor # e.g. release-v1.0
   branch-policy:
      significant-part: minor # major, minor, patch
      supported-versions-count: 2
      release-cadence: 2w # d (days), w (weeks), y | yr (years) | null

The `release-cadence` together with the `supported-versions-count` are used to determine an
estimated end-of-life date for each release. This method can be leveraged by a component-responsible
to convey the information what kind of maintenance/releases can be expected to the stakeholders. In
case either of both properties is set to `null`, there will be no end-of-life date being calculated.
This might be reasonable in case the component has an infrequent or irregular release schedule.

By setting the `create-release-branch: true` input for the `release.yaml` workflow, a successful
release will automatically create a new release branch according to the specified
`release-branch-template`. This will include an automatic version bump to the next patch version as
well.

By setting the `cleanup-release-branches: true` input for the `release.yaml` workflow, stale release
branches will be automatically deleted upon a successful release. A branch is considered stale if it
matches the `release-branch-template` and sts version (major, minor, or patch, according to
`significant-part`) is older than the current version by at least the `supported-versions-count`.
