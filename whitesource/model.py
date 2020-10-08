import dataclasses


@dataclasses.dataclass
class WhitesourceProject:
    name: str
    token: str
    vulnerability_report: dict

    def max_cve(self) -> tuple:
        max_score = 0
        cve_name = None

        for entry in self.vulnerability_report['vulnerabilities']:
            cve_score_key_name = 'cvss3_score'
            if cve_score_key_name not in entry:
                cve_score_key_name = 'score'

            # max() cannot be used since its necessary to get the corresponding cve name
            if float(entry[cve_score_key_name]) > float(max_score):
                max_score = entry[cve_score_key_name]
                cve_name = entry['name']

        return cve_name, float(max_score)
