import setuptools
import os

own_dir = os.path.abspath(os.path.dirname(__file__))


def requirements():
    yield 'gardener-cicd-libs'
    yield 'gardener-cicd-cli'

    with open(os.path.join(own_dir, 'requirements.dso.txt')) as f:
        for line in f.readlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            yield line


def modules(): return []


def version():
    with open(os.path.join(own_dir, 'VERSION')) as f:
        return f.read().strip()


setuptools.setup(
    name='gardener-cicd-dso',
    version=version(),
    description='Gardener CI/CD DevSecOps',
    python_requires='>=3.9.*',
    py_modules=modules(),
    packages=[
        'checkmarx',
        'clamav',
        'protecode',
        'whitesource',
    ],
    package_data={
        'ci':['version'],
    },
    install_requires=list(requirements()),
    entry_points={
    },
)
