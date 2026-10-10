FROM node:20-alpine AS web
WORKDIR /web
COPY Enterprise_AI_Platform/web/package.json Enterprise_AI_Platform/web/package-lock.json ./
RUN npm ci
COPY Enterprise_AI_Platform/web/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
COPY Enterprise_AI_Platform/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY Enterprise_AI_Platform/os_platform /app/os_platform
COPY --from=web /web/dist /app/web/dist

ENV PYTHONPATH=/app
ENV OS_HOST=0.0.0.0
ENV OS_PORT=8010
ENV SCM_GATEWAY_URL=http://gateway:8000
ENV SCM_LAYER2_URL=http://layer2:8001
ENV SCM_EVAL_URL=http://eval:8002
ENV SCM_TWIN_URL=http://twin:8003
ENV SCM_GRAPHRAG_URL=http://graphrag:8100

EXPOSE 8010

CMD ["python", "-m", "uvicorn", "os_platform.main:app", "--host", "0.0.0.0", "--port", "8010"]
