# Continuous Integration utils for 'gardener' project

## What is it

`cc-utils` is a collection of re-usable utils intended to be used in the
context of Continuous Integration and output qualification of components
relevant for the 'gardener' project.

## How to use it

A copy of cc-utils is contained in the default container image in which gardener
CI/CD jobs are run (`eu.gcr.io/gardener-project/cc/job-image`):

- `cli.py` is available from PATH
- all modules are available from PYTHONPATH

`Python 3.6` is required as a runtime (see requirements.txt for additional
runtime dependencies).

In addition to the Python API, some functions are exposed via a command line interface
(`./cli.py`).

## Special Modules

* `cli.py`: CLI generator
* `cli/*.py`: all defined functions are exposed via cli.py <module_name> <function_name>
