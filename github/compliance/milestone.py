import github3

import delivery.model


def _milestone_title(sprint: delivery.model.Sprint):
    return f'sprint-{sprint.name}'


def find_sprint_milestone(
    repo: github3.repos.Repository,
    sprint: delivery.model.Sprint,
):
    for ms in repo.milestones():
        if ms.title == _milestone_title(sprint=sprint):
            return ms
    return None


def find_or_create_sprint_milestone(
    repo: github3.repos.Repository,
    sprint: delivery.model.Sprint,
):
    if ms := find_sprint_milestone(repo=repo, sprint=sprint):
        return ms

    title = _milestone_title(sprint=sprint)
    ms = repo.create_milestone(
        title=title,
        state='open',
        description=f'used to track issues for upcoming sprint {title}',
        due_on=f'{sprint.release_decision.isoformat()}T00:00:00Z',
    )

    return ms
