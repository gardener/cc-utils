import setuptools
import os

own_dir = os.path.abspath(os.path.dirname(__file__))


def requirements():
    yield 'gardener-cicd-libs'

    with open(os.path.join(own_dir, 'requirements.cfg_mgmt.txt')) as f:
        for line in f.readlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            yield line


def version():
    with open(os.path.join(own_dir, 'cfg_mgmt', 'VERSION')) as f:
        return f.read().strip()


setuptools.setup(
    name='gardener-cicd-cfg-mgmt',
    version=version(),
    description='Gardener CI/CD Config Management',
    long_description='Gardener CI/CD Config Management',
    long_description_content_type='text/markdown',
    python_requires='>=3.12',
    packages=['cfg_mgmt'],
    install_requires=list(requirements()),
    entry_points={},
)
