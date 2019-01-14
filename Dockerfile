FROM eu.gcr.io/gardener-project/cc/job-image-base:0.16.0

COPY . /cc/utils/

# place version file into container's filesystem to make it easier to
# determine the image version during runtime
COPY VERSION /metadata/VERSION

# add cc-utils' cli.py to PATH and PYTHONPATH
ENV PATH /cc/utils/:/cc/utils/bin:$PATH
ENV PYTHONPATH /cc/utils

RUN pip3 install -r /cc/utils/requirements.txt

RUN EFFECTIVE_VERSION="$(cat /metadata/VERSION)" REPO_DIR=/cc/utils \
  /cc/utils/.ci/bump_job_image_version.py
