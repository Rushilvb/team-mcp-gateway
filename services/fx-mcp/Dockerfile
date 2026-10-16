# `test` stage: unit tests (make test runs it). `runtime`: the image we deploy.
FROM python:3.12.15-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY fx_mcp ./fx_mcp

FROM base AS test
COPY requirements-dev.txt pytest.ini ./
RUN pip install -r requirements-dev.txt
COPY tests ./tests
CMD ["python", "-m", "pytest", "-v"]

FROM base AS runtime
# Numeric non-root user: Kubernetes' runAsNonRoot can verify a number, not a name.
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "fx_mcp.server:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
