<%def name="time_resource(name, cron_trait)">
<%
  days = cron_trait.days
  timerange = cron_trait.timerange
%>
- name: ${name}
  type: time
  source:
    interval: ${cron_trait.interval}
    location: ${cron_trait.timezone}
%if days:
    days: ${list(days)}
%endif
%if timerange:
    start: ${timerange.begin.strftime('%H:%M')}
    stop: ${timerange.end.strftime('%H:%M')}
%endif
</%def>
