import collections.abc
import contextlib
import io
import queue
import re
import threading

# OCI Image Reference Spec; leading `/` is additionally permitted as a local/relative ref
_OCI_IMAGE_REF_RE = re.compile(
    r'^(?!.*://)[^:?#][^?#]*(?:/[^?#]*)*(?::[^:/@][^@]*)?(?:@[a-zA-Z][a-zA-Z0-9]*:[a-fA-F0-9]+)?$'
)


def normalise_image_reference(image_reference: str):
    if not isinstance(image_reference, str):
        raise ValueError(image_reference)

    if image_reference.startswith('/'):
        return image_reference

    if not _OCI_IMAGE_REF_RE.match(image_reference):
        raise ValueError(f'not a valid OCI image reference: {image_reference!r}')

    parts = image_reference.split('/')

    left_part = parts[0]
    host_part = left_part.split(':')[0]
    # heuristically check if we have a (potentially) valid hostname
    if '.' not in host_part and host_part != 'localhost':
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


def _netloc_and_path_segments(
    image_reference: str,
    is_prefix: bool=False,
) -> tuple[str, list[str]]:
    '''
    returns a (netloc, path-segments) tuple for the given image reference, used for
    component-wise credential-scope matching.

    The netloc is lower-cased (OCI hostnames are case-insensitive); path segments keep their
    case (repository paths are case-sensitive per the OCI spec).

    A dot-less first segment carrying a `:` (e.g. `myregistry:5000`) is ambiguous: syntactically
    it is a docker-hub image with a tag, but as a registry host it is `host:port`. It is treated
    as a `host:port` when a path follows (`myregistry:5000/x`), or when `is_prefix` is set (a
    configured credential prefix is always a registry location, never an image+tag). Otherwise it
    falls through to normalisation. `localhost[:port]` is already preserved by normalisation.
    '''
    # defensively strip a leading scheme (e.g. 'https://index.docker.io/v1/'); OCI references
    # never carry one, but externally-supplied prefixes occasionally do
    if '://' in image_reference:
        image_reference = image_reference.split('://', 1)[1]

    # a digest (@sha256:...) on a host-only reference (no repository path) would otherwise be
    # read as part of the host by urlparse ('eu.gcr.io@sha256:...' -> host 'eu.gcr.io@sha256:..');
    # drop it before host detection, as it never belongs to the netloc
    if '/' not in image_reference:
        image_reference = image_reference.split('@', 1)[0]

    first_segment = image_reference.split('/')[0]
    host_part = first_segment.split(':')[0]
    has_path = '/' in image_reference

    if (
        '.' not in host_part
        and host_part != 'localhost'
        and ':' in first_segment
        and (has_path or is_prefix)
    ):
        # dot-less host with explicit port, as a registry location: trust the raw reference
        # rather than normalising, which would otherwise inject the default docker-hub host
        netloc = first_segment
        path = image_reference[len(first_segment):].lstrip('/')
    else:
        # imported lazily to avoid a circular import (oci.model imports oci.util at module level)
        import oci.model
        parsed = oci.model.OciImageReference(image_reference, normalise=True)
        netloc = parsed.netloc
        path = parsed.name

    segments = [segment for segment in path.split('/') if segment]
    return netloc.lower(), segments


def image_reference_matches_prefix(image_reference: str, prefix: str) -> bool:
    '''
    returns whether the given image reference is scoped by the given prefix using component-wise
    matching: the netloc (host and port) must be equal, and the prefix's repository path must be
    a whole-segment prefix of the reference's path. A host-only prefix matches any repository on
    that exact host.

    This is the single credential-scope matching convention; raw `startswith` matching must not
    be reintroduced, as it allows lookalike hosts (eu.gcr.io.attacker.example) and sibling paths
    (team-evil) to match a trusted prefix.
    '''
    # local/relative references cannot be scoped to a host-bearing prefix
    if image_reference.startswith('/'):
        return False

    # a malformed reference or prefix must not raise: credential selection then fails closed
    # (no match) for that prefix, rather than aborting matching for every registry via the
    # caller's generator (model.container_registry.find_config)
    try:
        ref_netloc, ref_segments = _netloc_and_path_segments(image_reference)
        prefix_netloc, prefix_segments = _netloc_and_path_segments(prefix, is_prefix=True)
    except ValueError:
        return False

    if ref_netloc != prefix_netloc:
        return False

    # host-only prefix (no path segments) matches any repository on the host
    return ref_segments[:len(prefix_segments)] == prefix_segments


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

    def iter_contents(self) -> collections.abc.Generator[bytes, None, None]:
        while (buf := self._queue.get()):
            yield buf


@contextlib.contextmanager
def tee_stream(
    fileobj: io.BytesIO,
    tee_receiver: collections.abc.Callable[[collections.abc.Generator[bytes, None, None]], None],
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
