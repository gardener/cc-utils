import setuptools
import shutil
import os

own_dir = os.path.abspath(os.path.dirname(__file__))


def requirements():
    yield f'gardener-cicd-libs=={version()}'
    yield f'gardener-cicd-dso=={version()}'


def modules():
    return []
    return [
        os.path.basename(os.path.splitext(module)[0]) for module in
        os.scandir(path=own_dir)
        if module.is_file() and module.name.endswith('.py')
    ]


def version():
    d = own_dir
    while True:
        candidate = os.path.join(d, 'VERSION')
        if os.path.isfile(candidate):
            with open(candidate) as f:
                return f.read().strip()
        d = os.path.abspath(os.path.join(d, os.pardir))
    raise RuntimeError(f'did not find VERSION file in {own_dir} and all pardirs')


# cp scripts
src_bin_dir = os.path.join(own_dir, os.pardir, 'bin')
tgt_bin_dir = os.path.join(own_dir, 'bin')
shutil.copytree(src_bin_dir, tgt_bin_dir)


setuptools.setup(
    name='gardener-cicd-cli',
    version=version(),
    description='Gardener CI/CD Command Line Interface',
    python_requires='>=3.10',
    py_modules=modules(),
    packages=setuptools.find_packages(),
    install_requires=list(requirements()),
    scripts=[os.path.join(tgt_bin_dir, 'purge_history')],
    entry_points={
        'console_scripts': [
            'gardener-ci = gardener_ci.cli_gen:main',
            'cli.py = gardener_ci.cli_gen:main', # XXX backwards-compatibilty - rm this
            'yaml2json = gardener_ci.yaml2json:main'
        ],
    },
)
