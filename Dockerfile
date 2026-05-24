FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --prefer-binary -r requirements.txt

COPY . .

RUN chmod +x start.sh

EXPOSE 8000

CMD ["./start.sh"]
