import hashlib
import os
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from rest_framework.authtoken.models import Token

from . import forms
from .models import (
    Artikel, Brief, Eingangsrechnung, FestgeschriebenError, Firma, Kunde, Mahnstufe, Mahnung, Position, Rechnung,
    Textbaustein,
)
from .services import dokumente, einlesen, versand


def _fehler(request, e):
    for m in (e.messages if isinstance(e, ValidationError) else [str(e)]):
        messages.error(request, m)


def _firma_noetig(request):
    if not Firma.laden():
        messages.info(request, "Bitte zuerst die Firmendaten eintragen. Sie erscheinen auf jeder Rechnung.")
        return redirect("firma")
    return None


# --- Übersicht ---------------------------------------------------------------
def start(request):
    if (r := _firma_noetig(request)) is not None:
        return r
    heute = timezone.localdate()
    offen = list(Rechnung.objects.filter(status=Rechnung.STATUS_OFFEN).select_related("kunde"))
    jahr = Rechnung.objects.filter(datum__year=heute.year).exclude(status=Rechnung.STATUS_ENTWURF)
    umsatz = sum((-r.summe_netto if r.art == Rechnung.ART_GUTSCHRIFT else r.summe_netto for r in jahr), Decimal("0"))
    ueberfaellig = [r for r in offen if r.ist_ueberfaellig]
    return render(
        request,
        "buero/start.html",
        {
            "offen_summe": sum((r.offen for r in offen), Decimal("0")),
            "offen_anzahl": len(offen),
            "ueberfaellig": ueberfaellig,
            "ueberfaellig_summe": sum((r.offen for r in ueberfaellig), Decimal("0")),
            "umsatz": umsatz,
            "jahr": heute.year,
            "entwuerfe": Rechnung.objects.filter(status=Rechnung.STATUS_ENTWURF).select_related("kunde")[:10],
            "mahnvorschlaege": [(r, dokumente.mahnung_vorschlag(r)) for r in ueberfaellig],
            "eingang_offen": Eingangsrechnung.objects.filter(bezahlt=False)[:10],
        },
    )


# --- Firma und Einstellungen -------------------------------------------------
def firma(request):
    instanz = Firma.laden()
    form = forms.FirmaForm(request.POST or None, request.FILES or None, instance=instanz)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Firmendaten gespeichert.")
        return redirect("einstellungen")
    return render(request, "buero/formular.html", {"form": form, "titel": "Firmendaten", "multipart": True})


def einstellungen(request):
    token = Token.objects.filter(user=request.user).first()
    return render(
        request,
        "buero/einstellungen.html",
        {"mahnstufen": Mahnstufe.objects.all(), "textbausteine": Textbaustein.objects.all(), "token": token},
    )


@require_POST
def api_token(request):
    Token.objects.filter(user=request.user).delete()
    Token.objects.create(user=request.user)
    messages.success(request, "Neuer API-Schlüssel erstellt. Der alte ist ab sofort ungültig.")
    return redirect("einstellungen")


def _einfach_bearbeiten(request, modell, formklasse, pk, titel, ziel):
    instanz = get_object_or_404(modell, pk=pk) if pk else None
    form = formklasse(request.POST or None, instance=instanz)
    if request.method == "POST":
        if "loeschen" in request.POST and instanz:
            instanz.delete()
            messages.success(request, f"{titel} gelöscht.")
            return redirect(ziel)
        if form.is_valid():
            form.save()
            messages.success(request, f"{titel} gespeichert.")
            return redirect(ziel)
    return render(request, "buero/formular.html", {"form": form, "titel": titel, "loeschbar": bool(instanz)})


def mahnstufe_bearbeiten(request, pk=None):
    return _einfach_bearbeiten(request, Mahnstufe, forms.MahnstufeForm, pk, "Mahnstufe", "einstellungen")


def textbaustein_bearbeiten(request, pk=None):
    return _einfach_bearbeiten(request, Textbaustein, forms.TextbausteinForm, pk, "Textbaustein", "einstellungen")


# --- Kunden ------------------------------------------------------------------
def kunden(request):
    q = request.GET.get("q", "").strip()
    liste = Kunde.objects.all()
    if q:
        liste = liste.filter(
            Q(nummer__icontains=q) | Q(firma__icontains=q) | Q(nachname__icontains=q) | Q(vorname__icontains=q)
            | Q(ort__icontains=q) | Q(email__icontains=q)
        )
    return render(request, "buero/kunden.html", {"kunden": liste, "q": q})


def kunde_bearbeiten(request, pk=None):
    instanz = get_object_or_404(Kunde, pk=pk) if pk else None
    form = forms.KundeForm(request.POST or None, instance=instanz)
    if request.method == "POST" and form.is_valid():
        kunde = form.save()
        messages.success(request, f"Kunde {kunde.nummer} gespeichert.")
        return redirect(kunde)
    return render(
        request, "buero/formular.html", {"form": form, "titel": "Kunde bearbeiten" if instanz else "Neuer Kunde", "kunde_form": True}
    )


def kunde_detail(request, pk):
    kunde = get_object_or_404(Kunde, pk=pk)
    return render(
        request,
        "buero/kunde_detail.html",
        {"kunde": kunde, "rechnungen": kunde.rechnungen.all(), "briefe": kunde.briefe.all(), "umsatz": kunde.umsatz()},
    )


# --- Artikel -----------------------------------------------------------------
def artikel_liste(request):
    q = request.GET.get("q", "").strip()
    liste = Artikel.objects.all()
    if q:
        liste = liste.filter(Q(nummer__icontains=q) | Q(bezeichnung__icontains=q))
    return render(request, "buero/artikel.html", {"artikel": liste, "q": q})


def artikel_bearbeiten(request, pk=None):
    instanz = get_object_or_404(Artikel, pk=pk) if pk else None
    form = forms.ArtikelForm(request.POST or None, instance=instanz)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Artikel gespeichert.")
        return redirect("artikel_liste")
    return render(request, "buero/formular.html", {"form": form, "titel": "Artikel bearbeiten" if instanz else "Neuer Artikel"})


# --- Rechnungen ----------------------------------------------------------------
def rechnungen(request):
    status = request.GET.get("status", "")
    q = request.GET.get("q", "").strip()
    liste = Rechnung.objects.select_related("kunde").prefetch_related("positionen", "zahlungen")
    if status == "ueberfaellig":
        liste = liste.filter(status=Rechnung.STATUS_OFFEN, faellig_am__lt=timezone.localdate())
    elif status:
        liste = liste.filter(status=status)
    if q:
        liste = liste.filter(Q(nummer__icontains=q) | Q(kunde__firma__icontains=q) | Q(kunde__nachname__icontains=q) | Q(betreff__icontains=q))
    return render(request, "buero/rechnungen.html", {"rechnungen": liste, "status": status, "q": q, "stati": Rechnung.STATI})


def _artikel_daten():
    return {
        a.pk: {
            "bezeichnung": a.bezeichnung,
            "beschreibung": a.beschreibung,
            "einheit": a.einheit,
            "preis": str(a.preis_netto),
            "ust": str(a.ust_satz),
        }
        for a in Artikel.objects.filter(aktiv=True)
    }


def rechnung_bearbeiten(request, pk=None):
    if (r := _firma_noetig(request)) is not None:
        return r
    firma = Firma.laden()
    if pk:
        rechnung = get_object_or_404(Rechnung, pk=pk)
        if not rechnung.ist_entwurf:
            messages.error(request, "Abgeschlossene Rechnungen können nicht mehr geändert werden.")
            return redirect(rechnung)
    else:
        rechnung = Rechnung(einleitung=firma.rechnung_einleitung, schlusstext=firma.rechnung_schluss)
        if request.GET.get("kunde"):
            rechnung.kunde = Kunde.objects.filter(pk=request.GET["kunde"]).first()
    form = forms.RechnungForm(request.POST or None, instance=rechnung)
    formset = forms.PositionFormSet(request.POST or None, instance=rechnung)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            rechnung = form.save()
            formset.instance = rechnung
            positionen = formset.save(commit=False)
            for obj in formset.deleted_objects:
                obj.delete()
            for i, p in enumerate(positionen):
                p.rechnung = rechnung
                p.save()
            for i, f in enumerate(formset.ordered_forms if formset.can_order else formset.forms):
                if f.instance.pk and not f.cleaned_data.get("DELETE"):
                    Position.objects.filter(pk=f.instance.pk).update(reihenfolge=i)
        messages.success(request, "Entwurf gespeichert.")
        if "abschliessen" in request.POST:
            return abschliessen_ausfuehren(request, rechnung)
        return redirect(rechnung)
    return render(
        request,
        "buero/rechnung_form.html",
        {
            "form": form,
            "formset": formset,
            "rechnung": rechnung,
            "artikel_daten": _artikel_daten(),
            "textbausteine": Textbaustein.objects.filter(bereich__in=["einleitung", "schluss"]),
        },
    )


def rechnung_detail(request, pk):
    rechnung = get_object_or_404(Rechnung.objects.select_related("kunde", "bezug"), pk=pk)
    fehlend = []
    if rechnung.ist_entwurf:
        from .services.erechnung import pflichtangaben_pruefen

        fehlend = pflichtangaben_pruefen(
            rechnung, Firma.laden(), xrechnung=rechnung.kunde.rechnungsformat == Kunde.FORMAT_XRECHNUNG
        )
    return render(
        request,
        "buero/rechnung_detail.html",
        {
            "r": rechnung,
            "gruppen": rechnung.steuergruppen(),
            "zahlung_form": forms.ZahlungForm(initial={"betrag": rechnung.offen}),
            "mahnvorschlag": dokumente.mahnung_vorschlag(rechnung),
            "fehlend": fehlend,
        },
    )


def abschliessen_ausfuehren(request, rechnung):
    try:
        rechnung = dokumente.abschliessen(rechnung)
    except (ValidationError, FestgeschriebenError) as e:
        _fehler(request, e)
        return redirect(rechnung)
    messages.success(request, f"Rechnung {rechnung.nummer} abgeschlossen. Sie kann jetzt versendet werden.")
    return redirect(rechnung)


@require_POST
def rechnung_abschliessen(request, pk):
    return abschliessen_ausfuehren(request, get_object_or_404(Rechnung, pk=pk))


@require_POST
def rechnung_loeschen(request, pk):
    rechnung = get_object_or_404(Rechnung, pk=pk)
    if not rechnung.ist_entwurf:
        messages.error(request, "Nur Entwürfe können gelöscht werden. Abgeschlossene Rechnungen bitte stornieren.")
        return redirect(rechnung)
    rechnung.delete()
    messages.success(request, "Entwurf gelöscht.")
    return redirect("rechnungen")


@require_POST
def rechnung_kopieren(request, pk):
    alt = get_object_or_404(Rechnung, pk=pk)
    heute = timezone.localdate()
    neu = Rechnung.objects.create(
        kunde=alt.kunde, betreff=alt.betreff, einleitung=alt.einleitung, schlusstext=alt.schlusstext,
        befreiungsgrund=alt.befreiungsgrund, kaeufer_referenz=alt.kaeufer_referenz, datum=heute, leistung_von=heute,
    )
    for p in alt.positionen.all():
        p.pk = None
        p.rechnung = neu
        p.save()
    messages.success(request, "Kopie als neuer Entwurf angelegt.")
    return redirect("rechnung_bearbeiten", pk=neu.pk)


@require_POST
def rechnung_stornieren(request, pk):
    rechnung = get_object_or_404(Rechnung, pk=pk)
    try:
        storno = dokumente.stornieren(rechnung)
    except ValidationError as e:
        _fehler(request, e)
        return redirect(rechnung)
    messages.success(request, f"Rechnung {rechnung.nummer} storniert. Stornorechnung {storno.nummer} wurde erstellt.")
    return redirect(storno)


@require_POST
def zahlung_erfassen(request, pk):
    rechnung = get_object_or_404(Rechnung, pk=pk)
    if rechnung.status not in (Rechnung.STATUS_OFFEN, Rechnung.STATUS_BEZAHLT):
        messages.error(request, "Zahlungen können nur zu offenen Rechnungen erfasst werden.")
        return redirect(rechnung)
    form = forms.ZahlungForm(request.POST)
    if form.is_valid():
        z = form.save(commit=False)
        z.rechnung = rechnung
        z.save()
        dokumente.zahlung_pruefen(rechnung)
        messages.success(request, "Zahlung erfasst.")
    else:
        messages.error(request, "Bitte Datum und Betrag prüfen.")
    return redirect(rechnung)


@require_POST
def zahlung_loeschen(request, pk, zahlung_pk):
    rechnung = get_object_or_404(Rechnung, pk=pk)
    rechnung.zahlungen.filter(pk=zahlung_pk).delete()
    dokumente.zahlung_pruefen(rechnung)
    messages.success(request, "Zahlung entfernt.")
    return redirect(rechnung)


def rechnung_vorschau(request, pk):
    rechnung = get_object_or_404(Rechnung, pk=pk)
    pdf = dokumente.rechnung_pdf(rechnung, entwurf=rechnung.ist_entwurf)
    return HttpResponse(pdf, content_type="application/pdf", headers={"Content-Disposition": "inline; filename=vorschau.pdf"})


def _datei(feld, als_download=False):
    if not feld:
        raise Http404("Datei nicht vorhanden")
    return FileResponse(feld.open("rb"), as_attachment=als_download, filename=os.path.basename(feld.name))


def rechnung_datei(request, pk, art):
    rechnung = get_object_or_404(Rechnung, pk=pk)
    return _datei(rechnung.xml if art == "xml" else rechnung.pdf, als_download=art == "xml")


def rechnung_senden(request, pk):
    rechnung = get_object_or_404(Rechnung, pk=pk)
    if rechnung.ist_entwurf:
        messages.error(request, "Bitte die Rechnung zuerst abschließen.")
        return redirect(rechnung)
    initial = {
        "an": rechnung.kunde.email,
        "betreff": f"{rechnung.titel} {rechnung.nummer} von {Firma.laden().name}",
        "text": versand.standardtext_rechnung(rechnung),
    }
    form = forms.VersandForm(request.POST or None, initial=initial)
    anhaenge = [rechnung.xml] if rechnung.xml else [rechnung.pdf]
    if rechnung.xml:
        anhaenge.append(rechnung.pdf)
    if request.method == "POST" and form.is_valid():
        try:
            rechnung.versendet_am = versand.senden(form.cleaned_data["an"], form.cleaned_data["betreff"], form.cleaned_data["text"], anhaenge)
        except Exception as e:  # SMTP-Fehler verständlich anzeigen
            messages.error(request, f"Versand fehlgeschlagen: {e}. Bitte die E-Mail-Einstellungen prüfen.")
        else:
            rechnung.save(update_fields=["versendet_am"])
            messages.success(request, f"Rechnung an {form.cleaned_data['an']} gesendet.")
            return redirect(rechnung)
    return render(
        request,
        "buero/senden.html",
        {"form": form, "titel": f"{rechnung.titel} {rechnung.nummer} senden", "anhaenge": anhaenge, "zurueck": rechnung.get_absolute_url()},
    )


# --- Mahnwesen -----------------------------------------------------------------
def mahnwesen(request):
    offen = Rechnung.objects.filter(status=Rechnung.STATUS_OFFEN, faellig_am__lt=timezone.localdate()).select_related("kunde")
    return render(
        request,
        "buero/mahnwesen.html",
        {
            "ueberfaellig": [(r, dokumente.mahnung_vorschlag(r)) for r in offen],
            "mahnungen": Mahnung.objects.select_related("rechnung", "rechnung__kunde")[:50],
            "stufen_fehlen": not Mahnstufe.objects.exists(),
        },
    )


def mahnung_erstellen(request, pk):
    rechnung = get_object_or_404(Rechnung, pk=pk)
    vorschlag = dokumente.mahnung_vorschlag(rechnung) or Mahnstufe.objects.filter(stufe__gt=rechnung.letzte_mahnstufe).first()
    if not vorschlag:
        messages.error(request, "Keine weitere Mahnstufe vorhanden. Mahnstufen unter Einstellungen anlegen.")
        return redirect(rechnung)
    form = forms.MahnungForm(request.POST or None, initial={"stufe": vorschlag, "gebuehr": vorschlag.gebuehr})
    if request.method == "POST" and form.is_valid():
        try:
            m = dokumente.mahnung_erstellen(rechnung, form.cleaned_data["stufe"], form.cleaned_data["gebuehr"])
        except ValidationError as e:
            _fehler(request, e)
            return redirect(rechnung)
        messages.success(request, f"Mahnung {m.nummer} erstellt.")
        return redirect("mahnung_senden", pk=m.pk)
    return render(request, "buero/formular.html", {"form": form, "titel": f"Mahnung zu {rechnung.nummer}", "zurueck": rechnung.get_absolute_url()})


def mahnung_datei(request, pk):
    return _datei(get_object_or_404(Mahnung, pk=pk).pdf)


def mahnung_senden(request, pk):
    m = get_object_or_404(Mahnung.objects.select_related("rechnung", "rechnung__kunde"), pk=pk)
    r = m.rechnung
    firma = Firma.laden()
    initial = {
        "an": r.kunde.email,
        "betreff": f"Zahlungserinnerung zu Rechnung {r.nummer}" if m.stufe == 1 else f"{m.stufe}. Mahnung zu Rechnung {r.nummer}",
        "text": f"Sehr geehrte Damen und Herren,\n\nanbei erhalten Sie unser Schreiben {m.nummer} zur Rechnung {r.nummer}.\n\nMit freundlichen Grüßen\n{firma.kontakt_name}\n{firma.name}",
    }
    form = forms.VersandForm(request.POST or None, initial=initial)
    anhaenge = [m.pdf, r.pdf]
    if request.method == "POST" and form.is_valid():
        try:
            m.versendet_am = versand.senden(form.cleaned_data["an"], form.cleaned_data["betreff"], form.cleaned_data["text"], anhaenge)
        except Exception as e:
            messages.error(request, f"Versand fehlgeschlagen: {e}. Bitte die E-Mail-Einstellungen prüfen.")
        else:
            m.save(update_fields=["versendet_am"])
            messages.success(request, "Mahnung gesendet.")
            return redirect(r)
    return render(
        request,
        "buero/senden.html",
        {"form": form, "titel": f"Mahnung {m.nummer} senden", "anhaenge": anhaenge, "zurueck": r.get_absolute_url(), "pdf_link": f"/mahnungen/{m.pk}/pdf/"},
    )


# --- Briefe --------------------------------------------------------------------
def briefe(request):
    return render(request, "buero/briefe.html", {"briefe": Brief.objects.select_related("kunde")})


def brief_bearbeiten(request, pk=None):
    if (r := _firma_noetig(request)) is not None:
        return r
    instanz = get_object_or_404(Brief, pk=pk) if pk else Brief()
    if not pk and request.GET.get("kunde"):
        instanz.kunde = Kunde.objects.filter(pk=request.GET["kunde"]).first()
    form = forms.BriefForm(request.POST or None, instance=instanz)
    if request.method == "POST" and form.is_valid():
        brief = form.save()
        dokumente.brief_speichern_pdf(brief)
        messages.success(request, "Brief gespeichert.")
        return redirect(brief)
    return render(
        request,
        "buero/formular.html",
        {"form": form, "titel": "Brief bearbeiten" if pk else "Neuer Brief", "textbausteine": Textbaustein.objects.filter(bereich="brief")},
    )


def brief_detail(request, pk):
    return render(request, "buero/brief_detail.html", {"b": get_object_or_404(Brief, pk=pk)})


def brief_datei(request, pk):
    brief = get_object_or_404(Brief, pk=pk)
    if not brief.pdf:
        dokumente.brief_speichern_pdf(brief)
    return _datei(brief.pdf)


def brief_senden(request, pk):
    b = get_object_or_404(Brief, pk=pk)
    firma = Firma.laden()
    if not b.pdf:
        dokumente.brief_speichern_pdf(b)
    form = forms.VersandForm(
        request.POST or None,
        initial={"an": b.kunde.email, "betreff": b.betreff, "text": f"Sehr geehrte Damen und Herren,\n\nanbei erhalten Sie unser Schreiben.\n\nMit freundlichen Grüßen\n{firma.kontakt_name}\n{firma.name}"},
    )
    if request.method == "POST" and form.is_valid():
        try:
            b.versendet_am = versand.senden(form.cleaned_data["an"], form.cleaned_data["betreff"], form.cleaned_data["text"], [b.pdf])
        except Exception as e:
            messages.error(request, f"Versand fehlgeschlagen: {e}. Bitte die E-Mail-Einstellungen prüfen.")
        else:
            b.save(update_fields=["versendet_am"])
            messages.success(request, "Brief gesendet.")
            return redirect(b)
    return render(request, "buero/senden.html", {"form": form, "titel": f"Brief {b.nummer} senden", "anhaenge": [b.pdf], "zurueck": b.get_absolute_url()})


@require_POST
def brief_loeschen(request, pk):
    b = get_object_or_404(Brief, pk=pk)
    if b.versendet_am:
        messages.error(request, "Versendete Briefe bleiben zur Nachweisbarkeit erhalten.")
        return redirect(b)
    if b.pdf:
        b.pdf.delete(save=False)
    b.delete()
    messages.success(request, "Brief gelöscht.")
    return redirect("briefe")


# --- Eingangsrechnungen -------------------------------------------------------
def eingang(request):
    form = forms.EingangForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data["datei"]
        daten = upload.read()
        try:
            e = eingangsrechnung_speichern(daten, upload.name)
        except (einlesen.EinleseFehler, ValidationError) as fehler:
            _fehler(request, fehler)
        else:
            messages.success(request, f"E-Rechnung {e.nummer} von {e.lieferant} eingelesen.")
            return redirect(e)
    return render(request, "buero/eingang.html", {"form": form, "liste": Eingangsrechnung.objects.all()})


def eingangsrechnung_speichern(daten: bytes, dateiname: str) -> Eingangsrechnung:
    sha = hashlib.sha256(daten).hexdigest()
    vorhanden = Eingangsrechnung.objects.filter(sha256=sha).first()
    if vorhanden:
        raise ValidationError(f"Diese Datei wurde bereits am {vorhanden.eingegangen:%d.%m.%Y} eingelesen ({vorhanden.nummer}).")
    erg = einlesen.einlesen(daten, dateiname)
    e = Eingangsrechnung(
        dateiname=dateiname, sha256=sha, format=erg.format, xml=erg.xml, lieferant=erg.lieferant[:200],
        lieferant_ust_id=erg.lieferant_ust_id[:30], nummer=erg.nummer[:100], datum=erg.datum, faellig_am=erg.faellig_am,
        waehrung=erg.waehrung[:3], netto=erg.netto, steuer=erg.steuer, brutto=erg.brutto, iban=erg.iban[:34],
        daten={"positionen": erg.positionen, "profil": erg.profil, "lieferant_adresse": erg.lieferant_adresse, "kaeufer": erg.kaeufer},
    )
    e.datei.save(os.path.basename(dateiname) or "e-rechnung", ContentFile(daten), save=False)
    e.save()
    return e


def eingang_detail(request, pk):
    e = get_object_or_404(Eingangsrechnung, pk=pk)
    if request.method == "POST":
        e.bezahlt = "bezahlt" in request.POST
        e.notiz = request.POST.get("notiz", "")
        e.save(update_fields=["bezahlt", "notiz"])
        messages.success(request, "Gespeichert.")
        return redirect(e)
    gc = None
    if e.iban and not e.bezahlt and e.brutto:
        class _Empf:
            name = e.lieferant or "Empfänger"
            iban = e.iban
            bic = ""
        gc = dokumente.girocode(_Empf, e.brutto, e.nummer)
    return render(request, "buero/eingang_detail.html", {"e": e, "girocode": gc})


def eingang_datei(request, pk):
    return _datei(get_object_or_404(Eingangsrechnung, pk=pk).datei, als_download=True)
