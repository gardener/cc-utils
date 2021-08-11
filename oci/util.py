import contextlib
import io
import queue
import threading
import typing


def normalise_image_reference(image_reference: str):
  if not isinstance(image_reference, str):
    raise ValueError(image_reference)

  parts = image_reference.split('/')

  left_part = parts[0]
  # heuristically check if we have a (potentially) valid hostname
  if '.' not in left_part.split(':')[0]:
    # insert 'library' if only image name was given
    if len(parts) == 1:
      parts.insert(0, 'library')

    # probably, the first part is not a hostname; inject default registry host
    parts.insert(0, 'registry-1.docker.io')

  # of course, docker.io gets special handling
  if parts[0] == 'docker.io':
      parts[0] = 'registry-1.docker.io'

  return '/'.join(parts)


def urljoin(*parts):
    if len(parts) == 1:
        return parts[0]
    first = parts[0]
    last = parts[-1]
    middle = parts[1:-1]

    first = first.rstrip('/')
    middle = list(map(lambda s: s.strip('/'), middle))
    last = last.lstrip('/')

    return '/'.join([first] + middle + [last])


class _TeeFilelikeProxy:
    '''
    Takes a filelike object (which may be a non-seekable stream) and patches its
    read-method in such a way that upon every read, a copy of the read data is emitted
    through a generator.

    The generator will signal to be exhausted after the passed filelike object has been
    read until EOF.

    Note that the generator *MUST* be consumed in a different thread. Also note that if the
    generator is _not_ consumed, every stream read chunk will permanently be stored in
    this object's queue.

    In the context of the oci package, this is particularly useful for the special-case
    of converting container images w/ a legacy "v1"-manifest to the v2-version, as we need
    to both re-upload the gzip-compressed layer-blobs unmodified, but also calculate digest-hashes
    for uncompressed versions.
    '''
    def __init__(self, fileobj):
        self._queue = queue.Queue() # XXX: we might limit queue-size (could cause deadlocks, though)
        self._fp = fileobj

        # patch original read-method
        self._orig_read = fileobj.read
        self._fp.read = self._tee_read

    def _tee_read(self, size=-1):
        buf = self._orig_read(size)
        self._queue.put(buf)

        return buf

    def iter_contents(self) -> typing.Generator[bytes, None, None]:
        while (buf := self._queue.get()):
            yield buf


@contextlib.contextmanager
def tee_stream(
    fileobj: io.BytesIO,
    tee_receiver: typing.Callable[[typing.Generator[bytes, None, None]], None],
):
    '''
    ctx-mgr creating a "tee" for a given stream that will yield read chunks to the given
    `tee_receiver` callable. The passed-in stream patched and yielded back.

    The generator-receiving callback is executed in a thread, which will be joined when
    existing the managed ctx. As the thread will only terminate once the generator is
    exhausted (which in turn only happens if the stream is exhaused), the stream must
    be fully consumed before exiting the ctx.
    '''
    proxy = _TeeFilelikeProxy(fileobj=fileobj)

    def pass_tee_content_generator():
        tee_receiver(proxy.iter_contents())

    tee_receiver_thread = threading.Thread(
        target=pass_tee_content_generator,
        daemon=True,
    )

    try:
        tee_receiver_thread.start()
        yield fileobj
    finally:
        tee_receiver_thread.join()
