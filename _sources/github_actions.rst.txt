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
