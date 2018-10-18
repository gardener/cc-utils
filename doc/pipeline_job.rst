************
Pipeline Job
************

Each :doc:`Build Pipeline </pipeline>` may define an arbitrary amount of build jobs. Job definitions
reside below a pipeline's `variants` attribute. Each job defines their name as root element.


Attributes
##########

+-------------------+---------------------------------------------------------------------------+
| attribute         | explanation                                                               |
+===================+===========================================================================+
| <name>            | the user-chosen job name.                                                 |
+-------------------+---------------------------------------------------------------------------+
| repo              | main repository configuration                                             |
+-------------------+---------------------------------------------------------------------------+
| repos             | optionally defines additional repositories                                |
+-------------------+---------------------------------------------------------------------------+
| steps             | defines :doc:`Build Steps </pipeline_step>`                               |
+-------------------+---------------------------------------------------------------------------+
| traits            | defines :doc:`Traits </traits>`                                           |
+-------------------+---------------------------------------------------------------------------+

Repositories
############

Main repository
---------------

Each pipeline has a main repository. It is implied by the GitHub repository from which the
pipeline definition was read.

- logical repository name defaults to `source`
- branch an repo_path are determined by repository
- repository path is implied by repository


Additional repositories
-----------------------

Additional repositories may be referenced. Different from the main repository, all of the
following attributes must be specified:

- logical repository name
- branch name
- repository path

Repository Attributes
---------------------

.. model_element::
  :name: Repository Config
  :qualified_type_name: concourse.model.resources.RepositoryConfig
