FROM python:3.11-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev libgomp1 libglib2.0-0 libgl1 libsm6 libxext6 libxrender1 \
    && rm -rf /var/lib/apt/lists/*

COPY Enterprise_graphrag/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY deploy/wait_pg.py /wait_pg.py
COPY Enterprise_graphrag/ /app/

ENV PYTHONPATH=/app
ENV GRAPHRAG_HOST=0.0.0.0
ENV GRAPHRAG_PORT=8100
ENV SCM_PG_HOST=postgres
ENV SCM_GATEWAY_URL=http://gateway:8000
ENV SCM_EVAL_URL=http://eval:8002
ENV SCM_NEO4J_URI=bolt://neo4j:7687
ENV SCM_OLLAMA_URL=http://host.docker.internal:11434

EXPOSE 8100

# uvicorn skips full corpus reindex; run `python -m graphrag.cli reindex` when Neo4j is warm.
CMD ["sh", "-c", "python /wait_pg.py && python -m uvicorn graphrag.main:app --host 0.0.0.0 --port 8100"]
