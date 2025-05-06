FROM python:3.10.11 AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app


RUN python -m venv .venv
COPY requirements.txt ./
RUN .venv/bin/pip install -r requirements.txt
FROM python:3.10.11-slim
WORKDIR /app
COPY --from=builder /app/.venv .venv/
COPY . .
EXPOSE 5050
CMD ["/app/.venv/bin/python", "main.py"]
