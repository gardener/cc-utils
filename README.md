# CICD, Delivery, Compliance and Security Automation for Gardener
[![REUSE status](https://api.reuse.software/badge/github.com/gardener/cc-utils)](https://api.reuse.software/info/github.com/gardener/cc-utils)


![tests](https://concourse.ci.gardener.cloud/api/v1/teams/cicd/pipelines/cc-utils-master/jobs/master-head-update-job/badge?title=tests)
![release](https://concourse.ci.gardener.cloud/api/v1/teams/cicd/pipelines/cc-utils-master/jobs/master-release_job_image-job/badge?title=build)

![libs](https://badge.fury.io/py/gardener-cicd-libs.svg)

[![security: bandit](https://img.shields.io/badge/security-bandit-yellow.svg)](https://github.com/PyCQA/bandit)

## What is it

`cc-utils` is a collection of re-usable utils intended to be used in the
context of Continuous Integration and output qualification of components
relevant for the [gardener](https://github.com/gardener) project.

[End-User Documentation](https://gardener.github.io/cc-utils)

## How to contribute

Be sure to run tests, linter and codestyle checks:

- `.ci/lint`
- `.ci/test`

Run `.ci/install_git_hooks` to register recommended git hooks.

## How to use it

### Install using pip

`pip install gardener-cicd-libs` - install libraries (no CLI)

`pip install gardener-cicd-cli` - install CLI


### Consume from Container Image

A copy of cc-utils is contained in the default container image in which gardener
CI/CD jobs are run (`europe-docker.pkg.dev/gardener-project/releases/cicd/job-image`):

- `gardener-ci` is available from PATH

## Runtime environment requirements

### Python Runtime

`Python 3.11` or greater is required as a runtime (see requirements.txt for additional
runtime dependencies). Earlier Python versions.

As a general rule, contained sources are always qualified using the python3-version from
[alpine](https://endoflife.date/alpine)'s greatest release version.

In addition to the Python API, some functions are exposed via a command line interface
(`./cli.py`).

## Special Modules

* `cli/gardener_ci/*.py`: all defined functions are exposed via
gardener-ci <module_name> <function_name>
