import github3.exceptions
import github3.repos


class RefGuessingFailedError(Exception):
    pass


def guess_commit_from_source(
    artifact_name: str,
    github_repo: github3.repos.repo.Repository,
    ref: str,
    commit_hash: str=None,
):
    def in_repo(commit_ish):
        try:
            return github_repo.ref(commit_ish).object.sha
        except github3.exceptions.NotFoundError:
            pass

        try:
            return github_repo.commit(commit_ish).sha
        except (github3.exceptions.UnprocessableEntity, github3.exceptions.NotFoundError):
            return None

    # first guess: look for commit hash if defined
    if commit_hash:
        commit = in_repo(commit_hash)
        if commit:
            return commit

    # second guess: check for ref like 'refs/heads/main'
    if ref.startswith('refs/'):
        gh_ref = ref[len('refs/'):] # trim 'refs/' because of github3 api
        commit = in_repo(gh_ref)
        if commit:
            return commit
    else:
        commit = in_repo(ref)
        if commit:
            return commit

    # third guess: branch
    try:
        return github_repo.branch(ref).commit.sha
    except github3.exceptions.NotFoundError:
        pass

    # still unknown commit-ish throw error
    raise RefGuessingFailedError(
        f'failed to guess on ref for {artifact_name=} with {ref=}'
    )
