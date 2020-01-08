import json
import os
import uuid

import ci.util

import concourse.model.traits.meta


uuid_filename = 'job.uuid'
jobmetadata_filename = 'jobmetadata.json'


def get_out_dir():
    return os.path.join(
        ci.util.check_env('CC_ROOT_DIR'),
        concourse.model.traits.meta.DIR_NAME,
    )


def generate_uuid():
    generated_uuid = str(uuid.uuid4())
    return generated_uuid


def export_job_metadata():
    '''
    generates job metadata (currently only a UUID unambiguously identifying current build)
    and writes it into meta's output directory (hardcoded as contract)
    '''
    uuid_str = generate_uuid()
    metadata = {
        'uuid': uuid_str,
    }

    uuid_outfile = os.path.join(get_out_dir(), uuid_filename)
    with open(uuid_outfile, 'w') as f:
        f.write(uuid_str)

    jobmetadata_outfile = os.path.join(get_out_dir(), jobmetadata_filename)
    with open(jobmetadata_outfile, 'w') as f:
        json.dump(metadata, f)

    print(json.dumps(metadata))
