# Continuous Integration utils for 'gardener' project

## What is it

`ci-utils` is a collection of re-usable utils intended to be used in the
context of Continuous Integration and output qualification of components
relevant for the 'gardener' project.

## How to use it

Python3 is required as a runtime (see requirements.txt for additional
runtime dependencies). All functions are exposed via a command line interface
(cli.py).

## Modules

* `cli.py`: exposes all other modules' functions via a CLI
* `concourseutil.py`: concourse utils exposed via CLI
* `concourse/*`: concourse utils / REST API client
* `ctx.py`: used internally to pass arguments from CLI to modules
* `gcloud.py`: utils to interact with Google Cloud
* `github.py`: wrapper for GitHub API (webhook handling)
* `kubeutil.py`: utils for kubernetes API calls (for integration-tests)
* `util.py`: internal reuse functions shared by most modules
