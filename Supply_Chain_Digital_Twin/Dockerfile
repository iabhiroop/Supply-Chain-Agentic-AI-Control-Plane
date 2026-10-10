# Build context is the repo root (see docker-compose.yml).
FROM python:3.11-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY Supply_Chain_Digital_Twin/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY deploy/wait_pg.py /wait_pg.py
COPY Supply_Chain_Digital_Twin/ /app/
COPY Multi_Agent_Platform/database /opt/layer2/database

ENV PYTHONPATH=/app
ENV TWIN_HOST=0.0.0.0
ENV TWIN_PORT=8003
ENV TWIN_LAYER2_ROOT=/opt/layer2
ENV SCM_PG_HOST=postgres

EXPOSE 8003

CMD ["sh", "-c", "python /wait_pg.py && python -m twin.cli apply && python -m twin.cli load disruption && python -m uvicorn twin.main:app --host 0.0.0.0 --port 8003"]
