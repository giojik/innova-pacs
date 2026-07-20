FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    requests \
    psycopg2-binary \
    pyjwt \
    cryptography \
    python-multipart

COPY main.py .

EXPOSE 8006

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8006"]
