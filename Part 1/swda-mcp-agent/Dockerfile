# Image for the MCP server (production: built by CI, signed, pushed to ECR (10h), run on ECS Fargate (10f)).
FROM python:3.12-slim AS build
WORKDIR /app
COPY pyproject.toml ./
COPY swda_agent ./swda_agent
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY --from=build /install /usr/local
COPY swda_agent ./swda_agent
COPY config ./config
COPY data/programmes.csv ./data/programmes.csv
# Non-root, read-only code; traces go to a writable volume (production: OpenTelemetry exporter).
RUN useradd --uid 10001 --no-create-home app && mkdir -p /app/out && chown app /app/out
USER 10001
# The client token is injected at run time (production: Cognito bearer token per request), never baked in.
ENTRYPOINT ["python", "-m", "swda_agent", "serve"]
