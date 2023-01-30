import setuptools
import os

own_dir = os.path.abspath(os.path.dirname(__file__))


def requirements():
    yield 'gardener-cicd-libs'
    yield 'gardener-cicd-cli'


def modules(): return []


def version():
    with open(os.path.join(own_dir, 'VERSION')) as f:
        return f.read().strip()


setuptools.setup(
    name='gardener-cicd-dso',
    version=version(),
    description='Gardener CI/CD DevSecOps',
    python_requires='>=3.10',
    py_modules=modules(),
    packages=[
        'checkmarx',
        'clamav',
        'protecode',
    ],
    package_data={
        'ci':['version'],
    },
    install_requires=list(requirements()),
    entry_points={
    },
)
