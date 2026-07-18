FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ./
COPY app/ app/
COPY static/ static/
# Mission YAMLs live at the repo root, not under app/ — without them the
# reflex/mission features degrade to environment defaults on this image.
COPY configs/ configs/

# Non-root by default: the app only needs to write its SQLite file, which
# lives in the workdir it owns.
RUN useradd --create-home --uid 1000 sentinel && chown -R sentinel:sentinel /app
USER sentinel

EXPOSE 8000

# Container-level liveness: stdlib urllib against the health endpoint, so
# an orchestrator (or plain `docker ps`) sees a wedged worker as unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
