import slack_sdk

import model.slack


def client(slack_cfg: model.slack.SlackConfig):
    return slack_sdk.WebClient(token=slack_cfg.api_token())
