
.. _build_pipeline_reference_manual:

==========================================
CC-Utils - Build Pipeline Reference Manual
==========================================

.. toctree::
    :hidden:
    :titlesonly:
    :maxdepth: 2

    github_actions
    release_notes

.. note::
   There was a migrate from Concourse-Pipeline-Template to
   `GitHubActions <https://github.com/features/actions>`_

   See :doc:`Migration to GHA <github_actions>` for details.

   Documentation is currently still a stub.


Introduction
============

Gardener-Project makes use of GitHub-Actions for Build- and Release-Pipelines.

Common patterns (for example authenticating against OCI-Registries, describing deliverables with
OCM, ..) are extracted into re-usable actions and workflows in
`cc-utils <https://github.com/gardener/cc-utils>`_ repository.


Reuse and Branching Model
=========================

Many Actions and reusable workflows are maintained in `cc-utils` mono-repository. Hence, some
special handling is put into place to allow for both convenient developing and testing, as well
as downstream users to be served with pinpointed vectors of Actions and workflows.

Development continues on the ``master`` branch. After prequalification, the ``v1`` branch is
updated to a consistent, fully pinned snapshot (all internal cross-references pinned by commit
digest). See the
`pin-actions-and-workflows <https://github.com/gardener/cc-utils/tree/master/.github/actions/pin-actions-and-workflows>`_
action for technical details.

Downstream users should choose one of the following reference strategies:

``@v1`` (rolling, prequalified)
    Recommended for most users. Always points to the latest prequalified snapshot with all
    cross-references digest-pinned.

``@<commit-digest>`` (fully immutable)
    Pin to a specific commit digest that ``v1`` pointed (or points) to, for full
    reproducibility. Previous digests remain resolvable.

``@master`` (development head)
    Continues to work as before. Users are encouraged to switch to ``@v1`` or a specific
    commit digest to benefit from prequalification and consistent cross-reference pinning.


Indices and tables
==================

* :ref:`genindex`
