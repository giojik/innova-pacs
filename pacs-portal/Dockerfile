FROM python:3.11-slim

# 1. სისტემური ბიბლიოთეკები (PostgreSQL-ისთვის და კოდირებისთვის)
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2. საჭირო პითონ-ბიბლიოთეკების ინსტალაცია
# დავამატე 'qrcode' და 'psycopg2-binary'
RUN pip install --no-cache-dir \
    pydicom \
    pynetdicom \
    requests \
    fastapi \
    uvicorn \
    python-multipart \
    psycopg2-binary \
    qrcode

# 3. კოდის კოპირება
COPY . .

# შენიშვნა: CMD არ გვინდა აქ, რადგან docker-compose-ში თითოეულს თავისი ბრძანება აქვს