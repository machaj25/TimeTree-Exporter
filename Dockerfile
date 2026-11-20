# Container image for TimeTree Exporter.
#
# Build locally:
#   docker build -t timetree-exporter .
#
# Or pull the image that CI publishes from this repository:
#   docker pull ghcr.io/machaj25/timetree-exporter:latest
#
# Run (non-interactive, credentials from the environment, output on a volume):
#   docker run --rm \
#     -e TIMETREE_EMAIL=me@example.com -e TIMETREE_PASSWORD=secret \
#     -v "$PWD/export:/export" \
#     timetree-exporter -c my_calendar_code -o /export/timetree.ics --sync-state /export/sync.json
#
# Add -it to be prompted for credentials or to pick a calendar interactively.
# TIMETREE_SESSION_ID can replace the email and password.
#
# See compose.yaml for the same setup with Docker Compose.
FROM python:3.13-slim AS build

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY timetree_exporter ./timetree_exporter
RUN pip install --no-cache-dir --upgrade pip \
    && pip wheel --no-cache-dir --no-deps --wheel-dir /wheels .

FROM python:3.13-slim

ARG VERSION=unknown
ARG VCS_REF=unknown

LABEL org.opencontainers.image.title="TimeTree Exporter" \
      org.opencontainers.image.description="Export a TimeTree calendar to an RFC 5545 .ics file" \
      org.opencontainers.image.source="https://github.com/machaj25/TimeTree-Exporter" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --system timetree && useradd --system --gid timetree --create-home timetree

COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

# /export is where the .ics, images and sync state go. Make it writable for the
# unprivileged user so bind mounts created by Docker on first run work too.
RUN mkdir -p /export && chown timetree:timetree /export

USER timetree
WORKDIR /export
VOLUME ["/export"]

ENTRYPOINT ["timetree-exporter"]
CMD ["--help"]
