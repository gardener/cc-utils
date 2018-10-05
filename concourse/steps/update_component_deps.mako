<%def
  name="update_component_deps_step(job_step, job_variant, github_cfg_name, indent)",
  filter="indent_func(indent),trim"
>
<%
from makoutil import indent_func
main_repo = job_variant.main_repository()
repo_name = main_repo.logical_name().upper()
%>

import os
import pathlib
import subprocess
import sys
from tempfile import TemporaryDirectory

import semver

import ctx
import github.util
import gitutil
import product.model
import product.util
import util

from github.release_notes.util import ReleaseNotes
from github.util import GitHubRepositoryHelper
from util import check_env


# must point to this repository's root directory
REPO_ROOT = pathlib.Path(check_env('${repo_name}_PATH')).absolute()
REPO_BRANCH = check_env('${repo_name}_BRANCH')
REPO_OWNER, REPO_NAME = check_env('${repo_name}_GITHUB_REPO_OWNER_AND_NAME').split('/')

# must point to component_descriptor directory
COMPONENT_DESCRIPTOR_DIR = pathlib.Path(check_env('COMPONENT_DESCRIPTOR_DIR')).absolute()
COMPONENT_DESCRIPTOR = COMPONENT_DESCRIPTOR_DIR.joinpath('component_descriptor')


cfg_factory = util.ctx().cfg_factory()
github_cfg_name = '${github_cfg_name}'
github_cfg=cfg_factory.github(github_cfg_name)


component_resolver = product.util.ComponentResolver(cfg_factory=cfg_factory)
component_descriptor_resolver = product.util.ComponentDescriptorResolver(cfg_factory=cfg_factory)

# indicates whether or not an upstream component was defined as a reference
UPGRADE_TO_UPSTREAM = 'UPSTREAM_COMPONENT_NAME' in os.environ


def current_product_descriptor():
    raw = util.parse_yaml_file(COMPONENT_DESCRIPTOR)
    return product.model.Product.from_dict(raw)


def _component(product_descriptor, component_name):
    component = [c for c in product_descriptor.components() if c.name() == component_name]
    component_count = len(component)
    if component_count == 1:
        return component[0]
    elif component_count < 1:
        util.fail('Did not find component {cn}'.format(cn=component_name))
    elif component_count > 1:
        util.fail('Found more than one component with name ' + component_name)
    else:
        raise NotImplementedError # this line should never be reached


def current_component():
    product = current_product_descriptor()
    component_name = check_env('COMPONENT_NAME')
    return _component(product, component_name=component_name)


def upstream_reference_component():
    component_name = check_env('UPSTREAM_COMPONENT_NAME')
    latest_version = component_resolver.latest_component_version(component_name)

    component_reference = product.model.ComponentReference.create(
        name=component_name,
        version=latest_version,
    )

    reference_product = component_descriptor_resolver.retrieve_descriptor(
        component_reference=component_reference,
    )

    reference_component = _component(
        product_descriptor=reference_product,
        component_name=component_name,
    )

    return reference_component


def close_obsolete_pull_requests(upgrade_pull_requests, reference_component):
    obsolete_upgrade_requests = [
        pr for pr in
        upgrade_pull_requests if pr.is_obsolete(reference_component=reference_component)
    ]

    for obsolete_request in obsolete_upgrade_requests:
        obsolete_request.purge()


def upgrade_pr_exists(reference, upgrade_requests):
    return any(
        [upgrade_rq.target_matches(reference=reference) for upgrade_rq in upgrade_requests]
    )


def create_upgrade_pr(from_ref, to_ref, ls_repo):
    new_branch_name = util.random_str(prefix='ci-', length=12)
    head_sha = ls_repo.ref('heads/' + REPO_BRANCH).object.sha
    ls_repo.create_ref('refs/heads/' + new_branch_name, head_sha)

    repo_dir = str(REPO_ROOT)

    # have component create upgradation diff
    upgrade_script_path = REPO_ROOT.joinpath('.ci', 'set_dependency_version')
    cmd_env = os.environ.copy()
    cmd_env['DEPENDENCY_TYPE'] = to_ref.type_name()
    cmd_env['DEPENDENCY_NAME'] = to_ref.name()
    cmd_env['DEPENDENCY_VERSION'] = to_ref.version()
    cmd_env['REPO_DIR'] = repo_dir
    cmd_env['GITHUB_CFG_NAME'] = github_cfg_name

    # pass type-specific attributes
    if to_ref.type_name() == 'container_image':
      cmd_env['DEPENDENCY_IMAGE_REFERENCE'] = to_ref.image_reference()

    subprocess.run(
        [str(upgrade_script_path)],
        check=True,
        env=cmd_env
    )
    commit_msg = 'Upgrade {cn}\n\nfrom {ov} to {nv}'.format(
        cn=to_ref.name(),
        ov=from_ref.version(),
        nv=to_ref.version(),
    )

    # mv diff into commit and push it
    helper = gitutil.GitHelper(
        repo=repo_dir,
        github_cfg=github_cfg,
        github_repo_path=REPO_OWNER + '/' + REPO_NAME,
    )
    commit = helper.index_to_commit(message=commit_msg)
    helper.push(from_ref=commit.hexsha, to_ref='refs/heads/' + new_branch_name, use_ssh=True)
    helper.repo.git.checkout('.')

    with TemporaryDirectory() as temp_dir:
        from_github_cfg = cfg_factory.github(from_ref.config_name())

        gitutil.clone_repository(
            to_path=temp_dir,
            github_cfg=from_github_cfg,
            github_repo_path=from_ref.github_repo_path(),
        )
        temp_dir_repo = os.path.join(temp_dir, from_ref.github_repo())

        from_github_helper = GitHubRepositoryHelper(
            github_cfg=from_github_cfg,
            owner=from_ref.github_organisation(),
            name=from_ref.github_repo(),
        )
        from_git_helper = gitutil.GitHelper(
            repo=temp_dir_repo,
            github_cfg=from_github_cfg,
            github_repo_path=from_ref.github_repo_path()
        )
        commit_range = '{from_version}..{to_version}'.format(
            from_version=from_ref.version(),
            to_version=to_ref.version()
        )
        release_note_blocks = ReleaseNotes.create(
            github_helper=from_github_helper,
            git_helper=from_git_helper,
            commit_range=commit_range
        ).release_note_blocks()
        if release_note_blocks:
            text = '*Release Notes*:\n{blocks}'.format(
                blocks=release_note_blocks
            )
        else:
            text = None

    ls_repo.create_pull(
            title=github.util.PullRequestUtil.calculate_pr_title(
                reference=to_ref,
                from_version=from_ref.version(),
                to_version=to_ref.version()
            ),
            base=REPO_BRANCH,
            head=new_branch_name,
            body=text,
    )


reference_product = current_product_descriptor()

pull_request_util = github.util.PullRequestUtil(
    owner=REPO_OWNER,
    name=REPO_NAME,
    default_branch=REPO_BRANCH,
    github_cfg=github_cfg,
)

ls_repository = pull_request_util.repository

upgrade_pull_requests = pull_request_util.enumerate_upgrade_pull_requests()

close_obsolete_pull_requests(
    upgrade_pull_requests=upgrade_pull_requests,
    reference_component=current_component(),
)


immediate_dependencies = current_component().dependencies()

if UPGRADE_TO_UPSTREAM:
  def determine_reference_version(reference):
    upstream_deps = upstream_reference_component().dependencies()
    upstream_refs = upstream_deps.references(type_name=reference.type_name())
    # filter for our name
    upstream_refs = [
        ref for ref in upstream_refs
        if ref.name() == reference.name()
    ]
    # filter for greatest
    upstream_refs = list(product.util.greatest_references(upstream_refs))
    if not upstream_refs:
      return None # upstream component does not declare our ref
    if len(upstream_refs) > 1:
      raise ValueError('found more than one matching reference (this is a BUG)')

    upstream_ref = upstream_refs[0]
    return semver.parse_version_info(upstream_ref.version())

  def enumerate_dependencies():
    for type_name in ('component', 'web', 'generic', 'container_image'):
      yield from product.util.greatest_references(
        immediate_dependencies.references(type_name=type_name)
      )
else:
  # no upstream component
  def determine_reference_version(reference):
    if not reference.type_name() == 'component':
      raise ValueError('only component dependencies supported')
    return component_resolver.latest_component_version(reference.name())

  def enumerate_dependencies():
    yield from product.util.greatest_references(immediate_dependencies.components())


# find components that need to be upgraded
for reference in enumerate_dependencies():
    ref_ctor = product.model.reference_type(reference.type_name()).create
    latest_version = determine_reference_version(reference)
    if not latest_version:
      util.info('could not find reference version for {n} - skipping'.format(
        n=reference.name()
        )
      )
      continue
    latest_ref = ref_ctor(
      name=reference.name(),
      version=str(latest_version),
    )
    if latest_version <= semver.parse_version_info(reference.version()):
        util.info('skipping outdated component upgrade: {n}; our version: {ov}, found: {fv}'.format(
          n=reference.name(),
          ov=str(reference.version()),
          fv=str(latest_version),
          )
        )
        continue
    elif upgrade_pr_exists(reference=latest_ref, upgrade_requests=upgrade_pull_requests):
        util.info('skipping upgrade (PR already exists): ' + reference.name())
        continue
    else:
        util.info('creating upgrade PR: {n}->{v}'.format(
          n=reference.name(),
          v=str(latest_version),
          )
        )
        to_ref = ref_ctor(
            name=reference.name(),
            version=str(latest_version),
        )
        create_upgrade_pr(from_ref=reference, to_ref=to_ref, ls_repo=ls_repository)
</%def>
