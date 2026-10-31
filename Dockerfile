FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.8.13

WORKDIR /code

COPY pyproject.toml uv.lock ./
COPY rowing_coach ./rowing_coach
COPY skills ./skills
COPY plans ./plans
COPY data ./data
COPY mcp ./mcp
COPY backend ./backend
COPY main.py memory_bank_config.py pii_middleware.py governance.py local_proxy.py ./

RUN uv sync --frozen --no-dev

ENV PORT=8080
ENV APP_MODULE=main:app
EXPOSE 8080

CMD ["sh", "-c", "uv run uvicorn ${APP_MODULE:-main:app} --host 0.0.0.0 --port ${PORT}"]
