ARG BASE_IMAGE=europe-docker.pkg.dev/gardener-project/releases/cicd/job-image-base:0.95.0
FROM $BASE_IMAGE as builder
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

FROM ghcr.io/open-component-model/ocm/ocm.software/ocmcli/ocmcli-image:0.7.0 as ocm-cli
FROM $BASE_IMAGE

ARG TARGETARCH

COPY --from=builder /pkgs/usr /usr
COPY --from=ocm-cli /usr/bin/ocm /bin/ocm
COPY --from=builder /cc/utils/bin/component-cli /bin/component-cli

# path is hardcoded in our trait
COPY --from=builder /cc/utils/bin/launch-dockerd.sh /cc/utils/bin/launch-dockerd.sh
ENV PATH=$PATH:/cc/utils/bin

# place version file into container's filesystem to make it easier to
# determine the image version during runtime
COPY VERSION /metadata/VERSION

ENV HELM_V3_VERSION=v3.12.2
ENV HELM_ARCH="${TARGETARCH}"
# copy to where helm is expected
COPY --from=builder /cc/utils/bin/helm /usr/local/bin/helm
# backwards-compatibility
RUN ln -sf /usr/local/bin/helm /usr/local/bin/helm3
