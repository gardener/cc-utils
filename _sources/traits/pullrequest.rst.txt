====================
*pull-request* Trait
====================

.. trait::
    :name: pullrequest


Turns the declaring job into a pull-request job. This means it will be triggered upon the
creation or updating of GitHub pull-requests for the main repository (and only those) and
post executions results to the corresponding PRs.

Information about the pull-request being processed will be exposed to jobs at runtime:
`PULLREQUEST_URL` contains the full pull-request URL whereas `PULLREQUEST_ID` contains the
pull request number.


Policies / Pull-Request Label Handling
======================================

For security reasons, Pull-Requests are by default only reacted upon if a label is added to them.
To mitigate the risk of subsequent updates with malicious changes, said labels are removed at the
beginning of PR build job executions and have thus to be added again if a repeated PR build job
execution is required.

To make this more obvious, a "replacement label" is added after label removal.


Example
=======

.. code-block:: yaml

  traits:
    pull-request: ~
