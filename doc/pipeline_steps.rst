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

.. model_element::
    :name: Pipeline Step
    :qualified_type_name: concourse.model.step.PipelineStep
