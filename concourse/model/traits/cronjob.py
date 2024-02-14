# SPDX-FileCopyrightText: 2024 SAP SE or an SAP affiliate company and Gardener contributors
#
# SPDX-License-Identifier: Apache-2.0


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
        type=str | dict,
        doc='''
        If set, specifies the time range in between which to run the job.

        Has two forms:

            - `WORKING_HOURS`, which limits the job to be triggered between 08:00 and 18:00.
            - A dict with `begin` and `end` in the format of `HH:MM`, e.g.:

                .. code-block:: yaml

                    timerange:
                        begin: '09:45'
                        end: '13:30'

            The timerange is interpreted relative to the configured timezone

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

        if isinstance(timerange, str):
            if timerange == 'WORKING_HOURS':
                return TimeRange(
                    begin=datetime.time(hour=8, minute=0),
                    end=datetime.time(hour=20, minute=0),
                )
            else:
                raise ValueError(timerange)
        elif isinstance(timerange, dict):
            return TimeRange(
                begin=datetime.time.fromisoformat(timerange['begin']),
                end=datetime.time.fromisoformat(timerange['end']),
            )
        else:
            raise ValueError(timerange)

    def resource_name(self):
        # variant-names must be unique, so this should suffice, unless there are different
        # cron-resources (e.g. different days/timeranges)

        name = '-'.join((
            self.variant_name,
            self.interval,
        ))

        if tr := self.timerange:
            name += f'-{tr.begin.strftime("%H%M")}-{tr.end.strftime("%H%M")}'

        name += '-cron'

        return name

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
