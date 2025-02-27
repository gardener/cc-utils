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


Local Development and "component-cli"
=====================================

Setting up a local development environment for running the `component_descriptor` step may be
cumbersome. Therefore, a convenience command is provided from `gardener-cicd-cli` python package
that evaluates both `.ci/pipeline_definitions` (+ optional `branch.cfg`), and calls a
`.ci/component_descriptor` callback script, thus creating a similar output as if running in
cicd-pipeline.

Setting up Preliminaries
------------------------

- install python3 as indicated by `gardener-cicd-cli`-package (3.10+)
- run `pip3 install gardener-cicd-cli` (python-headers + c-compiler-toolchain might be required)
- optional: install `component-cli` to PATH
- install other runtime-dependencies as needed by local `.ci/component_descriptor` callback script

If repository in question uses `branch.cfg` (in special ref `refs/meta/ci`), fetch it into local
repository by running: `git fetch origin refs/meta/ci:refs/meta/ci` (use different origin as needed).

.. note::
   pass `--meta-ci fetch` to tell the command (see below) to fetch refs/meta/ci for you

Rendering Component-Descriptor
------------------------------

Run the following command (available from PATH after installing `gardener-cicd-cli`) (chdir into
repository's working tree):

`gardener-ci pipeline component_descriptor`

.. note::
   The `pipeline component_descriptor` command tries to guess things like component-name, or the
   pipeline to use (preferring a pipeline-job that is likely the release-job).
   Pass the `-h` (or `--help`) flag to display online-help. Most heuristics can be overwritten.

.. note::
   Component-Descriptors creating this way will be close to those that will be created by
   CICD Pipeline Jobs, but not necessarily 100% accurate (for example, image-tag-templates are
   not evaluated, which may lead to different image-tags in "base-component-descriptors").


Special-handling for "charts/images.yaml" / deprecating component-cli
=====================================================================

`component-cli` has been deprecated as of 2023-04-06. `component-cli` was tailored as an
opinionated tool considering some special-cases useful for many of Gardener's repositories in
mind. It's successor - `OCM-CLI <https://github.com/open-component-model/ocm#ocm-cli>`_ might
replace `component-cli`, however it will not feature said gardener-specific special-case-handling.

To phase-out `component-cli`, with little efforts all relevant commands are
re-implemented as part of CICD-Pipeline-Template as a drop-in-replacement.
Implementation can be found
`here <https://github.com/gardener/cc-utils/blob/master/bin/component-cli>`_.

The default instrumentation of component-cli commands can be found
`here <https://github.com/gardener/gardener/blob/master/hack/.ci/component_descriptor>`_.

"imagevector add" command / charts/images.yaml contract
-------------------------------------------------------

Some Gardener-Repositories use a standardised format to declare images to be exposed to both
helm-charts and `Component-Descriptors` via a regular file located at `charts/images.yaml` below
repository root.

The (deprecated) `component-cli` features a command `imagevector add` that converts data from such
`images.yaml` files to component-descriptors.

`images.yaml` is expected to be a YAML document (or multi-document) containing (oci-)image-entries.
Those are stored as a list below an attribute `images`. Depending on the defined attributes,
entries are handled differently.

In addition to attributes being absent, or present, there is also a list of "component-prefixes",
which defaults to `eu.gcr.io/gardener-project/gardener`, which influences whether an entry is
considered to be "local" (built by component's pipeline) or "external" (built by someone else).

Gardener-Components have a name that is by convention the github-repo-url (w/o scheme). If the
`sourceRepository` is different from current component name, a component-reference is added.

*Example*

.. code-block:: yaml

   # current component: github.com/gardener/gardener
   # current version: 1.67.0
   # github-repo: github.com/gardener/gardener

   images:
   - name: gardenlet
     sourceRepository: github.com/gardener/gardener # same as current component in this example
     repository: eu.gcr.io/gardener-project/gardener/gardenlet

*Results in:*

.. code-block:: yaml

   resources:
   - name: gardenlet # from name-attribute
     relation: local # from repository's prefix matching eu.gcr.io/gardener-project/gardener
     type: ociImage # hard-coded
     version: 1.67.0 # from current version
     access:
      imageReference: eu.gcr.io/gardener-project/gardener/gardenlet:1.67.0 # <repo>:<version>
      type: ociRegistry # hard-coded
    labels:
    - name: imagevector.gardener.cloud/name
      value: gardenlet # from name-attribute
    - name: imagevector.gardener.cloud/repository
      value: eu.gcr.io/gardener-project/gardener/gardenlet # from repository-attribute
    - name: imagevector.gardener.cloud/source-repository
      value: github.com/gardener/gardener # github-repo

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
