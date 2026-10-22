# Minimal image for running TimeTree Exporter, e.g. from cron or a scheduler.
#
#   docker build -t timetree-exporter .
#   docker run --rm -it \
#     -e TIMETREE_EMAIL=me@example.com -e TIMETREE_PASSWORD=secret \
#     -v "$PWD/export:/export" timetree-exporter -c my_calendar_code -o /export/timetree.ics
#
# Pass TIMETREE_SESSION_ID instead of the email and password to reuse an existing
# web session, and mount a state file with --sync-state /export/state.json to make
# repeated runs incremental.
FROM python:3.13-slim AS build

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY timetree_exporter ./timetree_exporter
RUN pip install --no-cache-dir --upgrade pip \
    && pip wheel --no-cache-dir --no-deps --wheel-dir /wheels .

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --system timetree && useradd --system --gid timetree --create-home timetree

COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

USER timetree
WORKDIR /export
VOLUME ["/export"]

ENTRYPOINT ["timetree-exporter"]
CMD ["--help"]
