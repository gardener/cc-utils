from concourse.model.traits.notifications import NotificationTriggeringPolicy
from concourse.client import from_cfg
from concourse.client.model import BuildStatus

import concourse.util

import os
import traceback

import ci.util
import mailutil


def meta_vars():
    build = concourse.util.find_own_running_build()
    pipeline_metadata = concourse.util.get_pipeline_metadata()
    config_set = ci.util.ctx().cfg_factory().cfg_set(pipeline_metadata.current_config_set_name)
    concourse_cfg = config_set.concourse()
    v = {
        'build-id': build.id(),
        'build-name': build.build_number(),
        'build-job-name': pipeline_metadata.job_name,
        'build-team-name': pipeline_metadata.team_name,
        'build-pipeline-name': pipeline_metadata.pipeline_name,
        'atc-external-url': concourse_cfg.external_url(),
    }

    return v


def job_url(v):
    return '/'.join([
        v['atc-external-url'],
        'teams',
        v['build-team-name'],
        'pipelines',
        v['build-pipeline-name'],
        'jobs',
        v['build-job-name'],
        'builds',
        v['build-name']
    ])


def determine_previous_build_status(v, cfg_set):
    concourse_api = from_cfg(cfg_set.concourse(), team_name=v['build-team-name'])
    try:
        build_number = int(float(v['build-name']))
        if build_number < 2:
            ci.util.info('this seems to be the first build - will notify')
            return BuildStatus.SUCCEEDED

        previous_build = str(build_number - 1)
        previous_build = concourse_api.job_build(
            pipeline_name=v['build-pipeline-name'],
            job_name=v['build-job-name'],
            build_name=previous_build
        )
        return previous_build.status()
    except Exception as e:
        if type(e) == SystemExit:
            raise e
        # in doubt, ensure notification is sent
        traceback.print_exc()
        return None


def should_notify(
    triggering_policy,
    meta_vars,
    cfg_set,
    determine_previous_build_status=determine_previous_build_status,
):
    if triggering_policy == NotificationTriggeringPolicy.ALWAYS:
        return True
    elif triggering_policy == NotificationTriggeringPolicy.NEVER:
        return False
    elif triggering_policy == NotificationTriggeringPolicy.ONLY_FIRST:
        previous_build_status = determine_previous_build_status(meta_vars, cfg_set)
        if not previous_build_status:
            ci.util.info('failed to determine previous build status - will notify')
            return True

        # assumption: current job failed
        if previous_build_status in (BuildStatus.FAILED, BuildStatus.ERRORED):
          ci.util.info('previous build was already broken - will not notify')
          return False
        return True
    else:
        raise NotImplementedError


def cfg_from_callback(
    repo_root,
    callback_path,
    effective_cfg_file,
):
    import os
    import subprocess
    import tempfile

    import ci.util

    tmp_file = tempfile.NamedTemporaryFile()
    cb_env = os.environ.copy()
    cb_env['REPO_ROOT'] = repo_root
    cb_env['NOTIFY_CFG_OUT'] = tmp_file.name
    cb_env['EFFECTIVE_CFG'] = effective_cfg_file

    subprocess.run(
        [callback_path],
        check=True,
        env=cb_env,
    )

    return ci.util.parse_yaml_file(tmp_file.name)


def components_with_version_changes(component_diff_path: str):
    if not os.path.isfile(component_diff_path):
        ci.util.info('no component_diff found at: ' + str(component_diff_path))
        return set()
    else:
        component_diff = ci.util.parse_yaml_file(component_diff_path)
        comp_names = component_diff.get('component_names_with_version_changes', set())
        return set(comp_names)


def retrieve_build_log(concourse_api, task_name):
    v = meta_vars()
    try:
      build_id = v['build-id']
      task_id = concourse_api.build_plan(build_id=build_id).task_id(task_name=task_name)
      build_events = concourse_api.build_events(build_id=build_id)
      build_log = '\n'.join(build_events.iter_buildlog(task_id=task_id))
      return build_log
    except Exception:
      traceback.print_exc() # print_err, but send email notification anyway
      return 'failed to retrieve build log'


def resolve_recipients_by_component_name(components, github_cfg_name):
    component_recipients = mailutil.determine_mail_recipients(
        github_cfg_name=github_cfg_name, # todo: actually this is not required here
        components=components,
    )
    return component_recipients
