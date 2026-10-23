FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MYDAY_DATA_DIR=/data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN DJANGO_DEBUG=1 python manage.py collectstatic --noinput \
    && useradd --uid 1000 --create-home myday \
    && mkdir -p /data && chown myday /data

USER myday
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["sh", "-c", "python manage.py migrate --noinput && exec gunicorn myday_project.wsgi:application --bind 0.0.0.0:8000 --workers 2 --threads 4 --access-logfile - --forwarded-allow-ips='*'"]
