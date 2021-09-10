# Copyright (c) 2019-2020 SAP SE or an SAP affiliate company. All rights reserved. This file is
# licensed under the Apache Software License, v. 2 except as noted otherwise in the LICENSE file
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

import functools

from ci.util import not_none

from concourse.model.base import ModelValidationError


@functools.cache
def _traits():
    from .component_descriptor import ComponentDescriptorTrait
    from .cronjob import CronTrait
    from .draft_release import DraftReleaseTrait
    from .image_alter import ImageAlterTrait
    from .image_scan import ImageScanTrait
    from .notifications import NotificationsTrait
    from .options import OptionsTrait
    from .publish import PublishTrait
    from .pullrequest import PullRequestTrait
    from .release import ReleaseTrait
    from .scheduling import SchedulingTrait
    from .slack import SlackTrait
    from .update_component_deps import UpdateComponentDependenciesTrait
    from .version import VersionTrait
    from .scan_sources import SourceScanTrait
    TRAITS = {
        'component_descriptor': ComponentDescriptorTrait,
        'cronjob': CronTrait,
        'draft_release': DraftReleaseTrait,
        'image_alter': ImageAlterTrait,
        'image_scan': ImageScanTrait,
        'notifications': NotificationsTrait,
        'options': OptionsTrait,
        'publish': PublishTrait,
        'pull-request': PullRequestTrait,
        'release': ReleaseTrait,
        'scheduling': SchedulingTrait,
        'slack': SlackTrait,
        'update_component_deps': UpdateComponentDependenciesTrait,
        'version': VersionTrait,
        'scan_sources': SourceScanTrait,
    }

    return TRAITS


class TraitsFactory:
    @staticmethod
    def create(
        name: str,
        variant_name: str,
        args_dict: dict,
        cfg_set,
    ):
        if name not in _traits():
            raise ModelValidationError('no such trait: ' + str(name))
        not_none(args_dict)

        ctor = _traits()[name]

        return ctor(
            name=name,
            variant_name=variant_name,
            raw_dict=args_dict,
            cfg_set=cfg_set,
        )
