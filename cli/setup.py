import setuptools
import shutil
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
    # HACK: as we can currently only manage a single version-file for monolithic release,
    # and gardener-oci should have no dependencies towards other packages, point to
    # oci-package's versionfile
    def iter_candidate():
        yield os.path.join(own_dir, 'oci', 'VERSION')
        yield os.path.join(own_dir, '..', 'VERSION')
        yield os.path.join(own_dir, '../..', 'VERSION')

    for path in iter_candidate():
        if not os.path.exists(path):
            print(f'did not find versionfile at {path=}')
            continue

        with open(path) as f:
            return f.read().strip()
    else:
        raise RuntimeError('did not find versionfile')


# cp scripts
src_bin_dir = os.path.join(own_dir, os.pardir, 'bin')
tgt_bin_dir = os.path.join(own_dir, 'bin')
shutil.copytree(src_bin_dir, tgt_bin_dir)


setuptools.setup(
    name='gardener-cicd-cli',
    version=version(),
    description='Gardener CI/CD Command Line Interface',
    python_requires='>=3.11',
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
