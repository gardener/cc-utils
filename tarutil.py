import logging
import tarfile
import typing

logger = logging.getLogger(__name__)


class _FilelikeProxy:
    def __init__(self, generator):
        '''
        a fake filelike-object that will mimic the required behaviour (read) "good enough" for
        usage w/ tarfile.open (in stream-mode)
        '''
        self.generator = generator

    def read(self, size: int=-1):
        try:
            return next(self.generator)
        except StopIteration:
            return b''


def filtered_tarfile_generator(
    src_tf: tarfile.TarFile | typing.Iterable[tarfile.TarFile],
    filter_func: typing.Callable[[tarfile.TarInfo], bool]=lambda tarinfo: True,
    chunk_size=tarfile.BLOCKSIZE,
    chunk_callback: typing.Callable[[bytes], None]=None,
) -> typing.Generator[bytes, None, None]:
    '''
    returns a generator yielding a tarfile that will by default contain the same members as
    the passed tarfile(s) (src_tf). If a filter-function is given, any entries (TarInfo objects)
    for which this function will return a "falsy" value will be removed from the resulting
    tarfile stream (which is the actual value-add from this function).

    This function is particularly useful for streaming. Note that `_FilelikeProxy` can be used
    to wrap a generator yielding an (input-) tarfile-stream.

    In combination, this can be used to - in a streaming manner - retrieve a tarfile-stream, e.g.
    using a http-request (e.g. with requests), and upload the filtered tarfile-stream (e.g. again
    with a http-request send e.g. with requests).
    '''
    offset = 0

    def filter_tarfile(
        src_tf: tarfile.TarFile,
        filter_func: typing.Callable[[tarfile.TarInfo], bool],
        chunk_size,
        chunk_callback: typing.Callable[[bytes], None],
    ):
        nonlocal offset
        for member in src_tf:
            if not filter_func(member):
                logger.debug(f'filtered out {member=}')
                continue

            # need to create a cp (to patch offsets w/o modifying original members, which would
            # break accessing file-contents)
            member_raw = member.tobuf()
            if len(member_raw) > tarfile.BLOCKSIZE:
                member_info = member.get_info()
                member_info['offset'] = offset
                member_info['offset_data'] = offset + len(member_raw)

                member_buf = member.create_pax_header(
                    info=member.get_info(),
                    encoding=tarfile.ENCODING
                )
            else:
                member_cp = tarfile.TarInfo.frombuf(
                    member_raw,
                    encoding=tarfile.ENCODING,
                    errors='surrogateescape',
                )
                member_cp.offset = offset
                member_cp.offset_data = offset + len(member_raw)

                member_buf = member_cp.tobuf()

            if chunk_callback:
                chunk_callback(member_buf)
            yield member_buf
            offset += tarfile.BLOCKSIZE

            if member.size > 0:
                if member.isfile():
                    fobj = src_tf.extractfile(member=member)
                    octets_sent = 0
                    octets_left = member.size
                    while octets_left and (chunk := fobj.read(min(octets_left, chunk_size))):
                        offset += (leng := len(chunk))
                        octets_sent += leng
                        octets_left -= leng
                        if chunk_callback:
                            chunk_callback(chunk)
                        yield chunk

                    # pad to full 512-blocks if member is not "aligned"
                    if member.size % tarfile.BLOCKSIZE == 0:
                        continue
                    if (missing := tarfile.BLOCKSIZE - (octets_sent % tarfile.BLOCKSIZE)):
                        padding = tarfile.NUL * missing
                        if chunk_callback:
                            chunk_callback(padding)
                        yield padding
                        offset += missing
                else:
                    # TODO: handle symlinks (will not work for streaming-mode)
                    raise NotImplementedError(member.type)

    if isinstance(src_tf, tarfile.TarFile):
        yield from filter_tarfile(
            src_tf=src_tf,
            filter_func=filter_func,
            chunk_size=chunk_size,
            chunk_callback=chunk_callback,
        )
    elif isinstance(src_tf, typing.Iterable):
        for tf in src_tf:
            yield from filter_tarfile(
                src_tf=tf,
                filter_func=filter_func,
                chunk_size=chunk_size,
                chunk_callback=chunk_callback,
            )
    else:
        raise ValueError(src_tf)

    final_padding = 2 * tarfile.BLOCKSIZE * tarfile.NUL # tarfiles should end w/ two empty blocks
    yield final_padding
    if chunk_callback:
        chunk_callback(final_padding)
