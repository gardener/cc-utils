# CICD, Delivery, Compliance and Security Automation for Gardener
[![REUSE status](https://api.reuse.software/badge/github.com/gardener/cc-utils)](https://api.reuse.software/info/github.com/gardener/cc-utils)

![build](https://github.com/gardener/cc-utils/actions/workflows/build-and-test.yaml/badge.svg)

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

`pip install gardener-cicd-libs`


## Runtime environment requirements

### Python Runtime

`Python 3.12` or greater is required as a runtime

As a general rule, contained sources are always qualified using the python3-version from
[alpine](https://endoflife.date/alpine)'s greatest release version.
