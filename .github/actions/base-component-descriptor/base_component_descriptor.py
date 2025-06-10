import dataclasses
import datetime

import ci.util
import ocm
import ocm.base_component

BaseComponent = ocm.base_component.BaseComponent


def fill_in_defaults(
    component: BaseComponent,
    name: str,
    version: str,
    provider: str,
    ocm_repo: str,
    main_source: ocm.Source,
    creation_time: datetime.datetime,
) -> BaseComponent:
    if not component.version:
        component.version = version
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


def add_resources_from_imagevector(
    imagevector_file: str,
    component: BaseComponent,
    component_prefixes: list[str],
) -> BaseComponent:
    # wrap function-call so we have a hook for unittesting
    return ocm.gardener.add_resources_from_imagevector(
        component=component,
        images=ocm.gardener.iter_images_from_imagevector(
            images_yaml_path=imagevector_file,
        ),
        component_prefixes=component_prefixes,
    )


def as_component_descriptor_dict(
    component: BaseComponent,
) -> dict:
    raw = dataclasses.asdict(component)

    raw.pop('main_source', None)

    return {
        'meta': dataclasses.asdict(ocm.Metadata()),
        'component': raw,
    }
