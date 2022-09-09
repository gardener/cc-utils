import logging
import tarfile
import typing

import ioutil

logger = logging.getLogger(__name__)


class FilelikeProxy:
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


def concat_blobs_as_tarstream(
    blobs: typing.Iterable[ioutil.BlobDescriptor],
) -> typing.Generator[bytes, None, None]:
    '''
    returns a generator yielding tarfile stream containing the passed blobs as members.

    In comparison to regularily creating a tarfile, member-contents are accepted as generators,
    thus allowing to concatenate multiple input streams into a concatenated output stream.
    '''
    offset = 0

    for idx, blob in enumerate(blobs):
        name = blob.name or f'{str(idx)}.tar'
        tarinfo = tarfile.TarInfo(name=name)
        tarinfo.size = blob.size
        tarinfo.offset = offset
        tarinfo.offset_data = offset + tarfile.BLOCKSIZE

        offset += blob.size + tarfile.BLOCKSIZE

        tarinfo_bytes = tarinfo.tobuf()
        yield tarinfo_bytes

        uploaded_bytes = len(tarinfo_bytes)

        for chunk in blob.content:
            uploaded_bytes += len(chunk)
            yield chunk

        # pad to full blocks
        if (missing := tarfile.BLOCKSIZE - (uploaded_bytes % tarfile.BLOCKSIZE)):
            offset += missing
            yield tarfile.NUL * missing

    # terminate tarchive w/ two empty blocks
    yield tarfile.NUL * tarfile.BLOCKSIZE * 2


def filtered_tarfile_generator(
    src_tf: tarfile.TarFile,
    filter_func: typing.Callable[[tarfile.TarInfo], bool]=lambda tarinfo: True,
    chunk_size=tarfile.BLOCKSIZE,
    chunk_callback: typing.Callable[[bytes], None]=None,
    tarinfo_callback: typing.Callable[[tarfile.TarInfo], tarfile.TarInfo] = lambda tarinfo: tarinfo,
    finalise: bool = True,
) -> typing.Generator[bytes, None, None]:
    '''
    returns a generator yielding a tarfile that will by default contain the same members as
    the passed tarfile (src_tf). If a filter-function is given, any entries (TarInfo objects)
    for which this function will return a "falsy" value will be removed from the resulting
    tarfile stream (which is the actual value-add from this function). Additionally, a callback
    function can be given that will be passed all TarInfo objects after filtering to perform
    modifications like renaming.

    This function is particularly useful for streaming. Note that _FilelikeProxy` can be used
    to wrap a generator yielding an (input-) tarfile-stream.

    In combination, this can be used to - in a streaming manner - retrieve a tarfile-stream, e.g.
    using a http-request (e.g. with requests), and upload the filtered tarfile-stream (e.g. again
    with a http-request send e.g. with requests).

    Finally, the `finalise` parameter controls whether the end-of-file marker (two 512-byte blocks
    filled with binary zeros) will be yielded by the returned generator. This can be used to combine
    several archives by by streaming their contents but only sending the EOF marker for the last
    one.
    '''
    offset = 0

    def filter_tarfile(
        src_tf: tarfile.TarFile,
        filter_func: typing.Callable[[tarfile.TarInfo], bool],
        chunk_size,
        chunk_callback: typing.Callable[[bytes], None],
        tarinfo_callback: typing.Callable[[tarfile.TarInfo], tarfile.TarInfo],
    ):
        nonlocal offset
        for member in src_tf:
            if not filter_func(member):
                logger.debug(f'filtered out {member=}')
                continue

            member = tarinfo_callback(member)

            # need to create a cp (to patch offsets w/o modifying original members, which would
            # break accessing file-contents)
            member_raw = member.tobuf()
            if len(member_raw) > tarfile.BLOCKSIZE:
                member_info = member.get_info()
                member_info['offset'] = offset
                member_info['offset_data'] = offset + len(member_raw)

                member_buf = member.create_pax_header(
                    info=member_info,
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

    yield from filter_tarfile(
        src_tf=src_tf,
        filter_func=filter_func,
        chunk_size=chunk_size,
        chunk_callback=chunk_callback,
        tarinfo_callback=tarinfo_callback,
    )

    if finalise:
        final_padding = 2 * tarfile.BLOCKSIZE * tarfile.NUL # tarfiles should end w/ two empty blocks
        yield final_padding
        if chunk_callback:
            chunk_callback(final_padding)
