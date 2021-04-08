import pytest

import processing.model
import gci.componentmodel as cm
import processing.processing_model as processing_model


@pytest.fixture
def job(oci_img=cm.Resource):
    def _job(oci_img):
        return processing_model.ProcessingJob(
            component=None,
            container_image=oci_img,
            download_request=None,
            upload_request=processing.model.ContainerImageUploadRequest(
                source_file='file:path',
                source_ref='source:ref',
                target_ref='target:ref',
                processing_callback=None,
            ),
            upload_context_url=None,
        )
    return _job


@pytest.fixture
def oci_img(name='image_name', version='1.2.3', ref='image_ref:1.2.3'):
    def _oci_img(name=name, version=version, ref=ref):
        return cm.Resource(
            name=name,
            version=version,
            type=cm.ResourceType.OCI_IMAGE,
            access=cm.OciAccess(
                type=cm.AccessType.OCI_REGISTRY,
                imageReference=ref,
            ),
            labels=cm.Label,
        )
    return _oci_img


@pytest.fixture
def comp(name='a.b/c/e', version='1.2.3'):
    def _comp(name=name, version=version):
        return cm.Component(
                    name=name,
                    version=version,
                    repositoryContexts=[
                        cm.RepositoryContext(
                            baseUrl='example.com/context',
                            type=cm.AccessType.OCI_REGISTRY,
                        ),
                    ],
                    provider=cm.Provider,
                    sources=cm.ComponentSource,
                    componentReferences=cm.ComponentReference,
                    resources=cm.Resource,
                )

    return _comp
