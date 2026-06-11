FROM python:3.14-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv
COPY pyproject.toml ./
RUN uv pip install --no-cache .
COPY bot ./
RUN mkdir -p /app/.cache/rendered
EXPOSE 8080
CMD ["python", "-m", "app.main"]
