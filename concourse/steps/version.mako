<%def name="version_step(job_step, job_variant, indent)", filter="indent_func(indent),trim">
<%
from makoutil import indent_func

main_repo = job_variant.main_repository()
head_sha_file = main_repo.head_sha_path()
version_trait = job_variant.trait('version')

path_to_repo_version_file = main_repo.resource_name() + '/' + version_trait.versionfile_relpath()
output_version_file = job_step.output('version_path') + '/version'
legacy_version_file = job_step.output('version_path') + '/number'

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
import os
import pathlib

import ci.util
import version

version_file = ci.util.existing_file(pathlib.Path(${quote_str(path_to_repo_version_file)}))

if ${quote_str(version_operation)} == 'inject-commit-hash':
  head_sha_file = ci.util.existing_file(pathlib.Path(${quote_str(head_sha_file)}))
  prerelease = 'dev-' + head_sha_file.read_text().strip()
else:
  prerelease = ${quote_str(prerelease)}

processed_version = version.process_version(
    version_str=version_file.read_text().strip(),
    prerelease=prerelease,
    **${version_operation_kwargs},
)
ci.util.info('version preprocessing operation: ${version_operation}')
ci.util.info(f'effective version: {processed_version}')

cc_version = '/metadata/VERSION'
if os.path.isfile(cc_version):
  with open(cc_version) as f:
    ci.util.info(f'cc-utils version: {f.read()}')

output_version_file = pathlib.Path(${quote_str(output_version_file)})
legacy_version_file = pathlib.Path(${quote_str(legacy_version_file)})

output_version_file.write_text(processed_version)
legacy_version_file.write_text(processed_version)
</%def>
