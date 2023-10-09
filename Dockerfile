ARG BASE_IMAGE_TAG=0.91.0
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

FROM eu.gcr.io/gardener-project/cc/ocm-cli:0.3.0-preview as ocm-cli
FROM eu.gcr.io/gardener-project/cc/job-image-base:$BASE_IMAGE_TAG

ARG TARGETARCH

COPY --from=builder /pkgs/usr /usr
COPY --from=ocm-cli /bin/ocm /bin/ocm
COPY --from=builder /cc/utils/bin/launch-dockerd.sh /cc/utils/bin/launch-dockerd.sh

# place version file into container's filesystem to make it easier to
# determine the image version during runtime
COPY VERSION /metadata/VERSION

ENV HELM_V3_VERSION=v3.12.2
ENV HELM_ARCH="${TARGETARCH}"
COPY --from=builder /cc/utils/bin/helm /usr/local/bin/helm
# backwards-compatibility
RUN ln -sf /usr/local/bin/helm /usr/local/bin/helm3
