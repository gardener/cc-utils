*******************
Pipeline Definition
*******************


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



