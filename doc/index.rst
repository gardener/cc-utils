
.. _build_pipeline_reference_manual:

==========================================
CC-Utils - Build Pipeline Reference Manual
==========================================

.. toctree::
    :hidden:
    :titlesonly:
    :maxdepth: 2

    pipeline
    pipeline_job
    pipeline_step
    traits
    release_notes

.. note::
   There is an ongoing migration from Concourse-Pipeline-Template to
   `GitHubActions <https://github.com/features/actions>`_

   See :doc:`Migration to GHA <github_actions>` for details.


Introduction
============

In order to run continuous delivery workloads for all components contributing to the
`Gardener <https://github.com/gardener>`_ project, we operate a central service.

Typical workloads encompass the execution of tests and builds of a variety of technologies,
as well as building and publishing container images, typically containing build results.

We are building our CI/CD offering around some principles:

*  **container-native** - each workload is executed within a container
   environment. Components may customise used container images
* **automation** - pipelines are generated without manual interaction
* **self-service** - components customise their pipelines by changing their sources
* **standardisation**

As an execution environment for CI/CD workloads, we use `Concourse <https://concourse-ci.org>`_.
We however abstract from the underlying "build executor" and instead offer a
`Pipeline Definition Contract`, through which components declare their build pipelines as
required.

Terms, Definitions and Concepts
===============================

Although we are using `Concourse <https://concourse-ci.org>`_ as underlying build scheduler, we
use some terms differently as done in the context of concourse.

+------------------------------+-----------------------------------------------------+
| term                         |  in Gardener CI/CD                                  |
+==============================+=====================================================+
| :doc:`Pipeline <pipeline>`   | a set of jobs defined in                            |
|                              | `.ci/pipeline_definitions`                          |
+------------------------------+-----------------------------------------------------+
| :doc:`Job <pipeline_job>`    |  a graph of build steps                             |
+------------------------------+-----------------------------------------------------+
| :doc:`Step <pipeline_step>`  | an executable with a container image as environment |
+------------------------------+-----------------------------------------------------+
| :doc:`Trait <traits>`        | adds certain semantics to a build job (does not     |
|                              | exist in concourse)                                 |
+------------------------------+-----------------------------------------------------+



Indices and tables
==================

* :ref:`genindex`
