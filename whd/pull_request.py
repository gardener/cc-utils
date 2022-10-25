import datetime
import functools
import logging
import random
import time
import typing

import ccc.concourse
import ccc.elasticsearch
import ccc.github
import ccc.secrets_server
import ci.util
import concourse.client.api
import concourse.client.model
import concourse.enumerator
import concourse.replicator
import model
import model.concourse
import model.webhook_dispatcher
import whd.dispatcher

from github3.exceptions import NotFoundError

from .pipelines import validate_repository_pipelines
from github.util import GitHubRepositoryHelper

from concourse.client.util import (
    jobs_not_triggered,
    pin_resource_and_trigger_build,
    PinningFailedError,
    PinningUnnecessary,
)
from concourse.client.model import (
    ResourceType,
)
from .model import (
    PullRequestAction,
    PullRequestEvent,
)
import whd.metric
import whd.util


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def process_pr_event(
    concourse_clients: typing.Iterable[concourse.client.api.ConcourseApiBase],
    cfg_factory,
    cfg_set: model.ConfigurationSet,
    whd_cfg: model.webhook_dispatcher.WebhookDispatcherConfig,
    pr_event: PullRequestEvent,
    es_client: ccc.elasticsearch.ElasticSearchClient,
    dispatch_start_time: datetime.datetime,
):
    if not (github_helper := github_api_for_pr_event(pr_event, cfg_set)):
        logger.error(
                f'Unable to create github-api for PR #{pr_event.number()} of '
                f'repository {pr_event.repository().repository_path()}.'
            )
        return

    if (
        pr_modified_pipeline_definitions(pr_event, cfg_set)
        and pr_event.action() in [PullRequestAction.OPENED, PullRequestAction.SYNCHRONIZE]
    ):
        logger.info(f'Validating .ci/pipeline-definition for PR #{pr_event.number()}')
        validate_pipeline_definitions(
            cfg_set=cfg_set,
            cfg_factory=cfg_factory,
            whd_cfg=whd_cfg,
            pr_event=pr_event,
            github_helper=github_helper,
            tagging_label='ci/broken-pipeline-definition',
        )

    for concourse_api in concourse_clients:
        resources = list(
            matching_resources(
                concourse_api=concourse_api,
                event=pr_event,
            )
        )

        if len(resources) == 0:
            continue

        if pr_event.action() is PullRequestAction.LABELED:
            required_labels = {
                resource.source.get('label')
                for resource in resources if resource.source.get('label') is not None
            }
            if not (l := pr_event.label()) in required_labels:
                # Label that was set will not trigger any pr-job.
                logger.info(
                    f"Label '{l}' was set, but is not required for any job that builds "
                    f"PR #{pr_event.number()} for repository "
                    f"'{pr_event.repository().repository_path()}'. Will not trigger "
                    'resource check.'
                )
                continue
        if (
            pr_event.action() in [PullRequestAction.OPENED, PullRequestAction.SYNCHRONIZE]
            and not set_pr_labels(pr_event, github_helper, cfg_set, resources)
        ):
            logger.warning(
                f'Unable to set required labels for PR #{pr_event.number()} for '
                f'repository {pr_event.repository().repository_path()}. Will not trigger '
                'resource check.'
            )
            continue

        logger.info(f'triggering resource check for PR #{pr_event.number()}')
        whd.util.trigger_resource_check(
            concourse_api=concourse_api,
            resources=resources,
        )
        ensure_pr_resource_updates(
            cfg_set=cfg_set,
            concourse_api=concourse_api,
            pr_event=pr_event,
            resources=resources,
            es_client=es_client,
        )
        # Give concourse a chance to react
        time.sleep(random.randint(5,10))
        handle_untriggered_jobs(pr_event=pr_event, concourse_api=concourse_api)

    webhook_delivery_metric = whd.metric.WebhookDelivery.create(
        delivery_id=pr_event.delivery(),
        event_type='pull_request',
        repository=pr_event.repository().repository_path(),
        hostname=pr_event.hostname(),
        process_total_seconds=(datetime.datetime.now() - dispatch_start_time).total_seconds(),
    )
    if es_client:
        ccc.elasticsearch.metric_to_es(
            es_client=es_client,
            metric=webhook_delivery_metric,
            index_name=whd.metric.index_name(webhook_delivery_metric),
        )


def matching_resources(
    concourse_api: concourse.client.api.ConcourseApiBase,
    event: PullRequestEvent,
) -> typing.Generator[concourse.client.model.PipelineConfigResource, None, None]:

    resources_gen = concourse_api.pipeline_resources(
        concourse_api.pipelines(),
        resource_type=ResourceType.PULL_REQUEST,
    )

    for resource in resources_gen:
        resource: concourse.client.model.PipelineConfigResource
        if not resource.has_webhook_token():
            continue
        ghs = resource.github_source()
        repository = event.repository()
        if not ghs.hostname() == repository.github_host():
            continue
        if not ghs.repo_path().lstrip('/') == repository.repository_path():
            continue

        yield resource


def github_api_for_pr_event(
    pr_event: PullRequestEvent,
    cfg_set: model.ConfigurationSet,
):
    repo = pr_event.repository()
    github_host = repo.github_host()
    repository_path = repo.repository_path()

    github_cfg = ccc.github.github_cfg_for_repo_url(
        repo_url=ci.util.urljoin(github_host, repository_path),
        cfg_factory=cfg_set,
    )
    owner, name = repository_path.split('/')

    try:
        github_helper = GitHubRepositoryHelper(
            owner=owner,
            name=name,
            github_cfg=github_cfg,
        )
    except NotFoundError:
        logger.warning(
            f"Unable to access repository '{repository_path}' on github '{github_host}'. "
            "Please make sure the repository exists and the technical user has the necessary "
            "permissions to access it."
        )
        return None

    return github_helper


def validate_pipeline_definitions(
    cfg_factory,
    cfg_set: model.ConfigurationSet,
    whd_cfg: model.webhook_dispatcher.WebhookDispatcherConfig,
    pr_event: PullRequestEvent,
    github_helper: GitHubRepositoryHelper,
    tagging_label: str,
):
    repo_url = pr_event.repository().repository_url()
    job_mapping_set = cfg_set.job_mapping()
    job_mapping = job_mapping_set.job_mapping_for_repo_url(repo_url, cfg_set)
    pr_number = pr_event.number()

    try:
        validate_repository_pipelines(
            repo_url=pr_event.head_repository().repository_url(),
            cfg_set=cfg_factory.cfg_set(job_mapping.replication_ctx_cfg_set()),
            whd_cfg=whd_cfg,
            branch=pr_event.head_ref(),
            job_mapping=job_mapping,
        )
    except concourse.replicator.PipelineValidationError as e:
        # If validation fails add a comment on the PR iff we haven't already commented, as
        # tracked by label
        logger.warning(
            f'Pipeline-definition in PR #{pr_number} of repository {repo_url} failed '
            'validation. Commenting on PR.'
        )
        github_helper.add_comment_to_pr(
            pull_request_number=pr_number,
            comment=(
                'This PR proposes changes that would break the pipeline definition:\n'
                f'```\n{e}\n```\n'
            ),
        )
        if tagging_label not in pr_event.label_names():
            github_helper.add_labels_to_pull_request(pr_number, tagging_label)
    else:
        # validation succeeded. Remove the label again, if it is currently set.
        if tagging_label in pr_event.label_names():
            logger.info(
                f'Pipeline-definition in PR #{pr_number} of repository {repo_url} passed '
                'validation again. Commenting on PR.'
            )
            github_helper.remove_label_from_pull_request(pr_number, tagging_label)
            github_helper.add_comment_to_pr(
                pull_request_number=pr_number,
                comment='The pipeline-definition has been fixed.',
            )


def _should_label(
    job_mapping: model.concourse.JobMapping,
    github_helper: GitHubRepositoryHelper,
    sender_login: str,
    owner: str,
) -> bool:
    trusted_teams = job_mapping.trusted_teams()
    if trusted_teams:
        if (
            any(
                github_helper.is_team_member(team_name=t, user_login=sender_login)
                for t in trusted_teams
            )
        ):
            return True
        else:
            return False

    elif github_helper.is_org_member(organization_name=owner, user_login=sender_login):
        return True

    return False


def set_pr_labels(
    pr_event: PullRequestEvent,
    github_helper: GitHubRepositoryHelper,
    cfg_set: model.ConfigurationSet,
    resources,
) -> bool:
    '''
    @ return True if the required label was set
    '''
    required_labels = {
        resource.source.get('label')
        for resource in resources if resource.source.get('label') is not None
    }
    if not required_labels:
        return True

    pr_number = pr_event.number()
    sender_login = pr_event.sender()['login']

    repo = pr_event.repository()
    repository_path = repo.repository_path()
    owner, _ = repository_path.split('/')
    job_mapping_set: model.concourse.JobMappingSet = cfg_set.job_mapping()
    job_mapping = job_mapping_set.job_mapping_for_repo_url(
        repo.repository_url(), cfg_set
    )

    if pr_event.action() is PullRequestAction.OPENED:
        if _should_label(job_mapping, github_helper, sender_login, owner):
            logger.info(
                f"New pull request by trusted member '{sender_login}' in "
                f"'{repository_path}' found. Setting required labels '{required_labels}'."
            )
            github_helper.add_labels_to_pull_request(pr_number, *required_labels)
            return True
        else:
            logger.debug(
                f"New pull request by member in '{repository_path}' found, but creator is not "
                f"member of '{owner}' - will not set required labels."
            )
            github_helper.add_comment_to_pr(
                pull_request_number=pr_number,
                comment=(
                    f"Thank you @{sender_login} for your contribution. Before I can start "
                    "building your PR, a member of the organization must set the required "
                    f"label(s) {required_labels}. Once started, you can check the build "
                    "status in the PR checks section below."
                )
            )
            return False
    elif pr_event.action() is PullRequestAction.SYNCHRONIZE:
        if _should_label(job_mapping, github_helper, sender_login, owner):
            logger.info(
                f"Update to pull request #{pr_event.number()} by trusted member '{sender_login}' "
                f" in '{repository_path}' found. "
                f"Setting required labels '{required_labels}'."
            )
            github_helper.add_labels_to_pull_request(pr_number, *required_labels)
            return True
        else:
            logger.debug(
                f"Update to pull request #{pr_event.number()} by '{sender_login}' "
                f" in '{repository_path}' found. Ignoring, since they are not an org member'."
            )
            return False
    return False


def ensure_pr_resource_updates(
    cfg_set,
    concourse_api,
    pr_event: PullRequestEvent,
    resources: typing.List[concourse.client.model.PipelineConfigResource],
    es_client: ccc.elasticsearch.ElasticSearchClient,
    retries=10,
    sleep_seconds=3,
):
    time.sleep(sleep_seconds)

    retries -= 1
    if retries < 0:
        try:
            log_outdated_resources(
                cfg_set=cfg_set,
                outdated_resources=resources,
            )
        # ignore logging errors
        except BaseException:
            pass

        outdated_resources_names = [r.name for r in resources]
        logger.info(f'could not update resources {outdated_resources_names} - giving up')
        if es_client:
            resource_update_failed_metric = whd.metric.WebhookResourceUpdateFailed.create(
                delivery_id=pr_event.delivery(),
                repository=pr_event.repository().repository_path(),
                hostname=pr_event.hostname(),
                event_type='pull_request',
                outdated_resources_names=outdated_resources_names,
                pr_id=pr_event.pr_id(),
                pr_action=pr_event.action().value,
            )
            ccc.elasticsearch.metric_to_es(
                es_client=es_client,
                metric=resource_update_failed_metric,
                index_name=whd.metric.index_name(resource_update_failed_metric),
            )
        return

    def resource_versions(resource):
        return concourse_api.resource_versions(
            pipeline_name=resource.pipeline_name(),
            resource_name=resource.name,
        )

    def is_up_to_date(resource, resource_versions) -> bool:
        # check if pr requires a label to be present
        require_label = resource.source.get('label')
        if require_label:
            if require_label not in pr_event.label_names():
                logger.info('skipping PR resource update (required label not present)')
                # regardless of whether or not the resource is up-to-date, it would not
                # be discovered by concourse's PR resource due to policy
                return True

        # assumption: PR resource is up-to-date if our PR-number is listed
        # XXX hard-code structure of concourse-PR-resource's version dict
        pr_numbers = map(lambda r: r.version()['pr'], resource_versions)

        return str(pr_event.number()) in pr_numbers

    # filter out all resources that are _not_ up-to-date (we only care about those).
    # Also keep resources that currently fail to check so that we keep retrying those
    outdated_resources = [
        resource for resource in resources
        if resource.failing_to_check()
        or not is_up_to_date(resource, resource_versions(resource))
    ]

    if not outdated_resources:
        logger.info('no outdated PR resources found')
        return # nothing to do

    logger.info(f'found {len(outdated_resources)} PR resource(s) that require being updated')
    whd.util.trigger_resource_check(concourse_api=concourse_api, resources=outdated_resources)
    logger.info(f'retriggered resource check will try again {retries} more times')

    ensure_pr_resource_updates(
        concourse_api=concourse_api,
        cfg_set=cfg_set,
        pr_event=pr_event,
        resources=outdated_resources,
        retries=retries,
        sleep_seconds=sleep_seconds*1.2,
        es_client=es_client,
    )


def handle_untriggered_jobs(
    pr_event: PullRequestEvent,
    concourse_api,
):
    for job, resource, resource_version in jobs_not_triggered(pr_event, concourse_api):
        logger.info(
            f'processing untriggered job {job.name=} of {resource.pipeline_name()=} '
            f'{resource.name=} {resource_version.version()=}. Triggered by '
            f'{pr_event.action()=} of {pr_event.delivery()=}'
        )
        try:
            pin_resource_and_trigger_build(
                job=job,
                resource=resource,
                resource_version=resource_version,
                concourse_api=concourse_api,
                retries=3,
            )
        except PinningUnnecessary as e:
            logger.info(e)
        except PinningFailedError as e:
            logger.warning(e)


def pr_modified_pipeline_definitions(
    pr_event: PullRequestEvent,
    cfg_set,
) -> bool:
    pr_number = pr_event.number()
    if not (github_helper := github_api_for_pr_event(pr_event=pr_event, cfg_set=cfg_set)):
        return False

    changed_files = (f.filename for f in github_helper.repository.pull_request(pr_number).files())

    return '.ci/pipeline_definitions' in changed_files


@functools.lru_cache()
def els_client(
    cfg_set,
):
    elastic_cfg = cfg_set.elasticsearch()
    elastic_client = ccc.elasticsearch.from_cfg(elasticsearch_cfg=elastic_cfg)
    return elastic_client


def log_outdated_resources(
    cfg_set,
    outdated_resources,
):
    els_index = cfg_set.webhook_dispatcher_deployment().logging_els_index()
    elastic_client = els_client(cfg_set=cfg_set)

    date = datetime.datetime.utcnow().isoformat()
    elastic_client.store_documents(
        index=els_index,
        body=[
            {
                'date': date,
                'resource_name': resource.name,
                'pipeline_name': resource.pipeline_name(),
            }
            for resource in outdated_resources
        ],
    )
