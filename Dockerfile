# babyseat image (chedim/babyseat; also pushed as chedim/membrane, the
# project's name before 0.7): `membrane standalone` -- the serve
# API + web UI on :4096 and the OpenAI-compatible memory proxy on :11500.
#
# A PUBLIC image: it holds the installed package and nothing else. Every
# site-specific value (backend URL, models, keys) arrives at run time through
# env or the /data volume. See docs/docker.md for run recipes.
#
#   docker build -t membrane:dev .
#   docker run -d -p 4096:4096 -p 11500:11500 -v membrane-data:/data \
#     -e MEMBRANE_EMBED_MODEL=mxbai-embed-large --stop-timeout 60 membrane:dev
#
# The ports carry no authentication today: publish them only on a network
# you trust.

FROM python:3.12-slim

ARG VERSION=dev
LABEL org.opencontainers.image.title="babyseat" \
      org.opencontainers.image.description="babyseat (formerly membrane): a seat for AI with its own web UI and membrane, its memory field + proxy" \
      org.opencontainers.image.source="https://github.com/chedim/membrane" \
      org.opencontainers.image.url="https://github.com/chedim/membrane" \
      org.opencontainers.image.documentation="https://github.com/chedim/membrane/blob/main/docs/docker.md" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}"

# A container has no terminal, no browser and no git checkout to update:
# the boot update-check, the skills auto-install into the workdir, the
# browser launch and the interactive prompts are all off.
ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MEMBRANE_NO_UPDATE_CHECK=1 \
    MEMBRANE_NO_AUTOINSTALL=1 \
    MEMBRANE_NO_BROWSER=1 \
    MEMBRANE_NO_ASK=1

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY membrane ./membrane
RUN pip install --no-cache-dir . && rm -rf /src

COPY docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh \
    && useradd --uid 10001 --home-dir /data --no-create-home --shell /bin/sh membrane \
    && mkdir -p /data && chown membrane:membrane /data

# HOME is the volume: the project field (/data/.membrane) and the per-user
# state under ~/.membrane (auth.json, the global field, project env) both
# persist. A bind mount must be writable by uid 10001; a named volume
# inherits the ownership on first use.
USER membrane
ENV HOME=/data
WORKDIR /data
VOLUME /data

EXPOSE 4096 11500

# /membrane/state on the proxy answers without the model backend; the first
# boot takes ~40-60 s and the first state query can be slow.
HEALTHCHECK --interval=30s --timeout=30s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:11500/membrane/state', timeout=25)" || exit 1

# docker stop: the seat's teardown lease runs up to 90 s -- run with
# --stop-timeout 60 (compose: stop_grace_period: 60s) or more.
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["standalone"]
