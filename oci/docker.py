'''
model classes and utils for dealing w/ legacy oci-images from legacy-version of docker
'''

import dataclasses
import datetime
import hashlib
import random

dc = dataclasses.dataclass


@dc
class DockerContainerCfg:
  Image: str # sha256-hash

  ArgsEscaped: bool = False
  AttachStderr: bool = False
  AttachStdin: bool = False
  AttachStdout: bool = False
  Cmd: list[str] | str | None = None
  Entrypoint: list[str] | str | None = None
  Domainname: str = ''
  Env: list[str] | None = (
      'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
  )
  Hostname: str = ''
  Labels: dict[str, str] | None = dataclasses.field(default_factory=dict)
  OnBuild: list[str] | None = None
  OpenStdin: bool = False
  StdinOnce: bool = False
  Tty: bool = False
  User: str = ''
  Volumes: list[str] | None = None
  WorkingDir: str = ''


@dc
class Docker_Fs:
    diff_ids: list[str] = dataclasses.field(default_factory=list) # [layer-sha256-digests]
    type: str = 'layers'


@dc
class DockerCfg:
  '''
  a cfg as created / understood by docker (use as cfg in oci-manifest)
  '''
  config: DockerContainerCfg
  container: str # container-hash
  container_config: DockerContainerCfg
  created: str # iso8601-ts
  rootfs: Docker_Fs | None

  architecture: str = 'amd64'
  docker_version: str = '18.09.7'
  history: tuple[dict] = ()
  os: str = 'linux'


def docker_cfg():
  now_ts = datetime.datetime.now().isoformat() + 'Z'
  container_id = hashlib.sha256(f'{random.randint(0, 2 ** 32)}'.encode('utf-8')).hexdigest()

  cfg = DockerContainerCfg(Image=f'sha256:{container_id}')

  return DockerCfg(
    config=cfg,
    container=container_id,
    container_config=cfg,
    created=now_ts,
    rootfs=Docker_Fs(), # TODO: check whether we need to pass-in layer-hash-digests
  )
