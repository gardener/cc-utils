import dataclasses
import datetime
import logging
import os
import typing
import yaml

import dateutil.parser

import ccc.elasticsearch
import cfg_mgmt.metrics
import cfg_mgmt.model as cmm
import cfg_mgmt.reporting as cmr
import cfg_mgmt.rotate as cmro
import ci.util
import gitutil
import model


logger = logging.getLogger(__name__)


def generate_cfg_element_status_reports(cfg_dir: str) -> list[cmr.CfgElementStatusReport]:
    ci.util.existing_dir(cfg_dir)

    cfg_factory = model.ConfigFactory._from_cfg_dir(
        cfg_dir,
        disable_cfg_element_lookup=True,
    )

    cfg_metadata = cmm.cfg_metadata_from_cfg_dir(cfg_dir)

    policies = cfg_metadata.policies
    rules = cfg_metadata.rules
    statuses = cfg_metadata.statuses
    responsibles = cfg_metadata.responsibles

    return [
        determine_status(
            element=element,
            policies=policies,
            rules=rules,
            statuses=statuses,
            responsibles=responsibles,
            element_storage=cfg_dir,
        ) for element in iter_cfg_elements(cfg_factory=cfg_factory)
    ]


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


def iter_cfg_elements_requiring_rotation(
    cfg_elements: typing.Iterable[model.NamedModelElement],
    cfg_metadata: cmm.CfgMetadata,
    cfg_target: typing.Optional[cmm.CfgTarget]=None,
    element_filter: typing.Callable[[model.NamedModelElement], bool]=None,
    rotation_method: cmm.RotationMethod=None,
) -> typing.Generator[model.NamedModelElement, None, None]:
    for cfg_element in cfg_elements:
        if cfg_target and not cfg_target.matches(element=cfg_element):
            continue

        if element_filter and not element_filter(cfg_element):
            continue

        status = determine_status(
            element=cfg_element,
            policies=cfg_metadata.policies,
            rules=cfg_metadata.rules,
            responsibles=cfg_metadata.responsibles,
            statuses=cfg_metadata.statuses,
        )

        # hardcode rule: ignore elements w/o rule and policy
        if not status.policy or not status.rule:
            continue

        # hardcode: ignore all policies we cannot handle (currently, only MAX_AGE)
        if not status.policy.type is cmm.PolicyType.MAX_AGE:
            continue

        if rotation_method and status.policy.rotation_method is not rotation_method:
            continue

        # if there is no status, assume rotation be required
        if not status.status:
            yield cfg_element
            continue

        last_update = dateutil.parser.isoparse(status.status.credential_update_timestamp)
        if status.policy.check(last_update=last_update):
            continue
        else:
            yield cfg_element


def determine_status(
    element: model.NamedModelElement,
    policies: list[cmm.CfgPolicy],
    rules: list[cmm.CfgRule],
    responsibles: list[cmm.CfgResponsibleMapping],
    statuses: list[cmm.CfgStatus],
    element_storage: str=None,
) -> cmr.CfgElementStatusReport:
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

    return cmr.CfgElementStatusReport(
        element_storage=element_storage,
        element_type=element._type_name,
        element_name=element._name,
        policy=policy,
        rule=rule,
        status=status,
        responsible=responsible,
    )


def create_config_queue_entry(
    queue_entry_config_element: model.NamedModelElement,
    queue_entry_data: dict,
) -> cmm.CfgQueueEntry:
    return cmm.CfgQueueEntry(
        target=cmm.CfgTarget(
            name=queue_entry_config_element.name(),
            type=queue_entry_config_element._type_name,
        ),
        deleteAfter=(datetime.datetime.today() + datetime.timedelta(days=7)).date().isoformat(),
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
    with open(os.path.join(cfg_dir, queue_file_name), 'w') as queue_file:
        yaml.dump(
            {
                'rotation_queue': [
                    dataclasses.asdict(cfg_queue_entry)
                    for cfg_queue_entry in cfg_metadata.queue
                ],
            },
            queue_file,
            Dumper=ci.util.MultilineYamlDumper,
        )


def cfg_compliance_status_to_es(
    es_client,
    cfg_report_summary_gen: typing.Generator[cmm.CfgStorageSummary, None, None],
):
    for cfg_report_summary in cfg_report_summary_gen:
        cc_cfg_compliance_status = cfg_mgmt.metrics.CcCfgComplianceStatus.create(
            url=cfg_report_summary.url,
            compliant_count=cfg_report_summary.compliantElementsCount,
            non_compliant_count=cfg_report_summary.noncompliantElementsCount,
        )

        ccc.elasticsearch.metric_to_es(
            es_client=es_client,
            metric=cc_cfg_compliance_status,
            index_name=cfg_mgmt.metrics.index_name(cc_cfg_compliance_status),
        )


def cfg_compliance_storage_responsibles_to_es(
    es_client,
    cfg_responsible_summary_gen: typing.Generator[cmm.CfgResponsibleSummary, None, None],
):
    for cfg_responsible_sum in cfg_responsible_summary_gen:
        cc_cfg_compliance_storage_responsibles = \
            cfg_mgmt.metrics.CcCfgComplianceStorageResponsibles.create(
            url=cfg_responsible_sum.url,
            compliant_count=cfg_responsible_sum.compliantElementsCount,
            non_compliant_count=cfg_responsible_sum.noncompliantElementsCount,
            responsible=cfg_responsible_sum.responsible,
        )

        ccc.elasticsearch.metric_to_es(
            es_client=es_client,
            metric=cc_cfg_compliance_storage_responsibles,
            index_name=cfg_mgmt.metrics.index_name(cc_cfg_compliance_storage_responsibles),
        )


def cfg_compliance_responsibles_to_es(
    es_client,
    cfg_element_statuses: typing.Iterable[cmr.CfgElementStatusReport],
):
    for cfg_element_status in cfg_element_statuses:

        status_evaluation = cmr.evaluate_cfg_element_status(cfg_element_status)

        cc_cfg_compliance_responsible = cfg_mgmt.metrics.CcCfgComplianceResponsible.create(
            element_name=cfg_element_status.element_name,
            element_type=cfg_element_status.element_type,
            element_storage=cfg_element_status.element_storage,
            is_compliant=status_evaluation.fullyCompliant,
            responsible=cfg_element_status.responsible,
            rotation_method=cfg_element_status.policy.rotation_method,
            non_compliant_reasons=status_evaluation.nonCompliantReasons,
        )

        ccc.elasticsearch.metric_to_es(
            es_client=es_client,
            metric=cc_cfg_compliance_responsible,
            index_name=cfg_mgmt.metrics.index_name(cc_cfg_compliance_responsible),
        )


def local_cfg_type_sources(
    cfg_element: model.NamedModelElement,
    cfg_factory: typing.Union[model.ConfigFactory, model.ConfigurationSet],
) -> typing.Iterable[str]:
    cfg_type = cfg_factory._cfg_type(cfg_element._type_name)
    return {
        src.file for src in cfg_type.sources() if isinstance(src, model.LocalFileCfgSrc)
    }


def write_named_elements(
    cfg_elements: typing.Iterable[model.NamedModelElement],
    cfg_dir: str,
    cfg_file_name: str,
):
    configs = {e.name(): e.raw for e in cfg_elements}
    with open(os.path.join(cfg_dir, cfg_file_name), 'w') as cfg_file:
        yaml.dump(configs, cfg_file, Dumper=ci.util.MultilineYamlDumper)


def write_changes_to_local_dir(
    cfg_element: model.NamedModelElement,
    secret_id: dict,
    cfg_metadata: cmm.CfgMetadata,
    cfg_factory: model.ConfigFactory,
    cfg_dir: str,
):
    elements = [
        e if e.name() != cfg_element.name() else cfg_element
        for e in cfg_factory._cfg_elements(cfg_element._type_name)
    ]

    local_cfg_files = local_cfg_type_sources(cfg_element, cfg_factory)

    if len(local_cfg_files) > 1:
        raise RuntimeError("Config elements with more than one local source file are not supported")

    if not (src_file := next((f for f in local_cfg_files), None)):
        raise RuntimeError(f"No local source file known for cfg type '{cfg_element._type_name}'")

    write_named_elements(elements, cfg_dir, src_file)

    cfg_metadata.queue.append(
        create_config_queue_entry(
            queue_entry_config_element=cfg_element,
            queue_entry_data=secret_id,
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

    local_cfg_files = local_cfg_type_sources(cfg_element, cfg_factory)

    if len(local_cfg_files) > 1:
        logger.warning("Config elements with more than one local source file are not supported")
        return False
    if not local_cfg_files:
        logger.warning(f"No local source file known for cfg type '{cfg_element._type_name}'")
        return False

    if ret_vals := cmro.rotate_cfg_element(
        cfg_factory=cfg_factory,
        cfg_element=cfg_element,
    ):
        revert_function, secret_id, updated_elem = ret_vals
    else:
        return False

    try:
        write_changes_to_local_dir(
            cfg_element=updated_elem,
            cfg_factory=cfg_factory,
            secret_id=secret_id,
            cfg_metadata=cfg_metadata,
            cfg_dir=cfg_dir,
        )
        git_helper.add_and_commit(
            message=f'rotate secret for {cfg_element._type_name}/{cfg_element.name()}',
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

    if not cmro.delete_expired_secret(
        cfg_element=cfg_element,
        cfg_queue_entry=cfg_queue_entry,
        cfg_factory=cfg_factory,
    ):
        return False

    cfg_metadata.queue.remove(cfg_queue_entry)
    write_config_queue(
        cfg_dir=cfg_dir,
        cfg_metadata=cfg_metadata,
    )

    try:
        git_helper.add_and_commit(
            message=f'process config queue for {cfg_element._type_name}/{cfg_element.name()}',
        )
        git_helper.push('@', target_ref)
    except:
        logger.warning('failed to push processed config queue - reverting')
        git_helper.repo.git.reset('--hard', '@~')

    return True
