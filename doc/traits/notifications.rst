=====================
*notifications* Trait
=====================

Used to customise build result notifications (most prominently sending error
mails upon errors).


.. trait::
    :name: notifications


Example
=======

.. code-block:: yaml

   traits:
       notifications:
           demo_breakers:
               on_error:
                   triggering_policy: 'only_first'
                   recipients:
                   - email_addresses:
                       - foo.bar@mycloud.com
                       - bar.bazz@mycloud.com
                   - committers
                   - component_diff_owners
                   - codeowners
                   slack:
                       channel_cfgs:
                         - channel_names: 
                           - 'my_slack_channel'
                           slack_cfg_name: 'my_slack_cfg_name'
                   inputs:
                   - component_descriptor_dir
