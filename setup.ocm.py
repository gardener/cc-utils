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
    with open(os.path.join(own_dir, 'ocm', 'VERSION')) as f:
        return f.read().strip()


setuptools.setup(
    name='gardener-ocm', # todo: switch to `ocm-lib` once we have support for different versions
    version=version(),
    description='Open-Component-Model (OCM) language bindings',
    long_description='Open-Component_model (OCM) language bindings',
    long_description_content_type='text/markdown',
    python_requires='>=3.11',
    py_modules=(),
    packages=('ocm',),
    package_data={
        'ocm': ('VERSION',),
    },
    install_requires=list(requirements()),
    entry_points={
        'console_scripts': [
            # avoid conflict w/ "ocm-cli" (github.com/open-component-model/ocm)
            'gardener-ocm = ocm.__main__:main'
        ],
    },
)
