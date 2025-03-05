import collections.abc

import yaml


def iter_artefacts(
    artefacts_yaml: str,
    artefacts_file: str,
) -> collections.abc.Generator[dict, None, None]:
    '''
    iterate over dicts found in either of artefacts_yaml or artefacts_file (both are interpreted
    as YAML-Documents). If toplevel element is a list, list-items are yielded.
    '''
    def iter_artefact(obj: list | dict):
        if isinstance(obj, list):
            yield from obj
        elif isinstance(obj, dict):
            yield obj
        else:
            raise RuntimeError(f'exepected either a list, or a dict, got: {obj=}')

    if artefacts_yaml:
        for obj in yaml.safe_load_all(artefacts_yaml):
            yield from iter_artefact(obj)

    if artefacts_file:
        with open(artefacts_file) as f:
            for obj in yaml.safe_load_all(f):
                yield from iter_artefact(obj)
