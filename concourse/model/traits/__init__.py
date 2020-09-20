# SPDX-FileCopyrightText: 2019 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0

from ci.util import not_none

from concourse.model.base import ModelValidationError

from .component_descriptor import ComponentDescriptorTrait
from .cronjob import CronTrait
from .draft_release import DraftReleaseTrait
from .image_alter import ImageAlterTrait
from .image_scan import ImageScanTrait
from .image_upload import ImageUploadTrait
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
    'image_upload': ImageUploadTrait,
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


class TraitsFactory(object):
    @staticmethod
    def create(
        name: str,
        variant_name: str,
        args_dict: dict,
        cfg_set,
    ):
        if name not in TRAITS:
            raise ModelValidationError('no such trait: ' + str(name))
        not_none(args_dict)

        ctor = TRAITS[name]

        return ctor(
            name=name,
            variant_name=variant_name,
            raw_dict=args_dict,
            cfg_set=cfg_set,
        )
