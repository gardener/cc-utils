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
      channel_cfgs:
      - channel_name: 'my_slack_channel'
        slack_cfg_name: 'my_slack_cfg_name'
