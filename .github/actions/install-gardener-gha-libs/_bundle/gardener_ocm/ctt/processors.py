# SPDX-FileCopyrightText: 2021 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import abc
import collections.abc
import os

import ctt.model


class ProcessorBase:
    @abc.abstractmethod
    def process(
        self,
        replication_resource_element: ctt.model.ReplicationResourceElement,
    ) -> ctt.model.ReplicationResourceElement:
        raise NotImplementedError()


class NoOpProcessor(ProcessorBase):
    def process(
        self,
        replication_resource_element: ctt.model.ReplicationResourceElement,
    ) -> ctt.model.ReplicationResourceElement:
        return replication_resource_element


class FileFilter(ProcessorBase):
    def __init__(
        self,
        filter_files: collections.abc.Iterable[str],
        base_dir: str='',
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

    def process(
        self,
        replication_resource_element: ctt.model.ReplicationResourceElement,
    ) -> ctt.model.ReplicationResourceElement:
        replication_resource_element.remove_files = self._remove_entries

        return replication_resource_element
