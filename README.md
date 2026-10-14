# REA – Rechnungen für das eigene Büro

REA ist eine selbst gehostete Rechnungs-App für eine Firma. Sie läuft als Docker-Projekt, zum Beispiel auf einer Synology.

## Funktionen

- **Kunden** mit Rechnungs-E-Mail, USt-IdNr., Leitweg-ID, eigenem Zahlungsziel und Rechnungsformat
- **Artikel und Leistungen** mit Einheit, Preis und Steuersatz
- **Rechnungen** mit Positionen, Rabatt, 19 %/7 %/0 %, Reverse Charge, Leistungszeitraum, Zahlungen, Storno und Kopie
- **E-Rechnung erstellen**: ZUGFeRD 2 (Profil EN 16931, PDF/A-3 mit eingebettetem XML) oder XRechnung 3.0 (CII)
- **E-Rechnung empfangen**: XRechnung (UBL und CII) und ZUGFeRD/Factur-X hochladen, anzeigen und das Original archivieren
- **Vorlagen**: Briefpapier mit Logo und Akzentfarbe, Nummernkreise, Textbausteine
- **Mahnwesen** mit 4 Stufen, Fristen, Gebühren und Texten (Vorschlag der nächsten Stufe)
- **Geschäftsbriefe** an Kunden als PDF
- **GiroCode** (EPC-QR-Code) auf Rechnungen und Mahnungen, außerdem zum Bezahlen von Eingangsrechnungen
- **Versand per E-Mail** über das eigene Postfach (SMTP)
- **REST-API** mit API-Schlüssel und Dokumentation unter `/api/doku/`

Abgeschlossene Rechnungen sind schreibgeschützt. Korrekturen laufen über eine Stornorechnung. Rechnungsnummern werden lückenlos vergeben.

## Installation auf der Synology

Voraussetzung: DSM 7.2 oder neuer mit dem Paket **Container Manager**.

1. Ordner `docker/rea` in File Station anlegen und den Inhalt dieses Repositorys hineinkopieren (oder per SSH `git clone`).
2. `.env.example` nach `.env` kopieren und die Werte anpassen. Mindestens `REA_SECRET_KEY`, `REA_DB_PASSWORD`, `REA_ADMIN_PASSWORD` und die E-Mail-Zugangsdaten.
3. Container Manager → **Projekt** → **Erstellen**: Pfad `docker/rea` wählen, die vorhandene `docker-compose.yml` verwenden, fertigstellen. Das erste Bauen dauert einige Minuten.
4. REA ist danach unter `http://192.168.2.68:8100` erreichbar. Anmelden mit `REA_ADMIN_USER` und `REA_ADMIN_PASSWORD`.
5. Unter **Einstellungen → Firmendaten** Firma, Steuernummer bzw. USt-IdNr., Bankverbindung und Logo eintragen.

### HTTPS einrichten (empfohlen)

Systemsteuerung → Anmeldeportal → Erweitert → **Reverse Proxy** → Erstellen:

- Quelle: HTTPS, Hostname `192.168.2.68` (oder ein eigener Name), Port `8443`
- Ziel: HTTP, `localhost`, Port `8100`
- Unter „Benutzerdefinierte Kopfzeile“ einen Eintrag anlegen: Name `X-Forwarded-Proto`, Wert `$scheme`

Danach in `.env` `REA_CSRF_TRUSTED_ORIGINS=https://192.168.2.68:8443` und `REA_SECURE_COOKIES=true` setzen und das Projekt neu starten.

## Daten und Sicherung

Alle Daten liegen im Projektordner unter `daten/`:

| Ordner | Inhalt |
|---|---|
| `daten/postgres` | Datenbank |
| `daten/dokumente` | Rechnungs-PDFs, XRechnungen, Mahnungen, Briefe, Eingangsrechnungen |
| `daten/sicherung` | tägliche Datenbanksicherung (14 Tage) |

Für die Aufbewahrungspflicht (8 bzw. 10 Jahre) den Ordner `docker/rea/daten` mit **Hyper Backup** zusätzlich auf ein externes Ziel sichern.

Wiederherstellen einer Sicherung:

```sh
docker compose exec -T db pg_restore -U rea -d rea --clean < daten/sicherung/rea-JJJJ-MM-TT.dump
```

## Update

```sh
cd /volume1/docker/rea
git pull
docker compose up -d --build
```

Oder im Container Manager: Projekt → Aktion → **Erstellen** (baut neu und startet).

## REST-API

Unter Einstellungen einen API-Schlüssel erstellen. Aufruf mit der Kopfzeile `Authorization: Token <Schlüssel>`.

```sh
curl -H "Authorization: Token <Schlüssel>" http://192.168.2.68:8100/api/rechnungen/?status=offen
```

| Endpunkt | Zweck |
|---|---|
| `/api/kunden/` | Kunden lesen, anlegen, ändern |
| `/api/artikel/` | Artikel lesen, anlegen, ändern |
| `/api/rechnungen/` | Rechnungsentwürfe mit Positionen anlegen und ändern |
| `/api/rechnungen/{id}/abschliessen/` | Nummer vergeben, PDF/XML erzeugen |
| `/api/rechnungen/{id}/stornieren/` | Stornorechnung erstellen |
| `/api/rechnungen/{id}/zahlungen/` | Zahlung erfassen |
| `/api/rechnungen/{id}/pdf/`, `/xml/` | Dateien abrufen |
| `/api/eingangsrechnungen/hochladen/` | E-Rechnung einlesen (multipart, Feld `datei`) |

Vollständige Beschreibung: `/api/doku/`.

## Entwicklung

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
REA_DEBUG=true python manage.py runserver
python manage.py test buero
```

Ohne `REA_DATABASE_URL` wird SQLite unter `data/` verwendet. Für WeasyPrint werden Pango und HarfBuzz benötigt (unter Debian/Ubuntu: `libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0`).

Die erzeugten E-Rechnungen wurden mit dem offiziellen KoSIT-Validator (1.5.0, XRechnung-Konfiguration 3.0.2) geprüft: XRechnung, ZUGFeRD, Stornorechnung und Reverse Charge ohne Fehler und Warnungen.
