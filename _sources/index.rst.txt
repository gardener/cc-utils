============================
Gardener CI/CD Documentation
============================

.. toctree::
    :includehidden:
    :hidden:
    :titlesonly:
    :maxdepth: 2

    pipeline_contract

CI/CD-Overview
==============

Image Build Pipelines
^^^^^^^^^^^^^^^^^^^^^

`Gardener <https://github.com/gardener>`_ consists of many (docker) images which are deployed on K8s. A repetitive task for developers is to build docker images and upload them to the image registry (GCR). We automate this image build process. Each Github repository which builds an image will have a corresponding image build pipeline.

.. image:: images/image_build_process.png
    :width: 600

Component Contract
^^^^^^^^^^^^^^^^^^

Each component declares a file :literal:`.ci/pipeline_definitions` in the component root directory.
A scanner periodically checks your repository and

* reads the pipeline_definitions file
* generates a Concourse pipeline
* deploys this pipeline to the external/internal concourse

Please check :ref:`our documentation <build_pipeline_reference_manual>` to understand how to define your own pipeline.

Indices and Tables
==================

* :ref:`genindex`
