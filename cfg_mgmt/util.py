import dataclasses
import datetime
import logging
import os
import typing

import pytimeparse
import ruamel.yaml
import ruamel.yaml.scalarstring

import cfg_mgmt.model as cmm
import cfg_mgmt.rotate as cmro
import concourse.util
import gitutil
import model
import model.base


logger = logging.getLogger(__name__)


def iter_cfg_elements(
    cfg_factory: typing.Union[model.ConfigFactory, model.ConfigurationSet],
    cfg_target: typing.Optional[cmm.CfgTarget] = None,
):
    if isinstance(cfg_factory, model.ConfigurationSet):
        type_names = cfg_factory.cfg_factory._cfg_types().keys()
    else:
        type_names = cfg_factory._cfg_types().keys()

    for type_name in type_names:
        # workaround: cfg-sets may reference non-local cfg-elements
        # also, cfg-elements only contain references to other cfg-elements
        # -> policy-checks will only add limited value
        if type_name == 'cfg_set':
            continue
        for cfg_element in cfg_factory._cfg_elements(cfg_type_name=type_name):
            if cfg_target and not cfg_target.matches(cfg_element):
                continue
            yield cfg_element


def iter_cfg_queue_entries_to_be_deleted(
    cfg_metadata: cmm.CfgMetadata,
    cfg_target: typing.Optional[cmm.CfgTarget]=None,
) -> typing.Generator[cmm.CfgQueueEntry, None, None]:
    now = datetime.datetime.now()
    for cfg_queue_entry in cfg_metadata.queue:
        if cfg_target and not cfg_target == cfg_queue_entry.target:
            continue

        if not cfg_queue_entry.to_be_deleted(now):
            continue

        yield cfg_queue_entry


def create_config_queue_entry(
    queue_entry_config_element: model.NamedModelElement,
    queue_entry_data: dict,
    delete_after_period: str,
) -> cmm.CfgQueueEntry:
    return cmm.CfgQueueEntry(
        target=cmm.CfgTarget(
            name=queue_entry_config_element.name(),
            type=queue_entry_config_element._type_name,
        ),
        deleteAfter=(
            datetime.datetime.today()
            + datetime.timedelta(seconds=pytimeparse.parse(delete_after_period))
        ).date().isoformat(),
        secretId=queue_entry_data,
    )


def update_config_status(
    cfg_status_file_path: str,
    config_element: model.NamedModelElement,
    config_statuses: typing.Iterable[cmm.CfgStatus],
):
    for cfg_status in config_statuses:
        if cfg_status.matches(
            element=config_element,
        ):
            break
    else:
        # does not exist
        cfg_status = cmm.CfgStatus(
            target=cmm.CfgTarget(
                type=config_element._type_name,
                name=config_element.name(),
            ),
            credential_update_timestamp=datetime.date.today().isoformat(),
        )
        config_statuses.append(cfg_status)
    cfg_status.credential_update_timestamp = datetime.date.today().isoformat()

    yaml = ruamel.yaml.YAML()
    with open(cfg_status_file_path, 'w') as f:
        yaml.dump(
            {
                'config_status': [
                    dataclasses.asdict(cfg_status)
                    for cfg_status in config_statuses
                ]
            },
            f,
        )


def write_config_queue(
    cfg_dir: str,
    cfg_metadata: cmm.CfgMetadata,
    queue_file_name: str=cmm.cfg_queue_fname,
):
    yaml = ruamel.yaml.YAML()
    with open(os.path.join(cfg_dir, queue_file_name), 'w') as queue_file:
        yaml.dump(
            {
                'rotation_queue': [
                    dataclasses.asdict(cfg_queue_entry)
                    for cfg_queue_entry in cfg_metadata.queue
                ],
            },
            queue_file,
        )


def local_cfg_type_sources(
    cfg_element: model.NamedModelElement,
    cfg_factory: typing.Union[model.ConfigFactory, model.ConfigurationSet],
) -> set[str]:
    cfg_type = cfg_factory._cfg_type(cfg_element._type_name)
    return {
        src.file for src in cfg_type.sources() if isinstance(src, model.LocalFileCfgSrc)
    }


def _local_cfg_file(
    cfg_element: model.NamedModelElement,
    cfg_factory: typing.Union[model.ConfigFactory, model.ConfigurationSet],
) -> str | None:
    '''Return the local source file for the given config element from the given factory, or None
    if it is not unambiguosly identifiable.
    '''
    local_cfg_files = local_cfg_type_sources(cfg_element, cfg_factory)

    if len(local_cfg_files) > 1:
        logger.warning("Config elements with more than one local source file are not supported")
        return None
    if not local_cfg_files:
        logger.warning(f"No local source file known for cfg type '{cfg_element._type_name}'")
        return None

    return local_cfg_files.pop()


def write_named_element(
    cfg_element,
    cfg_dir: str,
    cfg_file_name: str,
):
    yaml = ruamel.yaml.YAML()
    with open(os.path.join(cfg_dir, cfg_file_name)) as cfg_file:
        file_contents = yaml.load(cfg_file)

    # ruamel can preserve anchors, unless the entries that are anchored are _directly_ manipulated.
    # To avoid this for kubernetes configs, only update the part that holds the kubeconfig
    if cfg_element._type_name == 'kubernetes':
        file_contents[cfg_element.name()]['kubeconfig'] = cfg_element.raw['kubeconfig']

        if cfg_element.raw.get('bound_secret_name'):
            file_contents[cfg_element.name()]['bound_secret_name'] = \
                cfg_element.raw['bound_secret_name']

    # for all other cases, just replace the contents of the top-level element (this will resolve
    # anchors)
    else:
        file_contents[cfg_element.name()] = cfg_element.raw

    # use util function to convert strings to more readable block format
    ruamel.yaml.scalarstring.walk_tree(file_contents)

    with open(os.path.join(cfg_dir, cfg_file_name), 'wt') as cfg_file:
        yaml.dump(file_contents, cfg_file)


def write_changes_to_local_dir(
    cfg_element: model.NamedModelElement,
    secret_id: dict,
    delete_after_period: str,
    cfg_metadata: cmm.CfgMetadata,
    cfg_factory: model.ConfigFactory,
    cfg_dir: str,
):
    src_file = _local_cfg_file(cfg_element, cfg_factory)

    if not src_file:
        raise RuntimeError(
            f"Unable to determine local source file for cfg type '{cfg_element._type_name}. '"
            'See previous warnings for more details.'
        )

    write_named_element(cfg_element, cfg_dir, src_file)

    cfg_metadata.queue.append(
        create_config_queue_entry(
            queue_entry_config_element=cfg_element,
            queue_entry_data=secret_id,
            delete_after_period=delete_after_period,
        )
    )
    write_config_queue(
        cfg_dir=cfg_dir,
        cfg_metadata=cfg_metadata,
    )

    update_config_status(
        config_element=cfg_element,
        config_statuses=cfg_metadata.statuses,
        cfg_status_file_path=os.path.join(
            cfg_dir,
            cmm.cfg_status_fname,
        )
    )


def rotate_config_element_and_persist_in_cfg_repo(
    cfg_element: model.NamedModelElement,
    cfg_factory: model.ConfigFactory,
    cfg_metadata: cmm.CfgMetadata,
    cfg_dir: str,
    github_cfg,
    github_repo_path: str,
    target_ref: str = 'refs/heads/master',
) -> bool:
    '''Rotate the given config element and write it to the given cfg-repo, along with any additional
    config metadata created.

    Returns `True` if the rotation was successful and `False` if no rotation was performed (for
    example due to there being no rotation-function for the given type).
    '''
    git_helper = gitutil.GitHelper(
        repo=cfg_dir,
        github_cfg=github_cfg,
        github_repo_path=github_repo_path,
    )

    src_file = _local_cfg_file(cfg_element, cfg_factory)
    if not src_file:
        raise RuntimeError(
            f"Unable to determine local source file for cfg type '{cfg_element._type_name}. '"
            'See previous warnings for more details.'
        )

    if ret_vals := cmro.rotate_cfg_element(
        cfg_factory=cfg_factory,
        cfg_element=cfg_element,
    ):
        revert_function, secret_id, updated_elem = ret_vals
    else:
        return False

    status = determine_status(
        element=cfg_element,
        policies=cfg_metadata.policies,
        rules=cfg_metadata.rules,
        responsibles=cfg_metadata.responsibles,
        statuses=cfg_metadata.statuses,
    )

    build_url = None
    if concourse.util._running_on_ci():
        try:
            build_url = concourse.util.own_running_build_url(cfg_factory=cfg_factory)
        except Exception as e:
            logger.warning(
                'Unable to determine own job-url. Will not put it in commit-message if a '
                f'commit is created. Error: {e}'
            )

    try:
        write_changes_to_local_dir(
            cfg_element=updated_elem,
            cfg_factory=cfg_factory,
            secret_id=secret_id,
            cfg_metadata=cfg_metadata,
            cfg_dir=cfg_dir,
            delete_after_period=status.policy.delete_after_period,
        )

        commit_message = f'rotate secret for {cfg_element._type_name}/{cfg_element.name()}'
        if build_url:
            commit_message += f'\n\nJob url: {build_url}'

        git_helper.add_and_commit(
            message=commit_message,
        )
        git_helper.push('@', target_ref)
    except Exception as e:
        logger.warning(f'failed to push updated secret - reverting. Error: {e}')
        revert_function()
        git_helper.repo.git.reset('--hard', '@~')
        # intentionally do not return False here, as we would try another rotation in our pipeline
        # in that case.

    return True


def process_cfg_queue_and_persist_in_repo(
    cfg_element: model.NamedModelElement,
    cfg_factory: model.ConfigFactory,
    cfg_metadata: cmm.CfgMetadata,
    cfg_queue_entry: cmm.CfgQueueEntry,
    cfg_dir: str,
    github_cfg,
    github_repo_path: str,
    target_ref: str = 'refs/heads/master',
):
    '''Process the given config-queue entry by deleting the referenced credentials in the
    infrastructure and persist the updated config metadata in the given config-repository.

    Returns `True` if the config-entry was processed successfully and `False` if no processing
    has taken place.
    '''
    git_helper = gitutil.GitHelper(
        repo=cfg_dir,
        github_cfg=github_cfg,
        github_repo_path=github_repo_path,
    )

    updated_element, processing_successful = cmro.delete_expired_secret(
        cfg_element=cfg_element,
        cfg_queue_entry=cfg_queue_entry,
        cfg_factory=cfg_factory,
    )

    if (
        not processing_successful
        and not updated_element
    ):
        # Nothing happened, return early
        return False

    if updated_element:
        # processing the queue has lead to an update in the element in question. Write it back.
        src_file = _local_cfg_file(cfg_element, cfg_factory)
        if not src_file:
            raise RuntimeError(
                f"Unable to determine local source file for cfg type '{cfg_element._type_name}. '"
                'See previous warnings for more details.'
            )
        write_named_element(updated_element, cfg_dir, src_file)

    if processing_successful:
        cfg_metadata.queue.remove(cfg_queue_entry)
        write_config_queue(
            cfg_dir=cfg_dir,
            cfg_metadata=cfg_metadata,
        )

    build_url = None
    if concourse.util._running_on_ci():
        try:
            build_url = concourse.util.own_running_build_url(cfg_factory=cfg_factory)
        except Exception as e:
            logger.warning(
                'Unable to determine own job-url. Will not put it in commit-message if a '
                f'commit is created. Error: {e}'
            )

    try:
        commit_message = f'process config queue for {cfg_element._type_name}/{cfg_element.name()}'
        if build_url:
            commit_message += f'\n\nJob url: {build_url}'
        git_helper.add_and_commit(
            message=commit_message,
        )
        git_helper.push('@', target_ref)
    except:
        logger.warning('failed to push processed config queue - reverting')
        git_helper.repo.git.reset('--hard', '@~')

    return processing_successful


def determine_status(
    element: model.NamedModelElement,
    policies: list[cmm.CfgPolicy],
    rules: list[cmm.CfgRule],
    responsibles: list[cmm.CfgResponsibleMapping],
    statuses: list[cmm.CfgStatus],
    element_storage: str=None,
) -> cmm.CfgElementStatusReport:
    for rule in rules:
        if rule.matches(element=element):
            break
    else:
        rule = None # no rule was configured

    rule: typing.Optional[cmm.CfgRule]

    if rule:
        for policy in policies:
            if policy.name == rule.policy:
                break
        else:
            rule = None # inconsistent cfg: rule with specified name does not exist
    else:
        policy = None

    for responsible in responsibles:
        if responsible.matches(element=element):
            break
    else:
        responsible = None

    for status in statuses:
        if status.matches(element):
            break
    else:
        status = None

    return cmm.CfgElementStatusReport(
        element_storage=element_storage,
        element_type=element._type_name,
        element_name=element._name,
        policy=policy,
        rule=rule,
        status=status,
        responsible=responsible,
    )
