#!/usr/bin/env sh

set -eu

outfile=$1
version=$2

cat <<EOF > ${outfile}
name: upload-artifact
description: |
  a wrapper for actions/upload-artifact.

  depending on whether run on github.com, or a GHE-instance, it will dispatch to either v4 or v3
  (v4 is only available on github.com)

inputs:
  name:
    type: string
    required: true
  path:
    type: string
    required: false

runs:
  using: composite
  steps:
    - uses: actions/upload-artifact@${version}
      with:
        name: \${{ inputs.name }}
        path: \${{ inputs.path }}
EOF

echo "wrote to $outfile"
