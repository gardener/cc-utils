import tarfile

import dacite

import version
import unixutil.model as um


def _parse_os_release(contents: str):
    for line in contents.split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue

        name, value = line.split('=', 1)

        yield (name, value.strip('"'))


def _parse_centos_release(contents: str):
    contents = contents.strip()
    lines = [line for line in contents.split('\n') if line]

    if len(lines) != 1:
        raise ValueError('expected a single line')

    line = lines[0]

    # expected format: "CentOS Linux release <version> (Core)"
    parts = line.split(' ')
    version = parts[3]

    yield ('VERSION_ID', version)


def _parse_debian_version(contents: str):
    contents = contents.strip()
    lines = [line for line in contents.split('\n') if line]

    if len(lines) != 1:
        raise ValueError('expected a single line')

    # file is expected to contain exactly the version
    yield ('VERSION_ID', lines[0])


def determine_osinfo(tarfh: tarfile.TarFile) -> um.OperatingSystemId:
    '''
    tries to determine the operating system identification, roughly as specified by
        https://www.freedesktop.org/software/systemd/man/os-release.html
    and otherwise following some conventions believed to be common.

    The argument (an opened tarfile) is being read from its initial position, possibly (but
    not necessarily) to the end. The underlying stream does not need to be seekable.
    It is the caller's responsibility to close the tarfile handle after this function returns.

    The tarfile is expected to contain a directory tree from a "well-known" unix-style operating
    system distribution. In particular, the following (GNU/) Linux distributions are well-supported:
    - alpine
    - debian
    - centos

    In case nothing was recognised within the given tarfile, the returned OperatingSystemId's
    attributes will all be `None`.
    '''
    known_fnames = (
        'debian_version',
        'centos-release',
        'os-release',
    )

    os_info = {}

    for info in tarfh:
        fname = info.name.split('/')[-1]

        if not fname in known_fnames:
            continue

        if info.issym():
            # we assume fnames are the same (this assumption might not always be correct)
            continue

        if not info.isfile():
            continue

        # found an "interesting" file
        contents = tarfh.extractfile(info).read().decode('utf-8')

        if fname == 'os-release':
            for k,v in _parse_os_release(contents):
                if k in os_info:
                    if k == 'VERSION_ID' and version.is_semver_parseable(v) and \
                        not version.is_semver_parseable(os_info[k]):
                        pass
                    else:
                        continue # os-release has lesser precedence
                os_info[k] = v
            if os_info.get('ID') == 'ubuntu' and (ver := os_info.get('VERSION')):
                # of _course_ ubuntu requires a special hack
                os_info['VERSION_ID'] = ver.split(' ', 1)[0]
        elif fname == 'centos-release':
            for k,v in _parse_centos_release(contents):
                os_info[k] = v
        elif fname == 'debian_version':
            for k,v in _parse_debian_version(contents):
                if k in os_info:
                    if not version.is_semver_parseable(v):
                        continue # e.g. ubuntu has "misleading" debian_version
                os_info[k] = v
        else:
            raise NotImplementedError(fname)

    return dacite.from_dict(
        data_class=um.OperatingSystemId,
        data=os_info,
    )
