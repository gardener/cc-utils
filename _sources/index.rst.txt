============================
Gardener CI/CD Documentation
============================

.. toctree::
    :includehidden:
    :hidden:
    :titlesonly:
    :maxdepth: 2

    pipeline_contract


This documentation describes how the components of the `Gardener <https://github.com/gardener>`_ project
are produced. See the linked documentation for more details on Gardener itself.


Overview
========

.. image:: res/overview.svg


Gardener is tightly integrated into GitHub. In particular, each Gardener Component is represented by exactly
one GitHub repository. Releases of Gardener Components are represented by GitHub releases.

As is common practice in the Kubernetes eco-system, the main deliverables of each Gardener Component are
container images. Each release of a Gardener Component thus also encompasses a `Comonent Descriptor`, which
declares references to any container images that have been created for a given component release.


Indices and Tables
==================

* :ref:`genindex`
