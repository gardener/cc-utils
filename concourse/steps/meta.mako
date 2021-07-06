<%def name="meta_step(job_step, job_variant, indent)", filter="indent_func(indent),trim">
<%
import datetime
import os

from makoutil import indent_func
from concourse.steps import step_lib
import concourse.paths

extra_attrs = {}

if os.path.isdir(os.path.join(concourse.paths.repo_root_dir, '.git')):
    import git
    repo = git.Repo(concourse.paths.repo_root_dir)
    commit_hash = repo.head.commit.hexsha

    extra_attrs['cc-utils-version'] = commit_hash

extra_attrs['render-timestamp'] = datetime.datetime.now().isoformat()
%>

${step_lib('meta')}

export_job_metadata(extra_attrs=${extra_attrs})

</%def>
