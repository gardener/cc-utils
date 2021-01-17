import mailutil
import ci.util
import concourse.util


def send_mail(
    notification_config_path: str,
    source_dirs: [str]=[],
    codeowners_files: [str]=[],
    component_names: [str]=[],
):
    if not ci.util._running_on_ci():
        raise RuntimeError('This command can only be used from within our CI-infrastructure.')

    if component_names:
        # todo: resolve components using product.v2
        raise NotImplementedError

    pipeline_metadata = concourse.util.get_pipeline_metadata()
    cfg_factory = ci.util.ctx().cfg_factory()
    current_cfg_set = cfg_factory.cfg_set(pipeline_metadata.current_cfg_setname)

    notification_config = ci.util.parse_yaml_file(notification_config_path)

    recipients = set(notification_config['recipients'])

    if any([source_dirs, codeowners_files, component_names]):
        recipients |= {
            r for r in mailutil.determine_mail_recipients(
                github_cfg_name=current_cfg_set.github().name(),
                src_dirs=source_dirs,
                component_names=component_names,
                codeowners_files=codeowners_files,
            )
        }

    mailutil.notify(
        subject=notification_config['subject'],
        body=notification_config['mail_body'],
        email_cfg_name=current_cfg_set.email().name(),
        recipients=recipients,
    )
