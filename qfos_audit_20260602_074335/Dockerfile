FROM python:3.11-slim

ENV PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=20 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .

RUN --mount=type=cache,target=/root/.cache/pip python -m pip install --upgrade pip setuptools wheel && \
    python -m pip install --prefer-binary --timeout 300 --retries 20 -r requirements.txt

COPY . .

RUN chmod +x start.sh

EXPOSE 8080

CMD ["./start.sh"]
