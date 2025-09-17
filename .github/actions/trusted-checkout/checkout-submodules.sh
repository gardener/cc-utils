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

if [ -z "${APP_ID:-}" -a -n "${APP_KEY:-}" ]; then
  echo "Error: APP_ID and APP_KEY must both be set or not set"
  exit 1
elif [ -n "${APP_ID:-}" -a -z "${APP_KEY:-}" ]; then
  echo "Error: APP_ID and APP_KEY must both be set or not set"
  exit 1
elif [ -n "${APP_ID:-}" ]; then
  # we checked both are (un)set above
  have_app=true
else
  have_app=false
fi

function token() {
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

  if ! $have_app; then
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
  auth=$(echo -n x-access-token:$(token $org) | base64)
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
