'''
utils used for transitioning to new component-descriptor v2

see: https://github.com/gardener/component-spec
'''

import dataclasses
import enum
import hashlib
import io
import json
import os
import shutil
import tempfile
import typing
import yaml

import dacite

import gci.componentmodel as cm
import gci.oci
import oci.model as om

import ccc.oci
import ci.util
import container.registry
import product.model
import product.util
import version


CTF_OUT_DIR_NAME = 'cnudie-transport-format.out'


COMPONENT_TYPE_NAME = 'component'


def convert_to_v1(
    component_descriptor_v2: cm.ComponentDescriptor,
):
    component_v2 = component_descriptor_v2.component
    component_descriptor_v1 = product.model.ComponentDescriptor.from_dict(raw_dict={})

    component_v1 = _convert_component_to_v1(component_v2=component_v2)
    component_descriptor_v1.add_component(component_v1)

    component_deps = component_v1.dependencies()

    for component_ref in component_v2.componentReferences:
        component_deps.add_component_dependency(
            product.model.ComponentReference.create(
                name=component_ref.componentName,
                version=component_ref.version,
            )
        )
    # todo: also resolve component references (delegate for now)
    return component_descriptor_v1


def _convert_component_to_v1(
    component_v2: cm.Component,
):
    component_v1 = product.model.Component.create(
        name=component_v2.name,
        version=component_v2.version
    )
    component_deps = component_v1.dependencies()

    for resource in component_v2.resources:
        if hasattr(resource, 'relation'):
            if resource.relation is cm.ResourceRelation.LOCAL:
                v1_relation = product.model.Relation.LOCAL
            elif resource.relation is cm.ResourceRelation.EXTERNAL:
                v1_relation = product.model.Relation.THIRD_PARTY
            else:
                raise NotImplementedError
        else:
            v1_relation = product.model.Relation.LOCAL

        if resource.type is cm.ResourceType.OCI_IMAGE:
            component_deps.add_container_image_dependency(
                product.model.ContainerImage.create(
                    name=resource.name,
                    version=resource.version,
                    image_reference=resource.access.imageReference,
                    relation=v1_relation,
                )
            )
        elif resource.type is cm.ResourceType.GENERIC:
            component_deps.add_generic_dependency(
                product.model.GenericDependency.create(
                    name=resource.name,
                    version=resource.version,
                )
            )

    return component_v1


def _normalise_component_name(component_name:str) -> str:
    return component_name.lower()  # oci-spec allows only lowercase


def ensure_is_v2(
    component_descriptor_v2: cm.ComponentDescriptor,
):
    schema_version = component_descriptor_v2.meta.schemaVersion
    if not schema_version is cm.SchemaVersion.V2:
        raise RuntimeError(f'unsupported component-descriptor-version: {schema_version=}')


def _target_oci_repository_from_component_name(component_name: str, ctx_repo_base_url: str):
    component_name = _normalise_component_name(component_name)
    return ci.util.urljoin(
        ctx_repo_base_url,
        'component-descriptors',
        component_name,
    )


def _target_oci_ref(
    component: gci.componentmodel.Component,
    component_ref: gci.componentmodel.ComponentReference=None,
    component_version: str=None,
):

    if not component_ref:
        component_ref = component
        component_name = component_ref.name
    else:
        component_name = component_ref.componentName

    component_name = _normalise_component_name(component_name)
    component_version = component_ref.version

    # last ctx-repo is target-repository
    last_ctx_repo = component.repositoryContexts[-1]
    base_url = last_ctx_repo.baseUrl

    repository = _target_oci_repository_from_component_name(
        component_name=component_name,
        ctx_repo_base_url=base_url,
    )

    return f'{repository}:{component_version}'


def _target_oci_ref_from_ctx_base_url(
    component_name: str,
    component_version: str,
    ctx_repo_base_url: str,
):
    component_name = _normalise_component_name(component_name)

    return ci.util.urljoin(
        ctx_repo_base_url,
        'component-descriptors',
        f'{component_name}:{component_version}',
    )


def download_component_descriptor_v2(
    component_name: str,
    component_version: str,
    ctx_repo_base_url: str,
    absent_ok: bool=False,
    cache_dir: str=None,
    validation_mode: cm.ValidationMode=cm.ValidationMode.WARN,
):
    target_ref = _target_oci_ref_from_ctx_base_url(
        component_name=component_name,
        component_version=component_version,
        ctx_repo_base_url=ctx_repo_base_url,
    )

    if cache_dir:
        descriptor_path = os.path.join(
            cache_dir,
            ctx_repo_base_url.replace('/', '-'),
            f'{component_name}-{component_version}',
        )
        if os.path.isfile(descriptor_path):
            return cm.ComponentDescriptor.from_dict(
                ci.util.parse_yaml_file(descriptor_path),
                validation_mode=validation_mode,
            )
        else:
            base_dir = os.path.dirname(descriptor_path)
            os.makedirs(name=base_dir, exist_ok=True)

    component_descriptor =  retrieve_component_descriptor_from_oci_ref(
        manifest_oci_image_ref=target_ref,
        absent_ok=absent_ok,
    )

    if absent_ok and not component_descriptor:
        return None

    if cache_dir:
        try:
            f = tempfile.NamedTemporaryFile(mode='w', delete=False)
            # write to tempfile, followed by a mv to avoid collisions through concurrent
            # processes or threads (assuming mv is an atomic operation)
            yaml.dump(
                data=dataclasses.asdict(component_descriptor),
                Dumper=cm.EnumValueYamlDumper,
                stream=f.file,
            )
            shutil.move(f.name, descriptor_path)
        except:
            os.unlink(f.name)
            raise

    return component_descriptor


class UploadMode(enum.Enum):
    SKIP = 'skip'
    FAIL = 'fail'
    OVERWRITE = 'overwrite'


def write_component_descriptor_to_dir(
    component_descriptor: gci.componentmodel.ComponentDescriptor,
    cache_dir: str,
    on_exist=UploadMode.SKIP,
    ctx_repo_base_url: str=None, # if none, use current from component-descriptor
):
    if not os.path.isdir(cache_dir):
        raise ValueError(f'not a directory: {cache_dir=}')

    if not ctx_repo_base_url:
        ctx_repo_base_url = component_descriptor.component.current_repository_ctx().baseUrl

    component = component_descriptor.component
    descriptor_path = os.path.join(
        cache_dir,
        ctx_repo_base_url.replace('/', '-'),
        f'{component.name}-{component.version}',
    )

    if os.path.isfile(descriptor_path):
        if on_exist is UploadMode.SKIP:
            return component_descriptor,
        elif on_exist is UploadMode.FAIL:
            raise ValueError(f'already exists: {descriptor_path=}, but overwrite not allowed')
        elif on_exist is UploadMode.OVERWRITE:
            pass
        else:
            raise NotImplementedError(on_exist)

    if not os.path.isdir((pdir := os.path.dirname(descriptor_path))):
        os.makedirs(pdir, exist_ok=True)

    try:
        f = tempfile.NamedTemporaryFile(mode='w', delete=False)
        # write to tempfile, followed by a mv to avoid collisions through concurrent
        # processes or threads (assuming mv is an atomic operation)
        yaml.dump(
            data=dataclasses.asdict(component_descriptor),
            Dumper=cm.EnumValueYamlDumper,
            stream=f.file,
        )
        shutil.move(f.name, descriptor_path)
    except:
        os.unlink(f.name)
        raise


def upload_component_descriptor_v2_to_oci_registry(
    component_descriptor_v2: gci.componentmodel.ComponentDescriptor,
    on_exist=UploadMode.SKIP,
):
    ensure_is_v2(component_descriptor_v2)
    client = ccc.oci.oci_client()

    target_ref = _target_oci_ref(component_descriptor_v2.component)

    if on_exist in (UploadMode.SKIP, UploadMode.FAIL):
        # check whether manifest exists (head_manifest does not return None)
        if client.head_manifest(image_reference=target_ref, absent_ok=True):
            if on_exist is UploadMode.SKIP:
                return
            if on_exist is UploadMode.FAIL:
                # XXX: we might still ignore it, if the to-be-uploaded CD is equal to the existing
                # one
                raise ValueError(f'{target_ref=} already existed')
    elif on_exist is UploadMode.OVERWRITE:
        pass
    else:
        raise NotImplementedError(on_exist)

    raw_fobj = gci.oci.component_descriptor_to_tarfileobj(component_descriptor_v2)

    cd_digest = hashlib.sha256()
    while (chunk := raw_fobj.read(4096)):
        cd_digest.update(chunk)

    cd_octets = raw_fobj.tell()
    cd_digest = cd_digest.hexdigest()
    cd_digest_with_alg = f'sha256:{cd_digest}'
    raw_fobj.seek(0)

    client.put_blob(
        image_reference=target_ref,
        digest=cd_digest_with_alg,
        octets_count=cd_octets,
        data=raw_fobj,
        # mimetype=gci.oci.component_descriptor_mimetype,
    )

    cfg = gci.oci.ComponentDescriptorOciCfg(
        componentDescriptorLayer=gci.oci.ComponentDescriptorOciBlobRef(
            digest=cd_digest_with_alg,
            size=cd_octets,
        )
    )
    cfg_raw = json.dumps(dataclasses.asdict(cfg)).encode('utf-8')
    cfg_octets = len(cfg_raw)
    cfg_digest = hashlib.sha256(cfg_raw).hexdigest()
    cfg_digest_with_alg = f'sha256:{cfg_digest}'

    client.put_blob(
        image_reference=target_ref,
        digest=cfg_digest_with_alg,
        octets_count=cfg_octets,
        data=cfg_raw,
        # mimetype=container.registry.docker_http.OCI_CONFIG_JSON_MIME,
    )

    manifest = om.OciImageManifest(
        config=gci.oci.ComponentDescriptorOciCfgBlobRef(
            digest=f'sha256:{cfg_digest}',
            size=cfg_octets,
        ),
        layers=[
            gci.oci.ComponentDescriptorOciBlobRef(
                digest=cd_digest_with_alg,
                size=cd_octets,
            ),
        ],
    )

    manifest_dict = dataclasses.asdict(manifest)
    manifest_bytes = json.dumps(manifest_dict).encode('utf-8')

    client.put_manifest(
        image_reference=target_ref,
        manifest=manifest_bytes,
    )


def retrieve_component_descriptor_from_oci_ref(
    manifest_oci_image_ref: str,
    absent_ok=False,
    validation_mode: cm.ValidationMode=cm.ValidationMode.WARN,
):
    client = ccc.oci.oci_client()

    manifest = client.manifest(
        image_reference=manifest_oci_image_ref,
        absent_ok=absent_ok,
    )

    if not manifest and absent_ok:
        return None
    elif not manifest and not absent_ok:
        raise ValueError(f'did not find component-descriptor at {manifest_oci_image_ref=}')

    # XXX after "full" migration to v2, rm fallback coding below
    try:
        cfg_dict = json.loads(
            client.blob(
                image_reference=manifest_oci_image_ref,
                digest=manifest.config.digest,
            ).text
        )
        cfg = dacite.from_dict(
            data_class=gci.oci.ComponentDescriptorOciCfg,
            data=cfg_dict,
        )
        layer_digest = cfg.componentDescriptorLayer.digest
        layer_mimetype = cfg.componentDescriptorLayer.mediaType
    except Exception as e:
        print(f'Warning: failed to parse or retrieve component-descriptor-cfg: {e=}')
        print('falling back to single-layer')

        # by contract, there must be exactly one layer (tar w/ component-descriptor)
        if not (layers_count := len(manifest.layers) == 1):
            print(f'XXX unexpected amount of {layers_count=}')

        layer_digest = manifest.layers[0].digest
        layer_mimetype = manifest.layers[0].mediaType

    if not layer_mimetype == gci.oci.component_descriptor_mimetype:
        print(f'warning: {layer_mimetype=} was unexpected')
        # XXX: check for non-tar-variant

    blob_res = client.blob(
        image_reference=manifest_oci_image_ref,
        digest=layer_digest,
        stream=False, # manifests are typically small - do not bother w/ streaming
    )
    # wrap in fobj
    blob_fobj = io.BytesIO(blob_res.content)
    component_descriptor = gci.oci.component_descriptor_from_tarfileobj(
        fileobj=blob_fobj,
    )
    return component_descriptor


def resolve_dependency(
    component: gci.componentmodel.Component,
    component_ref: gci.componentmodel.ComponentReference,
    repository_ctx_base_url=None,
    cache_dir: str=None,
):
    '''
    resolves the given component version.

    - the component version is searched in the component's current ctx-repo
      if it is found, it is retrieved and returned
    '''
    if component_ref:
        cname = component_ref.componentName
        cversion = component_ref.version
    else:
        cname = component.name
        cversion = component.version

    return download_component_descriptor_v2(
        component_name=cname,
        component_version=cversion,
        ctx_repo_base_url=repository_ctx_base_url or component.current_repository_ctx().baseUrl,
        cache_dir=cache_dir,
        absent_ok=False,
        validation_mode=cm.ValidationMode.NONE,
    )


def resolve_dependencies(
    component: gci.componentmodel.Component,
    include_component=True,
    cache_dir=None,
):
  if include_component:
    yield component
  for component_ref in component.componentReferences:
    resolved_component_descriptor = resolve_dependency(
      component=component,
      component_ref=component_ref,
      cache_dir=cache_dir,
    )
    yield resolved_component_descriptor.component
    # XXX consider not resolving recursively, if immediate dependencies are present in ctx
    yield from resolve_dependencies(
        component=resolved_component_descriptor.component,
        include_component=False,
        cache_dir=cache_dir,
    )


def rm_component_descriptor(
    component: gci.componentmodel.Component,
    recursive=True,
):
    target_ref = _target_oci_ref(
        component=component,
        component_ref=component,
    )

    if recursive:
        for component_ref in component.componentReferences:
            component_descriptor = resolve_dependency(
                component,
                component_ref,
                repository_ctx_base_url=None,
            )
            rm_component_descriptor(
                component=component_descriptor.component,
                recursive=recursive,
            )

    container.registry.rm_tag(image_reference=target_ref)


def components(
    component_descriptor_v2: cm.ComponentDescriptor,
    cache_dir: str=None,
    _visited_component_versions: typing.Tuple[str, str]=(),
):
    component = component_descriptor_v2.component
    yield component

    new_visited_component_versions = _visited_component_versions + \
        (component.name, component.version) + \
        tuple((cref.componentName, cref.version) for cref in component.componentReferences)

    for component_ref in component.componentReferences:
        cref_version = (component_ref.componentName, component_ref.version)
        if cref_version in _visited_component_versions:
            continue

        component_descriptor_v2 = resolve_dependency(
            component=component,
            component_ref=component_ref,
            cache_dir=cache_dir,
        )
        yield from components(
            component_descriptor_v2=component_descriptor_v2,
            cache_dir=cache_dir,
            _visited_component_versions=new_visited_component_versions,
        )


class ResourceFilter(enum.Enum):
    LOCAL = 'local'
    EXTERNAL = 'external'
    ALL = 'all'


class ResourcePolicy(enum.Enum):
    IGNORE_NONMATCHING_ACCESS_TYPES = 'ignore_nonmatching_access_types'
    WARN_ON_NONMATCHING_ACCESS_TYPES = 'warn_on_nonmatching_access_types'
    FAIL_ON_NONMATCHING_ACCESS_TYPES = 'fail_on_nonmatching_access_types'


def resources(
    component: gci.componentmodel.Component,
    resource_access_types: typing.Iterable[gci.componentmodel.AccessType],
    resource_types: typing.Iterable[gci.componentmodel.ResourceType]=None,
    resource_filter: ResourceFilter=ResourceFilter.ALL,
    resource_policy: ResourcePolicy=ResourcePolicy.FAIL_ON_NONMATCHING_ACCESS_TYPES,
):
    if resource_filter is ResourceFilter.LOCAL:
        resources = [r for r in component.resources if r.relation is cm.ResourceRelation.LOCAL]
    elif resource_filter is ResourceFilter.EXTERNAL:
        resources = [r for r in component.resources if r.relation is cm.ResourceRelation.EXTERNAL]
    elif resource_filter is ResourceFilter.ALL:
        resources = component.resources
    else:
        raise NotImplementedError

    for resource in (r for r in resources if resource_types is None or r.type in resource_types):
        if resource.access is None and None in resource_access_types:
            yield resource
        elif resource.access and resource.access.type in resource_access_types:
            yield resource
        else:
            if resource_policy is ResourcePolicy.IGNORE_NONMATCHING_ACCESS_TYPES:
                continue
            elif resource_policy is ResourcePolicy.WARN_ON_NONMATCHING_ACCESS_TYPES:
                ci.util.warning(
                    f"Skipping resource with unhandled access type '{resource.access.type}'"
                )
                continue
            elif resource_policy is ResourcePolicy.FAIL_ON_NONMATCHING_ACCESS_TYPES:
                raise ValueError(resource)
            else:
                raise NotImplementedError


def enumerate_oci_resources(
    component_descriptor,
    cache_dir: str=None,
):
    for component in components(component_descriptor, cache_dir=cache_dir):
        for resource in resources(
            component=component,
            resource_types=[gci.componentmodel.ResourceType.OCI_IMAGE],
            resource_access_types=[gci.componentmodel.AccessType.OCI_REGISTRY],
        ):
            yield (component, resource)


def greatest_references(
    references: typing.Iterable[gci.componentmodel.ComponentReference],
) -> gci.componentmodel.ComponentReference:
    '''
    yields the component references from the specified iterable of ComponentReference that
    have the greates version (grouped by component name).
    Id est: if the sequence contains exactly one version of each contained component name,
    the sequence is returned unchanged.
    '''
    references = tuple(references)
    names = [r.name for r in references]

    for name in names:
        matching_refs = [r for r in references if r.name == name]
        if len(matching_refs) == 1:
            # in case reference name was unique, do not bother sorting
            # (this also works around issues from non-semver versions)
            yield matching_refs[0]
        else:
            # there might be multiple component versions of the same name
            # --> use the greatest version in that case
            matching_refs.sort(key=lambda r: version.parse_to_semver(r.version))
            # greates version comes last
            yield matching_refs[-1]


def greatest_component_version(component_name: str, ctx_repo_base_url: str) -> str:
    image_tags = component_versions(
        component_name=component_name,
        ctx_repo_base_url=ctx_repo_base_url,
    )
    return version.find_latest_version(image_tags)


def greatest_version_before(
    component_name: str,
    component_version: str,
    ctx_repo_base_url: str,
):
    versions = component_versions(
        component_name=component_name,
        ctx_repo_base_url=ctx_repo_base_url,
    )
    versions = sorted(versions, key=version.parse_to_semver)
    versions = [
        v for v in versions
        if version.parse_to_semver(v) < version.parse_to_semver(component_version)
    ]
    if len(versions) == 0:
        return None # no release before current was found
    return versions[-1]


# keep for backwards-compatibility for now
latest_component_version = greatest_component_version


def component_versions(component_name: str, ctx_repo_base_url: str) -> typing.Sequence[str]:
    oci_ref = _target_oci_repository_from_component_name(component_name, ctx_repo_base_url)
    client = ccc.oci.oci_client()
    return client.tags(image_reference=oci_ref)


def greatest_component_version_with_matching_minor(
    component_name: str,
    ctx_repo_base_url: str,
    reference_version: str,
) -> str:
    oci_image_repo = _target_oci_repository_from_component_name(component_name, ctx_repo_base_url)
    client = ccc.oci.oci_client()
    image_tags = client.tags(image_reference=oci_image_repo)
    return version.find_latest_version_with_matching_minor(
        reference_version=reference_version,
        versions=image_tags,
    )
