============================
*component_descriptor* Trait
============================

.. trait::
    :name: component_descriptor


If declared, a `Component Descriptor` is created during job execution. If their
:doc:`/traits/release` trait is also declared, the created component descriptor is added to the
component's release artifacts (as GitHub release asset).


Example
=======

.. code-block:: yaml

  traits:
    component_descriptor: ~


Default Component Descriptor
============================

If no additional customisation is done, a _default_ component descriptor is created with the
following data:

* component_name (the github repository path, e.g. `github.com/gardener/gardener`)
* component_version (the `effective version`)

In addition, any container image that is built by the component is added as container image
dependencies.


Declaring Additional Dependencies
=================================

The component descriptor should contain the full "bill-of-materials" that makes up a component.
For many components this will simply be the set of container images built and released by the
component (those are added automatically).

To declare additional dependencies, an executable *may* be placed at `.ci/component_descriptor`
at the component repository. If such an executable is present, it is called with a defined set
of environment variables and file system layout.

Before calling the `.ci/component_descriptor` callback, the default (or base) component descriptor
is generated and offered to the component_descriptor script.

After termination, the component_descriptor script is expected to leave a valid "final"
component_descriptor at a defined file system path. Unresolved `component references` are
automatically resolved before uploading as release artifact.

Environment Variables Passed to component_descriptor
====================================================

+-----------------------------+----------------------------------------------------------+
| name                        | explanation                                              |
+=============================+==========================================================+
| BASE_DEFINITION_PATH        | absolute file path to base component descriptor          |
+-----------------------------+----------------------------------------------------------+
| COMPONENT_DESCRIPTOR_PATH   | absolute file path to output final descriptor to         |
+-----------------------------+----------------------------------------------------------+
| ADD_DEPENDENCIES_CMD        | CLI cmd args to add dependency to base descriptor (see   |
|                             | `cc-utils/cli.py productutils add_dependencies -h`)      |
+-----------------------------+----------------------------------------------------------+
| COMPONENT_NAME              | own component name (e.g. github.com/gardener/vpn)        |
+-----------------------------+----------------------------------------------------------+
| COMPONENT_VERSION           | the effective version                                    |
+-----------------------------+----------------------------------------------------------+


Example
=======

How to declare dependencies towards:

* component `github.com/gardener/vpn` in version 1.2.3
* component `github.com/gardener/dashboard` in version 4.5.6
* container image `alpine:3.6`

.. code-block:: sh

  # inside .ci/component_descriptor, assuming it is a shell script
  ${ADD_DEPENDENCIES_CMD} \
      --component-dependencies \
      '{"name": "github.com/gardener/vpn", "version": "1.2.3"}' \
      --component-dependencies \
      '{"name": "github.com/gardener/dashboard", "version": "4.5.6"}' \
      --container-image-dependencies \
      '{"image_reference": "alpine:3.6", "version": "3.6", "name": "alpine"}'
  # don't forget to expose the image
  cp "${BASE_DEFINITION_PATH}" "${COMPONENT_DESCRIPTOR_PATH}"


Retention Policies (aka cleaning up old versions)
=================================================

The `retention_policies` attribute can be used to configure automated removal of
component descriptors and referenced `resources` (mostly OCI Container Images).

.. attention::
   Removal of component descriptors and referenced resources is _permanent_. There is no
   backup mechanism in place. Use with care. For example, if multiple component descriptors
   share reference to the same OCI Artefact (using the same registry, repository, and tag)
   removal of any of the referencing component descriptors will lead to stale references
   in other component descriptors.

Cleanup Semantics and Use-Case
------------------------------

If frequently publishing component-descriptors as snapshot-versions (e.g. for each head-update,
or for pull-request-validation), thus-produced build artefacts and component descriptors
typically are only relevant for a short period of time. In such cases, automated cleanup
of snapshot-versions can be configured (see attribute-documentation above).

It is possible to further narrow-down versions to cleanup, by setting the `restrict`-attribute
to `same-minor`. If thus-configured, cleanup will only be done among component descriptors that
share the same minor version w/ the current component version.

Policy rules are evaluated in the order they are defined. When cleanup is run, all existing
versions (in current component descriptor repository) are retrieved, and grouped by defined
cleanup rules (each version is added exactly to the first matching rule; if no rule matches,
versions are dropped (thus exempted from cleanup)).

Each thus-collected group of versions is ordered, acccording to "relaxed" semver-arithmetics,
from smallest to greatest. Depending on the amount of versions to "keep" (`keep` attribute),
starting from smallest, progressing to greatest, versions to be removed are determined. It is
possible that no version is identified as being subject for cleanup.

For each version to be removed the component-descriptor to be removed is fetched and processed:

Sources are ignored.

From declared resources, all resources that are supported for removal are removed.

A resource is considered to be supported for removal if it has been declared of `relation: local`
(i.e. it was built along w/ the component-descriptor), and if its access-type is supported
by underlying CICD Infrastructure. This is currently limited to OCI Artefacts (including
"multi-arch" Images), and subject to being extended over time. Blobs that are inlined within
component descriptor OCI Artefact will be implicitly along with the component descriptor.

Once all supported resources have been removed, the declaring component descriptor is removed.

For performance reasons, cleanup may be limited to an internally defined amount of versions.
