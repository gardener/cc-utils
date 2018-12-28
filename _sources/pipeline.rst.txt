===================
Pipeline Definition
===================


Any GitHub repository residing below an organisation owned by the Gardener team is scanned
for `.ci/pipeline_definitions` files within their default branch's source tree. If a valid pipeline
definition is found, all contained pipeline definitions are generated into concourse build
pipelines.

Pipeline definitions are valid `YAML <https://yaml.org>`_ files adhering to the schema defined
in this reference documentation.

Pipeline Definition Schema
==========================

Pipeline definition documents may contain an arbitrary amount of pipelines. Each top-level
attribute in a `.ci/pipeline_definitions` file declares a pipeline. The attribute name being
the pipeline name.

.. danger::
  All pipeline names share a global namespace. So be sure not to re-use an existing name
  already in use be another component.

Attributes
^^^^^^^^^^

+-------------------+---------------------------------------------------------------------------+
| attribute         | explanation                                                               |
+===================+===========================================================================+
| <name>            | the user-chosen pipeline name. Top-level attribute                        |
+-------------------+---------------------------------------------------------------------------+
| template          | pipeline template to use. Defaults to 'default' (for future extensions)   |
+-------------------+---------------------------------------------------------------------------+
| base_definition   | inherited from all :doc:`Jobs </pipeline_job>`                            |
+-------------------+---------------------------------------------------------------------------+
| variants          | defines :doc:`Jobs </pipeline_job>`                                       |
+-------------------+---------------------------------------------------------------------------+


.. note::
  Each pipeline should at least define one job. Otherwise it would be empty.


.. note::
  Pipeline names are displayed in the Concourse UI. Try to keep names reasonably short (< 20 chars)
  and refrain from using whitespace of non-ASCII charaters.


Example `.ci/pipeline_definitions`
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

* define a build pipeline named `my_pipeline`
* define two :doc:`Build Jobs </pipeline_job>` `job_A`, `job_B`

.. code-block:: yaml

  my_pipeline:
    template: 'default'   # default value - may be omitted
    base_definition:
      ... # same schema as for jobs applies
    variants:
      job_A:
        ... # see pipeline jobs definition for schema
      job_B: ~


Example - Inheritance / "base_definition"
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

* define a common build step "inherit_me"
* define two jobs inheriting it

.. code-block:: yaml

  another_pipeline:
    base_definition:
      steps:
        inherit_me: ~

    variants:
      job_a:
        steps:
          another_step:
            depends: ['inherit_me']
      job_b: ~


**Result**

* job_a has two steps: `inherit_me` (from base_definition) and `another_step`
* job_b has one step: `inherit_me`

Branch-specific configuration
=============================

By default, only the default branch is considered. An optional `branch.cfg` YAML file **may** be
placed in a repository's special ref `refs/meta/ci`.

If a branch configuration is present in a repository, then different semantics is applied when
searching the repository for pipeline definitions:

For each branch, a matching branch configuration element is looked up. Iff a matching element is
found, the pipeline definition file (if present) from that branch's head's worktree is used to
instantiate the defined pipelines. Branch-specific pipeline definition fragments (see `inherit`
attribute) are optionally applied.

A common usage scenario may be the declaration of hotfix release jobs for release branches.

Attributes
^^^^^^^^^^

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
^^^^^^^^^^^^^^^^^^^

.. code-block:: yaml

	cfgs:         # root attribute, required
	   <cfg_name>:
		branches: # branch filter
		   <list of branch names>
		inherit: ~ # optional branch-specific pipeline definition

Example (hotfix-branch release jobs)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

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
          branches: ['hotfix-.*']
          inherit:
              example-pipeline:
                  variants:
                      release-job:
                          traits:
