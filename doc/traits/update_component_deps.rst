.. trait::
    :name: update_component_deps

Declaring jobs receive "Component Dependencies upgrade" semantics. Upon execution, any
immediate dependencies declared in the component's `Component Descriptor` will be checked for
newer released versions (compared to the ones declared in the current component descriptor). Checking
for greater versions is done using `semver <https://semver.org>`_ semantics.

For each discovered component with a later release version, a `Upgrade Pull Request` is created to
the greates discovered component version. Outdated Upgrade Pull Requests are removed.

.. info::
  automatically created Upgrade PRs are identified using the following naming convention:

  `[ci:<dependency-type>:<dependency-name>:<current-version>-><target-version>]`


Component Upgrade Contract
##########################

Declaring components *must* offer an executable at `.ci/set_dependency_version` in their
repositories. It is called by the update component dependencies job for each discovered dependency
upon Pull Request creation.

The executable must modify the indicated component work tree such a (component-specific) way that the
changes contain the required changes for the requested dependency upgrade.

The execution environment is defined to the latest version of `cc-job-image`. This is may be assumed
that a Python3 runtime be available, along with all tools from `github.com/gardener/cc-utils`
(available from PYTHONPATH).

Passed environment variables
----------------------------

+--------------------+-------------------------------------------------------------+
| name               | explanation                                                 |
+====================+=============================================================+
| DEPENDENCY_TYPE    | one of: 'component', 'container_image', 'web', 'generic'    |
+--------------------+-------------------------------------------------------------+
| DEPENDENCY_NAME    | the dependency name as declared in component desscriptor    |
+--------------------+-------------------------------------------------------------+
| DEPENDENCY_VERSION | the discovered target component version (e.g. 1.2.3)        |
+--------------------+-------------------------------------------------------------+
| REPO_DIR           | the absolute path to component repo work tree               |
+--------------------+-------------------------------------------------------------+

Behavioural Contract
--------------------

The executable must return an exit code equal to zero iff all environment variables as described
above were set to sane values. I.e. an unknown dependency type or name *must* be signalled as an
error (exit code != zero).

The executable *should* output reasonable error descriptions in case of invalid or insane arguments.

The work tree specified via `REPO_DIR` may be assumed to be "clean" and writeable.

Extension Note
--------------

This contract is intended to be extended also for other dependency types. Therefore, implementations
of `.ci/set_dependency_version` are recommended to be implemented such as to reject dependency
types other than 'component' to avoid undefined behaviour.

