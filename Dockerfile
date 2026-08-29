FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY VERSION ./VERSION
COPY app ./app
RUN mkdir -p /data /photos /backups
ENV DATA_DIR=/data PHOTO_DIR=/photos PYTHONUNBUFFERED=1
EXPOSE 1975
CMD ["sh", "-c", "python -m app.photo_worker & exec gunicorn --bind 0.0.0.0:1975 --workers 2 --threads 4 --timeout 1800 app.wsgi:app"]
