# Continuous Integration utils for 'gardener' project

## What is it

`cc-utils` is a collection of re-usable utils intended to be used in the
context of Continuous Integration and output qualification of components
relevant for the [gardener](https://github.com/gardener) project.

[End-User Documentation](https://gardener.github.io/cc-utils)

## How to contribute

Be sure to run tests, linter and codestyle checks:

- `.ci/pycodestyle`
- `.ci/pyflakes-lint`
- `.ci/test`

Run `.ci/install_git_hooks` to register recommended git hooks.

## How to use it

### Install using pip

`pip install gardener-cicd-libs` - install libraries (no CLI)
`pip install gardener-cicd-cli` - install CLI

### Consume from Container Image

A copy of cc-utils is contained in the default container image in which gardener
CI/CD jobs are run (`eu.gcr.io/gardener-project/cc/job-image`):

- `gardener-ci` is available from PATH
- all modules are available from PYTHONPATH

## Runtime environment requirements

### Python Runtime

`Python 3.7` or greater is required as a runtime (see requirements.txt for additional
runtime dependencies). Earlier Python versions (3.6, 2.x) are *not* supported.

In addition to the Python API, some functions are exposed via a command line interface
(`./cli.py`).

## Special Modules

* `cli.py`: CLI executable
* `cli/*.py`: all defined functions are exposed via cli.py <module_name> <function_name>
