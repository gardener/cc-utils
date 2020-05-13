import subprocess

import concourse.model.traits.version as version_trait


def read_version(
    version_interface: version_trait.VersionInterface,
    path: str,
):
    if version_interface is version_trait.VersionInterface.FILE:
        with open(path) as f:
            return f.read()
    elif version_interface is version_trait.VersionInterface.CALLBACK:
        res = subprocess.run(
            [path],
            capture_output=True,
            check=True,
            text=True,
        )

        version_str = res.stdout.strip()

        return version_str
    else:
        raise NotImplementedError


def write_version(
    version_interface: version_trait.VersionInterface,
    version_str: str,
    path: str,
):
    version_str = version_str.strip()

    if version_interface is version_trait.VersionInterface.FILE:
        with open(path, 'w') as f:
            f.write(version_str)
        return
    elif version_interface is version_trait.VersionInterface.CALLBACK:
        subprocess.run(
            [path],
            check=True,
            text=True,
            input=version_str,
        )
    else:
        raise NotImplementedError
