import abc
import functools
import os

import container.model
import container.util

OWN_DIR = os.path.abspath(os.path.dirname(__file__))


class ProcessorBase:
    @abc.abstractmethod
    def process(self, processing_job):
        raise NotImplementedError()


class NoOpProcessor(ProcessorBase):
    def process(self, processing_job):
        return processing_job


class FileFilter(ProcessorBase):
    def __init__(
        self,
        filter_files,
    ):
        self._remove_entries = []
        for path in filter_files:
            with open(os.path.join(OWN_DIR, path)) as f:
                for line in f.readlines():
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    else:
                        self._remove_entries.append(line)

    def _processing_callback(self):
        return functools.partial(
            container.util.filter_container_image,
            remove_entries=self._remove_entries,
        )

    def process(self, processing_job):
        upload_request = processing_job.upload_request._replace(
            processing_callback=self._processing_callback(),
        )

        return processing_job._replace(upload_request=upload_request)
