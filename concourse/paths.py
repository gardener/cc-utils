import os

own_dir = os.path.abspath(os.path.dirname(__file__))
res_dir = os.path.join(own_dir, 'resources')
template_dir = os.path.join(own_dir, 'templates')

last_released_tag_file = os.path.join(res_dir, 'LAST_RELEASED_TAG')
