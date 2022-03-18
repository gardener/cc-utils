import dataclasses
import datetime
import typing


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
    creation_date: str

    @staticmethod
    def create(
        delivery_id: str,
        repository: str,
        hostname: str,
        event_type: str,
        outdated_resources_names: typing.List[str],
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
        )


def index_name(
    obj: typing.Union[WebhookDelivery, WebhookResourceUpdateFailed],
) -> str:
    if isinstance(obj, WebhookDelivery):
        return 'webhook_delivery'
    elif isinstance(obj, WebhookResourceUpdateFailed):
        return 'webhook_resource_update_failed'

    raise NotImplementedError(obj)
