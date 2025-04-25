import dataclasses
import datetime
import os

import dacite
import yaml

import ci.util
import ocm


@dataclasses.dataclass(kw_only=True)
class BaseComponent:
    '''
    model-class for "base-component" expected (by default) at `.ocm/base-component.yaml`.

    its attributes match some of those from ocm.Component, whith additional "short-cuts", e.g.
    `main_source` for conveniently customising the source-entry for repository for which
    GitHub-Action is run.

    attributes `version`, and `creationTime` are special, in that they are rejected if read
    from `base-component.yaml` file (they are present as attributes so they can be filled at
    runtime.

    Any existing attributes that match those from ocm.Component are merged into
    base-component-descriptor, as they are; absent attributes will be filled w/ defaults, if
    available.
    '''
    name: str | None
    version: str | None

    repositoryContexts: list[ocm.OciOcmRepository] = dataclasses.field(default_factory=list)
    provider: str | None

    componentReferences: list[ocm.ComponentReference] = dataclasses.field(default_factory=list)

    # cannot use ocm.Source | ocm.Resource, as we need to allow partial definitions
    resources: list[dict] = dataclasses.field(default_factory=list)
    sources: list[dict] = dataclasses.field(default_factory=list)

    labels: list[ocm.Label] = dataclasses.field(default_factory=list)

    creationTime: str | None

    main_source: dict = dataclasses.field(default_factory=dict)


def load_base_component(
    path: str,
    absent_ok: bool=True,
) -> BaseComponent:
    if os.path.isfile(path):
        with open(path) as f:
            raw = yaml.safe_load(f)
    else:
        if absent_ok:
            raw = {}
        else:
            print(f'Error: not an existing file {path=}')
            exit(1)

    for forbidden_attr in ('version', 'creationTime'):
        if forbidden_attr in raw:
            print(f'Error: must not specify {forbidden_attr=} in base-component')
            exit(1)

    if 'main-source' in raw: # also allow kebap-case
        raw['main_source'] = raw.pop('main-source')
    elif 'mainSource' in raw: # also allow camelCase
        raw['main_source'] = raw.pop('mainSource')
    elif 'MainSource' in raw: # also allow PascalCase
        raw['main_source'] = raw.pop('MainSource')

    return dacite.from_dict(
        data_class=BaseComponent,
        data=raw,
    )


def fill_in_defaults(
    component: BaseComponent,
    name: str,
    provider: str,
    ocm_repo: str,
    main_source: ocm.Source,
    creation_time: datetime.datetime,
) -> BaseComponent:
    if not component.name:
        component.name = name

    if not component.repositoryContexts:
        component.repositoryContexts = [
            ocm.OciOcmRepository(baseUrl=ocm_repo),
        ]

    if not component.provider:
        component.provider = provider

    if not component.creationTime:
        component.creationTime = creation_time.strftime('%Y-%m-%dT%H:%M:%SZ')

    main_source_raw = ci.util.merge_dicts(
        dataclasses.asdict(main_source),
        component.main_source,
    )
    # todo: we might want to guard against collisions (if user specified both a matching source,
    #       _and_ main_source)
    component.sources.append(main_source_raw)

    return component


def as_component_descriptor_dict(
    component: BaseComponent,
) -> dict:
    raw = dataclasses.asdict(component)

    raw.pop('main_source', None)

    return {
        'meta': dataclasses.asdict(ocm.Metadata()),
        'component': raw,
    }
