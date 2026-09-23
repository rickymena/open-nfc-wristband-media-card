# Two stages so the runtime image carries no compiler: pyscard is a C
# extension against libpcsclite and needs swig + headers to build, but only
# the shared library to run.

FROM docker.io/library/python:3.12-slim AS build

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc libc6-dev swig libpcsclite-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip wheel --wheel-dir /wheels pyscard


FROM docker.io/library/python:3.12-slim

# libpcsclite1 provides the client library. pcscd itself is NOT installed:
# the container talks to the host's daemon over its socket, so the USB device
# never has to be handed to the container.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpcsclite1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels pyscard \
    && rm -rf /wheels

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

# The pcscd socket is world read/write, so nothing here needs root.
RUN useradd --create-home --uid 10001 wristband
USER wristband

# Audit log location inside the container; mount a volume at /data to keep it.
ENV WRISTBAND_AUDIT_LOG=/data/audit.jsonl

EXPOSE 8080

# Health check lives in compose.yaml: podman builds OCI images by default
# and the OCI spec has no HEALTHCHECK field, so one here is silently dropped.

CMD ["wristband", "serve", "--host", "0.0.0.0", "--port", "8080"]
