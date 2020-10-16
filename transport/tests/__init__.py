import sys
import os

own_dir = os.path.abspath(os.path.dirname(__name__))
repo_root = os.path.abspath(os.path.join(own_dir, os.path.pardir))

sys.path.insert(1, repo_root)
