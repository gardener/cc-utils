import dataclasses
import enum
import typing

import dacite

import oci.client as oc
import oci.model as om


class OperatingSystem(enum.Enum):
    '''
    OperatingSystem contains the values for the 'os' property in an oci multiarch image.
    See https://go.dev/doc/install/source#environment.
    '''
    AIX = 'aix'
    ANDROID = 'android'
    DARWIN = 'darwin'
    DRAGONFLY = 'dragonfly'
    FREEBSD = 'freebsd'
    ILLUMOS = 'illumos'
    IOS = 'ios'
    JS = 'js'
    LINUX = 'linux'
    NETBSD = 'netbsd'
    OPENBSD = 'openbsd'
    PLAN9 = 'plan9'
    SOLARIS = 'solaris'
    WINDOWS = 'windows'

    @classmethod
    def contains_value(cls, value: str):
        return value in [v.value for v in OperatingSystem]


class Architecture(enum.Enum):
    '''
    Architecture contains the values for the 'architecture' property in an oci multiarch image.
    See https://go.dev/doc/install/source#environment.
    '''
    PPC64 = 'ppc64'
    _386 = '386'
    AMD64 = 'amd64'
    ARM = 'arm'
    ARM64 = 'arm64'
    WASM = 'wasm'
    LOONG64 = 'loong64'
    MIPS = 'mips'
    MIPSLE = 'mipsle'
    MIPS64 = 'mips64'
    MIPS64LE = 'mips64le'
    PPC64le = 'ppc64le'
    RISCV64 = 'riscv64'
    S390X = 's390x'

    @classmethod
    def contains_value(cls, value: str):
        return value in [v.value for v in Architecture]


def from_manifest(
    image_reference: om.OciImageReference,
    manifest: om.OciImageManifest,
    oci_client: oc.Client=None,
    base_platform: om.OciPlatform=None,
) -> om.OciPlatform:
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


def from_single_image(
    image_reference: typing.Union[str, om.OciImageReference],
    oci_client: oc.Client=None,
    base_platform: om.OciPlatform=None,
) -> om.OciPlatform:
    '''
    determines the platform from a "single oci image" (i.e. an oci image which is _not_
    a multiarch image).
    '''
    image_reference = om.OciImageReference.to_image_ref(image_reference)

    manifest = oci_client.manifest(image_reference=image_reference)

    if not isinstance(manifest, om.OciImageManifest):
        raise ValueError(f'{image_reference=} did not yield OciImageManifest: {type(manifest)=}')

    return from_manifest(
        image_reference=image_reference,
        manifest=manifest,
        oci_client=oci_client,
        base_platform=base_platform,
    )


def single_platform_manifest(
    image_reference: typing.Union[om.OciImageReference, str],
    oci_client: oc.Client,
    platform: om.OciPlatform=None,
):
    '''
    returns a single-platform OCI Image Manifest for the given image_reference.
    lookup and validation depend on presence of platform argument.

    if given image-ref points to a single-arch manifest, the returned result will be identical
    to invoking `oci_client.manifest`. If platform argument is passed, and the discovered
    platform does not match, a `ValueError` will be raised.

    if given image-ref points to a multi-arch manifest, content-negotiation depends on presence of
    platform-argument. If absent, no preference will be stated (i.e. accept-header will not be set).
    Some Oci-Image-registries will return a single-arch manifest (thus saving a roundtrip).
    If platform is passed, preference for multi-arch will be stated via accept-header; the specified
    platform will be looked-up and returned. If not found, `ValueError` will be raised.
    '''
    image_reference = om.OciImageReference.to_image_ref(image_reference)

    if platform:
        accept = om.MimeTypes.prefer_multiarch
    else:
        accept = None

    manifest = oci_client.manifest(
        image_reference=image_reference,
        accept=accept,
    )

    if isinstance(manifest, om.OciImageManifest):
        if not platform:
            return manifest

        actual_platform = from_manifest(
            image_reference=image_reference,
            manifest=manifest,
            oci_client=oci_client,
        )

        if not actual_platform == platform:
            raise ValueError(f'{image_reference=} does not match {platform=}: {actual_platform=}')

        return manifest
    elif isinstance(manifest, om.OciImageManifestList):
        pass
    else:
        raise NotImplementedError(manifest)

    for manifest in manifest.manifests:
        manifest: om.OciImageManifestListEntry
        if manifest.platform == platform:
            break
    else:
        raise ValueError(f'{image_reference=} does not contain {platform=}')

    manifest_ref = f'{image_reference.ref_without_tag}@{manifest.digest}'
    return oci_client.manifest(image_reference=manifest_ref)


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


class PlatformFilter:
    @staticmethod
    def create(
        included_platforms: typing.List[str],
    ) -> typing.Callable[[om.OciPlatform], bool]:
        matchers = []
        for included_platform in included_platforms:
            matchers.append(PlatformFilter._parse_expr(included_platform))

        def filter(platform_to_match: om.OciPlatform) -> bool:
            for m in matchers:
                if PlatformFilter._match(m, platform_to_match):
                    return True

            return False

        return filter

    @staticmethod
    def _parse_expr(platform_expr: str) -> dict:
        splitted = platform_expr.split('/')
        if len(splitted) < 2 or len(splitted) > 3:
            raise ValueError(f'invalid oci platform expression {platform_expr=}.'
                              ' expression must have the format os/architecture[/variant]')

        os = splitted[0]
        if os != '*' and not OperatingSystem.contains_value(os):
            raise ValueError(f'invalid os in oci platform expression {platform_expr=}.'
                             f' allowed values are {["*"] + [o.value for o in OperatingSystem]}')

        architecture = splitted[1]
        if architecture != '*' and not Architecture.contains_value(architecture):
            raise ValueError(f'invalid architecture in oci platform expression {platform_expr=}.'
                             f' allowed values are {["*"] + [a.value for a in Architecture]}')

        variant = '*'
        if len(splitted) == 3:
            variant = splitted[2]

        return {
            'os': os,
            'architecture': architecture,
            'variant': variant,
        }

    @staticmethod
    def _match(m: dict, p: om.OciPlatform) -> bool:
        normalised_p = p.normalise()
        return ((m['os'] == '*' or m['os'] == normalised_p.os) and
                (m['architecture'] == '*' or m['architecture'] == normalised_p.architecture) and
                (m['variant'] == '*' or m['variant'] == normalised_p.variant)
               )
