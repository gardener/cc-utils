import setuptools
import os

own_dir = os.path.abspath(os.path.dirname(__file__))


def requirements():
    with open(os.path.join(own_dir, 'requirements.ocm.txt')) as f:
        for line in f.readlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            yield line


def version():
    version_path = os.environ.get(
        'version_file',
        os.path.join(own_dir, 'ocm', 'VERSION'),
    )

    with open(version_path) as f:
        return f.read().strip()


setuptools.setup(
    name='gardener-ocm', # todo: switch to `ocm-lib` once we have support for different versions
    version=version(),
    description='Open-Component-Model (OCM) language bindings',
    long_description='Open-Component_model (OCM) language bindings',
    long_description_content_type='text/markdown',
    python_requires='>=3.11',
    py_modules=(
        'gziputil',
        'ioutil',
        'reutil',
        'tarutil',
        'version',
    ),
    packages=(
        'cnudie',
        'ctt',
        'ocm',
    ),
    package_data={
        'ctt': (
            'simple-cfg',
            'README',
        ),
        'ocm': (
            'VERSION',
            'ocm-component-descriptor-schema.yaml',
        ),
    },
    install_requires=list(requirements()),
    entry_points={
        'console_scripts': [
            # avoid conflict w/ "ocm-cli" (github.com/open-component-model/ocm)
            'gardener-ocm = ocm.__main__:main'
        ],
    },
)
