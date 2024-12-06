import dataclasses


@dataclasses.dataclass
class GithubIssueTemplateCfg:
    body: str
    type: str
