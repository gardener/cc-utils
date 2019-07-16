import setuptools
import os

own_dir = os.path.abspath(os.path.dirname(__file__))


def requirements():
    with open(os.path.join(own_dir, 'requirements.txt')) as f:
        for line in f.readlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            yield line


def modules():
    return [
        os.path.basename(os.path.splitext(module)[0]) for module in
        os.scandir(path=own_dir)
        if module.is_file() and module.name.endswith('.py')
    ]


def packages():
    return [
        'ccc',
        'clamav',
        'concourse',
        'container',
        'github',
        'kube',
        'landscape_setup',
        'mail',
        'model',
        'product',
        'protecode',
        'slackclient',
        'whd',
    ]


def version():
    with open(os.path.join(own_dir, 'VERSION')) as f:
        return f.read().strip()


setuptools.setup(
    name='cc-utils',
    version=version(),
    description='Gardener CI/CD Utils',
    python_requires='>=3.7.*',
    py_modules=modules(),
    packages=packages(),
    install_requires=list(requirements()),
    entry_points={
        'console_scripts': [
            'cli=cli:main',
        ],
    },
)
