# babyseat: the Rust seat (`cliffvm serve`) in a small image (#1143, docs/packaging-plan.md). NO Rust, Python or torch in it: the image is the release's dist tarball on debian-slim.
#
# The build context holds the release tarballs release-binaries.yml makes (the glibc ones: the dist folder inside is babyseat-<version>-<triple>/<version>/):
#   docker-dist/babyseat-<version>-x86_64-unknown-linux-gnu.tar.gz   docker-dist/babyseat-<version>-aarch64-unknown-linux-gnu.tar.gz
#   docker buildx build --build-arg VERSION=0.7.60 -t chedim/babyseat:0.7.60 .
# (docker.yml downloads them from the workflow's artifacts; scripts/dist/build.sh --archive makes one by hand, from a linux binary.)
#
# RUN:    docker run -p 4096:4096 -v babyseat:/data -e ANTHROPIC_API_KEY chedim/babyseat
#   the seat is on http://localhost:4096 ; its data (store, door secret, admin token) is the /data volume. THE OWNER TOKEN for a local tool: docker exec <container> cat /data/admin.token
#   The door binds 0.0.0.0 inside the container (docker's bridge is not loopback) and wants a token for everything but the page: publish the port to 127.0.0.1 unless you mean the network (-p 127.0.0.1:4096:4096).
# THE claude CLI IS NOT IN THE IMAGE (it is Anthropic's, with its own license and updates): the chat runs it as a child, so the image has a seat that boots, serves the page and answers /global/health,
#   and a chat that says how to supply it. Supply it by mounting a Linux claude executable and naming it:  -v /path/to/claude:/opt/claude:ro -e CLAUDE_BIN=/opt/claude
#   with the key in ANTHROPIC_API_KEY (the default auth: it is passed to claude; no login is stored in the image). The CLI's own login is an opt-in: add --fact chat_auth=login after the image name, with a mounted ~/.claude.
# STOP: docker stop sends SIGTERM to the seat (PID 1, exec'd): it ends cleanly.
ARG BASE=debian:bookworm-slim

# the dist folder: the local tarball is unpacked here (one top folder, the version) so the tarball itself is in no layer of the image
FROM ${BASE} AS dist
ARG VERSION=dev
ARG TARGETARCH
COPY docker-dist/ /tmp/docker-dist/
RUN set -eu; case "${TARGETARCH:-amd64}" in amd64) t=x86_64-unknown-linux-gnu ;; arm64) t=aarch64-unknown-linux-gnu ;; *) echo "no binary for ${TARGETARCH}" >&2; exit 1 ;; esac; \
    tar -xzf "/tmp/docker-dist/babyseat-${VERSION}-$t.tar.gz" -C /tmp/docker-dist; \
    mv "/tmp/docker-dist/${VERSION}" /opt/babyseat; \
    [ "$(cat /opt/babyseat/VERSION)" = "${VERSION}" ]

FROM ${BASE}
ARG VERSION=dev
LABEL org.opencontainers.image.title="babyseat" \
      org.opencontainers.image.description="babyseat (formerly membrane): a local-first seat for AI with a memory field (the Rust seat, cliffvm)" \
      org.opencontainers.image.source="https://github.com/chedim/membrane" \
      org.opencontainers.image.url="https://github.com/chedim/membrane" \
      org.opencontainers.image.documentation="https://github.com/chedim/membrane/blob/main/docs/docker.md" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}"

# ca-certificates: the seat's https calls; curl: the healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=dist /opt/babyseat /opt/babyseat

COPY docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh \
    && useradd --uid 10001 --home-dir /data --no-create-home --shell /usr/sbin/nologin membrane \
    && mkdir -p /data && chown membrane:membrane /data

# the data: a named volume inherits this ownership on first use; a bind mount must be writable by uid 10001
USER membrane
ENV HOME=/data
WORKDIR /data
VOLUME /data
EXPOSE 4096

# /global/health answers 401 without a token and 200 with it: either says the door is up
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD test "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:4096/global/health)" -ge 200 || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD []
