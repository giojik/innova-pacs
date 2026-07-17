# 1. ვიყენებთ პითონის მსუბუქ ვერსიას
FROM python:3.11-slim

# 2. PostgreSQL-ისთვის საჭირო სისტემური ბიბლიოთეკების ინსტალაცია
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 3. სამუშაო საქაღალდის შექმნა
WORKDIR /app

# 4. საჭირო ბიბლიოთეკების ინსტალაცია
# psycopg2-binary საჭიროა ბაზასთან კავშირისთვის
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    requests \
    psycopg2-binary \
    pyjwt \
    cryptography

# 5. კოდის კოპირება კონტეინერში
COPY main.py .

# 6. პორტის გახსნა
EXPOSE 8005

# 7. გაშვების ბრძანება
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8005"]