import setuptools
import shutil
import os

own_dir = os.path.abspath(os.path.dirname(__file__))


def requirements():
    yield f'gardener-cicd-libs=={version()}'


def version():
    with open(os.path.join(own_dir, 'gardener_ci', 'VERSION')) as f:
        return f.read().strip()


# cp scripts
src_bin_dir = os.path.join(own_dir, os.pardir, 'bin')
tgt_bin_dir = os.path.join(own_dir, 'bin')
shutil.copytree(src_bin_dir, tgt_bin_dir)


setuptools.setup(
    name='gardener-cicd-cli',
    version=version(),
    description='Gardener CI/CD Command Line Interface',
    long_description='Gardener CI/CD Command Line Interface',
    long_description_content_type='text/markdown',
    python_requires='>=3.11',
    py_modules=[],
    packages=['gardener_ci'],
    package_data={
        'gardener_ci': ['VERSION'],
    },
    install_requires=list(requirements()),
    scripts=[os.path.join(tgt_bin_dir, 'purge_history')],
    entry_points={
        'console_scripts': [
            'gardener-ci = gardener_ci.cli_gen:main',
            'yaml2json = gardener_ci.yaml2json:main'
        ],
    },
)
