# Transport tool for Component Descriptors v2

The tool can be use to transport component descriptors
and the images from a repository to and an other. **Airgap**
environment are also supported.

It takes as an input a component descriptor file or it
can retrieve a it from a registry based on the context URL.

## Requirements

- export `CC_CONFIG_DIR` to point to cc-config work tree
- retrieve a copy of github.com/gardener/cc-utils
  - install all dependencies declared in requirements.txt
  - add cc-utils worktree to PYTHONPATH

## Usage

With **sync**, two steps are executed, `download` and `upload`.
Images and component descriptors will be downloaded into
the `resources` directory so they can later be processed and
uploaded from this directory.

For **airgap** environment, the `download` must be executed where
an access to public images is available. Once all the images
are retrieved, the `resources` directory can then be copied and
used from somewhere else without public access.

For the copy, it is possible to use the archive parameter which
allows one to create an archive from the resources direct and
extract here in a different environment.


```
usage: transport.py [-h] {download,upload,sync,archive} ...

Transport tool for component descriptor v2

positional arguments:
  {download,upload,sync,archive}
                        Actions
    download            Download component descriptor and images
    upload              Upload component descriptor and images
    sync                Run download and upload
    archive             Manage resources archive

optional arguments:
  -h, --help            show this help message and exit
```
