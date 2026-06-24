# Cumulus — GCS file-based release-status locking

## Documentation

- File-upload release-status API:
  https://pages.github.tools.sap/Hyperspace-Documentation/cumulus/2--Getting-Started---Cumulus/2-6-Release-Status/Automatically-Set-Pipeline-Run-Release-Status-by-File-Upload/

## Registered pipelines

See `cumulus-pipelines.txt` for all onboarded pipelines with their IDs, pipeline-keys,
vault paths, and current status.

## GCS path layout

```
gs://<bucket>/<run-key>/sbom/...                                  ← SBOM files
gs://<bucket>/<run-key>/.status-log/release/release-status-<ts>.json  ← status control
```

- `<bucket>` = pipeline ID (UUID), registered with Cumulus
- `<run-key>` = typically the OCM component version (e.g. `1.2.3`)
- Cumulus watches `.status-log/release/` for JSON files; reads `releaseStatus` field

## Release status values and their effect

Empirically verified (cc-demo `sbom-release-status-test.yaml`):

| Status                   | `sbom/**` | root `<run-key>/` | `.status-log/` writable |
|--------------------------|:---------:|:-----------------:|:-----------------------:|
| `release-candidate`      | writable  | writable          | yes                     |
| `promoted`               | **locked**| writable          | yes                     |
| `deployed-to-production` | locked¹   | writable          | yes                     |
| `released`               | **locked**| **locked**        | yes                     |

¹ `deployed-to-production` itself adds no locks; `sbom/**` is locked by the preceding `promoted`.

`.status-log/` (where status files are written) remains writable in all states, including after
`released`. This is intentional — Cumulus must be able to receive further status updates.

Locks are applied asynchronously but quickly (typically ~30 s after the status file is written).

**GCS locks are permanent** — there is no GCS API to remove them once applied.
However, the Cumulus backend exposes a revert API that can undo the Cumulus-side lock
registration (used by the Sirius WUI):

```
POST /api/pipeline-run-release-status/:runId/releaseStatus/:status/revert
```

- `:runId` — Cumulus pipeline run identifier
- `:status` — the status to revert (e.g. `promoted`, `released`)
- Requires appropriate authentication (same token exchange as other Cumulus API calls)
- Exact request body, auth headers, and semantics are not yet fully documented;
  reverse-engineering from the Sirius WUI source is needed

**TODO**: investigate the revert endpoint — capture required auth headers, request body
(if any), and response semantics; then add a helper or action step for emergency unlocks.

## Critical ordering requirement

**`promoted` must be set before `released`.**

`promoted` is what registers the run-key with Cumulus. If you jump straight to `released`
(or use `release-candidate` first), Cumulus has no record of the run-key and never applies
any locks — writes to locked paths will continue to return HTTP 200 instead of 403.

Correct sequence:
```
promoted  →  released
```

Incorrect (no locks applied):
```
release-candidate  →  released   # ← run-key not registered; locks never applied
released                         # ← same problem
```

## Writing a status entry (raw curl)

```bash
ts=$(date -u +%s)
blob="${run_key}/.status-log/release/release-status-${ts}.json"
blob_enc="${blob//\//%2F}"
curl -s -X POST \
  "https://storage.googleapis.com/upload/storage/v1/b/${bucket}/o?uploadType=media&name=${blob_enc}" \
  -H "Authorization: Bearer ${gcs_token}" \
  -H 'Content-Type: application/json' \
  --data-binary '{"releaseStatus":"promoted"}'
```

## sbom-upload action integration

`.github/actions/sbom-upload` handles status writing automatically:
- `release-status` input controls which status is written after SBOM upload
- `on-already-released` (`skip`/`fail`) guards against re-runs on a terminal run-key
- The action checks existing status before uploading (incremental / idempotent)

## Testing

The `cc-demo` repo has a dedicated integration test workflow:
`.github/workflows/sbom-release-status-test.yaml`

Four parallel jobs, one per status value. Each uses a fresh timestamped run-key
(`0.0.<unix-ts>-<status>`). Tests:
- `release-candidate`: writes rc status, waits 5 min, asserts all paths remain writable
- `promoted`: writes promoted, polls until `sbom/` is locked (403), asserts root + status-log writable
- `deployed-to-production`: pre-writes `promoted` (to register the run-key), then writes dtp,
  waits 5 min, asserts root + status-log writable (sbom/ will be locked by promoted — expected)
- `released`: pre-writes `promoted`, then `released`, polls until root `<run-key>/` is locked,
  observes that `.status-log/` remains writable

Trigger manually via GitHub Actions → "SBOM Release Status Test" → "Run workflow".
