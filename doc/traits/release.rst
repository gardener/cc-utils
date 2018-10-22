`release` Trait
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


Example
-------

.. code-block::a yaml

  traits:
    version:
      preprocess: 'finalize' # recommended
    release:
      nextversion: 'bump_minor'
