# syntax=docker/dockerfile:1
# devloop-temporal-worker: the reference Temporal Orchestration Worker.
#
# Installs omneval-devloop from THIS repo's source (the freshest local build,
# rebuilt by CI after each SDK CI run) and starts devloop.worker, which registers
# the Dev Loop and Summarization workflows on the orchestration task queue and
# serves /healthz. Consumers who need custom workflows build their own image that
# installs omneval-devloop alongside their workflow code.
#
# Build context must be the repo root so the SDK source is available.

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.17 /uv /uvx /bin/

WORKDIR /app

# Install omneval-devloop from local source (the freshest build of the SDK).
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
RUN UV_HTTP_TIMEOUT=300 uv pip install --system --no-cache .

# /healthz (Kubernetes probes) and the webhook receiver port.
EXPOSE 8080 8088

CMD ["python", "-m", "devloop.worker"]
