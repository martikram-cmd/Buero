"""E-Mail-Versand über das eigene Postfach (SMTP aus den Einstellungen)."""

import os

from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone

from ..models import Firma, Kunde


def standardtext_rechnung(rechnung):
    anrede = "Sehr geehrte Damen und Herren,"
    k = rechnung.kunde
    if k.nachname and k.anrede in ("Herr", "Frau"):
        anrede = f"{'Sehr geehrter' if k.anrede == 'Herr' else 'Sehr geehrte'} {k.anrede} {k.nachname},"
    firma = Firma.laden()
    anhang = "die XRechnung" if rechnung.format == Kunde.FORMAT_XRECHNUNG else "die Rechnung als PDF (ZUGFeRD)"
    return (
        f"{anrede}\n\nanbei erhalten Sie {anhang} {rechnung.nummer} vom {rechnung.datum:%d.%m.%Y} "
        f"über {rechnung.text_ersetzen('{betrag}')}.\n\nMit freundlichen Grüßen\n{firma.kontakt_name}\n{firma.name}"
    )


def senden(an, betreff, text, anhaenge):
    """anhaenge: Liste von FieldFiles. Gibt nichts zurück, wirft bei Fehlern."""
    firma = Firma.laden()
    absender = settings.DEFAULT_FROM_EMAIL
    if firma and firma.email and absender.endswith("@localhost"):
        absender = firma.email
    mail = EmailMessage(
        subject=betreff,
        body=text,
        from_email=f"{firma.name} <{absender}>" if firma else absender,
        to=[a.strip() for a in an.split(",") if a.strip()],
        reply_to=[firma.email] if firma and firma.email else None,
    )
    for datei in anhaenge:
        if not datei:
            continue
        with datei.open("rb") as f:
            name = os.path.basename(datei.name)
            typ = "application/xml" if name.endswith(".xml") else "application/pdf"
            mail.attach(name, f.read(), typ)
    mail.send(fail_silently=False)
    return timezone.now()
