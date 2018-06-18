from util import ensure_not_none

from concourse.pipelines.modelbase import ModelValidationError

from .cron import CronTrait
from .publish import PublishTrait
from .pullrequest import PullRequestTrait
from .release import ReleaseTrait
from .scheduling import SchedulingTrait
from .version import VersionTrait

TRAITS = {
    'version': VersionTrait,
    'cronjob': CronTrait,
    'pull-request': PullRequestTrait,
    'release': ReleaseTrait,
    'scheduling': SchedulingTrait,
    'publish': PublishTrait,
}

class TraitsFactory(object):
    @staticmethod
    def create(name: str, variant_name: str, args_dict: dict):
        if not name in TRAITS:
            raise ModelValidationError('no such trait: ' + str(name))
        ensure_not_none(args_dict)

        ctor = TRAITS[name]

        return ctor(name=name, variant_name=variant_name, raw_dict=args_dict)


