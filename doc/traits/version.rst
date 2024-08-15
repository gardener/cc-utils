===============
*version* Trait
===============

.. trait::
    :name: version


Adds version handling for the pipeline's `main repository`. Implies that the
`main repository` hosts a `gardener component`.

During job execution, an `effective version` is calculated and made available
via regular file `${VERSION_PATH}/version` or via environment variable `EFFECTIVE_VERSION`.

Component versions must be valid `SemVer <https://semver.org>`_ versions.

`preprocess` Attribute
======================

+--------------------+------------------------------------------------------+
| value              | explanation                                          |
+====================+======================================================+
| noop               | no change                                            |
+--------------------+------------------------------------------------------+
| finalize           | remove suffix                                        |
+--------------------+------------------------------------------------------+
| inject-commit-hash | set version suffix to main repo's head commit hash   |
+--------------------+------------------------------------------------------+
| inject-timestamp   | set version suffix to the POSIX timestamp            |
+--------------------+------------------------------------------------------+
| inject-branch-name | set version suffix to main repository's branch name  |
+--------------------+------------------------------------------------------+
| use-branch-name    | set version to branch name                           |
+--------------------+------------------------------------------------------+
