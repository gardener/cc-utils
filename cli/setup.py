import setuptools
import os

own_dir = os.path.abspath(os.path.dirname(__file__))


def requirements():
    yield f'gardener-cicd-libs=={version()}'


def modules():
    return []
    return [
        os.path.basename(os.path.splitext(module)[0]) for module in
        os.scandir(path=own_dir)
        if module.is_file() and module.name.endswith('.py')
    ]


def version():
    with open(os.path.join(own_dir, os.pardir, 'ci','version')) as f:
        return f.read().strip()


setuptools.setup(
    name='gardener-cicd-cli',
    version=version(),
    description='Gardener CI/CD Command Line Interface',
    python_requires='>=3.8.*',
    py_modules=modules(),
    packages=setuptools.find_packages(),
    install_requires=list(requirements()),
    entry_points={
        'console_scripts': [
            'gardener-ci = gardener_ci.cli_gen:main',
            'cli.py = gardener_ci.cli_gen:main', # XXX backwards-compatibilty - rm this
            'yaml2json = gardener_ci.yaml2json:main'
        ],
    },
)
