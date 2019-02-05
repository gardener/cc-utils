===============
*release* Trait
===============

.. trait::
    :name: release


This trait add release job semantics to the declaring build job.

.. note::

  while not enforced, it is strongly recommended to configure the effective version operation
  to `finalize` (see :doc:`version`)


The following operations are performed:

* create a release commit (persisting effective version + optional changes from callback)
* push and tag said commit as `refs/tags/<effective_version>`
* create a GitHub release for said tag

  * add :doc:`Component Descriptor <component_descriptor>` if present

* calculate next development version and persist it ("bump commit")
* post release notes to slack if :doc:`Slack trait <slack>` is declared

.. note::

  Declaring this trait changes the default triggering behaviour to "manual"


Optional Release Callback
=========================

If an optional release-callback is specified, the release commit (if created) can be enriched
with custom diffs (e.g. to update a build-tool-specific dependency declarations file).

Contract
--------

- non-zero exit codes are considered as an error (leads to release failure)
- the following environment variables are passed:
  - `REPO_DIR`: absolute path to main repository
  - `EFFECTIVE_VERSION`: the effective version (see :doc:`version`)


Example
=======

.. code-block:: yaml

  traits:
    version:
      preprocess: 'finalize' # recommended
    release:
      nextversion: 'bump_minor'
      release_callback: 'release_callback' # relative to main repository root
