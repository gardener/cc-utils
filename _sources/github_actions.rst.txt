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
action, which will circumvent this limiation, and explicitly checkout commits from trusted
pullrequests. Pullrequests are considered to be trusted, if

- the fork's owner is the same as the target-repository (i.e. a fork within the same organisation)
OR
- the pullrequest-author is either of:
  - `COLLABORATOR`
  - `CONTRIBUTOR`
  - `MEMBER` (org-member)
  - `OWNER` (repository-owner)

.. note::
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

Caveats
-------

Regardless which of `on.pull_request` or `on.pull_request_target` is used, workflow-runs will
always be based on target-repository's local workflow- and actions-definitions.
