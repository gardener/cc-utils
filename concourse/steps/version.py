import subprocess

import ccc.github
import ci.util
import concourse.model.traits.version as version_trait


def has_version_conflict(
    target_tag: str,
    repository_name: str,
    repository_org: str,
    repository_hostname: str,
):
    github_cfg = ccc.github.github_cfg_for_repo_url(
        repo_url=ci.util.urljoin(repository_hostname, repository_org, repository_name),
    )
    github_api = ccc.github.github_api(github_cfg)

    target_tag = target_tag.removeprefix('refs/tags/')

    repository = github_api.repository(repository_org, repository_name)
    for tag in repository.tags():
        if tag.name == target_tag:
            return True

    return False


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
