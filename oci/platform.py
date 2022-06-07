import dataclasses
import typing

import dacite

import oci.client as oc
import oci.model as om


def from_single_image(
    image_reference: typing.Union[str, om.OciImageReference],
    oci_client: oc.Client=None,
    base_platform: om.OciPlatform=None,
) -> om.OciPlatform:
    '''
    determines the platform from a "single oci image" (i.e. an oci image which is _not_
    a multiarch image). the image_reference must have a digest-tag
    '''
    image_reference = om.OciImageReference.to_image_ref(image_reference)
    if not image_reference.has_digest_tag:
        raise ValueError(image_reference)

    manifest = oci_client.manifest(image_reference=image_reference)

    if base_platform:
        cfg = base_platform.as_dict()
    else:
        cfg = {}

    cfg |= oci_client.blob(
        image_reference=image_reference,
        digest=manifest.config.digest,
        stream=False, # we will need to json.load the (small) result anyhow
    ).json()

    return dacite.from_dict(
        data_class=om.OciPlatform,
        data=cfg,
    )


def iter_platforms(
    image_reference: typing.Union[str, om.OciImageReference],
    oci_client: oc.Client=None,
) -> typing.Generator[tuple[om.OciImageReference, om.OciPlatform], None, None]:
    image_reference = om.OciImageReference.to_image_ref(image_reference)

    manifest = oci_client.manifest(
        image_reference=image_reference,
        accept=om.MimeTypes.prefer_multiarch,
    )

    if isinstance(manifest, om.OciImageManifest):
        platform = from_single_image(
            image_reference=image_reference,
            oci_client=oci_client,
        )
        yield (image_reference, platform)
        return
    elif isinstance(manifest, om.OciImageManifestList):
        manifest: om.OciImageManifestList
    else:
        raise NotImplementedError(type(manifest))

    prefix = image_reference.ref_without_tag

    for sub_manifest in manifest.manifests:
        platform_dict = dataclasses.asdict(sub_manifest)

        sub_manifest = oci_client.manifest(
            image_reference=(sub_img_ref := f'{prefix}@{sub_manifest.digest}'),
        )
        platform = from_single_image(
            image_reference=sub_img_ref,
            oci_client=oci_client,
        )

        # merge platform-dicts - the one from cfg-blob is assumed to be more specific
        platform_dict |= dataclasses.asdict(platform)

        platform = dacite.from_dict(
            data_class=om.OciPlatform,
            data=platform_dict,
        )

        yield (sub_img_ref, platform)


def platform_manifest(
    image_reference: om.OciImageReference | str,
    oci_client: oc.Client,
    platform: om.OciPlatform=None,
):
    image_reference = om.OciImageReference.to_image_ref(image_reference)
