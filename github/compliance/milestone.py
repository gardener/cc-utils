import github3.repos

import delivery.model


def _milestone_title(sprint: delivery.model.Sprint) -> str:
    return f'sprint-{sprint.name}'


def find_sprint_milestone(
    repo: github3.repos.Repository,
    sprints: tuple[delivery.model.Sprint],
) -> tuple[
    delivery.model.Sprint,
    github3.repos.repo.milestone.Milestone,
    list[github3.repos.repo.milestone.Milestone],
]:
    sprint_milestone = None
    failed_milestones = []
    all_milestones = repo.milestones(state='all')

    for sprint in sprints:
        for ms in all_milestones:
            if ms.title == _milestone_title(sprint=sprint):
                sprint_milestone = ms
                break

        if not sprint_milestone:
            # milestone does not exist yet -> create it
            return (sprint, None, failed_milestones)

        if sprint_milestone.state == 'open':
            # milestone exists and is open -> use it
            return (sprint, sprint_milestone, failed_milestones)

        # milestone exists but is closed -> repeat with next sprint
        failed_milestones.append(sprint_milestone)
        sprint_milestone = None

    return (None, None, failed_milestones)


def find_or_create_sprint_milestone(
    repo: github3.repos.Repository,
    sprints: tuple[delivery.model.Sprint],
) -> tuple[
    github3.repos.repo.milestone.Milestone | None,
    list[github3.repos.repo.milestone.Milestone],
]:
    sprint, milestone, failed_milestones = find_sprint_milestone(
        repo=repo,
        sprints=sprints,
    )

    if milestone:
        return (milestone, failed_milestones)

    if not sprint:
        # all sprints have sprint milestones which were extraordinary closed
        # because sprints are still in the future but there milestone is closed
        # we can't do anything here so write info to ticket
        return (None, failed_milestones)

    title = _milestone_title(sprint=sprint)

    sprint_release_decision = sprint.find_sprint_date(
        name='release_decision',
    )

    ms = repo.create_milestone(
        title=title,
        state='open',
        description=f'used to track issues for upcoming sprint {title}',
        due_on=sprint_release_decision.value.isoformat(),
    )

    return (ms, failed_milestones)
