FROM eu.gcr.io/gardener-project/component/cli:latest AS component-cli
FROM eu.gcr.io/gardener-project/cc/job-image-base:0.51.0

COPY . /cc/utils/

COPY --from=component-cli /component-cli /bin/component-cli

# place version file into container's filesystem to make it easier to
# determine the image version during runtime
COPY VERSION /metadata/VERSION

# XXX backards compatibility (remove eventually)
ENV PATH /cc/utils/:/cc/utils/bin:$PATH
ENV HELM_V3_VERSION=v3.1.1

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
&& pip3 install gardener-component-model \
&& curl -L \
  https://get.helm.sh/helm-${HELM_V3_VERSION}-linux-amd64.tar.gz | tar xz -C /tmp --strip=1 \
&& mv /tmp/helm /bin/helm3 \
&& chmod +x /bin/helm3 \

RUN EFFECTIVE_VERSION="$(cat /metadata/VERSION)" REPO_DIR=/cc/utils \
  /cc/utils/.ci/bump_job_image_version.py
