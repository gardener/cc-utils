import collections.abc
import dataclasses
import datetime
import functools
import logging

import github3.repos

import delivery.client
import delivery.model as dm


logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class MilestoneConfiguration:
    title_callback: collections.abc.Callable[[dm.Sprint], str] = lambda sprint: sprint.name
    title_prefix: str | None = 'sprint-'
    title_suffix: str | None = None
    due_date_callback: collections.abc.Callable[[dm.Sprint], datetime.datetime] \
        = lambda sprint: sprint.find_sprint_date('release_decision').value


def _milestone_title(
    sprint: dm.Sprint,
    milestone_cfg: MilestoneConfiguration=None,
) -> str:
    if not milestone_cfg:
        milestone_cfg = MilestoneConfiguration()

    title = milestone_cfg.title_callback(sprint)
    title_prefix = milestone_cfg.title_prefix or ''
    title_suffix = milestone_cfg.title_suffix or ''

    return f'{title_prefix}{title}{title_suffix}'


def find_sprint_milestone(
    repo: github3.repos.Repository,
    sprints: tuple[dm.Sprint],
    milestone_cfg: MilestoneConfiguration=None,
) -> tuple[
    dm.Sprint,
    github3.repos.repo.milestone.Milestone,
    list[github3.repos.repo.milestone.Milestone],
]:
    sprint_milestone = None
    failed_milestones = []
    all_milestones = repo.milestones(state='all')

    for sprint in sprints:
        for ms in all_milestones:
            if ms.title == _milestone_title(
                sprint=sprint,
                milestone_cfg=milestone_cfg,
            ):
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
    sprints: tuple[dm.Sprint],
    milestone_cfg: MilestoneConfiguration=None,
) -> tuple[
    github3.repos.repo.milestone.Milestone | None,
    list[github3.repos.repo.milestone.Milestone],
]:
    if not milestone_cfg:
        milestone_cfg = MilestoneConfiguration()

    sprint, milestone, failed_milestones = find_sprint_milestone(
        repo=repo,
        sprints=sprints,
        milestone_cfg=milestone_cfg,
    )

    if milestone:
        return (milestone, failed_milestones)

    if not sprint:
        # all sprints have sprint milestones which were extraordinary closed
        # because sprints are still in the future but there milestone is closed
        # we can't do anything here so write info to ticket
        return (None, failed_milestones)

    title = _milestone_title(
        sprint=sprint,
        milestone_cfg=milestone_cfg,
    )

    due_date = milestone_cfg.due_date_callback(sprint)

    ms = repo.create_milestone(
        title=title,
        state='open',
        description=f'used to track issues for upcoming sprint {title}',
        due_on=due_date.isoformat(),
    )

    return (ms, failed_milestones)


@functools.cache
def _sprints(
    delivery_svc_client: delivery.client.DeliveryServiceClient,
    today: datetime.date=datetime.date.today(), # used to refresh the cache daily
) -> list[dm.Sprint]:
    return delivery_svc_client.sprints()


def target_sprints(
    delivery_svc_client: delivery.client.DeliveryServiceClient,
    latest_processing_date: datetime.date,
    sprints_count: int=1,
) -> tuple[dm.Sprint]:
    sprints = _sprints(
        delivery_svc_client=delivery_svc_client,
    )
    sprints.sort(key=lambda sprint: sprint.find_sprint_date(name='end_date').value.date())

    targets_sprints = []
    for sprint in sprints:
        if len(targets_sprints) == sprints_count:
            # found enough sprints -> early exiting
            break

        end_date = sprint.find_sprint_date(name='end_date').value.date()
        if end_date >= latest_processing_date:
            targets_sprints.append(sprint)

    if len(targets_sprints) < sprints_count:
        logger.warning(
            f'did not find {sprints_count} sprints starting from ' +
            f'{latest_processing_date}, only found {len(targets_sprints)} sprints'
        )

    return tuple(targets_sprints)
