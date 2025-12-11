#!/usr/bin/env bash

# a (slightly hacky) script to be used as a workaround for allowing to checkout submodules
# referenced with an SSH-URL with GitHub-Action's checkout-Action (using https / token-auth).
#
# Will only work for submodules on same GitHub-Instance. Only needed on GHE (checkout-action
# does have special-handling for github.com in place - or so they claim).
#
# This script is intended to be run after actions/checkout (which should be run w/o telling it
# to checkout submodules). This script assumes checkout's token is still present as
# http.<github-api>.extraHeader, and furthermore assumes this token is valid to retrieve each
# submodule (i.e.: no support for cross-github-submodules, and (obviously) no support for
# submodules not hosted in github).

set -euo pipefail

own_dir="$(dirname "${BASH_SOURCE[0]}")"

git --version
git submodule init
github_host=$(echo ${GITHUB_SERVER_URL:-https://github.com} | cut -d/ -f3)
audience="github-oidc-federation"


function identity_token() {
  token_request_url="${ACTIONS_ID_TOKEN_REQUEST_URL:-}"
  token_request_token="${ACTIONS_ID_TOKEN_REQUEST_TOKEN:-}"

  if [ -z "${token_request_url}" ]; then
    echo "ERROR: ACTIONS_ID_TOKEN_REQUEST_URL was not passed."
    echo 'That typically means this workflow was not run with `id-token: write`-permission'
    exit 1
  fi

  if [ -z "${token_request_token}" ]; then
    echo "ERROR: ACTIONS_ID_TOKEN_REQUEST_TOKEN was not passed."
    echo 'That typically means this workflow was not run with `id-token: write`-permission'
    exit 1
  fi

  out_path="/tmp/resp.json"

  resp=$(curl -sLS \
    -H "Authorization: Bearer ${token_request_token}" \
    -w "%{http_code}" \
    -o "${out_path}" \
    "${token_request_url}&audience=${audience}"
  )

  if [[ "${resp}" -lt 200 || "${resp}" -ge 300 ]]; then
    echo "ERROR: Failed to retrieve a GitHub identity-token" > /dev/stderr
    cat "${out_path}" > /dev/stderr
    return 1
  fi

  id_token=$(cat "${out_path}" | jq -r .value)

  if [ -z "${id_token}" ]; then
    echo "ERROR: Failed to retrieve a GitHub identity-token" > /dev/stderr
    return 1
  fi

  echo $id_token
}

function fed_token() {
  host=$1
  org=$2
  repo=$3
  id_token=$4

  out_path="/tmp/resp.json"

  payload="{
    \"host\": \"${host}\",
    \"organization\": \"${org}\",
    \"repositories\": [\"${repo}\"],
    \"permissions\": {\"contents\": \"read\"},
    \"token\": \"${id_token}\"
  }"

  resp=$(curl -sLS \
    -H "Content-Type: application/json" \
    -w "%{http_code}" \
    -o "${out_path}" \
    -d "${payload}" \
    "${TOKEN_SERVER}/token-exchange"
  )

  if [[ "${resp}" -lt 200 || "${resp}" -ge 300 ]]; then
    echo "ERROR: Failed to retrieve GitHub token" > /dev/stderr
    cat "${out_path}" > /dev/stderr
    return 1
  fi

  token=$(cat "${out_path}" | jq -r .token)

  if [ -z "${token}" ]; then
    echo "ERROR: Failed to retrieve GitHub token" > /dev/stderr
    return 1
  fi

  echo $token
}

function app_token() {
  org=$1

  "$own_dir/create_app_token.py" \
    --client-id "$APP_ID" \
    --private-key "$APP_KEY" \
    --github-org $org
}

git submodule status | while read -r s; do
  path=$(echo $s | cut -d' ' -f2-)
  cdig=$(echo $s | cut -d' ' -f1 | cut -d- -f2)

  echo "path: $path, digest: $cdig"

  echo "git-cmd to run: git config get submodule.$path.url"
  sm_url=$(git config submodule.$path.url)
  if [[ $sm_url == http* ]]; then
    echo "submodule at $path not configured to use SSH"
    continue
  fi
  echo "submodule at $path configured to use SSH - will reconfigure to https"

  if [[ -z "${TOKEN_SERVER:-}" && ( -z "${APP_ID:-}" || -z "${APP_KEY:-}" ) ]]; then
    echo "Neither a fed-server (inputs.token-server) nor a GitHub App (inputs.auth-app-private-key"
    echo "and inputs.auth-app-client-id) is specified, will try to fetch submodule anonymously"
    git submodule update $path
    continue
  fi

  # XXX: assume submodules-URL is of SSH-Type. Also assume submodule resides on same GH-Instance
  # strip prefixes (allow either of long/short form (ssh://git@ or just git@)
  sm_repo=${sm_url#ssh://}
  # strip git@-prefix
  sm_repo=${sm_repo#git@}
  # strip hostname (we do not know where we have long or short form, yet)
  sm_repo=${sm_repo#$github_host}
  # strip either / or : (depends on ssh-url-type)
  sm_repo=${sm_repo#:}
  sm_repo=${sm_repo#/}

  # git does not seem to honour http.<url>.extraheaders, so we have to jump through some (more)
  # hoops
  org=$(echo $sm_repo | cut -d/ -f1)
  repo=$(echo $sm_repo | cut -d/ -f2)

  # strip ".git" suffix from repository
  repo=${repo%.git}

  if [ -n "${TOKEN_SERVER:-}" ]; then
    id_token=$(identity_token)
    sleep 1s # ensure the token's iat is not in the future
    token=$(fed_token $github_host $org $repo $id_token)
  else
    # we checked both `APP_ID` and `APP_KEY` are set
    token=$(app_token $org)
  fi

  auth=$(echo -n x-access-token:${token} | base64)
  (
    unset GIT_DIR
    unset GIT_WORK_TREE
    cd $path
    git init
    git config http.https://$github_host/$sm_repo.extraHeader "AUTHORIZATION: basic $auth"
    git remote add origin https://$github_host/$sm_repo
    echo "will try to fetch submodule $sm_repo into $path"
    git fetch origin $cdig
    echo "fetched successfully"
    git checkout $cdig
  )

done

echo "done with initialisation of submodules"
