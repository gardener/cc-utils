FROM eu.gcr.io/gardener-project/cc/job-image-base:0.37.0

COPY . /cc/utils/

# place version file into container's filesystem to make it easier to
# determine the image version during runtime
COPY ci/version /metadata/VERSION

# XXX backards compatibility (remove eventually)
ENV PATH /cc/utils/:/cc/utils/bin:$PATH

RUN pip3 install --upgrade \
  --find-links /cc/utils/dist \
  gardener-cicd-libs \
  gardener-cicd-cli \
  gardener-cicd-whd

# XXX flake8 does not yet support the greates pyflakes version (required for python3.8)
RUN pip3 uninstall --yes flake8 && pip3 install git+https://github.com/PyCQA/flake8.git

RUN EFFECTIVE_VERSION="$(cat /metadata/VERSION)" REPO_DIR=/cc/utils \
  /cc/utils/.ci/bump_job_image_version.py
