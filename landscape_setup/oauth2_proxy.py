# Copyright (c) 2019-2020 SAP SE or an SAP affiliate company. All rights reserved. This file is
# licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from ensure import ensure_annotations

from ci.util import (
    ctx as global_ctx,
    not_empty,
)
from landscape_setup import kube_ctx
from landscape_setup.utils import (
    execute_helm_deployment,
)
from model.oauth2_proxy import Oauth2ProxyConfig
from model.ingress import IngressConfig


@ensure_annotations
def create_oauth2_proxy_helm_values(
    oauth2_proxy_config: Oauth2ProxyConfig,
    ingress_config: IngressConfig,
    deployment_name: str,
    config_factory,
):
    oauth2_proxy_chart_config = oauth2_proxy_config.oauth2_proxy_chart_config()
    github_oauth_cfg = oauth2_proxy_config.github_oauth_config()
    github_cfg = global_ctx().cfg_factory().github(github_oauth_cfg.github_cfg_name())
    ingress_host = oauth2_proxy_config.ingress_host(config_factory)

    helm_values = {
        'config': {
            'clientID': github_oauth_cfg.client_id(),
            'clientSecret': github_oauth_cfg.client_secret(),
            'cookieSecret': oauth2_proxy_chart_config.cookie_secret(),
            # configFile is expected with yamls '|-' syntax, i.e. newlines except for the last line
            'configFile': '\n'.join([
                'provider = "github"',
                'email_domains = [ "*" ]',
                'upstreams = [ "file:///dev/null" ]',
                f'cookie_name = "{oauth2_proxy_chart_config.cookie_name()}"',
                f'github_org = "{github_oauth_cfg.github_org()}"',
                f'github_team = "{github_oauth_cfg.github_team()}"',
                f'login_url = "{github_cfg.http_url()}/login/oauth/authorize"',
                f'redeem_url = "{github_cfg.http_url()}/login/oauth/access_token"',
                f'validate_url = "{github_cfg.api_url()}"',
                f'ssl_insecure_skip_verify = {str(github_oauth_cfg.no_ssl_verify()).lower()}',
                'whitelist_domains = ".gardener.cloud"',
                'cookie_domain = ".gardener.cloud"',
            ])
        },
        'ingress': {
            'enabled': True,
            'path': "/",
            'annotations': {
                'kubernetes.io/ingress.class': 'nginx',
                'kubernetes.io/tls-acme': "true",
                'cert.gardener.cloud/issuer': ingress_config.issuer_name(),
                'cert.gardener.cloud/purpose': 'managed',
                'dns.gardener.cloud/class': 'garden',
                'dns.gardener.cloud/dnsnames': ingress_host,
                'dns.gardener.cloud/ttl': str(ingress_config.ttl()),
            },
            'hosts': [ingress_host, oauth2_proxy_config.external_url()],
            'tls': [{
                'hosts': ingress_config.tls_host_names(),
                'secretName': f'{deployment_name}-tls'
            }],
        },
    }

    return helm_values


@ensure_annotations
def deploy_oauth2_proxy(
    oauth2_proxy_config: Oauth2ProxyConfig,
    deployment_name: str,
):
    not_empty(deployment_name)

    cfg_factory = global_ctx().cfg_factory()

    kubernetes_config = cfg_factory.kubernetes(oauth2_proxy_config.kubernetes_config_name())
    kube_ctx.set_kubecfg(kubernetes_config.kubeconfig())

    ingress_config = cfg_factory.ingress(oauth2_proxy_config.ingress_config())
    helm_values = create_oauth2_proxy_helm_values(
        oauth2_proxy_config=oauth2_proxy_config,
        ingress_config=ingress_config,
        deployment_name=deployment_name,
        config_factory=cfg_factory,
    )

    execute_helm_deployment(
        kubernetes_config,
        oauth2_proxy_config.namespace(),
        'stable/oauth2-proxy',
        deployment_name,
        helm_values,
    )
