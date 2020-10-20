#!/usr/bin/env python3

import argparse
from distutils.util import strtobool
import os
import tarfile

import ci.util
from processing.processing import Action
import processing.config as config
import processing.processing as processing
import processing.processing_component as pc


def parse_args():
    parent_parser = argparse.ArgumentParser(
        description='Transport tool for component descriptor v2',
    )

    subparsers = parent_parser.add_subparsers(dest="action", help="Actions", required=True)
    parser_download = subparsers.add_parser(
        name=Action.DOWNLOAD.value,
        help="Download component descriptor and images")
    parser_upload = subparsers.add_parser(
        name=Action.UPLOAD.value,
        help="Upload component descriptor and images")
    parser_sync = subparsers.add_parser(
        name=Action.SYNC.value,
        help="Run download and upload")
    parser_archive = subparsers.add_parser(
        name=Action.ARCHIVE.value,
        help="Manage resources archive"
    )

    for parser in [parser_download, parser_upload, parser_sync]:
        parser.add_argument(
            "--component-descriptor-name",
            action="store",
            nargs="?",
            default=os.environ.get("COMPONENT_DESCRIPTOR_NAME", None),
            help="Name of the component descriptor",
        )
        parser.add_argument(
            "--component-descriptor-version",
            action="store",
            nargs="?",
            default=os.environ.get("COMPONENT_DESCRIPTOR_VERSION", None),
            help="Version of the component descriptor",
        )
        parser.add_argument(
            "--context-base-url",
            action="store",
            nargs="?",
            default=os.environ.get("CONTEXT_BASE_URL", None),
            help="URL of of the context repository",
        )
        parser.add_argument(
            "--component-descriptor-path",
            action="store",
            nargs="?",
            default=os.environ.get("COMPONENT_DESCRIPTOR_PATH", None),
            help="Path to read the component descriptor file",
        )
        parser.add_argument(
            "--processing-cfg",
            action="store",
            nargs="?",
            default=os.environ.get("PROCESSING_CFG", config.PROCESSING_CFG),
            help="Path of the processing configuration file",
        )
        parser.add_argument(
            "--resources-dir",
            action="store",
            nargs="?",
            default=os.environ.get("RESOURCES_DIR", config.RESOURCES_DIR),
            help="Path where the resources will be downloaded",
        )
        parser.add_argument(
            "--dry-run",
            default=bool(strtobool(os.environ.get("DRY_RUN", "False"))),
            action="store_true", help="Do not download or upload resources",
        )

    parser_archive.add_argument(
        "archive_action",
        nargs="?",
        choices=[Action.CREATE.value, Action.EXTRACT.value]
    )
    parser_archive.add_argument(
        "--tar-file",
        action="store",
        nargs="?",
        default=os.environ.get("TAR_FILE", config.TAR_FILE),
        help="Name of the tar archive to create from resources dir",
    )

    args = parent_parser.parse_args()

    config.ACTIONS = [args.action]
    if args.action == Action.SYNC.value:
        config.ACTIONS = [Action.DOWNLOAD.value, Action.UPLOAD.value]

    if args.action != Action.ARCHIVE.value:
        config.PROCESSING_CFG = args.processing_cfg
        config.RESOURCES_DIR = args.resources_dir
        config.DRY_RUN = args.dry_run

    if args.action == Action.ARCHIVE.value:
        config.TAR_FILE = args.tar_file
        if args.archive_action is None:
            parser_archive.print_help()

    return args


def parse_processing_cfg(path):
    raw_cfg = ci.util.parse_yaml_file(path)
    return raw_cfg


def create_archive(source: str, out: str):
    ci.util.existing_dir(source)
    with tarfile.open(out, "w") as tar:
        tar.add(name=source)


def extract_archive(source: str):
    ci.util.existing_file(source)
    with tarfile.open(source, "r") as tar:
        tar.extractall()


def main():
    args = parse_args()
    processing_cfg = parse_processing_cfg(path=config.PROCESSING_CFG)

    if Action.ARCHIVE.value not in config.ACTIONS:
        if config.DRY_RUN:
            ci.util.warning('dry-run: not downloading or uploading any images')

        if args.component_descriptor_path is not None:
            descriptor = pc.parse_component_descriptor(args.component_descriptor_path)
            component = pc.ComponentTool.new_from_descriptor(descriptor)
        else:
            component = pc.ComponentTool(
                name=args.component_descriptor_name,
                version=args.component_descriptor_version,
                ctx_base_url=args.context_base_url,
                descriptor=None,
            )
            component.retrieve_descriptor()

        # retrieve the component references, it will first try to read the
        # descriptor locally and try to download if there is none
        component_obj_run = [component]
        if not config.DRY_RUN:
            for ref in component.retrieve_descriptor_references():
                _, descriptor = ref
                comp_obj = pc.ComponentTool.new_from_descriptor(descriptor)
                if comp_obj not in component_obj_run:
                    component_obj_run.append(comp_obj)

        # run the process on all components
        for comp_obj in component_obj_run:
            processing.ProcessComponent(
                processing_cfg=processing_cfg,
                component_obj=comp_obj,
            )

    if Action.ARCHIVE.value in config.ACTIONS:
        if args.archive_action == Action.CREATE.value:
            ci.util.info(f'Create resources archive from {config.RESOURCES_DIR} to '
                         f'{config.TAR_FILE}')
            create_archive(source=config.RESOURCES_DIR, out=config.TAR_FILE)

        if args.archive_action == Action.EXTRACT.value:
            ci.util.info(f'Extract resources archive {config.TAR_FILE}')
            extract_archive(source=config.TAR_FILE)

        return


if __name__ == '__main__':
    main()
