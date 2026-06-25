# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0
'''
Minimal S3 download helper for public buckets (no auth required).

Only uses stdlib — no boto3/botocore dependency.  Virtual-hosted-style URLs are used when a
region is known; path-style (us-east-1 endpoint) is used as fallback and lets AWS redirect.
'''
import urllib.error
import urllib.request


def s3_url(bucket: str, key: str, region: str | None = None) -> str:
    '''Return the HTTPS URL for a public S3 object.'''
    if region:
        return f'https://{bucket}.s3.{region}.amazonaws.com/{key}'
    return f'https://{bucket}.s3.amazonaws.com/{key}'


def iter_s3_object(
    bucket: str,
    key: str,
    region: str | None = None,
    chunk_size: int = 65536,
):
    '''
    Stream a public S3 object, yielding chunks of bytes.

    Follows redirects (e.g. region-correction from us-east-1 endpoint).
    Raises urllib.error.HTTPError on non-200 responses.
    '''
    url = s3_url(bucket, key, region)
    with urllib.request.urlopen(url) as resp:  # nosec B310
        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            yield chunk


def mangle_s3_path(s: str) -> str:
    '''
    Mangle an S3 bucket name or object key into a string safe for use in an OCI repository path.

    Replaces characters not in [a-z0-9._-] with underscores; collapses consecutive underscores.
    '''
    import re
    return re.sub(r'_+', '_', re.sub(r'[^a-z0-9._-]', '_', s.lower())).strip('_')


def synthetic_oci_ref(
    registry_base: str,
    bucket: str,
    key: str,
    content_digest: str,
) -> str:
    '''
    Build a stable synthetic OCI reference for an S3 object.

    Convention:
      <registry_base>/sbom-s3/<mangled-bucket>/<mangled-key>@<content_digest>

    The digest component makes the reference content-addressable, enabling cache lookups via
    sbom.inject.lookup_sbom_referrers without re-downloading.
    '''
    mangled_bucket = mangle_s3_path(bucket)
    # key may have directory components — replace slashes separately to keep structure
    mangled_key = '/'.join(mangle_s3_path(p) for p in key.split('/') if p)
    registry_base = registry_base.rstrip('/')
    return f'{registry_base}/sbom-s3/{mangled_bucket}/{mangled_key}@{content_digest}'
