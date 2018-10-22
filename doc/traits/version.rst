`version` Trait
===============

.. trait::
    :name: version


Adds version handling for the pipeline's `main repository`. Implies that the
`main repository` host a `gardener component`.

During job execution, an `effective version` is calculated and made available
via regular file `${VERSION_PATH}/version`.

Component versions must be valid [semver](https://semver.org) versions.

`preprocess` attribute
----------------------

+--------------------+------------------------------------------------------+
| value              | explanation                                          |
+====================+======================================================+
| noop               | no change                                            |
+--------------------+------------------------------------------------------+
| finalize           | remove suffix                                        |
+--------------------+------------------------------------------------------+
| inject-commit-hash | set version suffix to main repo's head commit hash   |
+--------------------+------------------------------------------------------+
| inject-branch-name | set version suffix to main repository's branch name  |
+--------------------+------------------------------------------------------+
| use-branch-name    | set version to branch name                           |
+--------------------+------------------------------------------------------+
