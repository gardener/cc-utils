#!/usr/bin/env bash
# Extracts *.ocm-artefacts.tar.gz files from the given directory (default: $PWD).
# Handles both artifact layouts produced by actions/download-artifact:
#   v3 (GHE): each tarball sits in a named subdir matching the artifact name
#   v6+ (github.com): tarballs are placed directly in outdir
set -eu

merge_into_outdir() {
    # move each item from $1 into cwd, merging directories rather than replacing them
    local src="$1"
    find "${src}" -maxdepth 1 -mindepth 1 | while IFS= read -r item; do
        local name
        name=$(basename "${item}")
        if [ -d "${item}" ] && [ -d "./${name}" ]; then
            # merge: move contents of the sub-directory, then remove the now-empty source dir
            find "${item}" -maxdepth 1 -mindepth 1 -exec mv -t "./${name}" {} +
            rmdir "${item}"
        else
            mv "${item}" .
        fi
    done
}

cd "${1:-$PWD}"

echo 'extracting ocm-fragment archives'
for tf in $(find . -name '*.ocm-artefacts.tar.gz'); do
    echo "extracting ${tf}"
    subdir=$(dirname "${tf}")
    if [ "${subdir}" = '.' ]; then
        tmpdir=$(mktemp -d -p .)
        tar xf "${tf}" -C "${tmpdir}"
        unlink "${tf}"
        merge_into_outdir "${tmpdir}"
        rmdir "${tmpdir}"
    else
        # artifact landed in a named subdir (download-artifact@v3 on GHE). the tarball
        # contains a file with the same name as the subdir, so we cannot mv it to outdir
        # while the subdir exists. extract to tmpdir, remove (now empty) subdir, mv to outdir.
        tmpdir=$(mktemp -d -p .)
        tar xf "${tf}" -C "${tmpdir}"
        unlink "${tf}"
        rmdir "${subdir}"
        merge_into_outdir "${tmpdir}"
        rmdir "${tmpdir}"
    fi
done
