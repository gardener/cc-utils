# Continuous Integration utils for 'gardener' project

## What is it

`ci-utils` is a collection of re-usable utils intended to be used in the
context of Continuous Integration and output qualification of components
relevant for the 'gardener' project.

## How to use it

Python3 is required as a runtime (see requirements.txt for additional
runtime dependencies). All functions are exposed via a command line interface
(`./cli.py`).

## Modules

* `cli.py`: CLI generator
* `cli/*.py`: all defined functions are exposed via cli.py <module_name> <function_name>
* `concourse/*`: concourse utils / REST API client
* `ctx.py`: used internally to pass arguments from CLI to modules
* `util.py`: internal reuse functions shared by most modules