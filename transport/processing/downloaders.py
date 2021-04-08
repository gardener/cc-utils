import dataclasses

import gci.componentmodel as cm
import processing.model


class Downloader:
    def _create_download_request(self, container_image, target_file: str):
        if container_image.access.type == cm.AccessType.OCI_REGISTRY:
            return processing.model.ContainerImageDownloadRequest(
                source_ref=container_image.access.imageReference,
                target_file=target_file,
            )

        if container_image.access.type == cm.AccessType.GITHUB:
            return None

        if container_image.access.type == cm.AccessType.HTTP:
            return None

    def process(self, processing_job, target_file: str):
        download_request = self._create_download_request(
            container_image=processing_job.container_image,
            target_file=target_file,
        )

        return dataclasses.replace(
            processing_job,
            download_request=download_request,
        )
