'''
minimal (but likely growing) subset of `gardener-cicd-libs` package for usage w/ GitHubActions.

This package takes special care to avoid dependencies specific towards Concourse-Pipeline-Template
or related infrastructure (e.g. `concourse`, `ccc`, `model` packages), and also omits dependencies
against some "heavy" packages, such as hyperscaler-SDKs, which are referenced by `gardener-cicd-libs`
for historical reasons.

Caveat: as this package has overlaps w/ `gardener-cicd-libs`, it is not adviseable to create mixed
installations (containing both `gardener-gha-libs` and `gardener-cicd-libs`).
'''

import setuptools
import os

own_dir = os.path.abspath(os.path.dirname(__file__))


def version():
    with open(os.path.join(own_dir, 'ci', 'VERSION')) as f:
        return f.read().strip()


def requirements():
    yield 'gardener-oci'
    yield 'gardener-ocm'

    # omit packages not needed for minimal subset of cc-utils / gardener-cicd-libs
    omit_package_names = (
        'Mako',
        'Sphinx',
        'aliyun-python-sdk-core',
        'aliyun-python-sdk-ecs',
        'aliyun-python-sdk-ram',
        'brypt',
        'boto3',
        'dockerfile-parse',
        'docutils',
        'google-api-core',
        'google-api-python-client',
        'google-auth',
        'google-cloud-storage',
        'google-crc32',
        'kubernetes',
        'msal',
        'openstacksdk',
        'oss2',
        'pycryptodome',
        'pylama',
        'pylint',
        'python-gitlab',
        'pytimeparse',
        'slack-sdk',
        'sphinx_rtd_theme',
        'sseclient-py',
        'urllib3',
    )

    with open(os.path.join(own_dir, 'requirements.txt')) as f:
        for line in f.readlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            skip = False

            for omit in omit_package_names:
                if line.lower().startswith(omit.lower()):
                    print(f'skipping: {line=}')
                    skip = True
                    break

            if skip:
                continue

            yield line


def modules():
    module_names = [
        os.path.basename(os.path.splitext(module)[0]) for module in
        os.scandir(path=own_dir)
        if module.is_file() and module.name.endswith('.py')
    ]

    # avoid modules that would introduce undesired dependencies
    omit_modules = (
        'ctx',
        'dockerutil',
        'http_requests',
        'mailutil',
        'makoutil',
    )
    for name in omit_modules:
        module_names.remove(name)

    # avoid including other setup-scripts
    module_names.remove('setup')
    module_names.remove('setup.gha')
    module_names.remove('setup.oci')
    module_names.remove('setup.ocm')
    module_names.remove('setup.whd')
    return module_names


def packages():
    package_names = setuptools.find_packages(exclude=['setup'])

    # avoid packages that would introduce undesired dependencies
    omit_packages = (
        'ccc',
        'concourse',
        'delivery',
        'mail',
        'model',
        'slackclient',
        'whd',
    )

    for package in omit_packages:
        package_names.remove(package)

    # skip packages installed by other distribution-packages
    package_names.remove('oci')
    package_names.remove('ocm')
    return package_names


setuptools.setup(
    name='gardener-gha-libs',
    version=version(),
    description='Gardener CI/CD Libraries for GitHubActions',
    long_description='Gardener CI/CD Libraries for GitHubActions',
    long_description_content_type='text/markdown',
    python_requires='>=3.12',
    py_modules=modules(),
    packages=packages(),
    package_data={
        'ci': ['VERSION'],
    },
    install_requires=list(requirements()),
    entry_points={
    },
)
