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

Terms and definitions
#####################

Although using `Concourse <https://concourse-ci.org>`_ as underlying build scheduler, we
use some terms differently as done in the context of concourse.

+------------------------------+-----------------------------------------------------+
| term                         |  in Gardener CI/CD                                  |
+==============================+=====================================================+
| Pipeline                     | a set of jobs (also: variants) defined in           |
|                              | `.ci/pipeline_definitions`                          |
+------------------------------+-----------------------------------------------------+
| Job                          |  a graph of build steps                             |
+------------------------------+-----------------------------------------------------+
| :doc:`Step <pipeline_steps>` | an executable with a container image as environment |
+------------------------------+-----------------------------------------------------+
| Trait                        | adds certain semantics to a build job (does not     |
|                              | exist in concourse)                                 |
+------------------------------+-----------------------------------------------------+


Pipeline Definition
###################

Any GitHub repository residing below an organisation owned by the Gardener team is scanned
for `.ci/pipeline_definitions` files within their default branch's source tree. If a valid pipeline
definition is found, all contained pipeline definitions are generated into concourse build
pipelines.

Pipeline definitions are valid `YAML <https://yaml.org>`_ files adhering to the schema defined
in this reference documentation.

Branch-specific configuration
*****************************

By default, only the default branch is considered. An optional `branch.cfg` YAML file *may* be
placed in a repository's special ref `refs/meta/ci`.o

If a branch configuration is present in a repository, then different semantics is applied when
searching the repository for pipeline definitions:

For each branch, a matching branch configuration element is looked up. Iff a matching element is
found, the pipeline definition file (if present) from that branch's head's worktree is used to
instantiate the defined pipelines. Branch-specific pipeline definition fragments (see `inherit`
attribute) are optionally applied.

A common usage scenario may be the declaration of hotfix release jobs for release branches

Attributes
----------

+------------+---------------------------------------------------------------------------+
| attribute  | explanation                                                               |
+============+===========================================================================+
| cfgs       | mandatory root attribute                                                  |
+------------+---------------------------------------------------------------------------+
| <cfg_name> | user-chosen configuration element name (ASCII-alphanumeric)               |
+------------+---------------------------------------------------------------------------+
| branches   | list of regular expcessions used to match branche names (at least one)    |
+------------+---------------------------------------------------------------------------+
| inherit    | optional pipeline definition fragment; inherited into pipeline definition |
+------------+---------------------------------------------------------------------------+


Example (schematic)
------------------

.. code-block:: yaml

	cfgs:         # root attribute, required
	   <cfg_name>:
		branches: # branch filter
		   <list of branch names>
		inherit: ~ # optional branch-specific pipeline definition

Example (hotfix-branch release jobs)
------------------------------------

.. code-block:: yaml

  cfgs:
      default:
          branches: ['master']
          inherit:
              example-pipeline:
                  variants:
                      release-job:
                          traits:
                              release:
                                  nextversion: 'bump_minor'
      hotfix:
          branches: ['rel-.*']
          inherit:
              example-pipeline:
                  variants:
                      release-job:
                          traits:


Traits
######

* :doc:`traits/component_descriptor`
* :doc:`traits/cronjob`
* :doc:`traits/draft_release`
* :doc:`traits/image_scan`
* :doc:`traits/options`
* :doc:`traits/publish`
* :doc:`traits/pullrequest`
* :doc:`traits/release`
* :doc:`traits/scheduling`
* :doc:`traits/slack`
* :doc:`traits/update_component_deps`
* :doc:`traits/version`

Indices and tables
##################

* :ref:`genindex`
