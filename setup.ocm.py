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


def modules():
    return [
    ]


def version():
    with open(os.path.join(own_dir, 'ocm', 'VERSION')) as f:
        return f.read().strip()


setuptools.setup(
    name='ocm-lib',
    version=version(),
    description='Open-Component-Model (OCM) language bindings',
    python_requires='>=3.11',
    py_modules=modules(),
    packages=('ocm',),
    package_data={
        'ocm': ('VERSION',),
    },
    install_requires=list(requirements()),
    entry_points={
    },
)
