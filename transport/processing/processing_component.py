import dataclasses
import enum
import logging
import os
import sys
import typing
import yaml

import gci.componentmodel as cm
import oci.model as om
import processing.config as config

import ci.util
import product.v2

LOGGER = logging.getLogger(__name__)


class FileExtension(enum.Enum):
    COMPONENT_DESCRIPTOR = 'yaml'
    TAR = 'tar'


def parse_component_descriptor(path: str):
    with open(path) as desc_file:
        return cm.ComponentDescriptor.from_dict(yaml.safe_load(desc_file))


def _download_descriptor(
        name: str,
        version: str,
        ctx_base_url: str,
) -> cm.ComponentDescriptor:
    try:
        return product.v2.download_component_descriptor_v2(
            component_name=name,
            component_version=version,
            ctx_repo_base_url=ctx_base_url,
        )
    except om.OciImageNotFoundException as err_not_found:
        ci.util.error(err_not_found)
        sys.exit(1)


def _resolve_cd_from_references(component_descriptor: cm.ComponentDescriptor):
    ctx_base_url = component_descriptor.component.repositoryContexts[-1].baseUrl

    def enumerate_cd_from_references(
            component_descriptor: cm.ComponentDescriptor,
            ctx_base_url: str,
    ):
        for comp_ref in component_descriptor.component.componentReferences:
            oci_ref = product.v2._target_oci_ref_from_ctx_base_url(
                component_name=comp_ref.componentName,
                component_version=comp_ref.version,
                ctx_repo_base_url=ctx_base_url,
            )
            descriptor_path = ComponentTool.gen_yaml_file_path(oci_ref)
            if os.path.isfile(descriptor_path):
                yield parse_component_descriptor(descriptor_path)
            else:
                yield _download_descriptor(comp_ref.componentName, comp_ref.version, ctx_base_url)

    for cd_from_ref in enumerate_cd_from_references(component_descriptor, ctx_base_url):
        yield (cd_from_ref.component.name, cd_from_ref)
        # Recurse if a component descriptor has itself other references
        if cd_from_ref.component.componentReferences:
            yield from _resolve_cd_from_references(cd_from_ref)


@dataclasses.dataclass
class ComponentTool:
    name: str
    version: str
    ctx_base_url: str
    descriptor: typing.Optional[cm.ComponentDescriptor] = None
    oci_ref: str = dataclasses.field(init=False)

    def __post_init__(self):
        self.oci_ref = product.v2._target_oci_ref_from_ctx_base_url(
            component_name=self.name,
            component_version=self.version,
            ctx_repo_base_url=self.ctx_base_url,
        )

    @staticmethod
    def new_from_descriptor(descriptor: cm.ComponentDescriptor):
        return ComponentTool(
            name=descriptor.component.name,
            version=descriptor.component.version,
            ctx_base_url=descriptor.component.repositoryContexts[-1].baseUrl,
            descriptor=descriptor,
        )

    @staticmethod
    def new_from_source_descriptor(
            descriptor: cm.ComponentDescriptor,
            context_url: str,
            external_resources: list,
            local_resources: list,
    ):
        return ComponentTool.new_from_descriptor(
            descriptor=cm.ComponentDescriptor(
                meta=descriptor.meta,
                component=cm.Component(
                    name=descriptor.component.name,
                    version=descriptor.component.version,
                    repositoryContexts=[
                        cm.RepositoryContext(
                            baseUrl=context_url,
                            type=cm.AccessType.OCI_REGISTRY,
                        ),
                    ],
                    provider=descriptor.component.provider,
                    sources=descriptor.component.sources,
                    componentReferences=descriptor.component.componentReferences,
                    externalResources=external_resources,
                    localResources=local_resources,
                )
            )
        )

    @staticmethod
    def gen_yaml_file_path(oci_ref: str):
        return ci.util.urljoin(
            config.RESOURCES_DIR,
            ci.util.file_extension_join(
                oci_ref,
                FileExtension.COMPONENT_DESCRIPTOR.value,
            )
        )

    def retrieve_descriptor(self):
        if self.descriptor is None:
            self.descriptor = _download_descriptor(
                name=self.name,
                version=self.version,
                ctx_base_url=self.ctx_base_url
            )

    def retrieve_descriptor_references(self):
        return _resolve_cd_from_references(
            component_descriptor=self.descriptor
        )

    @property
    def yaml_file_path(self):
        return ComponentTool.gen_yaml_file_path(self.oci_ref)

    def write_descriptor_to_file(self):
        LOGGER.info(f'Writing descriptor to {self.yaml_file_path}')
        os.makedirs(os.path.dirname(self.yaml_file_path), exist_ok=True)
        with open(file=self.yaml_file_path, mode='w') as desc_file:
            self.descriptor.to_fobj(fileobj=desc_file)
        ci.util.Checksum().create_file(self.yaml_file_path)


def new_oci_resource_image_ref(resource: cm.Resource, oci_ref: str) -> cm.Resource:
    return cm.Resource(
        name=resource.name,
        version=resource.version,
        type=resource.type,
        access=cm.OciAccess(
            type=cm.AccessType.OCI_REGISTRY,
            imageReference=oci_ref,
        ),
        labels=resource.labels,
    )
