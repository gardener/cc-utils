FROM eu.gcr.io/gardener-project/cc/job-image-base:0.24.0

COPY . /cc/utils/

# place version file into container's filesystem to make it easier to
# determine the image version during runtime
COPY VERSION /metadata/VERSION

# add cc-utils' cli.py to PATH and PYTHONPATH
ENV PATH /cc/utils/:/cc/utils/bin:$PATH
ENV PYTHONPATH /cc/utils

RUN pip3 install -r /cc/utils/requirements.txt --upgrade \
&& pip3 install --no-deps -r /cc/utils/requirements.nodeps.txt --upgrade

# XXX install clamav / run freshclam in default cc-job-image for now
COPY res/clamd.conf /etc/clamav/clamd.conf
RUN apk add clamav \
&& freshclam

RUN EFFECTIVE_VERSION="$(cat /metadata/VERSION)" REPO_DIR=/cc/utils \
  /cc/utils/.ci/bump_job_image_version.py
