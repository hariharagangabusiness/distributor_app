FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The SQLite file, uploads/, and secret_key.txt are meant to live on a
# mounted volume, not in the image - see .dockerignore and the Railway
# setup notes in README.md. db/schema.sql (code, not data) ships as-is.

ENV PORT=5000
EXPOSE 5000

# --workers 1: SQLite is single-writer anyway, and app.py starts a
# background reminder thread at import time that would fire once per
# worker process if this were increased.
CMD gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120 app:app
