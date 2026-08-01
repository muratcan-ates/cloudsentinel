# Both stages must sit on the same interpreter: the virtualenv built below
# records its base prefix, so a runtime on a different python would inherit
# a venv that points at an interpreter it does not have.
ARG PYTHON_IMAGE=python:3.12-slim

# Builder — resolves the pinned wheels into a virtualenv, so a future build
# dependency (a compiler, a -dev header) can be installed here without ever
# reaching the runtime. pip itself still ships: the runtime base carries one
# and `python -m venv` bootstraps another into /opt/venv. What makes that
# harmless is below — the venv stays root-owned and the rootfs is read-only,
# so nothing in the running container can install anything.
FROM ${PYTHON_IMAGE} AS builder

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt


FROM ${PYTHON_IMAGE} AS runtime

# No .pyc writes — a read-only root filesystem would refuse them anyway, and
# a failed write is a startup surprise nobody needs. Unbuffered stdout so a
# crash reaches the platform's log tail as it happens, not one buffer later.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

# Only what the server actually opens at runtime. Tests, docs and the dev
# scripts are deliberately absent: a smaller image has less to audit.
COPY main.py ./
COPY app/ app/
COPY static/ static/
# Mission YAMLs live at the repo root, not under app/ — without them the
# reflex/mission features degrade to environment defaults on this image.
COPY configs/ configs/

# The one writable path. The database moves out of the workdir so the code
# tree and the venv can stay root-owned and unwritable to the app user —
# the server never needs to rewrite its own source or its own dependencies.
# SQLite wants the directory, not just the file: the -wal and -shm siblings
# are created next to it.
#
#   docker run --read-only -v cloudsentinel-data:/data -p 8000:8000 cloudsentinel
#
# A named volume inherits /data's ownership from the image, so uid 1000 can
# write it with no further flags. Without --read-only the plain `docker run`
# still works — /data is then just another directory in the writable layer.
ENV SENTINEL_DB_PATH=/data/cloudsentinel.db

# Non-root by default.
RUN useradd --create-home --uid 1000 sentinel \
    && install -d -o sentinel -g sentinel /data
USER sentinel

EXPOSE 8000

# Container-level liveness: stdlib urllib against the health endpoint, so
# an orchestrator (or plain `docker ps`) sees a wedged worker as unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
