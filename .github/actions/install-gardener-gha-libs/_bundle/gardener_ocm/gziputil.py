import struct
import time
import zlib

'''
some useful utils for creating standard-compliant GZIP-streams that also work w/ python's
`tarfile` module when read in streaming mode.

see https://docs.fileformat.com/compression/gz/
'''


def gzip_header(fname: bytes=b'', mtime: int=None) -> bytes:
    '''
    returns a gzip header with some hard-coded defaults;
    matches a gzip-stream created w/o header using zlib's defaults
    and wbits=-15 (which suppresses gzip header output)

    see https://docs.fileformat.com/compression/gz/#gz-file-header
    '''
    buf = b''
    buf += b'\037\213' # magic
    buf += b'\010' # compression method
    buf += chr(8).encode('latin-1') # file-flags 0x08 -> contains fname str

    if not mtime:
        mtime = time.time()

    buf += struct.pack('<L', int(mtime))
    buf += b'\000' # compression level (default)
    buf += b'\377' # OS (unknown)
    buf += fname + b'\000'

    return buf


def gzip_footer(crc32: int, uncompressed_size: int) -> bytes:
    '''
    returns a gzip footer (8 octets) for the given source-file-data
    crc32 must be the crc32 checksum for the _source_ (uncompressed) data
    uncompressed_size must be the length in octets of the uncompressed data
    '''
    return struct.pack('<L', crc32) + \
        struct.pack('<L', uncompressed_size & 0xffffffff)


def zlib_compressobj():
    '''
    returns a zlib.compressobj suitable for being used w/ a gzip header as returned from
    the `gzip_header` function from this module.
    '''
    return zlib.compressobj(
        level=zlib.Z_DEFAULT_COMPRESSION,
        method=zlib.DEFLATED,
        wbits=-15, # disable header output
        memLevel=zlib.DEF_MEM_LEVEL,
        strategy=zlib.Z_DEFAULT_STRATEGY,
    )
