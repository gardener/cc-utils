import setuptools
import os

own_dir = os.path.abspath(os.path.dirname(__file__))


def requirements():
    yield 'gardener-oci>=' + version()

    with open(os.path.join(own_dir, 'requirements.txt')) as f:
        for line in f.readlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            yield line


def modules():
    module_names = [
        os.path.basename(os.path.splitext(module)[0]) for module in
        os.scandir(path=own_dir)
        if module.is_file() and module.name.endswith('.py')
    ]

    # avoid including other setup-scripts
    module_names.remove('setup.oci')
    module_names.remove('setup.ocm')
    module_names.remove('setup.whd')
    return module_names


def packages():
    package_names = setuptools.find_packages()

    # remove packages (distributed via separate distribution-packages)
    package_names.remove('whd')
    package_names.remove('oci')
    #package_names.remove('ocm')
    return package_names


def version():
    with open(os.path.join(own_dir, 'ci', 'VERSION')) as f:
        return f.read().strip()


setuptools.setup(
    name='gardener-cicd-libs',
    version=version(),
    description='Gardener CI/CD Libraries',
    long_description='Gardener CI/CD Libraries',
    long_description_content_type='text/markdown',
    python_requires='>=3.10',
    py_modules=modules(),
    packages=packages(),
    package_data={
        '':['*.mako'],
        'ci': ['VERSION'],
        'concourse':[
            'resources/LAST_RELEASED_TAG',
            'resources/*.mako',
            '*.mako',
        ],
        'gci':[
            'ocm-component-descriptor-schema.yaml',
        ],
        'ocm':[
            'ocm-component-descriptor-schema.yaml',
        ],
    },
    install_requires=list(requirements()),
    entry_points={
    },
)
