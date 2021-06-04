from enum import Enum
import typing
from dataclasses import dataclass

cve_name = str
cvss_score = float


@dataclass
class WhiteSrcProject:
    name: str
    token: str
    vulnerability_report: dict

    def max_cve(self) -> typing.Tuple[cve_name, cvss_score]:
        max_score = 0
        cve_name = 'None'

        for entry in self.vulnerability_report['vulnerabilities']:
            cve_score_key_name = 'cvss3_score'
            if cve_score_key_name not in entry:
                cve_score_key_name = 'score'

            # max() cannot be used since its necessary to get the corresponding cve name
            if float(entry[cve_score_key_name]) > float(max_score):
                max_score = entry[cve_score_key_name]
                cve_name = entry['name']

        return (cve_name, float(max_score))


@dataclass
class WhiteSrcDisplayProject:
    name: str
    highest_cve_name: str
    highest_cve_score: float


class FilterType(Enum):
    COMPONENT = 'component'
    SOURCE = 'source'
    RESOURCE = 'resource'


class ActionType(Enum):
    INCLUDE = 'include'
    EXCLUDE = 'exclude'


@dataclass(frozen=True)
class WhiteSourceFilterCfg:
    type: FilterType
    match: typing.Union[bool, dict]
    action: ActionType
