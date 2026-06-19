#!/usr/bin/env bash
# Tests .github/actions/merge-ocm-fragments/extract.sh directly.
# Pre-fills the filesystem layout that download-artifact produces and verifies extraction.
set -euo pipefail

repo_root="$(readlink -f "$(dirname "$0")/../..")"
extract="${repo_root}/.github/actions/merge-ocm-fragments/extract.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }

# Creates a realistic .ocm-artefacts tarball (matching export-ocm-fragments output format).
# Writes the tarball to dest_dir; echoes the fragment filename (the file the tarball contains).
make_tarball() {
    local dest_dir="$1"
    local fragment_digest='aabbccdd1234567890aabbccdd1234567890abcd'
    local tarball_digest='1234567890aabbccdd1234567890aabbccddee00'
    local fragment_file="${fragment_digest}.ocm-artefacts"

    local tmpdir; tmpdir=$(mktemp -d)
    printf 'resources: [{name: test-resource, version: 1.0.0}]\n' \
        > "${tmpdir}/${fragment_file}"
    tar czf "${dest_dir}/${tarball_digest}.ocm-artefacts.tar.gz" \
        -C "${tmpdir}" "${fragment_file}"
    rm -rf "${tmpdir}"
    echo "${fragment_file}"
}

echo '==> v6 layout: tarball flat in outdir'
outdir=$(mktemp -d)
fragment=$(make_tarball "${outdir}")
"${extract}" "${outdir}"
[ -f "${outdir}/${fragment}" ] || fail "expected ${fragment} in outdir"
[ -z "$(find "${outdir}" -name '*.tar.gz')" ] || fail 'leftover tarball'
echo '    PASS'
rm -rf "${outdir}"

echo '==> v3 layout: tarball in named subdir (subdir name == fragment file name)'
outdir=$(mktemp -d)
fragment=$(make_tarball /tmp)
# download-artifact@v3 creates a subdir named after the artifact;
# artifact name == fragment file name (e.g. aabbccdd...ocm-artefacts)
subdir="${outdir}/${fragment}"
mkdir "${subdir}"
mv /tmp/*.ocm-artefacts.tar.gz "${subdir}/"
"${extract}" "${outdir}"
[ -f "${outdir}/${fragment}" ] || fail "expected ${fragment} in outdir"
[ ! -d "${outdir}/${fragment}" ] || fail "${fragment} is a dir, not a file"
[ -z "$(find "${outdir}" -name '*.tar.gz')" ] || fail 'leftover tarball'
echo '    PASS'
rm -rf "${outdir}"

echo '==> v3 layout: multiple fragments'
outdir=$(mktemp -d)
n=3
for i in $(seq 1 "${n}"); do
    frag_digest="$(printf '%040d' "${i}")"
    tar_digest="$(printf 'tar%037d' "${i}")"
    tmpdir=$(mktemp -d)
    printf 'resources: [{name: resource-%s, version: 1.0.0}]\n' "${i}" \
        > "${tmpdir}/${frag_digest}.ocm-artefacts"
    tar czf "/tmp/${tar_digest}.ocm-artefacts.tar.gz" \
        -C "${tmpdir}" "${frag_digest}.ocm-artefacts"
    rm -rf "${tmpdir}"
    mkdir "${outdir}/${frag_digest}.ocm-artefacts"
    mv "/tmp/${tar_digest}.ocm-artefacts.tar.gz" \
        "${outdir}/${frag_digest}.ocm-artefacts/"
done
"${extract}" "${outdir}"
found=$(find "${outdir}" -maxdepth 1 -name '*.ocm-artefacts' -type f | wc -l)
[ "${found}" -eq "${n}" ] || fail "expected ${n} fragment files, found ${found}"
[ -z "$(find "${outdir}" -name '*.tar.gz')" ] || fail 'leftover tarballs'
echo '    PASS'
rm -rf "${outdir}"

echo ''
echo 'all tests passed'
