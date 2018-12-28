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
