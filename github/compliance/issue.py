import hashlib
import logging
import re
import typing

import github3

import gci.componentmodel as cm

import ci.log

'''
functionality for creating and maintaining github-issues for tracking compliance issues
'''

logger = logging.getLogger(__name__)
ci.log.configure_default_logging()


_label_bdba = 'vulnerabilities/bdba'


def resource_digest_label(
    component: cm.Component,
    resource: cm.Resource,
    length=18,
):
    '''
    calculates and returns a digest for the given component/resource

    this is useful as GitHub labels are limited to 50 characters
    '''
    label_str = f'{component.name}:{resource.name}'

    label_dig =  hashlib.shake_128(label_str.encode('utf-8')).hexdigest(length=length)

    label = f'ocm/resource/{label_dig}'

    if len(label) > 50:
        raise ValueError(f'{length=} would result in label exceeding 50 characters')

    return label


def repository_labels(
    component: cm.Component,
    resource: cm.Resource,
    issue_type: str=_label_bdba,
    extra_labels: typing.Iterable[str]=None
):
    yield 'area/security'
    yield 'cicd/auto-generated'
    yield f'cicd/{issue_type}'

    yield resource_digest_label(component=component, resource=resource)

    if extra_labels:
        yield from extra_labels


def enumerate_issues(
    component: cm.Component,
    resource: cm.Resource,
    repository: github3.repos.Repository,
    state=None, # 'open' | 'closed'
    issue_type=_label_bdba,
) -> typing.Generator[github3.issues.ShortIssue, None, None]:
    return repository.issues(
        state=state,
        labels=tuple(
            repository_labels(
                component=component,
                resource=resource,
                issue_type=issue_type,
            ),
        ),
    )


def _create_issue(
    component: cm.Component,
    resource: cm.Resource,
    repository: github3.repos.Repository,
    body:str,
    title:typing.Optional[str],
    assignees: typing.Iterable[str]=(),
    milestone: github3.issues.milestone.Milestone=None,
    issue_type: str=_label_bdba,
    extra_labels: typing.Iterable[str]=None,
) -> github3.issues.issue.ShortIssue:
    if not title:
        title = f'[{issue_type}] - {component.name}:{resource.name}'

    assignees = tuple(assignees)

    labels = tuple(repository_labels(
        component=component,
        resource=resource,
        issue_type=issue_type,
        extra_labels=extra_labels,
    ))

    try:
        return repository.create_issue(
            title=title,
            body=body,
            assignees=assignees,
            milestone=milestone.number if milestone else None,
            labels=labels,
        )
    except github3.exceptions.GitHubError as ghe:
        logger.warning(f'received error trying to create issue: {ghe=}')
        logger.warning(f'{ghe.message=} {ghe.code=} {ghe.errors=}')
        logger.warning(f'{component.name=} {resource.name=} {assignees=} {labels=}')
        raise ghe


def _update_issue(
    component: cm.Component,
    resource: cm.Resource,
    repository: github3.repos.Repository,
    body:str,
    title:typing.Optional[str],
    issue: github3.issues.Issue,
    milestone: github3.issues.milestone.Milestone=None,
    assignees: typing.Iterable[str]=(),
    issue_type: str=_label_bdba,
    extra_labels: typing.Iterable[str]=None,
) -> github3.issues.issue.ShortIssue:
    kwargs = {}
    if not issue.assignees:
        kwargs['assignees'] = tuple(assignees)

    if title:
        kwargs['title'] = title

    if milestone and not issue.milestone:
        kwargs['milestone'] = milestone.number

    labels = tuple(repository_labels(
        component=component,
        resource=resource,
        issue_type=issue_type,
        extra_labels=extra_labels,
    ))

    kwargs['labels'] = labels

    issue.edit(
        body=body,
        **kwargs,
    )

    return issue


def create_or_update_issue(
    component: cm.Component,
    resource: cm.Resource,
    repository: github3.repos.Repository,
    body:str,
    title:str=None,
    assignees: typing.Iterable[str]=(),
    milestone: github3.issues.milestone.Milestone=None,
    issue_type: str=_label_bdba,
    extra_labels: typing.Iterable[str]=None,
    preserve_labels_regexes: typing.Iterable[str]=(),
) -> github3.issues.issue.ShortIssue:
    open_issues = tuple(
        enumerate_issues(
            component=component,
            resource=resource,
            repository=repository,
            state='open',
        )
    )

    if (issues_count := len(open_issues)) > 1:
        raise RuntimeError(f'more than one open issue found for {component.name=}{resource.name=}')
    elif issues_count == 0:
        return _create_issue(
            component=component,
            resource=resource,
            repository=repository,
            issue_type=issue_type,
            body=body,
            title=title,
            assignees=assignees,
            milestone=milestone,
            extra_labels=extra_labels,
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
            component=component,
            resource=resource,
            repository=repository,
            issue_type=issue_type,
            body=body,
            title=title,
            assignees=assignees,
            milestone=milestone,
            issue=open_issue,
            extra_labels=extra_labels,
        )
    else:
        raise RuntimeError('this line should never be reached') # all cases should be handled before


def close_issue_if_present(
    component: cm.Component,
    resource: cm.Resource,
    repository: github3.repos.Repository,
    issue_type: str=_label_bdba,
):
    open_issues = tuple(
        enumerate_issues(
            component=component,
            resource=resource,
            repository=repository,
            state='open',
        )
    )

    if (issues_count := len(open_issues)) > 1:
        logger.warning(f'more than one open issue found for {component.name=}{resource=}')
    elif issues_count == 0:
        logger.info(f'no open issue found for {component.name=}{resource.name=}')
        return # nothing to do

    succ = True

    for issue in open_issues:
        issue: github3.issues.ShortIssue
        issue.create_comment('closing ticket, because there are no longer unassessed findings')
        succ &= issue.close()
        if not succ:
            logger.warning(f'failed to close {issue.id=}, {repository.url=}')

    return succ
