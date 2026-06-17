# SBOM Documents — Storage Convention

SBOM documents are generated for every OCI image resource and stored in two ways:
as **OCI referrer manifests** (primary, for direct OCI consumers) and as **OCM resources**
(for consumers working through component descriptors).

## OCI referrers

Each image gets two referrer manifests stored in the same OCI repository, linked to the
image by digest via the [OCI 1.1 referrers API][oci-referrers]:

| Format | `artifactType` |
|--------|----------------|
| SPDX 2.3 | `application/spdx+json` |
| CycloneDX 1.6 | `application/vnd.cyclonedx+json` |

Retrieve with [`oras`][oras]:

```sh
oras discover --artifact-type application/spdx+json <image-ref>
oras blob fetch <repo>@<referrer-digest> --output sbom.spdx.json
```

Or directly via the registry API:

```
GET /v2/<repo>/referrers/<digest>?artifactType=application%2Fspdx%2Bjson
```

The SBOM document is the single layer of the referrer manifest
(`manifest.layers[0].digest`).

The referrer manifest carries two annotations:
- `org.opencontainers.image.created` — scan timestamp (ISO 8601)
- `gardener.cloud/sbom/tool-version` — syft version used (if available)

## OCM resources

In OCM component descriptors the SBOM documents appear as additional resources alongside
the image resource they describe.  Both share the image resource's `name` and `version`;
the `extraIdentity` field distinguishes them:

| Field | Value |
|-------|-------|
| `sbom-format` | `spdx-2.3` or `cyclonedx-1.6` |
| `version` | same as the source image resource |
| platform fields | `os`, `architecture` (present when the source image is platform-specific) |

The resource `type` matches the SBOM media type above.

Labels on each SBOM resource:

| Label | Content |
|-------|---------|
| `gardener.cloud/sbom/source-image` | original image reference |
| `gardener.cloud/sbom/source-image-digest` | digest of the scanned image manifest |
| `gardener.cloud/sbom` | `{data-source: {tool, tool-version}, format}` |

Access is either `localBlob` (SBOM inlined into the component descriptor blob store) or
`ociRegistry` pointing at the referrer manifest digest.

[oci-referrers]: https://github.com/opencontainers/distribution-spec/blob/main/spec.md#listing-referrers
[oras]: https://oras.land/
