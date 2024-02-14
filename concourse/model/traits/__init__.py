# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


import functools

from ci.util import not_none

from concourse.model.base import ModelValidationError


@functools.cache
def _traits():
    from .component_descriptor import ComponentDescriptorTrait
    from .cronjob import CronTrait
    from .draft_release import DraftReleaseTrait
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
