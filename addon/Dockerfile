# The babyseat add-on image (#1143): the seat's release dist folder (the MUSL build: the base is Alpine) on the Home Assistant base image. No Rust, no Python, NO claude CLI.
# The Supervisor builds it on the device (config.yaml has no `image`) and passes BUILD_ARCH (amd64 | aarch64) and BUILD_VERSION (config.yaml's version).
# Supervisor 2026.04+ passes no BUILD_FROM, so it has a pinned default (contributors.home-assistant.io/apps/guides/dockerfile/).
#
# The tarball: babyseat-<version>-<x86_64|aarch64>-unknown-linux-musl.tar.gz and its .sha256, from the GitHub release of the version (BABYSEAT_BASE_URL overrides the place),
# or from this folder's dist/ when it holds them (a local build). install-dist.sh checks the sha256 before unpacking and the folder's MANIFEST.sha256 after.
#   a local build from a checkout (DOCS.md, "Build it yourself"):
#     sh scripts/dist/build.sh --bin <linux musl cliffvm> --target x86_64-unknown-linux-musl --archive addon/dist/babyseat-<version>-x86_64-unknown-linux-musl.tar.gz
#     docker buildx build --build-arg BUILD_ARCH=amd64 --build-arg BUILD_VERSION=<version> -t local/babyseat-addon addon
ARG BUILD_FROM=ghcr.io/home-assistant/base:3.24
FROM ${BUILD_FROM}
ARG BUILD_ARCH=amd64
ARG BUILD_VERSION
ARG BABYSEAT_BASE_URL=

COPY install-dist.sh /tmp/install-dist.sh
COPY dist/ /tmp/dist/
RUN set -eu; \
    [ -n "${BUILD_VERSION:-}" ] || { echo "BUILD_VERSION (the add-on version) is needed" >&2; exit 1; }; \
    t=$([ "${BUILD_ARCH}" = aarch64 ] && echo aarch64 || echo x86_64)-unknown-linux-musl; \
    if [ -f "/tmp/dist/babyseat-${BUILD_VERSION}-$t.tar.gz" ]; then src="--local /tmp/dist"; \
    elif [ -n "${BABYSEAT_BASE_URL}" ]; then src="--base-url ${BABYSEAT_BASE_URL}"; else src=""; fi; \
    sh /tmp/install-dist.sh --version "${BUILD_VERSION}" --arch "${BUILD_ARCH}" --dest /opt/babyseat $src; \
    rm -rf /tmp/install-dist.sh /tmp/dist

COPY run.sh options.sh announce.sh /opt/babyseat-addon/
RUN chmod 0755 /opt/babyseat-addon/*.sh

LABEL org.opencontainers.image.title="babyseat add-on" \
      org.opencontainers.image.source="https://github.com/chedim/membrane" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /data
ENV HOME=/data
EXPOSE 4096
# the health the Supervisor watches (the add-on linter wants this, not config.yaml's `watchdog`): /global/health answers 401 without a token and 200 with it, either says the door is up
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD test "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:4096/global/health)" -ge 200 || exit 1
ENTRYPOINT ["/opt/babyseat-addon/run.sh"]
CMD []
