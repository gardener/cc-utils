import os

own_dir = os.path.abspath(os.path.dirname(__file__))

default_json_schema_path = os.path.join(
    own_dir,
    'component-descriptor-v2-schema.yaml',
)
