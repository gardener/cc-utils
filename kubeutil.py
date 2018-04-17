# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

import base64
import json
import os
import re
import time
import math
import shlex

from ensure import ensure, ensure_annotations
from urllib3.exceptions import ReadTimeoutError
from urllib3.exceptions import ProtocolError

from kubernetes import config, client, watch
from kubernetes.client.rest import ApiException
from kubernetes.client import (
    CoreV1Api, AppsV1Api, ExtensionsV1beta1Api, V1ObjectMeta, V1Secret, V1ServiceAccount,
    V1LocalObjectReference, V1Namespace, V1Service, V1Deployment,
    V1beta1Ingress,
)
import kubernetes.client
from kubernetes.config.kube_config import KubeConfigLoader

from util import fail, info, ensure_file_exists, ensure_not_empty, ensure_not_none
from util import ctx as global_ctx

class Ctx(object):
    '''
    handles the execution context of kubernetes-api calls.
    Most prominently the retrieval of the 'kubeconfig' to use, which is
    either passed via CLI (--kubeconfig) or via env var KUBECONFIG.
    '''
    def __init__(self, kubeconfig_dict: dict=None):
        if not kubeconfig_dict:
            self.kubeconfig = None
            return

        configuration = kubernetes.client.Configuration()
        cfg_loader = KubeConfigLoader(dict(kubeconfig_dict))
        cfg_loader.load_and_set(configuration)
        # pylint: disable=no-member
        kubernetes.client.Configuration.set_default(configuration)
        # pylint: enable=no-member
        self.kubeconfig = configuration

    def get_kubecfg(self):
        if self.kubeconfig:
            return kubernetes.client.ApiClient(configuration=self.kubeconfig)
        kubeconfig = os.environ.get('KUBECONFIG', None)
        args = global_ctx().args
        if args and hasattr(args, 'kubeconfig') and args.kubeconfig:
            kubeconfig = args.kubeconfig
        if self.kubeconfig:
            kubeconfig = self.kubeconfig
        if not kubeconfig:
            fail('KUBECONFIG env var must be set')
        return config.load_kube_config(ensure_file_exists(kubeconfig))

    def secret_helper(self) -> 'KubernetesSecretHelper':
        return KubernetesSecretHelper(self.create_core_api())

    def service_account_helper(self) -> 'KubernetesServiceAccountHelper':
        return KubernetesServiceAccountHelper(self.create_core_api())

    def namespace_helper(self) -> 'KubernetesNamespaceHelper':
        return KubernetesNamespaceHelper(self.create_core_api())

    def service_helper(self) -> 'KubernetesServiceHelper':
        return KubernetesServiceHelper(self.create_core_api())

    def deployment_helper(self) -> 'KubernetesDeploymentHelper':
        return KubernetesDeploymentHelper(self.create_apps_api())

    def ingress_helper(self) -> 'KubernetesIngressHelper':
        return KubernetesIngressHelper(self.create_extensions_v1beta1_api())

    def create_core_api(self):
        cfg = self.get_kubecfg()
        return client.CoreV1Api(cfg)

    def create_rbac_api(self):
        cfg = self.get_kubecfg()
        return client.RbacAuthorizationV1beta1Api(cfg)

    def create_custom_api(self):
        cfg = self.get_kubecfg()
        return client.CustomObjectsApi(cfg)

    def create_apps_api(self):
        cfg = self.get_kubecfg()
        return client.AppsV1Api(cfg)

    def create_extensions_v1beta1_api(self):
        cfg = self.get_kubecfg()
        return client.ExtensionsV1beta1Api(cfg)

def __add_module_command_args(parser):
    parser.add_argument('--kubeconfig', required=False)
    return parser

class KubernetesSecretHelper(object):
    '''Helper class for handling kubernetes secret objects'''
    @ensure_annotations
    def __init__(self, core_api: CoreV1Api):
        self.core_api = core_api

    def create_gcr_secret(
        self,
        namespace: str,
        name: str,
        password: str,
        email: str,
        user_name: str='_json_key',
        server_url: str='https://eu.gcr.io'
      ):
        metadata = V1ObjectMeta(name=name, namespace=namespace)
        secret = V1Secret(metadata=metadata)

        auth = '{user}:{gcr_secret}'.format(
          user=user_name,
          gcr_secret=password
        )

        docker_config = {
          server_url: {
            'username': user_name,
            'email': email,
            'password': password,
            'auth': base64.b64encode(auth.encode('utf-8')).decode('utf-8')
          }
        }

        encoded_docker_config = base64.b64encode(
          json.dumps(docker_config).encode('utf-8')
        ).decode('utf-8')

        secret.data = {
          '.dockercfg': encoded_docker_config
        }
        secret.type = 'kubernetes.io/dockercfg'

        self.core_api.create_namespaced_secret(namespace=namespace, body=secret)

    def put_secret(self, name: str, data: dict, namespace: str='default'):
        '''creates or updates (replaces) the specified secret.
        the secret's contents are expected in a dictionary containing only scalar values.
        In particular, each value is converted into a str; the result returned from
        to-str conversion is encoded as a utf-8 byte array. Thus such a conversion must
        not have done before.
        '''
        ne = ensure_not_empty
        metadata = V1ObjectMeta(name=ne(name), namespace=ne(namespace))

        secret_data = {
            k: base64.b64encode(str(v).encode('utf-8')).decode('utf-8')
            for k,v in data.items()
        }

        secret = V1Secret(metadata=metadata, data=secret_data)

        # find out whether we have to replace or to create
        try:
            self.core_api.read_namespaced_secret(name=name, namespace=namespace)
            secret_exists = True
        except ApiException as ae:
            # only 404 is expected
            if not ae.status == 404:
                raise ae
            secret_exists = False

        if secret_exists:
            self.core_api.replace_namespaced_secret(name=name, namespace=namespace, body=secret)
        else:
            self.core_api.create_namespaced_secret(namespace=namespace, body=secret)

    def get_secret(self, name: str, namespace: str) -> V1Secret:
        '''Returns the `V1Secret` with the given name in the given namespace, or `None`'''
        try:
            secret = self.core_api.read_namespaced_secret(name=name, namespace=namespace)
        except ApiException as ae:
            if not ae.status == 404:
                raise ae
            else:
                return None
        return secret


class KubernetesServiceAccountHelper(object):
    '''Helper class for kubernetes service-account objects'''
    def __init__(self, core_api: CoreV1Api):
        self.core_api = core_api

    def patch_image_pull_secret_into_service_account(
        self, name: str,
        namespace: str,
        image_pull_secret_name: str
      ):
        '''Patches the given (by name) image-pull-secret into the specified service-account.'''
        service_account = V1ServiceAccount()
        reference = V1LocalObjectReference()
        reference.name = image_pull_secret_name
        service_account.image_pull_secrets = [reference]
        self.core_api.patch_namespaced_service_account(name=name, namespace=namespace, body=service_account)


class KubernetesNamespaceHelper(object):
    '''Helper class for kubernetes namespace objects'''

    @ensure_annotations
    def __init__(self, core_api: CoreV1Api):
        self.core_api = core_api

    def create_namespace(self, namespace: str):
        '''Creates a new namespace and returns it'''
        ensure_not_empty(namespace)
        metadata = V1ObjectMeta(name=namespace)
        ns = V1Namespace(metadata=metadata)
        return self.core_api.create_namespace(ns)

    def create_if_absent(self, namespace: str):
        '''Create a new namespace iff it does not already exist'''
        ensure_not_empty(namespace)

        existing_namespace = self.get_namespace(namespace)
        if not existing_namespace:
            self.create_namespace(namespace)

    @ensure_annotations
    def delete_namespace(self, namespace: str):
        ensure_not_empty(namespace)
        self.core_api.delete_namespace(name=namespace, body={})

    def get_namespace(self, namespace: str):
        '''Returns the `V1Namespace` corresponding to the given name, or `None`'''
        for ns in self.core_api.list_namespace().items:
            # check if 'tis our namespace
            name = ns.metadata.name
            if not name == namespace:
                continue
            return ns
        return None

class KubernetesServiceHelper(object):
    def __init__(self, core_api: CoreV1Api):
        self.core_api = core_api

    def replace_or_create_service(self, namespace: str, service: V1Service):
        '''Create a service in a given namespace. If the service already exists,
        the previous version will be deleted beforehand
        '''
        ensure_not_empty(namespace)
        ensure_not_none(service)

        service_name = service.metadata.name
        existing_service = self.get_service(namespace=namespace, name=service_name)
        if existing_service:
            self.core_api.delete_namespaced_service(namespace=namespace, name=service_name)
        self.create_service(namespace=namespace, service=service)

    def create_service(self, namespace: str, service: V1Service):
        '''Create a service in a given namespace. Raises an `ApiException` if such a Service
        already exists.
        '''
        ensure_not_empty(namespace)
        ensure_not_none(service)

        self.core_api.create_namespaced_service(namespace=namespace, body=service)

    def get_service(self, namespace: str, name: str) -> V1Service:
        '''Return the `V1Service` with the given name in the given namespace, or `None` if
        no such service exists.
        '''
        ensure_not_empty(namespace)
        ensure_not_empty(name)

        try:
            service = self.core_api.read_namespaced_service(name=name, namespace=namespace)
        except ApiException as ae:
            if ae.status == 404:
                return None
            raise ae
        return service


class KubernetesDeploymentHelper(object):
    def __init__(self, apps_api: AppsV1Api):
        self.apps_api = apps_api

    def replace_or_create_deployment(self, namespace: str, deployment: V1Deployment):
        '''Create a deployment in a given namespace. If the deployment already exists,
        the previous version will be deleted beforehand.
        '''
        ensure_not_empty(namespace)
        ensure_not_none(deployment)

        deployment_name = deployment.metadata.name
        existing_deployment = self.get_deployment(namespace=namespace, name=deployment_name)
        if existing_deployment:
            self.apps_api.delete_namespaced_deployment(namespace=namespace, name=deployment_name, body=kubernetes.client.V1DeleteOptions())
        self.create_deployment(namespace=namespace, deployment=deployment)

    def create_deployment(self, namespace: str, deployment: V1Deployment):
        '''Create a deployment in a given namespace. Raises an `ApiException` if such a deployment
        already exists.'''
        ensure_not_empty(namespace)
        ensure_not_none(deployment)

        self.apps_api.create_namespaced_deployment(namespace=namespace, body=deployment)

    def get_deployment(self, namespace: str, name: str) -> V1Deployment:
        '''Return the `V1Deployment` with the given name in the given namespace, or `None` if
        no such deployment exists.'''
        ensure_not_empty(namespace)
        ensure_not_empty(name)

        try:
            deployment = self.apps_api.read_namespaced_deployment(name=name, namespace=namespace)
        except ApiException as ae:
            if ae.status == 404:
                return None
            raise ae
        return deployment

    def wait_until_deployment_available(self, namespace: str, name: str, timeout_seconds: int=60) -> bool:
        '''Block until the given deployment has at least one available replica or `timeout_seconds` seconds elapsed.
        Return `True` if the deployment is available, `False` if a timeout occured.
        '''
        ensure_not_empty(namespace)
        ensure_not_empty(name)

        w = watch.Watch()
        # Work around IncompleteRead errors resulting in ProtocolErrors - no fault of our own
        start_time = int(time.time())
        while (start_time + timeout_seconds) > time.time():
            try:
                for event in w.stream(
                    self.apps_api.list_namespaced_deployment,
                    namespace=namespace,
                    timeout_seconds=timeout_seconds
                ):
                    deployment_spec = event['object']
                    if deployment_spec is not None:
                        if deployment_spec.metadata.name == name:
                            if deployment_spec.status.available_replicas is not None and deployment_spec.status.available_replicas > 0:
                                return True
                    # Check explicitly if timeout occurred, since we might've been restarted due to a ProtocolError
                    if (start_time + timeout_seconds) < time.time():
                        return False
                # Regular Watch.stream() timeout occurred, no need for further checks
                return False
            except ProtocolError as err:
                info('http connection error - ignored')


class KubernetesIngressHelper(object):
    def __init__(self, extensions_v1beta1_api: ExtensionsV1beta1Api):
        self.extensions_v1beta1_api = extensions_v1beta1_api

    def replace_or_create_ingress(self, namespace: str, ingress: V1beta1Ingress):
        '''Create an ingress in a given namespace. If the ingress already exists,
        the previous version will be deleted beforehand.
        '''
        ensure_not_empty(namespace)
        ensure_not_none(ingress)

        ingress_name = ingress.metadata.name
        existing_ingress = self.get_ingress(namespace=namespace, name=ingress_name)
        if existing_ingress:
            self.extensions_v1beta1_api.delete_namespaced_ingress(namespace=namespace, name=ingress_name, body=kubernetes.client.V1DeleteOptions())
        self.create_ingress(namespace=namespace, ingress=ingress)

    def create_ingress(self, namespace: str, ingress: V1beta1Ingress):
        '''Create an ingress in a given namespace. Raises an `ApiException` if such an ingress
        already exists.'''
        ensure_not_empty(namespace)
        ensure_not_none(ingress)

        self.extensions_v1beta1_api.create_namespaced_ingress(namespace=namespace, body=ingress)

    def get_ingress(self, namespace: str, name: str) -> V1beta1Ingress:
        '''Return the `V1beta1Ingress` with the given name in the given namespace, or `None` if
        no such ingress exists.'''
        ensure_not_empty(namespace)
        ensure_not_empty(name)

        try:
            ingress = self.extensions_v1beta1_api.read_namespaced_ingress(name=name, namespace=namespace)
        except ApiException as ae:
            if ae.status == 404:
                return None
            raise ae
        return ingress


ctx = Ctx()

def create_namespace(namespace: str):
    namespace_helper = ctx.namespace_helper()
    return namespace_helper.create_namespace(namespace)

def delete_namespace(namespace):
    namespace_helper = ctx.namespace_helper()
    namespace_helper.delete_namespace(namespace)

def delete_namespace_unless_shoots_present(namespace):
    ensure_not_empty(namespace)
    custom_api = ctx.create_custom_api()

    result = custom_api.list_namespaced_custom_object(
      group='garden.sapcloud.io',
      version='v1',
      namespace=namespace,
      plural='shoots'
    )
    if len(result['items']) > 0:
        fail('namespace contained {} shoot(s) - not removing'.format(len(result)))

    delete_namespace(namespace)

def copy_secrets(from_ns: str, to_ns: str, secret_names: [str]):
    for arg in [from_ns, to_ns, secret_names]:
        ensure_not_empty(arg)
    info('args: from: {}, to: {}, names: {}'.format(from_ns, to_ns, secret_names))

    core_api = ctx.create_core_api()

    # new metadata used to overwrite the ones from retrieved secrets
    metadata = V1ObjectMeta(namespace=to_ns)

    for name in secret_names:
        secret = core_api.read_namespaced_secret(name=name, namespace=from_ns, export=True)
        metadata.name=name
        secret.metadata = metadata
        core_api.create_namespaced_secret(namespace=to_ns, body=secret)

def wait_for_ns(namespace):
    ensure_not_empty(namespace)

    core_api = ctx.create_core_api()
    w = watch.Watch()
    for e in w.stream(core_api.list_namespace, _request_timeout=120):
        # check if 'tis our namespace
        ns = e['object'].metadata.name
        if not ns == namespace:
            continue
        info(e['type'])
        if not e['type'] == 'ADDED':
            continue # ignore
        w.stop()

def wait_for_shoot_cluster_operation_success(namespace:str, shoot_name:str, optype:str, timeout_seconds:int=120):
    ensure_not_empty(namespace)
    ensure_not_empty(shoot_name)
    optype = ensure_not_empty(optype).lower()
    info('will wait for a maximum of {} minute(s) for cluster {} to reach state {}d'.format(
      math.ceil(timeout_seconds/60), shoot_name, optype)
    )

    def on_event(event)->(bool,str):
        shoot = event['object']
        status = shoot.get('status', None)
        if status:
            last_operation = status['lastOperation']
            operation_type = last_operation['type'].lower()
            operation_state = last_operation['state'].lower()
            operation_progress = last_operation['progress']
        else:
            # state is unknown, yet
            return False,'unknown'

        if shoot_name != shoot['metadata']['name']:
            return False,None
        if optype != operation_type:
            info('not in right optype: ' + operation_type)
            return False,operation_state
        # we reached the right operation type
        if operation_state == 'succeeded':
            return True,operation_state
        info('operation {} is {} - progress: {}%'.format(optype, operation_state, operation_progress))
        return False,operation_state

    try:
        _wait_for_shoot(namespace, on_event=on_event, expected_result='succeeded', timeout_seconds=timeout_seconds)
        info('Shoot cluster successfully reached state {}d'.format(optype))
    except RuntimeError as rte:
        fail('Shoot cluster reached a final error state: ' + str(rte))
    except ReadTimeoutError:
        fail('Shoot cluster did not reach state {}d within {} minute(s)'.format(optype, math.ceil(timeout_seconds/60)))

def wait_for_shoot_cluster_to_become_healthy(namespace:str, shoot_name:str, timeout_seconds:int=120):
    ensure_not_empty(namespace)
    ensure_not_empty(shoot_name)
    info('will wait for a maximum of {} minute(s) for cluster {} to become healthy'.format(
      math.ceil(timeout_seconds/60),
      shoot_name
      )
    )
    def on_event(event)->(bool,str):
        # TODO: this is copy-pasta from wait_for_shoot_cluster_operation_success
        #   --> remove redundancy
        shoot = event['object']
        status = shoot.get('status', None)
        if not status:
            # state is unknown, yet
            return False,'unknown'
        if shoot_name != shoot['metadata']['name']:
            return False,None

        conditions = status.get('conditions',None)
        if not conditions:
            return False,'unknown'

        health_status = [(c['type'],True if c['status'] == 'True' else False) for c in conditions]
        all_healthy = all(map(lambda s:s[1], health_status))
        if all_healthy:
            return True,'healthy'

        unhealthy_components = [c for c,s in health_status if not s]
        info('the following components are still unhealthy: {}'.format(' '.join(unhealthy_components)))
        return False,'unhealthy'

    try:
        _wait_for_shoot(namespace, on_event=on_event, expected_result='healthy', timeout_seconds=timeout_seconds)
        info('Shoot cluster became healthy')
    except RuntimeError as rte:
        fail('cluster did not become healthy')
    except ReadTimeoutError:
        fail('cluster did not become healthy within {} minute(s)'.format(math.ceil(timeout_seconds/60)))


def _wait_for_shoot(namespace, on_event, expected_result, timeout_seconds:int=120):
    ensure_not_empty(namespace)
    start_time = int(time.time())

    custom_api = ctx.create_custom_api()
    w = watch.Watch()
    # very, very sad: workaround until fixed:
    #    https://github.com/kubernetes-incubator/client-python/issues/124
    # (after about a minute, "some" watches (e.g. not observed when watching namespaces),
    # return without an error.
    # apart from being ugly, this has the downside that existing events will repeatedly be
    # received each time the watch is re-applied
    should_exit = False
    result = None
    while not should_exit and (start_time + timeout_seconds) > time.time():
        try:
            for e in w.stream(custom_api.list_namespaced_custom_object,
              group='garden.sapcloud.io',
              version='v1beta1',
              namespace=namespace,
              plural='shoots',
              # we need to reduce the request-timeout due to our workaround
              _request_timeout=(timeout_seconds - int(time.time() - start_time))
              ):
                should_exit,result = on_event(e)
                if should_exit:
                    w.stop()
                    if result != expected_result:
                        raise RuntimeError(result)
                    return
        except ConnectionResetError as cre:
            # ignore connection errors against k8s api endpoint (these may be temporary)
            info('connection reset error from k8s API endpoint - ignored: ' + str(cre))
        except ProtocolError as err:
            info('http connection error - ignored')
        except KeyError as err:
            info("key {} not yet available - ignored".format(str(err)))
    # handle case where timeout was exceeded, but w.stream returned erroneously (see bug
    # description above)
    raise RuntimeError(result)


def retrieve_controller_manager_log_entries(
  pod_name:str,
  namespace:str,
  only_if_newer_than_rfc3339_ts:str=None,
  filter_for_shoot_name:str=None,
  minimal_loglevel:str=None
  ):
    ensure_not_empty(namespace)
    ensure_not_empty(pod_name)
    if filter_for_shoot_name:
        ensure_not_empty(filter_for_shoot_name)

    kwargs = {'name': pod_name, 'namespace': namespace}

    passed_seconds = None
    if only_if_newer_than_rfc3339_ts:
        import dateutil.parser
        import time
        date = dateutil.parser.parse(only_if_newer_than_rfc3339_ts)
        # determine difference to "now"
        now = time.time()
        passed_seconds = int(now - date.timestamp())
        if passed_seconds < 1:
            passed_seconds = 1
        kwargs['since_seconds']=passed_seconds

    api = ctx.create_core_api()
    raw_log = api.read_namespaced_pod_log(
     **kwargs
    )

    # pylint: disable=no-member
    lines = raw_log.split('\n')
    # pylint: enable=no-member

    # filter our helm logs (format: yyyy/mm/dd ...)
    helm_log_re = re.compile(r'^\d{4}/\d{2}/\d{2}')
    lines = filter(lambda l: not helm_log_re.match(l), lines)
    def parse_line(line):
        parts = shlex.split(line)
        parts = filter(lambda s: '=' in s, parts)
        return dict(map(lambda s: s.split('=', 1), parts))
    parsed = map(parse_line, lines)
    if minimal_loglevel:
        log_levels = {'debug': 0, 'info': 1, 'warning': 2, 'error': 3, 'fatal': 4, 'panic': 5}
        minimal = log_levels[minimal_loglevel]
        parsed = filter(lambda p: log_levels[p.get('level', 'error')] >= minimal, parsed)
    if filter_for_shoot_name:
        parsed = filter(lambda p: p.get('shoot',None) == filter_for_shoot_name, parsed)
    for p in parsed:
        keys = ['time', 'level', 'msg', 'shoot']
        output = ' '.join([k + '="' + p.get(k, 'None') + '"' for k in keys])
        print(output)


def create_gcr_secret(
  namespace: str,
  name: str,
  secret_file: str,
  email: str,
  user_name: str='_json_key',
  server_url: str='https://eu.gcr.io'
):
    ensure_file_exists(secret_file)
    secret_helper = ctx.secret_helper()
    with open(secret_file, 'r') as fh:
        gcr_secret = fh.read()
        secret_helper.create_gcr_secret(
          namespace=namespace,
          name=name,
          password=gcr_secret,
          email=email,
          user_name=user_name,
          server_url=server_url
        )
