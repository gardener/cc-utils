import setuptools
import os

own_dir = os.path.abspath(os.path.dirname(__file__))


def requirements():
    with open(os.path.join(own_dir, 'requirements.ctt.txt')) as f:
        for line in f.readlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            yield line


def version():
    with open(os.path.join(own_dir, 'ctt', 'VERSION')) as f:
        return f.read().strip()


setuptools.setup(
    name='gardener-ctt',
    version=version(),
    description='CTT (replication tool for OCM)',
    long_description='CTT (replication tool for OCM)',
    long_description_content_type='text/markdown',
    python_requires='>=3.12',
    py_modules=(),
    packages=(
        'ctt',
        'cnudie', # hack: until cnudie.retrieve is not factored into gardener-ocm, package directly
    ),
    package_data={
        'ctt': (
            'cli',
            'simple-cfg',
        ),
    },
    install_requires=list(requirements()),
    entry_points={
        'console_scripts': [
            'ctt = ctt.__main__:main'
        ],
    },
)
