FROM python:3.11-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY Agent_Evaluation_Platform/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY deploy/wait_pg.py /wait_pg.py
COPY Agent_Evaluation_Platform/ /app/

ENV PYTHONPATH=/app
ENV EVAL_HOST=0.0.0.0
ENV EVAL_PORT=8002
ENV SCM_PG_HOST=postgres
ENV SCM_GATEWAY_URL=http://gateway:8000
ENV SCM_API_BASE_URL=http://layer2:8001

EXPOSE 8002

CMD ["sh", "-c", "python /wait_pg.py && python -m uvicorn eval_platform.main:app --host 0.0.0.0 --port 8002"]
