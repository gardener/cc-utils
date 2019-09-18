import slack

import model.slack


def client(slack_cfg: model.slack.SlackConfig):
    return slack.WebClient(token=slack_cfg.api_token())
