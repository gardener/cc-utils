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


@functools.cache
def sprints_cached(
    delivery_svc_client: delivery.client.DeliveryServiceClient,
) -> list[dm.Sprint]:
    return delivery_svc_client.sprints()


@functools.cache
def milestones_cached(
    repo: github3.repos.Repository,
    state: str='all',
) -> collections.abc.Iterable[github3.repos.repo.milestone.Milestone]:
    return repo.milestones(state=state)


def iter_and_create_github_milestones(
    sprints: collections.abc.Iterable[dm.Sprint],
    repo: github3.repos.Repository,
    milestone_cfg: MilestoneConfiguration | None=None,
    state: str='open',
) -> collections.abc.Iterable[github3.repos.repo.milestone.Milestone]:
    '''
    Yields the respective GitHub milestones for the specified `sprints`. Comparison is done via the
    title. Only milestones matching the provided `state` are yielded, others are skipped. If a
    milestone is not existing yet, it will be created ad-hoc.
    '''
    if not milestone_cfg:
        milestone_cfg = MilestoneConfiguration()

    all_milestones = milestones_cached(
        repo=repo,
    )

    for sprint in sprints:
        title = _milestone_title(
            sprint=sprint,
            milestone_cfg=milestone_cfg,
        )

        if milestone := find_milestone_for_title(
            milestones=all_milestones,
            title=title,
        ):
            logger.debug(f'GitHub milestone {title} is already existing - skipping creation')

        else:
            due_date = milestone_cfg.due_date_callback(sprint)

            milestone = repo.create_milestone(
                title=title,
                state='open',
                description=f'used to track issues for upcoming sprint {title}',
                due_on=due_date.isoformat(),
            )

            logger.info(f'created GitHub milestone {title} with {due_date=}')

        if state == 'all' or milestone.state == state:
            yield milestone


def find_milestone_for_title(
    milestones: collections.abc.Iterable[github3.repos.repo.milestone.Milestone],
    title: str,
    absent_ok: bool=True,
) -> github3.repos.repo.milestone.Milestone | None:
    for milestone in milestones:
        if milestone.title == title:
            return milestone

    if not absent_ok:
        raise ValueError(f'did not find GitHub milestone {title}')

    return None


def find_milestone_for_due_date(
    milestones: collections.abc.Iterable[github3.repos.repo.milestone.Milestone],
    due_date: datetime.date,
    offset: int=0,
    use_fallback: bool=True,
    absent_ok: bool=True,
) -> github3.repos.repo.milestone.Milestone | None:
    def early_exit(
        message: str,
        milestone: github3.repos.repo.milestone.Milestone | None,
    ) -> github3.repos.repo.milestone.Milestone | None:
        if not absent_ok:
            raise ValueError(message)

        logger.warning(message)

        if use_fallback and milestone:
            logger.warning(f'will use {milestone=} as a fallback')
            return milestone

        return None

    sorted_milestones = sorted(milestones, key=lambda milestone: milestone.due_on)

    for idx, milestone in enumerate(sorted_milestones):
        if milestone.due_on.date() >= due_date: # is due before milestone ends
            break
    else:
        return early_exit(
            message=f'did not find GitHub milestone for {due_date=}',
            milestone=sorted_milestones[-1] if sorted_milestones else None,
        )

    tgt_idx = idx + offset

    if tgt_idx < 0:
        return early_exit(
            message=f'did not find GitHub milestone for {due_date=} considering {offset=}',
            milestone=sorted_milestones[0], # first milestone as a fallback
        )

    if tgt_idx >= len(sorted_milestones):
        return early_exit(
            message=f'did not find GitHub milestone for {due_date=} considering {offset=}',
            milestone=sorted_milestones[-1], # last milestone as a fallback
        )

    return sorted_milestones[tgt_idx]
