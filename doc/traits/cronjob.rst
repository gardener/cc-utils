`cronjob` Trait
===============

.. trait::
    :name: cronjob

Declaring :doc:`Build Jobs </pipeline_job>` will be triggered (executed) periodically.

.. note::

  The presence of this trait affects the default triggering behaviour for head updates in the
  main repository (changing it to `false`; i.e.: head-updates will be ignored).


Example
-------

.. code-block:: yaml

  traits:
    cronjob:
      interval: '42m' # run every 42 minutes
