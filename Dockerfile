FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Bibliotheken für die PDF-Erzeugung (WeasyPrint) und Schriften
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN REA_SECRET_KEY=build python manage.py collectstatic --noinput -v0 \
 && useradd --uid 1000 --create-home rea \
 && mkdir -p /app/data/media && chown -R rea /app/data

# Startet als root, übergibt den Datenordner an den Benutzer rea und läuft dann ohne Root-Rechte (siehe docker/start.sh)
EXPOSE 8000
ENTRYPOINT ["/app/docker/start.sh"]
