**************
Pipeline Steps
**************

Pipeline steps define the actual payloads that are run during job executions. Each pipeline
step is run in a defined container image. Components may specify an arbitrary graph of steps.
Some traits add steps to build jobs as well (see traits documentation).

Each build step *must* be given a unique name (per job). By default, an executable named
`.ci/<step_name>` is expected in the main repository's work tree. It is run inside a container
with a specifiable container image with a defined set of environment variables.

The resulting exit code is used to determine whether or not the step execution has succeeded.
A zero exit code is interpreted as success, wheras non-zero exit codes are interpreted as
failures.

* all job steps are executed in parallel by default
* dependendencies between steps may be declared
* job steps may publish changes to repositories


.. model_element::
  :name: Pipeline Step
  :qualified_type_name: concourse.model.step.PipelineStep


Examples
########

.. code-block:: yaml

  steps:
    first_step: ~     # execute .ci/first_step

    custom_executable:
      execute:
        "my_script"   # execute .ci/my_script

    custom_image:
      image: 'alpine:3.6'

    executable_with_args:
      execute:
      - "another_executable"  # .ci/another_executable
      - "--with-an-option"
      - "args may contain whitespace characters"

    build_and_expose_output:
      output_dir: 'build_result_dir' # may be used e.g. by publish trait

    publish_commits:
      depends:
      - build_and_expose_output  # run only after build_and_expose_output finished
      publish_to:
      - source    # 'source' is the default name for the main repository

    force_push:
      publish_to:
        another_repo:
          force_push: true # use with caution!

    define_env_vars:
      vars:
        AN_ENV_VAR: '"my_important_value"'         # assign my_important_value to AN_ENV_VAR
        ANOTHER: 'pipeline_descriptor.get("name")' # assign pipeline name to ANOTHER
