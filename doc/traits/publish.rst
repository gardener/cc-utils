`publish` Trait
===============

.. trait::
    :name: publish


Used to build and publish container images in the declaring build jobs. An arbitrary amount of
container images may be specified (at least one).

The effective version is used as image tag. Optionally, created images may be tagged as
'latest'.

Each container image build is run in a directory with definable contents (see `inputs` attribute)
using the specified `Dockerfile`.

`inputs` attribute
------------------

By default, the main repository's work tree is copied into the build directory. This behaviour
may be changed by defining different logical repository names for `inputs.repos`.

To consume (build) results created by other build steps, those outputs are specified with the
`inputs.steps` attribute.

Build steps that are specified as inputs may declare the optional `output_dir` attribute. They
are expected to place their outputs into a directory indicated by an environment variable named
`<OUTPUT_DIR>_PATH` (defaults to `BINARY_PATH`).


Example
-------

.. code-block:: yaml

  steps:
    build:
      output_dir: 'build_result'  # 'build' must cp to ${BUILD_RESULT_PATH}
  traits:
    publish
      dockerimages:
        first_image: # logical image name
          image: 'eu.grc.io/gardener-project/example/image'
          dockerfile: 'Dockerfile'
          tag_as_latest: True
          inputs:
            repos:
              source: ~  # this is the default (--> use main repository)
            steps:
              build: ~   # copy results of step 'build' over source tree
        second_image:
          image: 'eu.gcr.io/gardener-project/example/second_image
          dockerfile: 'AnotherDockerfile'
