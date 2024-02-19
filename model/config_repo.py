import dataclasses
import dacite
import os

import ci.util


@dataclasses.dataclass
class SecretsServerTarget:
    type: str
    secrets_server_config: str
    secret_config: str
    kubernetes_config_name: str
    team_config: str


@dataclasses.dataclass
class KubernetesSecretTarget:
    type: str
    kubernetes_config_name: str
    secret_namespace: str
    secret_name: str
    secret_key: str


@dataclasses.dataclass
class ReplicationMapping:
    target: SecretsServerTarget | KubernetesSecretTarget
    cfg_set: str


@dataclasses.dataclass
class ReplicationTargetConfig:
    replication_mappings: list[ReplicationMapping]


def replication_config_from_cfg_dir(cfg_dir: str) -> ReplicationTargetConfig:
    replication_config = ci.util.parse_yaml_file(
        os.path.join(cfg_dir, 'config_replication_targets.yaml')
    )
    return dacite.from_dict(
        data_class=ReplicationTargetConfig,
        data=replication_config,
        config=dacite.Config(),
    )
