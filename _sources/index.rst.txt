
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
`cc-config <https://github.com/gardener/cc-config` repository.


Indices and tables
==================

* :ref:`genindex`
