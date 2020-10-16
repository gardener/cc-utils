import ci.util

from dataclasses import dataclass


class IdentityUploader:
    def process(self, processing_job, target_as_source=False):
        upload_request = processing_job.upload_request
        if not target_as_source:
            upload_request = processing_job.upload_request._replace(
                target_ref=processing_job.upload_request.source_ref,
            )
        return processing_job._replace(upload_request=upload_request)


@dataclass
class PrefixUploader:
    prefix: str
    context_url: str
    mangle: bool = True

    def process(self, processing_job, target_as_source=False):
        if not target_as_source:
            image_reference = processing_job.container_image.access.imageReference
            src_name, src_tag = image_reference.rsplit(':', 1)
        else:
            # Use last two part if the repository specifies a port
            src_name, src_tag = processing_job.upload_request.target_ref.rsplit(':', 1)

        if self.mangle:
            src_ref = ':'.join([src_name.replace('.', '_'), src_tag])
        else:
            src_ref = ':'.join((src_name, src_tag))

        tgt_ref = ci.util.urljoin(self.prefix, src_ref)

        upload_request = processing_job.upload_request._replace(
            target_ref=tgt_ref,
        )

        return processing_job._replace(
            upload_request=upload_request,
            upload_context_url=self.context_url
        )


@dataclass
class TagSuffixUploader:
    suffix: str
    separator: str = '-'

    def process(self, processing_job, target_as_source=False):
        if not target_as_source:
            image_reference = processing_job.container_image.access.imageReference
            src_name, src_tag = image_reference.rsplit(':', 1)
        else:
            src_name, src_tag = processing_job.upload_request.target_ref.rsplit(':', 1)

        tgt_tag = self.separator.join((src_tag, self.suffix))
        tgt_ref = ':'.join((src_name, tgt_tag))

        upload_request = processing_job.upload_request._replace(
            target_ref=tgt_ref,
        )

        return processing_job._replace(upload_request=upload_request)
