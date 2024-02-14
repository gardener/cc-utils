# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


from model.base import (
    NamedModelElement,
)


class VictoropsConfig(NamedModelElement):
    def api_id(self):
        return self.raw['api_token']

    def api_key(self):
        return self.raw['api_key']

    def team_slug(self):
        return self.raw['team_slug']

    def dod_policy_slug(self):
        return self.raw['dod_policy_slug']

    def mod_policy_slug(self):
        return self.raw['mod_policy_slug']
