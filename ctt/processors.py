# SPDX-FileCopyrightText: Contributors to the Gardener project
#
# SPDX-License-Identifier: Apache-2.0

import abc
import collections.abc
import os

import ctt.model
import ctt.oci_util


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
        filter_files: collections.abc.Iterable[str]=(),
        filter_globs: collections.abc.Iterable[str]=(),
        base_dir: str='',
    ):
        self._remove_entries = []
        for path in filter_files or ():
            with open(os.path.join(base_dir, path)) as f:
                for line in f.readlines():
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    else:
                        self._remove_entries.append(line)

        self._remove_globs = [g.lstrip('/') for g in (filter_globs or ())]

    def process(
        self,
        replication_resource_element: ctt.model.ReplicationResourceElement,
    ) -> ctt.model.ReplicationResourceElement:
        replication_resource_element.remove_files = self._remove_entries
        replication_resource_element.remove_file_globs = self._remove_globs

        return replication_resource_element


class ManifestFilterProcessor(ProcessorBase):
    '''Marks resources for stripping of attributes from the top-level OCI manifest
    document during replication. No image modification performed here.'''
    def __init__(
        self,
        strip_manifest_attributes: collections.abc.Iterable[str],
    ):
        for path in strip_manifest_attributes:
            ctt.oci_util.parse_attribute_path(path) # fail early for invalid paths
        self._strip_manifest_attributes = list(strip_manifest_attributes)

    def process(
        self,
        replication_resource_element: ctt.model.ReplicationResourceElement,
    ) -> ctt.model.ReplicationResourceElement:
        replication_resource_element.strip_manifest_attributes = self._strip_manifest_attributes
        return replication_resource_element


class AppendFiles(ProcessorBase):
    def __init__(
        self,
        src_path: str,
        tgt_path: str,
        mode: str='replace_removed',
        layer_idx: int | None=None,
        base_dir: str='',
    ):
        if not os.path.isabs(src_path) and '..' in src_path.split('/'):
            raise ValueError(f'{src_path=} must not contain `..`')

        abs_src = (
            src_path
            if os.path.isabs(src_path)
            else os.path.join(base_dir, src_path) if base_dir else src_path
        )
        if not os.path.exists(abs_src):
            raise ValueError(f'{abs_src=} does not exist')

        tgt = tgt_path.lstrip('/')
        if not tgt or '..' in tgt.split('/'):
            raise ValueError(f'{tgt_path=} must be a non-empty in-image path without `..`')

        try:
            self._mode = ctt.model.AppendMode(mode)
        except ValueError as e:
            raise ValueError(f'unsupported {mode=}') from e

        if self._mode is ctt.model.AppendMode.INSERT_INTO_LAYER and layer_idx is None:
            raise ValueError('layer_idx is required when mode is `insert_into_layer`')
        if self._mode is not ctt.model.AppendMode.INSERT_INTO_LAYER and layer_idx is not None:
            raise ValueError('layer_idx is only supported when mode is `insert_into_layer`')

        self._src_path = abs_src
        self._tgt_path = tgt
        self._layer_idx = layer_idx

    def process(
        self,
        replication_resource_element: ctt.model.ReplicationResourceElement,
    ) -> ctt.model.ReplicationResourceElement:
        # if entries are already present (e.g. from a previous processor invocation),
        # mode/idx must agree with what they were configured with.
        if replication_resource_element.append_files:
            if (
                replication_resource_element.append_mode != self._mode
                or replication_resource_element.append_layer_idx != self._layer_idx
            ):
                raise ValueError(
                    'conflicting append_mode/append_layer_idx across AppendFiles entries: '
                    f'previous={replication_resource_element.append_mode!r}/'
                    f'{replication_resource_element.append_layer_idx!r}, '
                    f'current={self._mode!r}/{self._layer_idx!r}'
                )

        entry = ctt.model.AppendFilesEntry(
            src_path=self._src_path,
            tgt_path=self._tgt_path,
        )
        if entry not in replication_resource_element.append_files:
            replication_resource_element.append_files = [
                *replication_resource_element.append_files,
                entry,
            ]

        replication_resource_element.append_mode = self._mode
        replication_resource_element.append_layer_idx = self._layer_idx
        return replication_resource_element


class ChainedProcessor(ProcessorBase):
    '''
    Applies a list of processor instances in order. Useful for combining independent
    processors (e.g. FileFilter for removal + AppendFiles for injection) when the
    processing-pipeline model only allows a single processor per rule.
    '''
    def __init__(
        self,
        processors: list,
    ):
        if not processors:
            raise ValueError('ChainedProcessor requires a non-empty list of processors')
        self._processors = list(processors)

    def process(
        self,
        replication_resource_element: ctt.model.ReplicationResourceElement,
    ) -> ctt.model.ReplicationResourceElement:
        for proc in self._processors:
            replication_resource_element = proc.process(replication_resource_element)
        return replication_resource_element


class SbomInjectionProcessor(ProcessorBase):
    '''Marks resources for SBOM injection. No image modification performed here.'''
    def process(
        self,
        replication_resource_element: ctt.model.ReplicationResourceElement,
    ) -> ctt.model.ReplicationResourceElement:
        replication_resource_element.inject_sboms = True
        return replication_resource_element
