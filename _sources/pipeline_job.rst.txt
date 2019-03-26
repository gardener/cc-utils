============
Pipeline Job
============

Each :doc:`Build Pipeline </pipeline>` may define an arbitrary amount of build jobs. Job definitions
reside below a pipeline's `jobs` attribute. Each job defines their name as root element.


Attributes
==========

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

(GitHub) Repositories
=====================

Main Repository
^^^^^^^^^^^^^^^

Each pipeline has a main repository. It is implied by the GitHub repository from which the
pipeline definition was read.

- logical repository name defaults to `source`
- branch and repo_path are determined by repository
- repository path is implied by repository


Additional Repositories
^^^^^^^^^^^^^^^^^^^^^^^

Additional repositories may be referenced. Different from the main repository, all of the
following attributes must be specified:

- logical repository name
- branch name
- repository path

Repository Attributes
^^^^^^^^^^^^^^^^^^^^^

.. model_element::
  :name: Repository Config
  :qualified_type_name: concourse.model.resources.RepositoryConfig


Default behaviour for `trigger` attribute
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The `trigger` attribute specifies whether or not head updates should trigger a job execution.

If it is not explicitly configured, the default behaviour is defined as follows:

* additional repositories default to `false`
* main repository:

  * presence of `release` or `cronjob` trait --> `false`
  * presence of `pull-request` trait --> `true`
  * if none of the above traits are present --> `true`


`trigger_paths` attribute
~~~~~~~~~~~~~~~~~~~~~~~~~

Allows to restrict build triggering by repository changes. If `trigger` attribute evaluates to
`false`, this attribute has no effect.

Using `trigger_paths`, it is possible to ignore updates in case they touch (or do not touch)
certain repository paths (globbing syntax supported).

* include: only react on specified paths - ignore all others
* exclude: ignore changes to specified paths (inverse of include)



`cfg_names` attribute
~~~~~~~~~~~~~~~~~~~~~

Specifies the GitHub instance hosting the repository. For each Concourse instance, there is a
default GitHub instance that is used in case no `cfg_name` is specified.

Available configurations are stored in a private configuration repository (`kubernetes/cc-config`).

Valid cfg_names are:
- github_com
- github_wdf_sap_corp


Examples
--------

* reference an additional repository `github.com/foo/bar`, name it `my_repo`
* the repository will be made available to builds at `${MY_REPO_PATH}`

.. code-block:: yaml

  repos:
  - name: 'my_repo'
    path: 'foo/bar'
    branch: 'master'    # must be specified - does not default to master
    cfg_name: 'github_com'


Environment Variables
=====================

Depending on pipeline definition, build jobs are run with a set of environment variables.
The variables that are defined depend on:

* which repositories are defined (and their logical names)
* which traits are defined

In case user-specified identifiers are used as input to construct environment variable names,
those are converted to UPPER-case. Kebap-case is converted into snake-case (or in other words:
any occurrence of dash `-` characters are converted to underscore `_` characters).

.. note::

  For non-ASCII or non-alphanumeric characters, the behaviour is undefined. Usage of those
  characters is forbidden for user-defined identifiers.


Environment Variables from repositories
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

For each repository, the following environment variable set is defined:

* <NAME>_PATH -> relative path to repository's work tree
* <NAME>_BRANCH -> the configured branch
* <NAME>_GITHUB_REPO_OWNER_AND_NAME -> github_path (e.g. gardener/gardener)

In addition, the :strong:`relative` path to the main repository is always stored in the
:literal:`MAIN_REPO_DIR` env variable.

Example
~~~~~~~

In case the main repository has not been explicitly configured with a name, its default logical
name is `source`. Therefore, the following environment variables will then be defined:

* SOURCE_PATH
* SOURCE_BRANCH
* SOURCE_GITHUB_REPO_OWNER_AND_NAME
* MAIN_REPO_DIR
