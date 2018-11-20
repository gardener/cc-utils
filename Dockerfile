FROM eu.gcr.io/gardener-project/cc/job-image-base:0.12.0

COPY . /cc/utils/

# place version file into container's filesystem to make it easier to
# determine the image version during runtime
COPY VERSION /metadata/VERSION
# set env-variable so that 'requests' python library uses the system's trust-store
ENV REQUESTS_CA_BUNDLE /etc/ssl/certs/ca-certificates.crt

# add cc-utils' cli.py to PATH and PYTHONPATH
ENV PATH /cc/utils/:$PATH
ENV PYTHONPATH /cc/utils

RUN pip3 install -r /cc/utils/requirements.txt
