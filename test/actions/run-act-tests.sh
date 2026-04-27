#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
#
# Driver for act-based action wiring tests.
# Each test lives under test/actions/<action-name>/<test-name>/
# and must contain a workflow.yaml. A fixture/ subdirectory, if present,
# is initialised as a git repo and used as the working directory for act.
#
# Usage: test/actions/run-act-tests.sh [test-filter]
#   test-filter: optional substring to restrict which tests are run

set -euo pipefail

repo_root="$(readlink -f "$(dirname "$0")/../..")"
tests_dir="${repo_root}/test/actions"
filter="${1:-}"
failures=0
passed=0

# pre-build a bundle so act's install-gardener-gha-libs uses local source
# instead of checking out master from github.com at test time
bundle_dir="${repo_root}/.github/actions/install-gardener-gha-libs/_bundle"
echo "==> building gardener-gha-libs bundle from local source"
pip install --quiet setuptools
python3 "${repo_root}/.github/actions/bundle-gardener-gha-libs/bundle.py" \
    --repo-root "${repo_root}" \
    --bundle-dir "${bundle_dir}"
trap 'rm -rf "${bundle_dir}"' EXIT

run_test() {
    local test_dir="$1"
    local action_name fixture_dir workdir event_json

    action_name="$(basename "$(dirname "${test_dir}")")"
    local test_name
    test_name="$(basename "${test_dir}")"

    if [ -n "${filter}" ] && [[ "${action_name}/${test_name}" != *"${filter}"* ]]; then
        return 0
    fi

    echo ""
    echo "==> ${action_name}/${test_name}"

    fixture_dir="${test_dir}/fixture"
    if [ -d "${fixture_dir}" ]; then
        workdir="$(mktemp -d)"
        cp -r "${fixture_dir}/." "${workdir}/"
        # initialise as git repo if not already one
        if [ ! -d "${workdir}/.git" ]; then
            git -C "${workdir}" init -q
            git -C "${workdir}" config user.email 'act-test@example.com'
            git -C "${workdir}" config user.name 'act-test'
            git -C "${workdir}" add .
            git -C "${workdir}" commit -q -m 'fixture'
        fi
    else
        workdir="${repo_root}"
    fi

    event_json="${test_dir}/event.json"
    if [ ! -f "${event_json}" ]; then
        event_json="${repo_root}/test/actions/default-event.json"
    fi

    if act push \
        --workflows "${test_dir}/workflow.yaml" \
        --directory "${workdir}" \
        --eventpath "${event_json}" \
        --local-repository "gardener/cc-utils@master=${repo_root}" \
        --env "GITHUB_TOKEN=${GITHUB_TOKEN:-dummy}" \
        --env PIP_BREAK_SYSTEM_PACKAGES=1 \
        --no-cache-server \
        --pull=false \
        -P ubuntu-latest=ghcr.io/catthehacker/ubuntu:act-latest \
        2>&1; then
        echo "    PASS"
        ((passed+=1))
    else
        echo "    FAIL"
        ((failures+=1))
    fi

    if [ "${workdir}" != "${repo_root}" ]; then
        rm -rf "${workdir}"
    fi
}

# discover all tests (directories containing workflow.yaml under test/actions/*/*/)
while IFS= read -r -d '' workflow; do
    run_test "$(dirname "${workflow}")"
done < <(find "${tests_dir}" -mindepth 3 -maxdepth 3 -name 'workflow.yaml' -print0 | sort -z)

echo ""
echo "act tests: ${passed} passed, ${failures} failed"
[ "${failures}" -eq 0 ]
