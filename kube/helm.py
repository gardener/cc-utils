import logging
import os
import subprocess
import tempfile
import yaml

from collections import namedtuple

import ci.log
import kube.ctx

from ci.util import which
from model.kubernetes import KubernetesConfig


logger = logging.getLogger(__name__)
ci.log.configure_default_logging()

CONCOURSE_HELM_CHART_REPO = "https://concourse-charts.storage.googleapis.com/"
BasicAuthCred = namedtuple('BasicAuthCred', ['user', 'password'])


# Stuff used for yaml formatting, when dumping a dictionary
class LiteralStr(str):
    """Used to create yaml block style indicator | """


def literal_str_representer(dumper, data):
    """Used to create yaml block style indicator"""
    return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')


def ensure_helm_setup():
    """Ensure up-to-date helm installation. Return the path to the found Helm executable"""

    helm_executable = which('helm')
    with open(os.devnull) as devnull:
        subprocess.run(
            [helm_executable, 'repo', 'add', 'concourse', CONCOURSE_HELM_CHART_REPO],
            check=True,
            stdout=devnull
        )
        subprocess.run([helm_executable, 'repo', 'update'], check=True, stdout=devnull)
    return helm_executable


def execute_helm_deployment(
    kubernetes_config: KubernetesConfig,
    namespace: str,
    chart_name: str,
    release_name: str,
    *values: dict,
    chart_version: str = None,
):
    yaml.add_representer(LiteralStr, literal_str_representer)
    helm_executable = ensure_helm_setup()

    kubeconfig_dict = kubernetes_config.kubeconfig()
    # create namespace if absent
    namespace_helper = kube.ctx.Ctx(
        kubeconfig_dict=kubeconfig_dict
    ).namespace_helper()
    namespace_helper.create_if_absent(namespace)

    KUBECONFIG_FILE_NAME = "kubecfg"

    # prepare subprocess args using relative file paths for the values files
    subprocess_args = [
        helm_executable,
        "upgrade",
        release_name,
        chart_name,
        "--install",
        "--force",
        "--namespace",
        namespace,
    ]

    if chart_version:
        subprocess_args += ["--version", chart_version]

    for idx, _ in enumerate(values):
        subprocess_args.append("--values")
        subprocess_args.append("value" + str(idx))

    helm_env = os.environ.copy()
    helm_env['KUBECONFIG'] = KUBECONFIG_FILE_NAME

    # create temp dir containing all previously referenced files
    with tempfile.TemporaryDirectory() as temp_dir:
        for idx, value in enumerate(values):
            with open(os.path.join(temp_dir, "value" + str(idx)), 'w') as f:
                yaml.dump(value, f)

        with open(os.path.join(temp_dir, KUBECONFIG_FILE_NAME), 'w') as f:
            yaml.dump(kubeconfig_dict, f)

        # run helm from inside the temporary directory so that the prepared file paths work
        subprocess.run(subprocess_args, check=True, cwd=temp_dir, env=helm_env)
