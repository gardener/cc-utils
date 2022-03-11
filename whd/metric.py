import dataclasses
import datetime


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
    ):
        '''
        convenience method to create a `CcCfgComplianceStorageResponsibles`
        '''
        return WebhookDelivery(
            creation_date=datetime.datetime.now().isoformat(),
            deliveryId=delivery_id,
            eventType=event_type,
            repository=repository,
            hostname=hostname,
            processTotalSeconds=process_total_seconds,
        )


def index_name(
    obj: WebhookDelivery,
) -> str:
    if isinstance(obj, WebhookDelivery):
        return 'webhook_delivery'

    raise NotImplementedError(obj)
