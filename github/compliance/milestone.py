import datetime
import functools
import logging

import github3.repos

import delivery.client
import delivery.model


logger = logging.getLogger(__name__)


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


@functools.cache
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


@functools.cache
def _upcoming_sprints(
    delivery_svc_client: delivery.client.DeliveryServiceClient,
    today: datetime.date=datetime.date.today(), # used to refresh the cache daily
) -> list[delivery.model.Sprint]:
    sprints = delivery_svc_client.sprints()
    upcoming_sprints = [
        sprint for sprint in sprints
        if sprint.find_sprint_date(name='end_date').value.date() >= today
    ]

    if len(upcoming_sprints) == 0:
        raise ValueError(f'no upcoming sprints found, all sprints ended before {today}')

    return upcoming_sprints


def target_sprints(
    delivery_svc_client: delivery.client.DeliveryServiceClient,
    latest_processing_date: datetime.date=None,
    sprint_end_date: datetime.date=None,
    sprints_count: int=1,
) -> tuple[delivery.model.Sprint]:
    if not latest_processing_date and not sprint_end_date:
        raise ValueError(
          "At least one of 'latest_processing_date' and 'sprint_end_date' must not be 'None'"
        )
    today = datetime.date.today()
    sprints = _upcoming_sprints(
        delivery_svc_client=delivery_svc_client,
        today=today,
    )

    if latest_processing_date:
        date = latest_processing_date
        offset = -1 # find the sprint that ends before the specified date
    else:
        date = sprint_end_date
        offset = 0 # find the sprint that includes the specified date

    sprints.sort(key=lambda sprint: sprint.find_sprint_date(name='end_date').value.date())

    targets_sprints = []
    for idx, sprint in enumerate(sprints):
        if len(targets_sprints) == sprints_count:
            # found enough sprints -> early exiting
            break

        end_date = sprint.find_sprint_date(name='end_date').value.date()
        if end_date > date:
            if idx + offset == -1: # compare to "-1" instead of "<0" to print warning _once_
                logger.warning(
                    f'did not find not ended sprints starting from {date} with an offset '
                    f'of {offset}, will return the first {sprints_count} not ended sprints'
                )
            elif idx + offset >= len(sprints):
                # index + offset keep being out of bounds -> early exiting
                break
            else:
                targets_sprints.append(sprints[idx + offset])

    if len(targets_sprints) < sprints_count:
        logger.warning(
            f'did not find {sprints_count} sprints starting from {date} with ' +
            f'an offset of {offset}, only found {len(targets_sprints)} sprints'
        )

    return tuple(targets_sprints)
