==================
*image_scan* Trait
==================

If defined, OCI-Images declared as resources in the current component's `Component Descriptor`
are scanned using the configured scanning tools (see attributes documentation).

.. note::
   Unless mentioned otherwise, all OCI-Layers will be scanned. This means that files that are
   "logically" removed by a later layer will be included in scans. In case a file is overwritten
   with different contents, all variants are subject to being scanned.

.. trait::
    :name: image_scan
