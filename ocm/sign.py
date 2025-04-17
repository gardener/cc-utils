'''
Functionality for normalising and hashing OCM-Component-Descriptors.
'''

import collections.abc
import dataclasses
import datetime
import hashlib
import json

import ocm


class DigestMismatchException(Exception):
    pass


def normalise_obj(
    obj: dict,
) -> list[dict]:
    '''
    Recursively converts a dictionary `obj` to a list of objects with a single key/value-pair,
    sorted by the keys, to create a stable representation of `obj`.
    '''
    return [
        {
            key: normalise_obj(value) if isinstance(value, dict) else value,
        }
        for key, value in sorted(
            obj.items(),
            key=lambda item: item[0],
        )
    ]


def normalise_label(
    label: ocm.Label,
) -> list[dict]:
    label_raw = dataclasses.asdict(label)

    if not label.version:
        del label_raw['version']

    return normalise_obj(label_raw)


def normalise_resource(
    resource: ocm.Resource,
    access_to_digest_lookup: collections.abc.Callable[[ocm.Access], ocm.DigestSpec],
    verify_digests: bool=False,
) -> list[dict]:
    resource_raw = dataclasses.asdict(resource)

    # drop properties not relevant for signing
    del resource_raw['access']
    del resource_raw['srcRefs']

    if labels := [normalise_label(l) for l in resource.labels if l.signing]:
        resource_raw['labels'] = labels
    else:
        del resource_raw['labels']

    # digest is ignored in case no access is specified; otherwise, calculate digest if it is not
    # existing yet
    if resource.access:
        if (
            resource.digest
            and verify_digests
            and not (
                resource.digest.hashAlgorithm == ocm.NO_DIGEST
                and resource.digest.normalisationAlgorithm == ocm.EXCLUDE_FROM_SIGNATURE
                and resource.digest.value == ocm.NO_DIGEST
            )
        ):
            digest = access_to_digest_lookup(resource.access)
            resource_raw['digest'] = dataclasses.asdict(digest)

            if resource.digest.value != digest.value:
                e = DigestMismatchException(
                    f'calculated digest {digest.value} mismatches existing digest '
                    f'{resource.digest.value}'
                )
                e.add_note(f'{resource=}')
                raise e

        elif not resource.digest:
            resource_raw['digest'] = dataclasses.asdict(access_to_digest_lookup(resource.access))

    else:
        del resource_raw['digest']

    # extra-identity is expected to be null instead of an empty object by OCM-cli in case it does
    # not contain any elements
    if not resource.extraIdentity:
        resource_raw['extraIdentity'] = None

    return normalise_obj(resource_raw)


def normalise_component_reference(
    component_reference: ocm.ComponentReference,
    component_descriptor_lookup: collections.abc.Callable[[ocm.ComponentIdentity], ocm.ComponentDescriptor], # noqa: E501
    access_to_digest_lookup: collections.abc.Callable[[ocm.Access], ocm.DigestSpec],
    verify_digests: bool=False,
    normalisation: ocm.NormalisationAlgorithm=ocm.NormalisationAlgorithm.JSON_NORMALISATION,
) -> list[dict]:
    component_reference_raw = dataclasses.asdict(component_reference)

    if labels := [normalise_label(l) for l in component_reference.labels if l.signing]:
        component_reference_raw['labels'] = labels
    else:
        del component_reference_raw['labels']

    if not component_reference.digest or verify_digests:
        component_descriptor = component_descriptor_lookup(ocm.ComponentIdentity(
            name=component_reference.componentName,
            version=component_reference.version,
        ))

        digest = component_descriptor_digest(
            component_descriptor=component_descriptor,
            component_descriptor_lookup=component_descriptor_lookup,
            access_to_digest_lookup=access_to_digest_lookup,
            verify_digests=verify_digests,
            normalisation=normalisation,
        )

        component_reference_raw['digest'] = dataclasses.asdict(ocm.DigestSpec(
            hashAlgorithm='SHA-256',
            normalisationAlgorithm=normalisation,
            value=digest,
        ))

    if (
        verify_digests
        and component_reference.digest
        and component_reference.digest.value != digest
    ):
        e = DigestMismatchException(
            f'calculated digest {digest} mismatches existing digest '
            f'{component_reference.digest.value}'
        )
        e.add_note(f'{component_reference=}')
        raise e

    # extra-identity is expected to be null instead of an empty object by OCM-cli in case it does
    # not contain any elements
    if not component_reference.extraIdentity:
        component_reference_raw['extraIdentity'] = None

    return normalise_obj(component_reference_raw)


def normalise_component(
    component: ocm.Component,
    component_descriptor_lookup: collections.abc.Callable[[ocm.ComponentIdentity], ocm.ComponentDescriptor], # noqa: E501
    access_to_digest_lookup: collections.abc.Callable[[ocm.Access], ocm.DigestSpec],
    verify_digests: bool=False,
    normalisation: ocm.NormalisationAlgorithm=ocm.NormalisationAlgorithm.JSON_NORMALISATION,
) -> list[dict]:
    component_raw = dataclasses.asdict(component)

    # drop properties not relevant for signing
    del component_raw['repositoryContexts']
    del component_raw['sources']

    if labels := [normalise_label(l) for l in component.labels if l.signing]:
        component_raw['labels'] = labels
    else:
        del component_raw['labels']

    # calculate missing digests for component references
    component_raw['componentReferences'] = [
        normalise_component_reference(
            component_reference=cref,
            component_descriptor_lookup=component_descriptor_lookup,
            access_to_digest_lookup=access_to_digest_lookup,
            verify_digests=verify_digests,
            normalisation=normalisation,
        ) for cref in component.componentReferences
    ]

    # calculate missing digests for resources; also, match OCM-cli's extra-identity handling by
    # implicitly adding the version to the extra-identity if the resource is not unique by its name
    # + existing extra-identity yet (not for the last resource as this is unique already if all
    # other resources have the version added to their extra-identity)
    resources = []
    for idx, resource in enumerate(component.resources):
        for peer in component.resources[idx+1:]:
            if (
                peer.identity(peers=()) == resource.identity(peers=())
                and 'version' not in resource.extraIdentity
            ):
                resource = dataclasses.replace(resource) # create copy
                resource.extraIdentity['version'] = resource.version

        resources.append(normalise_resource(
            resource=resource,
            access_to_digest_lookup=access_to_digest_lookup,
            verify_digests=verify_digests,
        ))
    component_raw['resources'] = resources

    # match OCM-cli's creation time normalisation by dropping the microseconds (round up if
    # necessary) and match expected format %Y-%m-%dT%H:%M:%SZ
    if component.creationTime:
        creation_time = datetime.datetime.fromisoformat(component.creationTime)
        if creation_time.microsecond >= 500000:
            creation_time += datetime.timedelta(seconds=1)
        component_raw['creationTime'] = creation_time.strftime('%Y-%m-%dT%H:%M:%SZ')
    else:
        del component_raw['creationTime']

    return normalise_obj(component_raw)


def normalise_component_descriptor(
    component_descriptor: ocm.ComponentDescriptor,
    component_descriptor_lookup: collections.abc.Callable[[ocm.ComponentIdentity], ocm.ComponentDescriptor], # noqa: E501
    access_to_digest_lookup: collections.abc.Callable[[ocm.Access], ocm.DigestSpec],
    verify_digests: bool=False,
    normalisation: ocm.NormalisationAlgorithm=ocm.NormalisationAlgorithm.JSON_NORMALISATION,
) -> list[dict]:
    '''
    Returns a normalised version of the component descriptor by dropping signing-irrelevant
    properties, calculating missing digests and converting properties to the format expected by the
    OCM-cli (i.e. extra-identity handling and datetime-format).

    @param component_descriptor:
        the component descriptor of which a normalised representation should be created
    @param component_descriptor_lookup:
        lookup for component descriptors by their identity
    @param access_to_digest_lookup:
        used to retrieve the digest of an resource by its access specification
    @param verify_digests:
        if set, verify already existing digests instead of assuming they are correct
    @param normalisation:
        the algorithm used to create a normalised representation of the component descriptor
    '''
    component_descriptor_raw = dataclasses.asdict(component_descriptor)

    # drop properties not relevant for signing
    del component_descriptor_raw['signatures']
    del component_descriptor_raw['nestedDigests']

    component_descriptor_raw['component'] = normalise_component(
        component=component_descriptor.component,
        component_descriptor_lookup=component_descriptor_lookup,
        access_to_digest_lookup=access_to_digest_lookup,
        verify_digests=verify_digests,
        normalisation=normalisation,
    )

    return normalise_obj(component_descriptor_raw)


def component_descriptor_digest(
    component_descriptor: ocm.ComponentDescriptor,
    component_descriptor_lookup: collections.abc.Callable[[ocm.ComponentIdentity], ocm.ComponentDescriptor], # noqa: E501
    access_to_digest_lookup: collections.abc.Callable[[ocm.Access], ocm.DigestSpec],
    verify_digests: bool=False,
    normalisation: ocm.NormalisationAlgorithm=ocm.NormalisationAlgorithm.JSON_NORMALISATION,
) -> str:
    '''
    Calculates the hexdigest of the recursively normalised component descriptor.

    @param component_descriptor:
        the component descriptor of which the digest should be calculated
    @param component_descriptor_lookup:
        lookup for component descriptors by their identity
    @param access_to_digest_lookup:
        used to retrieve the digest of an resource by its access specification
    @param verify_digests:
        if set, verify already existing digests instead of assuming they are correct
    @param normalisation:
        the algorithm used to create a normalised representation of the component descriptor as
        input for the digest calculation
    '''
    normalised_component_descriptor = normalise_component_descriptor(
        component_descriptor=component_descriptor,
        component_descriptor_lookup=component_descriptor_lookup,
        access_to_digest_lookup=access_to_digest_lookup,
        verify_digests=verify_digests,
        normalisation=normalisation,
    )

    serialised_component_descriptor = json.dumps(
        obj=normalised_component_descriptor,
        separators=(',', ':'), # remove spaces after separators (match OCM-cli)
    )

    return hashlib.sha256(serialised_component_descriptor.encode()).hexdigest()
