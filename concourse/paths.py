import os

own_dir = os.path.abspath(os.path.dirname(__file__))
repo_root_dir = os.path.join(own_dir, os.pardir)
res_dir = os.path.join(own_dir, 'resources')
template_dir = os.path.join(own_dir, 'templates')
template_include_dir = own_dir

last_released_tag_file = os.path.join(res_dir, 'LAST_RELEASED_TAG')

# available in cc-job-image, only
cc_bin_dir = '/cc/utils/bin'
launch_dockerd = os.path.join(cc_bin_dir, 'launch-dockerd.sh')
