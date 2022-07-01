====================
*scan_sources* Trait
====================

.. trait::
    :name: scan_sources

This trait enables different compliance scans for sources and resources of your component descriptor.

Path filtering semantics
========================

* no paths specified: no filtering, all files will be scanned
* only exclude paths: scan everything except excplicitly excluded
* only include paths: scan only excplicitly included
* include and exclude paths specified: only included then filter out excluded

Supported labels
================

checkmarx
---------
The checkmarx scan will be triggered when the `source_analysis` label is absent or the source defines the label with the policy set to 'scan'.

If the checkmarx scan should be skipped define the label with the policy attribute set to `skip`.

+---------------+-----------+--------------------------------------------------------------------+
| name          | type      | description                                                        |
+===============+===========+====================================================================+
| policy        | enum      | whether to scan source or not. Must either be 'scan' or 'skip'     |
+---------------+-----------+--------------------------------------------------------------------+
| exclude_paths | list[str] | (optional) regex paths of your source to exclude from the scan     |
+---------------+-----------+--------------------------------------------------------------------+
| include_paths | list[str] | (optional) regex paths of your source to include from the scan     |
+---------------+-----------+--------------------------------------------------------------------+

Example label:

.. code-block:: yaml

  - name: 'cloud.gardener.cnudie/dso/scanning-hints/source_analysis/v1'
    value:
      policy: 'scan' # | 'skip'
      path_config:
        include_paths:
        - 'src/.*'
        - 'pgk/.*'
        exclude_paths:
        - 'src/test.*'
        - 'pkg/hack.*'


whitesource
-----------
The whitesource scan will be triggered if the `source_id` label is absent or defined with the policy set to `scan`.

To skip the whitesource scan the source_id label has to be set with the policy: 'skip'.

+---------------+-----------+--------------------------------------------------------------------+
| name          | type      | description                                                        |
+===============+===========+====================================================================+
| policy        | enum      | whether to scan source or not. Must either be 'scan' or 'skip'     |
+---------------+-----------+--------------------------------------------------------------------+
| exclude_paths | list[str] | (optional) regex paths of your source to exclude from the scan     |
+---------------+-----------+--------------------------------------------------------------------+
| include_paths | list[str] | (optional) regex paths of your source to include from the scan     |
+---------------+-----------+--------------------------------------------------------------------+

.. code-block:: yaml

  - name: 'cloud.gardener.cnudie/dso/scanning-hints/source_id/v1'
    value:
      policy: 'scan' # | 'skip' | 'scan_source'
      path_config:
        exclude_paths:
        - 'test.*'
        - 'foo.*'
        - '.*bar.*'


protecode
---------

TBD