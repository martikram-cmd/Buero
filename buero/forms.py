from decimal import Decimal

from django import forms
from django.forms import inlineformset_factory

from .models import Artikel, Brief, Firma, Kunde, Mahnstufe, Position, Rechnung, Textbaustein, Zahlung


class DatumInput(forms.DateInput):
    input_type = "date"

    def __init__(self, **kwargs):
        super().__init__(format="%Y-%m-%d", **kwargs)


class FirmaForm(forms.ModelForm):
    class Meta:
        model = Firma
        fields = [
            "name", "inhaber", "strasse", "plz", "ort", "land", "kontakt_name", "telefon", "email", "webseite",
            "steuernummer", "ust_id", "handelsregister", "bank_name", "iban", "bic", "logo", "zahlungsziel_tage",
            "rechnung_einleitung", "rechnung_schluss", "fusszeile", "akzentfarbe",
        ]
        widgets = {
            "rechnung_einleitung": forms.Textarea(attrs={"rows": 3}),
            "rechnung_schluss": forms.Textarea(attrs={"rows": 3}),
            "fusszeile": forms.Textarea(attrs={"rows": 2}),
            "akzentfarbe": forms.TextInput(attrs={"type": "color"}),
        }

    def clean_iban(self):
        iban = self.cleaned_data["iban"].replace(" ", "").upper()
        if iban and not _iban_gueltig(iban):
            raise forms.ValidationError("Die IBAN ist ungültig (Prüfziffer stimmt nicht).")
        return iban


def _iban_gueltig(iban):
    if len(iban) < 15 or not iban[:2].isalpha():
        return False
    umgestellt = iban[4:] + iban[:4]
    try:
        zahl = int("".join(str(int(c, 36)) for c in umgestellt))
    except ValueError:
        return False
    return zahl % 97 == 1


class KundeForm(forms.ModelForm):
    class Meta:
        model = Kunde
        fields = [
            "typ", "firma", "anrede", "vorname", "nachname", "zusatz", "strasse", "plz", "ort", "land", "email",
            "telefon", "ust_id", "leitweg_id", "zahlungsziel_tage", "rechnungsformat", "notizen",
        ]
        widgets = {
            "anrede": forms.TextInput(attrs={"list": "anreden"}),
            "notizen": forms.Textarea(attrs={"rows": 3}),
        }


class ArtikelForm(forms.ModelForm):
    class Meta:
        model = Artikel
        fields = ["nummer", "bezeichnung", "beschreibung", "einheit", "preis_netto", "ust_satz", "aktiv"]
        widgets = {"beschreibung": forms.Textarea(attrs={"rows": 3})}


class RechnungForm(forms.ModelForm):
    class Meta:
        model = Rechnung
        fields = [
            "kunde", "datum", "leistung_von", "leistung_bis", "faellig_am", "betreff", "bestellnummer",
            "kaeufer_referenz", "einleitung", "schlusstext", "befreiungsgrund",
        ]
        widgets = {
            "datum": DatumInput(),
            "leistung_von": DatumInput(),
            "leistung_bis": DatumInput(),
            "faellig_am": DatumInput(),
            "einleitung": forms.Textarea(attrs={"rows": 3}),
            "schlusstext": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {"faellig_am": "Leer = Zahlungsziel des Kunden bzw. der Firma"}

    def clean(self):
        daten = super().clean()
        von, bis = daten.get("leistung_von"), daten.get("leistung_bis")
        if von and bis and bis < von:
            self.add_error("leistung_bis", "Das Ende liegt vor dem Beginn.")
        return daten


class PositionForm(forms.ModelForm):
    class Meta:
        model = Position
        fields = [
            "artikel", "bezeichnung", "beschreibung", "menge", "einheit", "einzelpreis", "rabatt",
            "ust_kategorie", "ust_satz",
        ]
        widgets = {"beschreibung": forms.Textarea(attrs={"rows": 1})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["artikel"].queryset = Artikel.objects.filter(aktiv=True)
        self.fields["artikel"].required = False

    def clean(self):
        daten = super().clean()
        kat, satz = daten.get("ust_kategorie"), daten.get("ust_satz")
        if kat and kat != "S" and satz not in (None, Decimal("0")):
            daten["ust_satz"] = Decimal("0.00")
        if kat == "S" and satz == Decimal("0"):
            self.add_error("ust_satz", "Bei Regelsteuersatz bitte 19 % oder 7 % wählen.")
        if daten.get("menge") == 0:
            self.add_error("menge", "Die Menge darf nicht 0 sein.")
        return daten


PositionFormSet = inlineformset_factory(
    Rechnung, Position, form=PositionForm, extra=0, can_delete=True, min_num=1, validate_min=True
)


class ZahlungForm(forms.ModelForm):
    class Meta:
        model = Zahlung
        fields = ["datum", "betrag", "notiz"]
        widgets = {"datum": DatumInput()}


class BriefForm(forms.ModelForm):
    class Meta:
        model = Brief
        fields = ["kunde", "datum", "betreff", "text"]
        widgets = {"datum": DatumInput(), "text": forms.Textarea(attrs={"rows": 14})}


class VersandForm(forms.Form):
    an = forms.CharField(label="An", help_text="Mehrere Adressen mit Komma trennen")
    betreff = forms.CharField(label="Betreff")
    text = forms.CharField(label="Text", widget=forms.Textarea(attrs={"rows": 10}))


class MahnungForm(forms.Form):
    stufe = forms.ModelChoiceField(label="Mahnstufe", queryset=Mahnstufe.objects.all())
    gebuehr = forms.DecimalField(label="Mahngebühr", max_digits=8, decimal_places=2, min_value=0)


class EingangForm(forms.Form):
    datei = forms.FileField(label="E-Rechnung", help_text="XRechnung (XML) oder ZUGFeRD/Factur-X (PDF)")


class MahnstufeForm(forms.ModelForm):
    class Meta:
        model = Mahnstufe
        fields = ["stufe", "bezeichnung", "tage_nach_faelligkeit", "gebuehr", "frist_tage", "text"]
        widgets = {"text": forms.Textarea(attrs={"rows": 5})}


class TextbausteinForm(forms.ModelForm):
    class Meta:
        model = Textbaustein
        fields = ["name", "bereich", "text"]
        widgets = {"text": forms.Textarea(attrs={"rows": 6})}
