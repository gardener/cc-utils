# SPDX-FileCopyrightText: 2021 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import abc
import dataclasses
import os


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
        base_dir='',
    ):
        self._remove_entries = []
        for path in filter_files:
            with open(os.path.join(base_dir, path)) as f:
                for line in f.readlines():
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    else:
                        self._remove_entries.append(line)

    def process(self, processing_job):
        upload_request = dataclasses.replace(
            processing_job.upload_request,
            remove_files=tuple(self._remove_entries),
        )

        return dataclasses.replace(
            processing_job,
            upload_request=upload_request
        )
