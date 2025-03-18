ARG BASE_IMAGE=ghcr.io/gardener/cc-utils/job-image-base:0.106.0
FROM $BASE_IMAGE AS builder
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
  pycryptodome

FROM ghcr.io/open-component-model/ocm/ocm.software/ocmcli/ocmcli-image:0.18.0 AS ocm-cli
FROM $BASE_IMAGE

ARG TARGETARCH

COPY --from=ocm-cli /bin/ocm /bin/ocm

COPY --from=builder /pkgs/usr /usr
COPY --from=builder /cc/utils/bin /cc/utils/bin
COPY --from=builder /usr/lib/libmagic.so.1 /usr/lib/libmagic.so.1
COPY --from=builder /usr/lib/libmagic.so.1.0.0 /usr/lib/libmagic.so.1.0.0
COPY --from=builder /usr/share/misc/magic.mgc /usr/share/misc/magic.mgc

ENV PATH=$PATH:/cc/utils/bin
