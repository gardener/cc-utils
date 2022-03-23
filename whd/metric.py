import dataclasses
import datetime
import json
import typing


@dataclasses.dataclass(frozen=True)
class ExceptionMetric:
    service: str
    stacktrace: typing.List[str]
    request: str
    params: str
    creation_date: str

    @staticmethod
    def create(
        service: str,
        stacktrace: typing.List[str],
        request: typing.Optional[dict] = None,
        params: typing.Optional[dict] = None,
    ) -> 'ExceptionMetric':
        '''
        convenience method to create a `ExceptionMetric`
        '''
        return ExceptionMetric(
            creation_date=datetime.datetime.now().isoformat(),
            service=service,
            stacktrace=stacktrace,
            request=json.dumps(request),
            params=json.dumps(params),
        )


@dataclasses.dataclass(frozen=True)
class WebhookDelivery:
    deliveryId: str
    eventType: str #TODO: introduce enum
    repository: str
    hostname: str
    processTotalSeconds: float
    creation_date: str

    @staticmethod
    def create(
        delivery_id: str,
        event_type: str,
        repository: str,
        hostname: str,
        process_total_seconds: float
    ) -> 'WebhookDelivery':
        '''
        convenience method to create a `WebhookDelivery`
        '''
        return WebhookDelivery(
            creation_date=datetime.datetime.now().isoformat(),
            deliveryId=delivery_id,
            eventType=event_type,
            repository=repository,
            hostname=hostname,
            processTotalSeconds=process_total_seconds,
        )


@dataclasses.dataclass(frozen=True)
class WebhookResourceUpdateFailed:
    deliveryId: str
    repository: str
    hostname: str
    eventType: str
    outdatedResourcesNames: typing.List[str]
    prId: int
    prAction: str
    creation_date: str

    @staticmethod
    def create(
        delivery_id: str,
        repository: str,
        hostname: str,
        event_type: str,
        outdated_resources_names: typing.List[str],
        pr_id: int,
        pr_action: str,
    ) -> 'WebhookResourceUpdateFailed':
        '''
        convenience method to create a `WebhookResourceUpdateFailed`
        '''
        return WebhookResourceUpdateFailed(
            creation_date=datetime.datetime.now().isoformat(),
            deliveryId=delivery_id,
            repository=repository,
            hostname=hostname,
            eventType=event_type,
            outdatedResourcesNames=outdated_resources_names,
            prId=pr_id,
            prAction=pr_action,
        )


def index_name(
    obj: typing.Union[WebhookDelivery, WebhookResourceUpdateFailed, ExceptionMetric],
) -> str:
    if isinstance(obj, WebhookDelivery):
        return 'webhook_delivery'
    elif isinstance(obj, WebhookResourceUpdateFailed):
        return 'webhook_resource_update_failed'
    elif isinstance(obj, ExceptionMetric):
        return 'cicd_services_exception'

    raise NotImplementedError(obj)
