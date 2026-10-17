FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=Europe/Warsaw
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY szukacz ./szukacz
RUN mkdir -p /app/data

CMD ["python", "-m", "szukacz.main", "--config", "/app/config.yaml", "--db", "/app/data/szukacz.db"]
