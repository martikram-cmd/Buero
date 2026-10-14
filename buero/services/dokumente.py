"""PDF-Erzeugung, Abschließen, Stornieren und Mahnen."""

import base64
import io
from datetime import timedelta

import segno
from segno import helpers as segno_helpers
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from ..models import Brief, Firma, Kunde, Mahnstufe, Mahnung, Nummernkreis, Position, Rechnung
from . import erechnung


def girocode(firma: Firma, betrag, verwendungszweck: str):
    """EPC-QR-Code (GiroCode) als SVG-Data-URI, oder None wenn keine IBAN hinterlegt ist."""
    if not firma or not firma.iban or betrag is None or betrag <= 0:
        return None
    qr = segno_helpers.make_epc_qr(
        name=firma.name[:70],
        iban=firma.iban.replace(" ", ""),
        amount=float(betrag),
        text=verwendungszweck[:140],
        bic=firma.bic or None,
    )
    puffer = io.BytesIO()
    qr.save(puffer, kind="svg", scale=3, border=0)
    return "data:image/svg+xml;base64," + base64.b64encode(puffer.getvalue()).decode()


def _logo(firma):
    if firma and firma.logo:
        try:
            with firma.logo.open("rb") as f:
                daten = f.read()
            typ = "image/svg+xml" if firma.logo.name.lower().endswith(".svg") else "image/png"
            if firma.logo.name.lower().endswith((".jpg", ".jpeg")):
                typ = "image/jpeg"
            return f"data:{typ};base64," + base64.b64encode(daten).decode()
        except OSError:
            return None
    return None


def pdf_rendern(template, kontext, pdf_a=False) -> bytes:
    from weasyprint import HTML

    firma = Firma.laden()
    kontext = {"firma": firma, "logo": _logo(firma), **kontext}
    html = render_to_string(template, kontext)
    optionen = {"pdf_variant": "pdf/a-3b"} if pdf_a else {}
    return HTML(string=html).write_pdf(**optionen)


def rechnung_pdf(rechnung: Rechnung, entwurf=False) -> bytes:
    firma = Firma.laden()
    kontext = {
        "r": rechnung,
        "positionen": rechnung.positionen.all(),
        "gruppen": rechnung.steuergruppen(),
        "adresse": rechnung.empfaenger_adresse.splitlines() if rechnung.empfaenger_adresse else rechnung.kunde.adresse_zeilen,
        "einleitung": rechnung.text_ersetzen(rechnung.einleitung),
        "schlusstext": rechnung.text_ersetzen(rechnung.schlusstext),
        "entwurf": entwurf,
        "girocode": None
        if rechnung.art == Rechnung.ART_GUTSCHRIFT
        else girocode(firma, rechnung.summe_brutto, f"{rechnung.nummer or 'Entwurf'} {rechnung.kunde.nummer}"),
    }
    return pdf_rendern("buero/pdf/rechnung.html", kontext, pdf_a=not entwurf)


def abschliessen(rechnung: Rechnung):
    """Vergibt die Rechnungsnummer, erzeugt PDF/XML und schreibt die Rechnung fest."""
    rechnung.pruefe_aenderbar()
    firma = Firma.laden()
    fmt = rechnung.kunde.rechnungsformat
    if fmt == Kunde.FORMAT_PDF and rechnung.kunde.typ == Kunde.TYP_FIRMA:
        raise ValidationError(
            "Geschäftskunden müssen seit 2025 eine E-Rechnung erhalten. Bitte beim Kunden ZUGFeRD oder XRechnung wählen."
        )
    fehler = erechnung.pflichtangaben_pruefen(rechnung, firma, xrechnung=fmt == Kunde.FORMAT_XRECHNUNG)
    if fehler:
        raise ValidationError(fehler)

    with transaction.atomic():
        rechnung = Rechnung.objects.select_for_update().get(pk=rechnung.pk)
        rechnung.pruefe_aenderbar()
        rechnung.nummer = Nummernkreis.naechste(Nummernkreis.ART_RECHNUNG, rechnung.datum)
        if not rechnung.faellig_am:
            tage = rechnung.kunde.zahlungsziel_tage
            if tage is None:
                tage = firma.zahlungsziel_tage
            rechnung.faellig_am = rechnung.datum + timedelta(days=tage)
        rechnung.empfaenger_adresse = "\n".join(rechnung.kunde.adresse_zeilen)
        rechnung.format = fmt

        xml = None
        if fmt in (Kunde.FORMAT_ZUGFERD, Kunde.FORMAT_XRECHNUNG):
            xml = erechnung.erzeuge_xml(rechnung, xrechnung=fmt == Kunde.FORMAT_XRECHNUNG)
            schemafehler = erechnung.schema_pruefen(xml)
            if schemafehler:
                raise ValidationError(["Die E-Rechnung ist technisch fehlerhaft:"] + schemafehler[:5])

        pdf = rechnung_pdf(rechnung)
        if fmt == Kunde.FORMAT_ZUGFERD:
            from drafthorse.pdf import attach_xml

            pdf = attach_xml(
                pdf,
                xml,
                level="EN 16931",
                metadata={
                    "author": firma.name,
                    "keywords": "Factur-X, ZUGFeRD, Rechnung",
                    "title": f"{firma.name}: {rechnung.titel} {rechnung.nummer}",
                    "subject": f"{rechnung.titel} {rechnung.nummer} vom {rechnung.datum:%d.%m.%Y}",
                },
                lang="de-DE",
            )
        rechnung.pdf.save(f"{rechnung.nummer}.pdf", ContentFile(pdf), save=False)
        if fmt == Kunde.FORMAT_XRECHNUNG:
            rechnung.xml.save(f"{rechnung.nummer}.xml", ContentFile(xml), save=False)
        rechnung.status = Rechnung.STATUS_OFFEN
        rechnung.festgeschrieben_am = timezone.now()
        rechnung.save()
    return rechnung


def stornieren(rechnung: Rechnung) -> Rechnung:
    """Erzeugt eine Stornorechnung (Gutschrift mit Bezug) und markiert das Original als storniert."""
    if rechnung.ist_entwurf:
        raise ValidationError("Entwürfe können einfach gelöscht werden.")
    if rechnung.status == Rechnung.STATUS_STORNIERT:
        raise ValidationError("Diese Rechnung ist bereits storniert.")
    if rechnung.art == Rechnung.ART_GUTSCHRIFT:
        raise ValidationError("Gutschriften können nicht storniert werden.")
    with transaction.atomic():
        storno = Rechnung.objects.create(
            art=Rechnung.ART_GUTSCHRIFT,
            kunde=rechnung.kunde,
            datum=timezone.localdate(),
            leistung_von=rechnung.leistung_von,
            leistung_bis=rechnung.leistung_bis,
            faellig_am=timezone.localdate(),
            kaeufer_referenz=rechnung.kaeufer_referenz,
            bestellnummer=rechnung.bestellnummer,
            betreff=f"Storno zu Rechnung {rechnung.nummer}",
            einleitung=f"Hiermit stornieren wir die Rechnung {rechnung.nummer} vom {rechnung.datum:%d.%m.%Y} vollständig.",
            schlusstext="",
            befreiungsgrund=rechnung.befreiungsgrund,
            bezug=rechnung,
        )
        for p in rechnung.positionen.all():
            Position.objects.create(
                rechnung=storno,
                reihenfolge=p.reihenfolge,
                artikel=p.artikel,
                bezeichnung=p.bezeichnung,
                beschreibung=p.beschreibung,
                menge=p.menge,
                einheit=p.einheit,
                einzelpreis=p.einzelpreis,
                rabatt=p.rabatt,
                ust_kategorie=p.ust_kategorie,
                ust_satz=p.ust_satz,
            )
        storno = abschliessen(storno)
        storno.status = Rechnung.STATUS_BEZAHLT
        storno.save(update_fields=["status"])
        rechnung.status = Rechnung.STATUS_STORNIERT
        rechnung.save(update_fields=["status"])
    return storno


def zahlung_pruefen(rechnung: Rechnung):
    """Setzt den Status nach Zahlungseingang auf bezahlt bzw. zurück auf offen."""
    if rechnung.status in (Rechnung.STATUS_OFFEN, Rechnung.STATUS_BEZAHLT):
        neu = Rechnung.STATUS_BEZAHLT if rechnung.offen <= 0 else Rechnung.STATUS_OFFEN
        if neu != rechnung.status:
            rechnung.status = neu
            rechnung.save(update_fields=["status"])


def mahnung_vorschlag(rechnung: Rechnung):
    """Nächste sinnvolle Mahnstufe oder None."""
    if not rechnung.ist_ueberfaellig:
        return None
    naechste = rechnung.letzte_mahnstufe + 1
    return Mahnstufe.objects.filter(stufe=naechste, tage_nach_faelligkeit__lte=rechnung.tage_ueberfaellig).first()


def mahnung_erstellen(rechnung: Rechnung, stufe: Mahnstufe, gebuehr=None) -> Mahnung:
    if rechnung.status != Rechnung.STATUS_OFFEN:
        raise ValidationError("Nur offene Rechnungen können gemahnt werden.")
    heute = timezone.localdate()
    gebuehr = stufe.gebuehr if gebuehr is None else gebuehr
    neue_frist = heute + timedelta(days=stufe.frist_tage)
    text = rechnung.text_ersetzen(stufe.text).replace("{neue_frist}", neue_frist.strftime("%d.%m.%Y"))
    m = Mahnung(
        nummer=Nummernkreis.naechste(Nummernkreis.ART_MAHNUNG, heute),
        rechnung=rechnung,
        stufe=stufe.stufe,
        datum=heute,
        gebuehr=gebuehr,
        offener_betrag=rechnung.offen,
        neue_frist=neue_frist,
        text=text,
    )
    firma = Firma.laden()
    pdf = pdf_rendern(
        "buero/pdf/mahnung.html",
        {
            "m": m,
            "r": rechnung,
            "stufe": stufe,
            "adresse": rechnung.empfaenger_adresse.splitlines(),
            "girocode": girocode(firma, m.gesamt, f"{rechnung.nummer} {m.nummer}"),
        },
    )
    m.pdf.save(f"{m.nummer}.pdf", ContentFile(pdf), save=False)
    m.save()
    return m


def brief_pdf(brief: Brief) -> bytes:
    return pdf_rendern("buero/pdf/brief.html", {"b": brief, "adresse": brief.kunde.adresse_zeilen})


def brief_speichern_pdf(brief: Brief):
    pdf = brief_pdf(brief)
    if brief.pdf:
        brief.pdf.delete(save=False)
    brief.pdf.save(f"{brief.nummer}.pdf", ContentFile(pdf), save=False)
    brief.save()
