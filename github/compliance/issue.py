import datetime
import hashlib
import logging
import re
import textwrap
import traceback
import typing

import concourse.util
import github3
import github3.issues
import github3.issues.comment
import github3.issues.issue
import github3.issues.milestone
import github3.repos

import cfg_mgmt.model as cmm
import ci.log
import delivery.model
import github.compliance.model as gcm
import github.retry
import github.util

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

_label_no_responsible = 'cfg/policy-violation/no-responsible'
_label_no_rule = 'cfg/policy-violation/no-rule'
_label_no_status = 'cfg/policy-violation/no-status'
_label_outdated_credentials = 'cfg/policy-violation/credentials-outdated'
_label_undefined_policy = 'cfg/policy-violation/undefined-policy'

_label_prefix_ocm_artefact = 'ocm/artefact'
_label_prefix_cicd_cfg_element = 'cicd-cfg-element'
_label_prefix_ctx = 'ctx'


def prefix_for_element(
    scanned_element: gcm.Target,
) -> str:
    if gcm.is_ocm_artefact_node(scanned_element):
        return _label_prefix_ocm_artefact

    elif isinstance(scanned_element, cmm.CfgElementStatusReport):
        return _label_prefix_cicd_cfg_element

    else:
        raise TypeError(scanned_element)


def unique_name_for_element(
    scanned_element: gcm.Target,
    issue_type: str,
    latest_processing_date: datetime.date=None,
) -> str:
    if gcm.is_ocm_artefact_node(scanned_element):
        artifact = gcm.artifact_from_node(scanned_element)
        if issue_type == _label_bdba or issue_type == _label_licenses:
            name = (
                f'{scanned_element.component.name}:{scanned_element.component.version}'
                f':{artifact.name}:{artifact.version}'
            )
            if latest_processing_date:
                name += f":{latest_processing_date.strftime('%Y-%m-%d')}"
        else:
            name = f'{scanned_element.component.name}:{artifact.name}'
        return name

    elif isinstance(scanned_element, cmm.CfgElementStatusReport):
        return scanned_element.name

    else:
        raise TypeError(scanned_element)


def digest_label(
    prefix: str,
    digest_str: str,
    max_length: int=50,
) -> str:
    '''
    concatenates and returns a fixed prefix with digest calculated from `digest_str`.

    this is useful as GitHub labels are limited to 50 characters
    '''
    digest_length = max_length - (len(prefix) + 1) # prefix + slash
    digest_length = int(digest_length / 2) # hexdigest is of double length

    # pylint does not know `length` parameter (it is even required, though!)
    digest = hashlib.shake_128(digest_str.encode('utf-8')).hexdigest( # noqa: E1123
        length=digest_length,
    )

    label = f'{prefix}/{digest}'

    if len(label) > max_length:
        raise ValueError(f'{digest_str=} and {prefix=} would result '
            f'in label exceeding length of {max_length=}')

    return label


def _search_labels(
    scanned_element: gcm.Target | None,
    issue_type: str,
    extra_labels: typing.Iterable[str]=(),
    latest_processing_date: datetime.date=None,
) -> typing.Generator[str, None, None]:
    if not issue_type:
        raise ValueError('issue_type must not be None or empty')

    if extra_labels:
        yield from extra_labels

    yield 'area/security'
    yield 'cicd/auto-generated'
    yield f'cicd/{issue_type}'

    if scanned_element:
        yield digest_label(
            prefix=prefix_for_element(
                scanned_element=scanned_element,
            ),
            digest_str=unique_name_for_element(
                scanned_element=scanned_element,
                issue_type=issue_type,
                latest_processing_date=latest_processing_date,
            ),
        )


@github.retry.retry_and_throttle
def enumerate_issues(
    scanned_element: gcm.Target | None,
    known_issues: typing.Sequence[github3.issues.issue.ShortIssue],
    issue_type: str,
    extra_labels: typing.Iterable[str]=(),
    state: str | None = None, # 'open' | 'closed ; `None` ignores issue state
    latest_processing_date: datetime.date=None,
) -> typing.Generator[github3.issues.ShortIssue, None, None]:
    '''Return an iterator iterating over those issues from `known_issues` that match the given
    parameters.
    '''
    labels = frozenset(_search_labels(
        scanned_element=scanned_element,
        issue_type=issue_type,
        extra_labels=extra_labels,
        latest_processing_date=latest_processing_date,
    ))

    def filter_relevant_issues(issue: github3.issues.issue.ShortIssue):
        # state is of type str, explicitly check for None
        if state != None and issue.state != state:
            return False

        issue_labels = frozenset((l.name for l in issue.original_labels))
        if not issue_labels & labels == labels:
            return False
        return True

    for issue in filter(filter_relevant_issues, known_issues):
        yield issue


@github.retry.retry_and_throttle
def _create_issue(
    scanned_element: gcm.Target,
    issue_type: str,
    repository: github3.repos.Repository,
    body: str,
    title: str,
    extra_labels: typing.Iterable[str]=(),
    assignees: typing.Iterable[str]=(),
    assignees_statuses: set[delivery.model.Status] = set(),
    milestone: github3.issues.milestone.Milestone=None,
    failed_milestones: list[github3.issues.milestone.Milestone]=[],
    latest_processing_date: datetime.date|datetime.datetime=None,
) -> github3.issues.issue.ShortIssue:
    assignees = tuple(assignees)

    labels = frozenset(_search_labels(
        scanned_element=scanned_element,
        issue_type=issue_type,
        extra_labels=extra_labels,
        latest_processing_date=latest_processing_date,
    ))

    try:
        issue = repository.create_issue(
            title=title,
            body=body,
            assignees=assignees,
            milestone=milestone.number if milestone else None,
            labels=sorted(labels),
        )

        if latest_processing_date and issue_type != _label_bdba and issue_type != _label_licenses:
            latest_processing_date = latest_processing_date.isoformat()
            issue.create_comment(f'{latest_processing_date=}')

        if assignees_statuses:
            comment_body = textwrap.dedent('''\
                There have been anomalies during initial ticket assignment, please see details below:
                | Message Type | Message |
                | --- | --- |'''
            )
            for status in assignees_statuses:
                comment_body += f'\n|`{status.type}`|`{status.msg}`'

            issue.create_comment(comment_body)

        if failed_milestones:
            issue.create_comment(
                'Failed to automatically assign ticket to one of these milestones: ' +
                f'{", ".join([milestone.title for milestone in failed_milestones])}. ' +
                'Milestones were probably closed before all associated findings were assessed.'
            )

        return issue

    except github3.exceptions.GitHubError as ghe:
        logger.warning(f'received error trying to create issue: {ghe=}')
        logger.warning(f'{ghe.message=} {ghe.code=} {ghe.errors=}')
        logger.warning(f'{labels=} {assignees=}')
        raise ghe


@github.retry.retry_and_throttle
def _update_issue(
    scanned_element: gcm.Target,
    issue_type: str,
    body:str,
    title:typing.Optional[str],
    issue: github3.issues.Issue,
    extra_labels: typing.Iterable[str]=(),
    milestone: github3.issues.milestone.Milestone=None,
    assignees: typing.Iterable[str]=(),
    latest_processing_date: datetime.date=None,
) -> github3.issues.issue.ShortIssue:
    kwargs = {}
    if not issue.assignees and assignees:
        kwargs['assignees'] = tuple(assignees)

    if title:
        kwargs['title'] = title

    if milestone and not issue.milestone:
        kwargs['milestone'] = milestone.number

    labels = sorted(_search_labels(
        scanned_element=scanned_element,
        issue_type=issue_type,
        extra_labels=extra_labels,
        latest_processing_date=latest_processing_date,
    ))

    kwargs['labels'] = labels

    issue.edit(
        body=body,
        **kwargs,
    )

    return issue


def _create_or_update_problems_comment(
    issue: github3.github.issues.ShortIssue,
    message: str,
):
    '''
    Create or update a comment on an issue stating encountered problems.
    '''

    job_url = concourse.util.own_running_build_url()
    now = datetime.datetime.now()

    issue_problems_comment_header = (
        'An error occurred when updating this issue:'
    )

    def find_existing_comment() -> github3.issues.comment.IssueComment | None:
        for comment in issue.comments(number=32):
            if issue_problems_comment_header in comment.body:
                return comment

    problems_comment = find_existing_comment()

    comment_body = (
        f'{issue_problems_comment_header}\n\n'
        f'{message}\n\n'
        f'Job url: {job_url}\n'
        f'Date of occurrence: `{now.isoformat(timespec="minutes")}`'
    )

    if not problems_comment:
        # Create new comment containing the message
        issue.create_comment(comment_body)
    else:
        # Update comment replacing old message
        problems_comment.edit(comment_body)


def create_or_update_issue(
    scanned_element: gcm.Target,
    issue_type: str,
    repository: github3.repos.Repository,
    body: str,
    known_issues: typing.Iterable[github3.issues.issue.ShortIssue],
    title: str,
    assignees: typing.Iterable[str]=(),
    assignees_statuses: set[delivery.model.Status] = set(),
    milestone: github3.issues.milestone.Milestone=None,
    failed_milestones: list[github3.issues.milestone.Milestone]=[],
    latest_processing_date: datetime.date=None,
    extra_labels: typing.Iterable[str]=None,
    preserve_labels_regexes: typing.Iterable[str]=(),
    ctx_labels: typing.Iterable[str]=(),
) -> github3.issues.issue.ShortIssue:
    '''
    Creates or updates a github issue for the given scanned_element. If no issue exists, yet, it will
    be created, otherwise, it is checked that at most one matching issue exists; which will then be
    updated.
    Issues are found by labels. Some of those are derived from the scanned_element. If given,
    ctx_labels are also included in search-query (i.e. an issue must have all of the given ctx_labels
    to be considered a match). All of those labels + any given extra_labels are assigned to the
    issue, both in case it is created anew, or updated. Extra labels that are not ignored via the
    preserve_labels_regexes argument are dropped.
    '''

    open_issues = tuple(
        enumerate_issues(
            scanned_element=scanned_element,
            issue_type=issue_type,
            known_issues=known_issues,
            state='open',
            extra_labels=ctx_labels,
            latest_processing_date=latest_processing_date,
        )
    )
    if (issues_count := len(open_issues)) > 1:
        raise RuntimeError(
            'more than one open issue found for '
                + f'{unique_name_for_element(scanned_element, issue_type, latest_processing_date)=}'
        )
    elif issues_count == 0:
        if extra_labels:
            extra_labels = set(extra_labels) | set(ctx_labels)
        else:
            extra_labels = set(ctx_labels)

        return _create_issue(
            scanned_element=scanned_element,
            issue_type=issue_type,
            extra_labels=extra_labels,
            repository=repository,
            body=body,
            title=title,
            assignees=assignees,
            assignees_statuses=assignees_statuses,
            milestone=milestone,
            failed_milestones=failed_milestones,
            latest_processing_date=latest_processing_date,
        )
    elif issues_count == 1:

        open_issue = open_issues[0] # we checked there is exactly one
        open_issue: github3.issues.issue.ShortIssue

        def labels_to_preserve():
            nonlocal preserve_labels_regexes

            # always keep ctx_labels
            ctx_label_regex = f'{_label_prefix_ctx}.*'
            preserve_labels_regexes = set(preserve_labels_regexes) | set({ctx_label_regex})

            for label in open_issue.original_labels:
                for r in preserve_labels_regexes:
                    if re.fullmatch(pattern=r, string=label.name):
                        yield label.name
                        break

        if extra_labels:
            extra_labels = set(extra_labels) | set(labels_to_preserve())
        else:
            extra_labels = labels_to_preserve()
        try:
            return _update_issue(
                scanned_element=scanned_element,
                issue_type=issue_type,
                extra_labels=extra_labels,
                body=body,
                title=title,
                assignees=assignees,
                milestone=milestone,
                issue=open_issue,
                latest_processing_date=latest_processing_date,
            )
        except Exception as e:
            message = f'```\n{traceback.format_exc()}\n```'
            if isinstance(e, github3.exceptions.GitHubError) and e.errors: # noqa: E1101
                # also add much more helpful "errors", if available
                message += f'Additional error-details supplied by github: \n{e.errors}' # noqa: E1101
            _create_or_update_problems_comment(issue=open_issue, message=message)
            raise

    else:
        raise RuntimeError('this line should never be reached') # all cases should be handled before


@github.retry.retry_and_throttle
def close_issue_if_present(
    scanned_element: gcm.Target,
    issue_type: str,
    repository: github3.repos.Repository,
    known_issues: typing.Iterable[github3.issues.issue.ShortIssue],
    ctx_labels: typing.Iterable[str]=(),
    latest_processing_date: datetime.date=None,
):
    open_issues = tuple(
        enumerate_issues(
            scanned_element=scanned_element,
            issue_type=issue_type,
            known_issues=known_issues,
            state='open',
            extra_labels=ctx_labels,
            latest_processing_date=latest_processing_date,
        )
    )
    open_issues: tuple[github3.issues.ShortIssue]

    logger.info(f'{len(open_issues)=} found for closing {open_issues=}')

    if (issues_count := len(open_issues)) > 1:
        logger.warning(
            'more than one open issue found for '
                + f'{unique_name_for_element(scanned_element, issue_type, latest_processing_date)=}'
        )
    elif issues_count == 0:
        logger.info('no open issue found for '
            + f'{unique_name_for_element(scanned_element, issue_type, latest_processing_date)=}')
        return # nothing to do

    open_issue = open_issues[0]
    logger.info(f'labels for issue for closing: {[l.name for l in open_issue.original_labels]}')

    for issue in open_issues:
        issue: github3.issues.ShortIssue
        issue.create_comment('closing ticket, because there are no longer unassessed findings')
        if not github.util.close_issue(issue):
            logger.warning(f'failed to close {issue.id=}, {repository.url=}')

    return issue
