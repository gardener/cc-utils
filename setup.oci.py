import setuptools
import os

own_dir = os.path.abspath(os.path.dirname(__file__))


def requirements():
    with open(os.path.join(own_dir, 'requirements.oci.txt')) as f:
        for line in f.readlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            yield line


def modules():
    return [
    ]


def version():
    with open(os.path.join(own_dir, 'VERSION')) as f:
        return f.read().strip()


setuptools.setup(
    name='gardener-oci',
    version=version(),
    description='gardener OCI lib',
    python_requires='>=3.9.*',
    py_modules=modules(),
    packages=['oci'],
    package_data={
        'ci':['version'],
    },
    install_requires=list(requirements()),
    entry_points={
    },
)
