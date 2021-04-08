FROM eu.gcr.io/gardener-project/component/cli:latest AS component-cli
FROM registry-1.docker.io/gardenerci/cc-job-image-base:0.50.0

COPY . /cc/utils/

COPY --from=component-cli /component-cli /bin/component-cli

# place version file into container's filesystem to make it easier to
# determine the image version during runtime
COPY VERSION /metadata/VERSION

# XXX backards compatibility (remove eventually)
ENV PATH /cc/utils/:/cc/utils/bin:$PATH

RUN pip3 install --upgrade \
  pip \
  wheel \
&& pip3 install --upgrade \
  --find-links /cc/utils/dist \
  gardener-cicd-libs \
  gardener-cicd-cli \
  gardener-cicd-whd \
  gardenlinux \
  pycryptodome \
&& pip3 uninstall -y gardener-component-model \
&& pip3 install gardener-component-model

RUN EFFECTIVE_VERSION="$(cat /metadata/VERSION)" REPO_DIR=/cc/utils \
  /cc/utils/.ci/bump_job_image_version.py
