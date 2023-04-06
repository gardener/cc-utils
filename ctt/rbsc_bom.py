# SPDX-FileCopyrightText: 2021 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

import dataclasses
import enum
from os import path
import typing

import yaml
import git
import tempfile
from urllib.parse import urlparse, ParseResult
import re
import logging

import ctx
import gci.componentmodel as cm
import ci.util

logger = logging.getLogger(__name__)


class BOMEntryType(enum.Enum):
    Docker = 'docker'


@dataclasses.dataclass
class BOMEntry:
    url: str
    type: BOMEntryType
    # component name
    comp: str
    mode: str = 'PREFER_MULTIARCH'


@dataclasses.dataclass
class ProductVersion:
    # product_version_id
    pv: str
    distributed: bool
    release_strategy: str
    # service_pack
    sp: str
    # patch_level
    pl: str
    name: str
    bom: typing.Sequence[BOMEntry] = ()


@dataclasses.dataclass
class RBSCBom:
    bomvers: str
    name: str
    # product_id
    p: str
    # product_versions
    pvs: typing.Sequence[ProductVersion] = ()


def injectCredentialsIntoRepoUrl(repo_url):
    ctx_factory = ctx.cfg_factory()
    gitlab = ctx_factory.gitlab("gitlab_rbsc_dev")
    credentials = gitlab.credentials()
    credentials_str = ':'.join((credentials.username(), credentials.passwd()))
    parsed_url = urlparse(repo_url)
    new = ParseResult(scheme=parsed_url.scheme, netloc=f'{credentials_str}@{parsed_url.netloc}',
                      path=parsed_url.path, params=parsed_url.params, query=parsed_url.query, fragment=parsed_url.fragment)
    return new.geturl()


def buildAndApplyBOM(bom_repository_url: str, bom_branch: str, bom_entries: typing.Sequence[BOMEntry]):
    if bom_branch not in ["master", "val", "dev"]:
        raise ValueError("--rbsc-git-branch has to to be master, dev or val!")

    bom_repository_url = injectCredentialsIntoRepoUrl(bom_repository_url)

    # for the RBSC, it expects all urls to have the scheme and port explicitly specified.
    # Defaulting schema to https and port to 443 if not otherwise set.
    def normalizeUrlsWithSchemaAndPort(bom_entry: BOMEntry):
        url = bom_entry.url

        # urlparse requires the a protocol as officially specified. Prepend https if not set.
        if not re.search(r'^[A-Za-z0-9+.\-]+://', bom_entry.url):
            url = 'https://{0}'.format(bom_entry.url)

        parsed_url = urlparse(url)
        new = ParseResult(scheme=parsed_url.scheme, netloc="{}:{}".format(parsed_url.hostname, parsed_url.port if parsed_url.port else 443),
                          path=parsed_url.path, params=parsed_url.params, query=parsed_url.query, fragment=parsed_url.fragment)
        new_url = new.geturl()
        bom_entry.url = new_url
        return bom_entry

    with tempfile.TemporaryDirectory() as local_path:
        repo, origin = _pullGitForBranch(bom_repository_url, bom_branch, local_path)
        bom = _parseBOM(local_path)
        if len(bom.pvs) != 1:
            raise NotImplementedError("More than one product version is not supported right now")

        normalized_boms = list(map(normalizeUrlsWithSchemaAndPort, bom_entries))

        # sort by name to have a reproducable bom with good diff highlighting
        normalized_sorted_boms = sorted(normalized_boms, key=lambda entry: entry.comp)

        # Deduplicate bom list based on url (since rbsc does not support two entries with the same url)
        deduplicated_bom_list = []
        preexisting_urls = set()
        for bom_entry in normalized_sorted_boms:
            if bom_entry.url not in preexisting_urls:
                deduplicated_bom_list.append(bom_entry)
                preexisting_urls.add(bom_entry.url)
        bom.pvs[0].bom = deduplicated_bom_list

        _writeBOM(bom, local_path)
        _pushToGit(repo, origin)


def _parseBOM(local_path: str) -> RBSCBom:
    with open(path.join(local_path, "bom.yaml"), "r") as bom_file:
        raw_src_bom = yaml.load(bom_file, Loader=yaml.SafeLoader)
        ci.util._count_elements(raw_src_bom)
        src_bom = RBSCBom(
            bomvers=raw_src_bom.get("bomvers"),
            name=raw_src_bom.get("name"),
            p=raw_src_bom.get("p"),
            pvs=[ProductVersion(
                pv=pv.get("pv"),
                distributed=pv.get("distributed"),
                release_strategy=pv.get("release_strategy"),
                sp=pv.get("sp"),
                pl=pv.get("pl"),
                name=pv.get("name"),
                bom=[
                    BOMEntry(
                        comp=entry.get("comp"),
                        type=entry.get("type"),
                        url=entry.get("url"),
                    ) for entry in pv.get("bom")
                ],
            ) for pv in raw_src_bom.get("pvs")],
        )
        return src_bom


def _writeBOM(bom: RBSCBom, local_path: str):
    with open(path.join(local_path, "bom.yaml"), 'w') as bom_file:
        yaml.dump(
            data=dataclasses.asdict(bom),
            stream=bom_file,
            Dumper=cm.EnumValueYamlDumper,
        )


def _pullGitForBranch(bom_repository_url: str, bom_branch: str, local_path: str):
    repo = git.Repo.init(local_path, mkdir=True)

    # setup remote
    origin = repo.create_remote('origin', bom_repository_url)
    assert origin.exists()
    assert origin == repo.remotes.origin == repo.remotes['origin']
    origin.fetch()

    remote_ref = origin.refs[bom_branch]
    if remote_ref is None:
        raise RuntimeError(f"Remote Branch for {bom_branch} does not exist!")
    logging.info(
        f'Git pull repo with bom.yaml from {origin.name}...'
    )
    repo.create_head(bom_branch, remote_ref).set_tracking_branch(remote_ref).checkout()
    origin.pull()
    logging.info(
        f'Git pull repo with bom.yaml from {origin.name} completed.'
    )
    return repo, origin


def _pushToGit(repo: git.Repo, origin: git.Remote):
    logging.info(
        f'Git push repo with bom.yaml to {origin.name}...'
    )

    actor = git.Actor("CNUDIE-Transport-Tool", "")

    index = repo.index
    index.add([path.join(repo.working_tree_dir, 'bom.yaml')])
    index.commit("New BOM Version", author=actor, committer=actor)
    push_response = origin.push()

    if len(push_response) == 0:
        logging.error(
            f'Git push repo with bom.yaml to {origin.name} failed.'
        )

    failed_heads = [r for r in push_response if r.flags == git.remote.PushInfo.ERROR]
    if len(failed_heads) > 0:
        logging.error(
            f'Git push repo with bom.yaml to {origin.name} failed for heads {failed_heads}.'
        )
    else:
        logging.info(
                f'Git push repo with bom.yaml to {origin.name} completed.'
        )
