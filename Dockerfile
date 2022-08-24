ARG BASE_IMAGE_TAG=0.77.0
FROM eu.gcr.io/gardener-project/cc/job-image-base:$BASE_IMAGE_TAG as builder
COPY VERSION /metadata/VERSION
COPY . /cc/utils/
RUN pip3 install --user --upgrade --no-cache-dir \
  pip \
  wheel \
&& pip3 install --user --upgrade --no-cache-dir \
  --find-links /cc/utils/dist \
  gardener-cicd-libs==1.1844.0 \
  gardener-cicd-cli==1.1844.0 \
  gardener-cicd-dso==1.1844.0 \
  pycryptodome

FROM eu.gcr.io/gardener-project/component/cli:latest AS component-cli
FROM eu.gcr.io/gardener-project/cc/job-image-base:$BASE_IMAGE_TAG

COPY . /cc/utils/
COPY --from=component-cli /component-cli /bin/component-cli
COPY --from=builder /root/.local /root/.local

# place version file into container's filesystem to make it easier to
# determine the image version during runtime
COPY VERSION /metadata/VERSION

# XXX backards compatibility (remove eventually)
ENV PATH /cc/utils/:/cc/utils/bin:/root/.local/bin:$PATH
ENV HELM_V3_VERSION=v3.8.0

# backwards-compatibility
RUN ln -sf /cc/utils/bin/helm /bin/helm3

RUN EFFECTIVE_VERSION="$(cat /metadata/VERSION)" REPO_DIR=/cc/utils \
  /cc/utils/.ci/bump_job_image_version.py
