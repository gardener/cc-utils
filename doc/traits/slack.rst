=============
*slack* Trait
=============

.. trait::
    :name: slack


If declared, release notes are published to the configured slack channels upon release.


Example
=======

.. code-block:: yaml

  traits:
    release: ~
    slack:
      default_channel: 'internal_scp_workspace'
      channel_cfgs:
        internal_scp_workspace':
          channel_name': 'my_slack_channel'
          slack_cfg_name: 'scp_workspace' # see cc-config repository
