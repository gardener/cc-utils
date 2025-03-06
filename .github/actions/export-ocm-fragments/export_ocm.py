import collections.abc

import yaml


def iter_artefacts(
    artefacts_files: collections.abc.Iterable[str],
) -> collections.abc.Generator[dict, None, None]:
    '''
    iterate over dicts found in artefacts_files (interpreted
    as YAML-Documents). If toplevel element is a list, list-items are yielded.
    '''
    def iter_artefact(obj: list | dict):
        if isinstance(obj, list):
            yield from obj
        elif isinstance(obj, dict):
            yield obj
        else:
            raise RuntimeError(f'exepected either a list, or a dict, got: {obj=}')

    for artefacts_file in artefacts_files:
        with open(artefacts_file) as f:
            for obj in yaml.safe_load_all(f):
                yield from iter_artefact(obj)
