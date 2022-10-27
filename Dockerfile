ARG BASE_IMAGE_TAG=0.82.0
FROM eu.gcr.io/gardener-project/cc/job-image-base:$BASE_IMAGE_TAG as builder
COPY VERSION /metadata/VERSION
COPY . /cc/utils/

RUN cat /cc/utils/gardener-cicd-libs.apk-packages \
    | xargs apk add --no-cache \
&& pip3 install --root /pkgs --upgrade --no-cache-dir \
  wheel \
&& pip3 install --root /pkgs --upgrade --no-cache-dir \
  --find-links /cc/utils/dist \
  gardener-cicd-libs==$(cat /metadata/VERSION) \
  gardener-cicd-cli==$(cat /metadata/VERSION) \
  gardener-cicd-dso==$(cat /metadata/VERSION) \
  pycryptodome

FROM eu.gcr.io/gardener-project/component/cli:latest AS component-cli
FROM eu.gcr.io/gardener-project/cc/job-image-base:$BASE_IMAGE_TAG

ARG TARGETARCH

COPY . /cc/utils/
COPY --from=component-cli /component-cli /bin/component-cli
COPY --from=builder /pkgs/usr /usr

# place version file into container's filesystem to make it easier to
# determine the image version during runtime
COPY VERSION /metadata/VERSION

# XXX backards compatibility (remove eventually)
ENV PATH /cc/utils/:/cc/utils/bin:$PATH
ENV HELM_V3_VERSION=v3.8.0
ENV HELM_ARCH="${TARGETARCH}"

# backwards-compatibility
RUN ln -sf /cc/utils/bin/helm /bin/helm3

RUN EFFECTIVE_VERSION="$(cat /metadata/VERSION)" REPO_DIR=/cc/utils \
  /cc/utils/.ci/bump_job_image_version.py
