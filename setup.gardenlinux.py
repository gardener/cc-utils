import setuptools
import os

own_dir = os.path.abspath(os.path.dirname(__file__))


def requirements():
    yield 'gardener-cicd-libs'

    with open(os.path.join(own_dir, 'requirements.gardenlinux.txt')) as f:
        for line in f.readlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            yield line


def modules():
    return [
    ]


def version():
    with open(os.path.join(own_dir, 'ci', 'version')) as f:
        return f.read().strip()


setuptools.setup(
    name='gardenlinux',
    version=version(),
    description='gardenlinux CICD utils',
    python_requires='>=3.8.*',
    py_modules=modules(),
    packages=['gardenlinux'],
    package_data={
        'ci':['version'],
    },
    install_requires=list(requirements()),
    entry_points={
    },
)
