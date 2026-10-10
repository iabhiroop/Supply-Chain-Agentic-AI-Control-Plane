FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src/ /app/src/
COPY config/ /app/config/

RUN pip install --no-cache-dir build
RUN pip install --no-cache-dir -e ".[prod]"

ENV PYTHONPATH=/app/src
ENV GATEWAY_ENV=production
ENV GATEWAY_HOST=0.0.0.0
ENV GATEWAY_PORT=8000

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
