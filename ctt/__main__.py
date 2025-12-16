import argparse

import cnudie.retrieve
import ctt.process_dependencies
import oci.auth
import oci.client

'''
exposes a CLI for "CTT" (fka: CNUDIE Transport Tool)
'''


def configure_parser(parser):
    parser.add_argument(
        '--src-repo',
        required=True,
        help='path to OCM-Repository-Root',
    )
    parser.add_argument(
        '--ocm-component',
        required=True,
        help='the OCM-Component-version to replicate (format: <name>:<version>)',
    )
    parser.add_argument(
        '--docker-config',
        default=None,
    )
    parser.add_argument(
        '--processing-cfg',
        required=True,
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        default=False,
    )
    parser.add_argument(
        '--jobs', '-j',
        type=int,
        default=8,
        help='how many replication-tasks should be run in parallel.',
    )


def replicate(parsed):
    if not ':' in parsed.ocm_component:
        print(f'{parsed.ocm_component=} does not match expected format (<name>:<version>)')
        exit(1)

    oci_client = oci.client.Client(
        credentials_lookup=oci.auth.docker_credentials_lookup(
            docker_cfg=parsed.docker_config,
        ),
    )

    component_descriptor_lookup = cnudie.retrieve.create_default_component_descriptor_lookup(
        ocm_repository_lookup=cnudie.retrieve.ocm_repository_lookup(parsed.src_repo),
        oci_client=oci_client,
    )

    component_descriptor = component_descriptor_lookup(
        parsed.ocm_component,
        absent_ok=False, # let the exception propagate to convey a detailed error-message
    )

    if parsed.dry_run:
        processing_mode = ctt.process_dependencies.ProcessingMode.DRY_RUN
    else:
        processing_mode = ctt.process_dependencies.ProcessingMode.REGULAR

    max_workers = parsed.jobs
    if max_workers < 0:
        print('--jobs must be positive or 0')
        exit(1)
    elif max_workers == 0:
        max_workers = None

    print(f'starting replication of {parsed.ocm_component} {processing_mode=}')
    for _ in ctt.process_dependencies.process_images(
        processing_cfg_path=parsed.processing_cfg,
        root_component_descriptor=component_descriptor,
        component_descriptor_lookup=component_descriptor_lookup,
        oci_client=oci_client,
        processing_mode=processing_mode,
        max_workers=max_workers,
    ):
        pass


def main():
    parser = argparse.ArgumentParser()
    configure_parser(parser)

    parsed = parser.parse_args()

    replicate(parsed)


if __name__ == '__main__':
    main()
