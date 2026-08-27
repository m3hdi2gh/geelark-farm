# The service, built for a 1 vCPU box.
#
# Two stages: one that installs the dependencies into a virtualenv, and one
# that carries that virtualenv and the source. The split is not about image
# size here - it is about the build. Editing a line of Python must not
# reinstall gspread and cryptography on a single core, and it does not,
# because the layer that installs them is keyed on pyproject.toml alone.
#
# The interpreter is pinned to the version CI tests and development uses. The
# host happens to carry 3.12, which nothing has ever tested this against, and
# a container is exactly the tool for not caring what the host carries.

FROM python:3.13-slim AS deps

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Only what names the dependencies. Nothing here changes when the code does,
# so this layer is cached until a dependency actually moves.
COPY pyproject.toml README.md ./
RUN mkdir -p src/geelark_farm \
 && printf '__version__ = "0.0.0"\n' > src/geelark_farm/__init__.py \
 && pip install .


FROM python:3.13-slim AS runtime

# Which commit this image was built from. `--version` reads it out of a
# checkout when there is one, and an image built from a copy of the tree has
# no `.git` - so the build says instead, and the answer to "which code is on
# this server" survives being containerised.
ARG GEELARK_REVISION=""
ENV GEELARK_REVISION=${GEELARK_REVISION}

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC

# Not root. The GeeLark key in this container can create and delete phones and
# the service account can write to the spreadsheet; there is no reason for the
# process holding them to be able to write to the image as well.
RUN useradd --create-home --uid 10001 geelark

COPY --from=deps /opt/venv /opt/venv

WORKDIR /app
COPY --chown=geelark:geelark pyproject.toml README.md ./
COPY --chown=geelark:geelark src/ ./src/
COPY --chown=geelark:geelark scripts/ ./scripts/

# Editable, because REPO_ROOT is worked out from the package's own location -
# two levels above `src/geelark_farm/` - and that is where `.env`, `state/`,
# `logs/` and `secrets/` are looked for. A normal install puts the package in
# site-packages and sends all four somewhere nobody mounted.
RUN pip install --no-deps -e . \
 && mkdir -p state logs artifacts \
 && chown -R geelark:geelark /app

USER geelark

# Whether a pass has run recently. `restart: always` brings back a process
# that died; it does nothing for one that is alive and stuck, and only the
# loop can tell those apart. The start period is generous because the first
# pass syncs the sheet before it says anything.
HEALTHCHECK --interval=5m --timeout=30s --start-period=10m --retries=2 \
    CMD geelark serve --healthcheck || exit 1

ENTRYPOINT ["geelark"]
CMD ["serve"]
