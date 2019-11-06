import setuptools
import os

own_dir = os.path.abspath(os.path.dirname(__file__))


def requirements():
    with open(os.path.join(own_dir, 'requirements.txt')) as f:
        for line in f.readlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            # we only need yaml, yamllint termcolor, urllib
            if not 'yaml' in line:
                continue
            if not 'termcolor' in line:
                continue
            if not 'urllib3' in line:
                continue
            yield line


def modules():
    return [
        'util',
        'ctx',
    ]


def version():
    with open(os.path.join(own_dir, 'ci', 'version')) as f:
        return f.read().strip()


setuptools.setup(
    name='gardener-cicd-base',
    version=version(),
    description='Gardener CI/CD Base Libraries',
    python_requires='>=3.7.*',
    py_modules=modules(),
    packages=['ci', 'model'],
    package_data={
        'ci':['version'],
    },
    install_requires=list(requirements()),
    entry_points={
    },
)
