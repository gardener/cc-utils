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

import dataclasses
import datetime

from concourse.model.job import JobVariant
from concourse.model.base import Trait, TraitTransformer, AttributeSpec


@dataclasses.dataclass
class TimeRange:
    begin: datetime.time
    end: datetime.time


ATTRIBUTES = (
    AttributeSpec.optional(
        name='interval',
        default='5m',
        doc='''
        go-style time interval between job executions. Supported suffixes are: `s`, `m`, `h`
        ''',
    ),
    AttributeSpec.optional(
        name='timezone',
        default='Europe/Berlin',
        doc='''
        timezone to use for start/end-times (if not configured, timezone has no effect)
        `List of timezones <https://en.wikipedia.org/wiki/List_of_tz_database_time_zones>`_
        ''',
    ),
    AttributeSpec.optional(
        name='days',
        default=None,
        type=list[str] | str,
        doc='''
        If set, specifies the weekdays on which to run the job. Has two forms:
        - list of strings (Monday, Tuesday, Wednesday, Thursday, Friday, Saturday, Sunday)
        - single string (WORKING_DAYS - i.e. MO..FR)
        ''',
    ),
    AttributeSpec.optional(
        name='timerange',
        default=None,
        type=str,
        doc='''
        If set, specifies the time range in between which to run the job.

        Currently, the only supported value is `WORKING_HOURS`, which limits the job to be
        triggered between 08:00 and 18:00 (relative to configured timezone)
        ''',
    ),
)


class CronTrait(Trait):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @classmethod
    def _attribute_specs(cls):
        return ATTRIBUTES

    @property
    def interval(self):
        return self.raw['interval']

    @property
    def timezone(self):
        return self.raw['timezone']

    @property
    def days(self):
        if not (days := self.raw.get('days')):
            return None

        work_days = (
            'Monday',
            'Tuesday',
            'Wednesday',
            'Thursday',
            'Friday',
        )
        weekend_days = (
            'Saturday',
            'Sunday',
        )
        all_days = work_days + weekend_days

        if isinstance(days, str):
            if days == 'WORKING_DAYS':
                return work_days
            else:
                raise ValueError('days must equal WORKING_DAYS if set to a single str')
        if isinstance(days, list):
            for d in days:
                if d not in all_days:
                    raise ValueError(f'{d} not in {all_days}')
            return days
        else:
            raise ValueError(days)

    @property
    def timerange(self):
        if not (timerange := self.raw['timerange']):
            return None

        if timerange == 'WORKING_HOURS':
            return TimeRange(
                begin=datetime.time(hour=8, minute=0),
                end=datetime.time(hour=20, minute=0),
            )
        else:
            raise ValueError(timerange)

    def resource_name(self):
        # variant-names must be unique, so this should suffice, unless there are different
        # cron-resources (e.g. different days/timeranges)

        return '-'.join((
            self.variant_name,
            self.interval,
            'cron',
        ))

    def transformer(self):
        return CronTraitTransformer()


class CronTraitTransformer(TraitTransformer):
    name = 'cronjob'

    def process_pipeline_args(self, pipeline_args: JobVariant):
        main_repo = pipeline_args.main_repository()
        if main_repo:
            if 'trigger' not in pipeline_args.raw['repo']:
                main_repo._trigger = False
        # todo: inject cron-resource
