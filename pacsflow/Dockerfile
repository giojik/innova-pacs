FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn python-multipart requests
COPY main.py .
RUN mkdir -p /app/data
EXPOSE 8010
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010"]