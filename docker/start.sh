#!/bin/sh
set -e
# Bind-Mounts der Synology gehören oft einem anderen Benutzer: Datenordner dem App-Benutzer übergeben
mkdir -p "${REA_MEDIA_ROOT:-/app/data/media}"
chown -R rea:rea "${REA_MEDIA_ROOT:-/app/data/media}"
python manage.py migrate --noinput
# Ersten Benutzer anlegen, wenn noch keiner existiert (Werte aus REA_ADMIN_USER / REA_ADMIN_PASSWORD)
python manage.py shell -c "
import os
from django.contrib.auth import get_user_model
U = get_user_model()
name, pw = os.environ.get('REA_ADMIN_USER'), os.environ.get('REA_ADMIN_PASSWORD')
if name and pw and not U.objects.exists():
    U.objects.create_superuser(name, '', pw)
    print(f'Benutzer {name} angelegt')
"
exec gunicorn rea.wsgi:application --user rea --group rea --bind 0.0.0.0:8000 --workers 2 --timeout 120 --access-logfile -
