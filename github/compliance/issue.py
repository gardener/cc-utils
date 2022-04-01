import hashlib

import github3

import gci.componentmodel as cm

'''
functionality for creating and maintaining github-issues for tracking compliance issues
'''


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
    issue_type: str='vulnerabilities/bdba',
):
    yield 'area/security'
    yield 'cicd/auto-generated'
    yield f'cicd/{issue_type}'

    yield resource_digest_label(component=component, resource=resource)


def enumerate_issues(
    component: cm.Component,
    resource: cm.Resource,
    repository: github3.repos.Repository,
    state=None, # 'open' | 'closed'
):
    return repository.issues(
        state=state,
        labels=tuple(
            repository_labels(
                component=component,
                resource=resource
            ),
        ),
    )


def _create_issue(
    component: cm.Component,
    resource: cm.Resource,
    repository: github3.repos.Repository,
    body:str,
    issue_type: str='vulnerabilities/bdba',
):
    title = f'[{issue_type}] - {component.name}:{resource.name}'

    return repository.create_issue(
        title=title,
        body=body,
        assignee=None, # XXX
        labels=tuple(repository_labels(
            component=component,
            resource=resource,
            issue_type=issue_type,
        )),
    )


def _update_issue(
    component: cm.Component,
    resource: cm.Resource,
    repository: github3.repos.Repository,
    body:str,
    issue: github3.issues.Issue,
    issue_type: str='vulnerabilities/bdba',
):
    issue.edit(
        body=body,
    )


def create_or_update_issue(
    component: cm.Component,
    resource: cm.Resource,
    repository: github3.repos.Repository,
    body:str,
    issue_type: str='vulnerabilities/bdba',
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
        raise RuntimeError(f'more than one open issue found for {component=}{resource=}')
    elif issues_count == 0:
        return _create_issue(
            component=component,
            resource=resource,
            repository=repository,
            issue_type=issue_type,
            body=body,
        )
    elif issues_count == 1:
        open_issue = open_issues[0] # we checked there is exactly one
        return _update_issue(
            component=component,
            resource=resource,
            repository=repository,
            issue_type=issue_type,
            body=body,
            issue=open_issue,
        )
    else:
        raise RuntimeError('this line should never be reached') # all cases should be handled before
