import setuptools
import os

own_dir = os.path.abspath(os.path.dirname(__file__))


def requirements():
    in_list = ('yaml', 'yamllint', 'termcolor', 'urllib', 'elasticsearch')
    with open(os.path.join(own_dir, 'requirements.txt')) as f:
        for line in f.readlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if any(s in line for s in in_list):
                yield line


def modules():
    return [
        'util',
        'ctx',
    ]


def version():
    with open(os.path.join(own_dir, 'VERSION')) as f:
        return f.read().strip()


setuptools.setup(
    name='gardener-cicd-base',
    version=version(),
    description='Gardener CI/CD Base Libraries',
    python_requires='>=3.10.*',
    py_modules=modules(),
    packages=['ccc', 'ci', 'model'],
    package_data={
        'ci':['version'],
    },
    install_requires=list(requirements()),
    entry_points={
    },
)
