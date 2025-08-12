=====================
Release Notes Process
=====================

In order to support the developers when creating a new release of a
component, a new process is established to gather all release-relevant
information since the last release to build the release notes text.

Format
======

The release-relevant information has to be flagged as such, thus we
introduce the following format, which is based on the format that
`kubernetes <https://raw.githubusercontent.com/kubernetes/kubernetes/master/.github/PULL_REQUEST_TEMPLATE.md>`__
uses for building the release notes. ::

  ```<category> <target group>

  <your release note>

  ```

Possible values:

  - category: breaking\|feature\|bugfix\|doc\|other
  - target\_group: user\|operator\|developer\|dependency

Example: ::

  ```improvement user

  This is my first release note

  ```

If no release note is required, just write :literal:`NONE` within the block or delete the block altogether.

Category - Title Mapping
^^^^^^^^^^^^^^^^^^^^^^^^

+---------------+------------------------------+
| category      | release note section title   |
+===============+==============================+
| improvement   | Improvement                  |
+---------------+------------------------------+
| noteworthy    | Most notable changes         |
+---------------+------------------------------+
| action        | Action Required              |
+---------------+------------------------------+

How to Contribute to Release Notes
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Now that we know the format for tagging release-relevant information,
where do we put it so that it can be fetched automatically?

There are two options:

1. As description in *pull requests* (preferred)
2. As *commit message*

Pull Requests
~~~~~~~~~~~~~

The preferred option is to create a pull request that contains your
proposed changes (to the code-basis) and in the description a code-block
like above.

**Example:** Here is an example pull request that contains release
notes: https://github.com/gardener/dashboard/pull/147 The release notes
appeared in the release
https://github.com/gardener/dashboard/releases/tag/1.18.0.

.. note:: Pull requests that are still open and not merged are not considered for the release notes.

Even if the pull request is already merged you can still edit the
description in case you need to make changes to your release note and it
will be used once the release is created.

To make it easier for the community to contribute to the release notes
you can add a pull request template to your repository, e.g. like the
`template <https://raw.githubusercontent.com/gardener/dashboard/master/.github/pull_request_template.md>`__
used for the gardener dashboard.

See
https://help.github.com/articles/creating-a-pull-request-template-for-your-repository/
on how to create pull request template for your repository.

Commits
~~~~~~~

A second option on how to contribute to the release notes is to add a
code-block like above to your commit message. This option is not
preferred as once you have pushed your changes to the remote repository
it cannot easily be changed anymore so you have to be very cautious.

Draft Release
^^^^^^^^^^^^^

In order to see how the release notes would look like, draft releases
are created/updated - usually on every head update.

.. note:: Only users with write access to the repository can view drafts of releases

To enable draft releases, add the
`draft\_release <https://github.com/gardener/dashboard/blob/51fc9792af32da137d3c1b3e69635b2093dbbfd7/.ci/pipeline_definitions#L28>`__
trait to your job that has (or inherits) the *version* trait.
Usually you would add it to the *head-update* job.

Transporting Release Notes
^^^^^^^^^^^^^^^^^^^^^^^^^^

A component can depend upon other components. When releasing a
component, ideally the release notes of the source repository (for which
the release was created) should be included in the target component.
This can be achieved by reusing the pull request mechanism as explained
above:

1. On release of a source component, all pull requests (and commits are fetched since the last release)
2. The release notes are extracted from the pull requests and commits
3. A new pull request is created on the target component which contains the release notes within the description as code-blocks
4. On release of the target component the release notes text is generated
5. It will start over again for the next component with step 1

Posting Release Notes To Slack
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

When naming a ``default_channel`` and adding channel\_cfgs as shown in the example below to the ``slack`` trait of your pipeline definition, the release notes will be posted to specified channel (channel name or channel id). The ``slack_cfg_name`` has to correspond to the config element name of a known slack config on our Concourse.

Example:

.. code:: yaml

        release:
          traits:
            release:
              nextversion: 'bump_minor'
            slack:
              default_channel: 'channel_cfg' # This channel config will be used for posting the release notes to
              channel_cfgs:
                channel_cfg: # channel config name
                  channel_name: 'my_slack_channel_name' # you can specify the channel name or channel id
                  slack_cfg_name: 'example_slack_workspace' # Specifies the slack configuration that holds the slack api key (which is bound to a slack workspace)
                  post_full_release_notes: False
