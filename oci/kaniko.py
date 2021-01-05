'''
utils for processing image-tar as created by kaniko

see:
    https://github.com/GoogleContainerTools/kaniko
'''

import contextlib
import dataclasses
import io
import json
import tarfile
import threading
import typing

import dacite

import oci.model


@dataclasses.dataclass
class KanikoManifest:
  Config: str # <algorithm>:<digest>
  RepoTags: typing.List[str]
  Layers: typing.List[str]


@dataclasses.dataclass
class _KanikoBlob(io.BytesIO):
  read_chunk: typing.Callable[[int, int], bytes] # see _KanikoImageReadCtx._read_chunk
  offset: int
  name: str
  size: int
  hash_algorithm: str = 'sha256'
  seek_pos = 0

  def __post_init__(self):
    self.__iter__ = self.iter_contents

  def __len__(self):
    return self.size

  def read(self, size: int=-1):
    if size == -1:
      offset = self.seek_pos + self.offset
      self.seek_pos = size - 1

      return self.read_chunk(
        offset=offset,
        length=self.size,
      )

    if self.seek_pos + 1 == self.size:
        return b''

    offset = self.offset + self.seek_pos
    length = min(size, self.size - self.seek_pos)
    self.seek_pos += size
    if self.seek_pos + 1 > self.size:
        self.seek_pos = self.size - 1

    return self.read_chunk(
        offset=offset,
        length=length,
    )

  def tell(self):
    return self.seek_pos

  def iter_contents(self, chunk_size=1024 * 1024):
    remaining = self.size
    read = 0

    while remaining > 0:
      this_chunk = min(chunk_size, remaining)

      yield self.read_chunk(
        offset=self.offset + read,
        length=this_chunk
      )

      remaining -= this_chunk
      read += this_chunk

  def digest_hash(self):
    if ':' in self.name:
      return self.name.split(':')[-1]
    elif '.' in self.name:
      return self.name.split('.')[0]

  def digest_str(self):
    return f'{self.hash_algorithm}:{self.digest_hash()}'.strip()


class _KanikoImageReadCtx:
  def __init__(
      self,
      img_tarfile: tarfile.TarFile,
  ):
    self.tarfile = img_tarfile
    self.fileobj = img_tarfile.fileobj
    self.kaniko_manifest = self._kaniko_manifest()
    self._lock = threading.Lock()

  def _kaniko_manifest(self):
    manifest_info = self.tarfile.getmember('manifest.json')
    self.fileobj.seek(manifest_info.offset_data)
    manifest_raw = self.fileobj.read(manifest_info.size)
    manifest_list = json.loads(manifest_raw.decode('utf-8'))

    if not (leng := len(manifest_list)) == 1:
      raise NotImplementedError(leng)

    return dacite.from_dict(
        data_class=KanikoManifest,
        data=manifest_list[0],
    )

  def _read_chunk(self, offset: int, length: int):
    with self._lock:
      self.fileobj.seek(offset)
      return self.fileobj.read(length)

  def cfg_blob(self):
    cfg_info = self.tarfile.getmember(name=self.kaniko_manifest.Config)

    return _KanikoBlob(
      read_chunk=self._read_chunk,
      offset=cfg_info.offset_data,
      name=cfg_info.name,
      size=cfg_info.size,
      hash_algorithm=cfg_info.name.split(':')[0],
    )

  def layer_blobs(self):
    for layer_name in self.kaniko_manifest.Layers:
      layer_info = self.tarfile.getmember(name=layer_name)

      yield _KanikoBlob(
        read_chunk=self._read_chunk,
        offset=layer_info.offset_data,
        name=layer_name,
        size=layer_info.size,
        hash_algorithm='sha256', # XXX hardcode for now
      )

  def blobs(self):
    yield self.cfg_blob()
    yield from self.layer_blobs()

  def oci_manifest(self):
    cfg = self.cfg_blob()

    return oci.model.OciImageManifest(
      config=oci.model.OciBlobRef(
        digest=cfg.digest_str(),
        mediaType='application/json',
        size=cfg.size,
      ),
      layers=[
        oci.model.OciBlobRef(
          digest=layer.digest_str(),
          mediaType='application/data', # XXX actually, it is tar
          size=layer.size,
        ) for layer in self.layer_blobs()
      ],
    )


@contextlib.contextmanager
def read_kaniko_image_tar(tar_path: str):
  '''
  @param tar_path: path to image-tar created by kaniko
  '''
  with tarfile.open(name=tar_path, mode='r:*') as tf:
    yield _KanikoImageReadCtx(img_tarfile=tf)
