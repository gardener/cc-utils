# Copyright (c) 2018 SAP SE or an SAP affiliate company. All rights reserved. This file is licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from util import not_none

from concourse.pipelines.modelbase import ModelValidationError

from .component_descriptor import ComponentDescriptorTrait
from .cron import CronTrait
from .publish import PublishTrait
from .pullrequest import PullRequestTrait
from .release import ReleaseTrait
from .scheduling import SchedulingTrait
from .version import VersionTrait
from .options import OptionsTrait
from .update_component_deps import UpdateComponentDependenciesTrait

TRAITS = {
    'version': VersionTrait,
    'cronjob': CronTrait,
    'component_descriptor': ComponentDescriptorTrait,
    'pull-request': PullRequestTrait,
    'release': ReleaseTrait,
    'scheduling': SchedulingTrait,
    'publish': PublishTrait,
    'options': OptionsTrait,
    'update_component_deps': UpdateComponentDependenciesTrait,
}

class TraitsFactory(object):
    @staticmethod
    def create(name: str, variant_name: str, args_dict: dict):
        if not name in TRAITS:
            raise ModelValidationError('no such trait: ' + str(name))
        not_none(args_dict)

        ctor = TRAITS[name]

        return ctor(name=name, variant_name=variant_name, raw_dict=args_dict)


