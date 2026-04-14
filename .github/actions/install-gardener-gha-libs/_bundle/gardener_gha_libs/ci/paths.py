import os

own_dir = os.path.abspath(os.path.dirname(__file__))
repo_root = os.path.abspath(os.path.join(own_dir, os.pardir))
version_file = os.path.join(repo_root, 'VERSION')
