******************************************
CC-Utils - Build Pipeline Reference Manual
******************************************

.. toctree::
    :titlesonly:
    :maxdepth: 2
    :glob:

Introduction
############

In order to run continuous delivery workloads for all components contributing to the
`Gardener <https://github.com/gardener>`_ project, we operate a central service.

Typical workloads encompass the execution of tests and builds of a variety of technologies,
as well as building and publishing container images, typically containing build results.

We are building our CI/CD offering around some principles:

*  *container-native* - each workload is executed within a container
   environment. Components may customise used container images
* *automation* - pipelines are generated without manual interaction
* *self-service* - components customise their pipelines by changing their sources
* *standardisation*

As a execution environment for CI/CD workloads, we use `Concourse <https://concourse-ci.org>`_.
We however abstract from the underlying "build build executor" and instead offer a
`Pipeline Definition Contract`, through which components declare their build pipelines as
required.

Terms, definitions and concepts
###############################

Although using `Concourse <https://concourse-ci.org>`_ as underlying build scheduler, we
use some terms differently as done in the context of concourse.

+------------------------------+-----------------------------------------------------+
| term                         |  in Gardener CI/CD                                  |
+==============================+=====================================================+
| :doc:`Pipeline <pipeline>`   | a set of jobs (also: variants) defined in           |
|                              | `.ci/pipeline_definitions`                          |
+------------------------------+-----------------------------------------------------+
| :doc:`Job <pipeline_job>`    |  a graph of build steps                             |
+------------------------------+-----------------------------------------------------+
| :doc:`Step <pipeline_steps>` | an executable with a container image as environment |
+------------------------------+-----------------------------------------------------+
| :doc:`Trait <traits>`        | adds certain semantics to a build job (does not     |
|                              | exist in concourse)                                 |
+------------------------------+-----------------------------------------------------+



Indices and tables
##################

* :ref:`genindex`
