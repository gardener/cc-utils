import hashlib
import itertools
import logging
import re
import typing

import cachetools
import github3
import github3.issues
import github3.issues.issue
import github3.issues.milestone
import github3.repos

import gci.componentmodel as cm

import ci.log
import github.retry

'''
functionality for creating and maintaining github-issues for tracking compliance issues
'''

logger = logging.getLogger(__name__)
ci.log.configure_default_logging()


_label_checkmarx = 'vulnerabilities/checkmarx'
_label_bdba = 'vulnerabilities/bdba'
_label_licenses = 'licenses/bdba'
_label_os_outdated = 'os/outdated'
_label_malware = 'malware/clamav'


@cachetools.cached(cache={})
def _issue_labels(
    issue,
):
    return frozenset((l.name for l in issue.labels()))


def artifact_digest_label(
    component: cm.Component,
    artifact: cm.Artifact | str,
    length: int=18,
):
    '''
    calculates and returns a digest for the given component/resource

    this is useful as GitHub labels are limited to 50 characters
    '''
    if isinstance(artifact, cm.Resource):
        name = artifact.name
    else:
        name = artifact

    label_str = f'{component.name}:{name}'

    # pylint does not know `length` parameter (it is even required, though!)
    label_dig =  hashlib.shake_128(label_str.encode('utf-8')).hexdigest(length=length) # noqa

    label = f'ocm/resource/{label_dig}'

    if len(label) > 50:
        raise ValueError(f'{length=} would result in label exceeding 50 characters')

    return label


def repository_labels(
    component: cm.Component | None,
    artifact: cm.Artifact | None,
    issue_type: str | None=_label_bdba,
    extra_labels: typing.Iterable[str]=None
) -> typing.Generator[str, None, None]:
    if any((component, artifact)) and not all((component, artifact)):
        raise ValueError('either all or none of component, resource must be given')

    yield 'area/security'
    yield 'cicd/auto-generated'

    if issue_type:
        yield f'cicd/{issue_type}'

    if component:
        yield artifact_digest_label(component=component, artifact=artifact)

    if extra_labels:
        yield from extra_labels


@github.retry.retry_and_throttle
def enumerate_issues(
    labels: frozenset[str],
    known_issues: typing.Sequence[github3.issues.issue.ShortIssue],
    state: str | None = None, # 'open' | 'closed'
) -> typing.Generator[github3.issues.ShortIssue, None, None]:
    '''Return an iterator iterating over those issues from `known_issues` that match the given
    parameters.
    '''
    def filter_relevant_issues(issue):
        if issue.state != state:
            return False
        if not _issue_labels(issue) & labels == labels:
            return False
        return True

    for issue in filter(filter_relevant_issues, known_issues):
        yield issue


@github.retry.retry_and_throttle
def _create_issue(
    labels: frozenset[str],
    repository: github3.repos.Repository,
    body: str,
    title: str,
    assignees: typing.Iterable[str]=(),
    milestone: github3.issues.milestone.Milestone=None,
) -> github3.issues.issue.ShortIssue:
    assignees = tuple(assignees)

    try:
        return repository.create_issue(
            title=title,
            body=body,
            assignees=assignees,
            milestone=milestone.number if milestone else None,
            labels=sorted(labels),
        )
    except github3.exceptions.GitHubError as ghe:
        logger.warning(f'received error trying to create issue: {ghe=}')
        logger.warning(f'{ghe.message=} {ghe.code=} {ghe.errors=}')
        logger.warning(f'{labels=} {assignees=}')
        raise ghe


@github.retry.retry_and_throttle
def _update_issue(
    labels: frozenset[str],
    body:str,
    title:typing.Optional[str],
    issue: github3.issues.Issue,
    milestone: github3.issues.milestone.Milestone=None,
    assignees: typing.Iterable[str]=(),
) -> github3.issues.issue.ShortIssue:
    kwargs = {}
    if not issue.assignees and assignees:
        kwargs['assignees'] = tuple(assignees)

    if title:
        kwargs['title'] = title

    if milestone and not issue.milestone:
        kwargs['milestone'] = milestone.number

    kwargs['labels'] = sorted(labels)

    issue.edit(
        body=body,
        **kwargs,
    )

    return issue


def create_or_update_issue(
    labels: frozenset[str],
    repository: github3.repos.Repository,
    body: str,
    known_issues: typing.Iterable[github3.issues.issue.ShortIssue],
    title: str,
    assignees: typing.Iterable[str]=(),
    milestone: github3.issues.milestone.Milestone=None,
    extra_labels: typing.Iterable[str]=None,
    preserve_labels_regexes: typing.Iterable[str]=(),
) -> github3.issues.issue.ShortIssue:
    open_issues = tuple(
        enumerate_issues(
            labels=labels,
            known_issues=known_issues,
            state='open',
        )
    )
    if (issues_count := len(open_issues)) > 1:
        raise RuntimeError(f'more than one open issue found for {labels=}')
    elif issues_count == 0:
        return _create_issue(
            labels=frozenset(itertools.chain(
                labels,
                extra_labels,
            )),
            repository=repository,
            body=body,
            title=title,
            assignees=assignees,
            milestone=milestone,
        )
    elif issues_count == 1:

        open_issue = open_issues[0] # we checked there is exactly one

        def labels_to_preserve():
            if not preserve_labels_regexes:
                return

            for label in open_issue.labels():
                for r in preserve_labels_regexes:
                    if re.fullmatch(pattern=r, string=label.name):
                        yield label.name
                        break

        if extra_labels:
            extra_labels = set(extra_labels) | set(labels_to_preserve())
        else:
            extra_labels = labels_to_preserve()

        return _update_issue(
            labels=frozenset(itertools.chain(
                labels,
                extra_labels,
            )),
            body=body,
            title=title,
            assignees=assignees,
            milestone=milestone,
            issue=open_issue,
        )
    else:
        raise RuntimeError('this line should never be reached') # all cases should be handled before


@github.retry.retry_and_throttle
def close_issue_if_present(
    labels: frozenset[str],
    repository: github3.repos.Repository,
    known_issues: typing.Iterable[github3.issues.issue.ShortIssue],
):
    open_issues = tuple(
        enumerate_issues(
            labels=labels,
            known_issues=known_issues,
            state='open',
        )
    )
    open_issues: tuple[github3.issues.ShortIssue]

    logger.info(f'{len(open_issues)=} found for closing {open_issues=}')

    if (issues_count := len(open_issues)) > 1:
        logger.warning(f'more than one open issue found for {labels=}')
    elif issues_count == 0:
        logger.info(f'no open issue found for {labels=}')
        return # nothing to do

    open_issue = open_issues[0]
    logger.info(f'labels for issue for closing: {[l.name for l in open_issue.labels()]}')

    succ = True

    for issue in open_issues:
        issue: github3.issues.ShortIssue
        issue.create_comment('closing ticket, because there are no longer unassessed findings')
        succ &= issue.close()
        if not succ:
            logger.warning(f'failed to close {issue.id=}, {repository.url=}')

    return issue
