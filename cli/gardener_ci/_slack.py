import ccc.slack
import ctx


__cmd_name__ = 'slack'


def send_message(
    slack_cfg_name: str,
    recipient: str, # can be channel-, group- or user-id
    message: str,
):
    factory = ctx.cfg_factory()
    slack_cfg = factory.slack(slack_cfg_name)
    client = ccc.slack.client(slack_cfg)

    client.chat_postMessage(
        channel=recipient,
        text=message,
    )
