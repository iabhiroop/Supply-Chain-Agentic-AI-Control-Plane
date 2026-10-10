FROM python:3.11-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY Multi_Agent_Platform/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY deploy/wait_pg.py /wait_pg.py
COPY Multi_Agent_Platform/ /app/

ENV PYTHONPATH=/app
ENV SCM_API_HOST=0.0.0.0
ENV SCM_API_PORT=8001
ENV SCM_PG_HOST=postgres
ENV SCM_GATEWAY_URL=http://gateway:8000
ENV SCM_TWIN_URL=http://twin:8003
ENV SCM_GRAPHRAG_URL=http://graphrag:8100

EXPOSE 8001

CMD ["sh", "-c", "python /wait_pg.py && python -m api.main"]
