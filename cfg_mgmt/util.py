import os.path
import typing

import cfg_mgmt.model as cmm
import cfg_mgmt.reporting as cmr
import ci.util
import model


def generate_cfg_element_status_reports(cfg_dir: str) -> list[cmr.CfgElementStatusReport]:
    ci.util.existing_dir(cfg_dir)

    cfg_factory = model.ConfigFactory._from_cfg_dir(
        cfg_dir,
        disable_cfg_element_lookup=True,
    )

    policies = cmm.cfg_policies(
        policies=cmm._parse_cfg_policies_file(
            path=os.path.join(cfg_dir, cmm.cfg_policies_fname),
        )
    )
    rules = cmm.cfg_rules(
        rules=cmm._parse_cfg_policies_file(
            path=os.path.join(cfg_dir, cmm.cfg_policies_fname),
        )
    )
    statuses = cmm.cfg_status(
        status=cmm._parse_cfg_status_file(
            path=os.path.join(cfg_dir, cmm.cfg_status_fname),
        )
    )
    responsibles = cmm.cfg_responsibles(
        responsibles=cmm._parse_cfg_responsibles_file(
            path=os.path.join(cfg_dir, cmm.cfg_responsibles_fname),
        )
    )

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
        yield from cfg_factory._cfg_elements(cfg_type_name=type_name)


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
