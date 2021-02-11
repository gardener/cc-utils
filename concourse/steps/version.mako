<%def name="version_step(job_step, job_variant, indent)", filter="indent_func(indent),trim">
<%
import os
from makoutil import indent_func
from concourse.steps import step_lib

main_repo = job_variant.main_repository()
head_sha_file = main_repo.head_sha_path()
version_trait = job_variant.trait('version')

path_to_repo_version_file = os.path.join(
  main_repo.resource_name(),
  version_trait.versionfile_relpath(),
)
output_version_file = os.path.join(job_step.output('version_path'), 'version')
legacy_version_file = os.path.join(job_step.output('version_path'), 'number')

if (read_callback := version_trait.read_callback()):
  read_callback = os.path.join(main_repo.resource_name(), read_callback)

if (write_callback := version_trait.write_callback()):
  write_callback = os.path.join(main_repo.resource_name(), write_callback)

version_operation = version_trait._preprocess()
branch_name = main_repo.branch()

version_operation_kwargs = dict()
prerelease = None

if version_operation == 'inject-commit-hash':
  version_operation_kwargs['operation'] = 'set_prerelease'
elif version_operation in ('finalize', 'finalise'):
  version_operation_kwargs['operation'] = 'finalize_version'
elif version_operation == 'noop':
  version_operation_kwargs['operation'] = 'noop'
elif version_operation == 'inject-branch-name':
  version_operation_kwargs['operation'] = 'set_prerelease'
  prerelease = branch_name
elif version_operation == 'use-branch-name':
  version_operation_kwargs['operation'] = 'set_verbatim'
  version_operation_kwargs['verbatim_version'] = branch_name
else:
  raise ValueError(f"unknown version operation: '{version_operation}'")

def quote_str(value):
  if isinstance(value, str):
    return f"'{value}'"
  elif value is None:
    return None
  else:
    raise ValueError

%>

${step_lib('version')}
import logging
import os
import pathlib

import ci.util
import ci.paths
import concourse.model.traits.version as version_trait
import version

logger = logging.getLogger('version.step')

version_interface = version_trait.VersionInterface('${version_trait.version_interface().value}')

if version_interface is version_trait.VersionInterface.FILE:
  version_path = '${path_to_repo_version_file}'
elif version_interface is version_trait.VersionInterface.CALLBACK:
  version_path = '${read_callback}'
else:
  raise NotImplementedError

current_version = read_version(
  version_interface=version_interface,
  path=version_path,
)

if ${quote_str(version_operation)} == 'inject-commit-hash':
  head_sha_file = ci.util.existing_file(pathlib.Path(${quote_str(head_sha_file)}))
  prerelease = 'dev-' + head_sha_file.read_text().strip()
else:
  prerelease = ${quote_str(prerelease)}

effective_version = version.process_version(
    version_str=current_version,
    prerelease=prerelease,
    **${version_operation_kwargs},
)
logger.info('version preprocessing operation: ${version_operation}')
logger.info(f'effective version: {effective_version}')

cc_version = '/metadata/VERSION'
if os.path.isfile(cc_version):
  with open(cc_version) as f:
    logger.info(f'cc-utils version: {f.read()}')
if os.path.isdir(os.path.join(ci.paths.repo_root, '.git')):
  import git
  repo = git.Repo(ci.paths.repo_root)
  try:
    logger.info(f'cc-utils-commit: {repo.head.commit.hexsha=}')
  except:
    pass

if version_interface is version_trait.VersionInterface.CALLBACK:
  write_version(
    version_interface=version_interface,
    version_str=effective_version,
    path='${write_callback}',
  )
elif version_interface is version_trait.VersionInterface.FILE:
  write_version(
    version_interface=version_interface,
    version_str=effective_version,
    path='${legacy_version_file}',
  )
else:
  raise NotImplementedError

# always write version to `managed-version` dir (abstract from callback)
write_version(
  version_interface=version_trait.VersionInterface.FILE,
  version_str=effective_version,
  path='${output_version_file}',
)
</%def>
