from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone

CENT = Decimal("0.01")


def runden(betrag):
    return Decimal(betrag).quantize(CENT, rounding=ROUND_HALF_UP)


# Einheitencodes nach UN/ECE Recommendation 20, wie sie die E-Rechnung verlangt
EINHEITEN = [
    ("C62", "Stück"),
    ("HUR", "Stunde"),
    ("MIN", "Minute"),
    ("DAY", "Tag"),
    ("WEE", "Woche"),
    ("MON", "Monat"),
    ("ANN", "Jahr"),
    ("LS", "Pauschale"),
    ("KGM", "Kilogramm"),
    ("MTR", "Meter"),
    ("MTK", "Quadratmeter"),
    ("LTR", "Liter"),
    ("KMT", "Kilometer"),
]
EINHEIT_TEXT = dict(EINHEITEN)

# Umsatzsteuer-Kategorien nach EN 16931 (UNTDID 5305)
UST_KATEGORIEN = [
    ("S", "Regelsteuersatz"),
    ("AE", "Steuerschuldnerschaft des Leistungsempfängers (Reverse Charge)"),
    ("K", "Innergemeinschaftliche Lieferung/Leistung"),
    ("G", "Ausfuhr (Drittland)"),
    ("E", "Steuerbefreit"),
]
UST_SAETZE = [(Decimal("19.00"), "19 %"), (Decimal("7.00"), "7 %"), (Decimal("0.00"), "0 %")]


class Firma(models.Model):
    """Die eigene Firma. Es gibt genau einen Datensatz (pk=1)."""

    name = models.CharField("Firmenname", max_length=200)
    inhaber = models.CharField("Inhaber/Geschäftsführung", max_length=200, blank=True)
    strasse = models.CharField("Straße und Hausnummer", max_length=200)
    plz = models.CharField("PLZ", max_length=10)
    ort = models.CharField("Ort", max_length=100)
    land = models.CharField("Land (ISO-Code)", max_length=2, default="DE")
    kontakt_name = models.CharField("Ansprechpartner", max_length=200, help_text="Pflichtangabe für XRechnung")
    telefon = models.CharField("Telefon", max_length=50, help_text="Pflichtangabe für XRechnung")
    email = models.EmailField("E-Mail", help_text="Absender für Rechnungen, Pflichtangabe für XRechnung")
    webseite = models.CharField("Webseite", max_length=200, blank=True)
    steuernummer = models.CharField("Steuernummer", max_length=50, blank=True)
    ust_id = models.CharField("USt-IdNr.", max_length=20, blank=True)
    handelsregister = models.CharField("Handelsregister", max_length=200, blank=True)
    bank_name = models.CharField("Bank", max_length=100, blank=True)
    iban = models.CharField("IBAN", max_length=34, blank=True)
    bic = models.CharField("BIC", max_length=11, blank=True)
    logo = models.ImageField("Logo", upload_to="firma/", blank=True)
    zahlungsziel_tage = models.PositiveIntegerField("Zahlungsziel (Tage)", default=14)
    rechnung_einleitung = models.TextField(
        "Standard-Einleitung Rechnung", blank=True, default="Vielen Dank für Ihren Auftrag. Wir berechnen Ihnen folgende Leistungen:"
    )
    rechnung_schluss = models.TextField(
        "Standard-Schlusstext Rechnung",
        blank=True,
        default="Bitte überweisen Sie den Rechnungsbetrag bis zum {faellig_am} unter Angabe der Rechnungsnummer {nummer}.",
    )
    fusszeile = models.TextField("Zusätzliche Fußzeile", blank=True)
    akzentfarbe = models.CharField("Akzentfarbe für PDFs", max_length=7, default="#1f6f54")

    class Meta:
        verbose_name = "Firma"
        verbose_name_plural = "Firma"

    def __str__(self):
        return self.name

    def clean(self):
        if not (self.steuernummer or self.ust_id):
            raise ValidationError("Bitte Steuernummer oder USt-IdNr. angeben (Pflicht auf jeder Rechnung).")

    @classmethod
    def laden(cls):
        return cls.objects.filter(pk=1).first()

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @property
    def adresse_zeilen(self):
        return [self.name, self.strasse, f"{self.plz} {self.ort}"]


class Nummernkreis(models.Model):
    """Fortlaufende, lückenlose Nummern je Belegart und Jahr."""

    ART_RECHNUNG = "rechnung"
    ART_KUNDE = "kunde"
    ART_MAHNUNG = "mahnung"
    ART_BRIEF = "brief"
    ARTEN = [(ART_RECHNUNG, "Rechnungen"), (ART_KUNDE, "Kunden"), (ART_MAHNUNG, "Mahnungen"), (ART_BRIEF, "Briefe")]

    art = models.CharField(max_length=20, choices=ARTEN, unique=True)
    muster = models.CharField(
        "Muster", max_length=50, help_text="Platzhalter: {jahr}, {nr}. Beispiel: RE-{jahr}-{nr:04d}"
    )
    jaehrlich_neu = models.BooleanField("Jedes Jahr bei 1 beginnen", default=True)
    jahr = models.PositiveIntegerField(default=0)
    letzte_nr = models.PositiveIntegerField("Zuletzt vergeben", default=0)

    STANDARD = {
        ART_RECHNUNG: ("RE-{jahr}-{nr:04d}", True),
        ART_KUNDE: ("K{nr:05d}", False),
        ART_MAHNUNG: ("MA-{jahr}-{nr:04d}", True),
        ART_BRIEF: ("BR-{jahr}-{nr:04d}", True),
    }

    class Meta:
        verbose_name = "Nummernkreis"
        verbose_name_plural = "Nummernkreise"

    def __str__(self):
        return self.get_art_display()

    @classmethod
    def naechste(cls, art, datum=None):
        """Vergibt die nächste Nummer. Muss innerhalb einer Transaktion laufen, damit nichts doppelt vergeben wird."""
        jahr = (datum or timezone.localdate()).year
        with transaction.atomic():
            muster, jaehrlich = cls.STANDARD[art]
            kreis, _ = cls.objects.select_for_update().get_or_create(
                art=art, defaults={"muster": muster, "jaehrlich_neu": jaehrlich, "jahr": jahr}
            )
            if kreis.jaehrlich_neu and kreis.jahr != jahr:
                kreis.jahr = jahr
                kreis.letzte_nr = 0
            kreis.letzte_nr += 1
            kreis.save()
            return kreis.muster.format(jahr=jahr, nr=kreis.letzte_nr)


class Kunde(models.Model):
    TYP_FIRMA = "firma"
    TYP_PRIVAT = "privat"
    TYPEN = [(TYP_FIRMA, "Geschäftskunde"), (TYP_PRIVAT, "Privatkunde")]

    FORMAT_PDF = "pdf"
    FORMAT_ZUGFERD = "zugferd"
    FORMAT_XRECHNUNG = "xrechnung"
    FORMATE = [
        (FORMAT_ZUGFERD, "ZUGFeRD (PDF mit eingebetteten Daten)"),
        (FORMAT_XRECHNUNG, "XRechnung (XML, z. B. für Behörden)"),
        (FORMAT_PDF, "Nur PDF (nur für Privatkunden zulässig)"),
    ]

    nummer = models.CharField("Kundennummer", max_length=30, unique=True, blank=True)
    typ = models.CharField("Kundenart", max_length=10, choices=TYPEN, default=TYP_FIRMA)
    firma = models.CharField("Firma", max_length=200, blank=True)
    anrede = models.CharField("Anrede", max_length=30, blank=True)
    vorname = models.CharField("Vorname", max_length=100, blank=True)
    nachname = models.CharField("Nachname", max_length=100, blank=True)
    zusatz = models.CharField("Adresszusatz", max_length=200, blank=True)
    strasse = models.CharField("Straße und Hausnummer", max_length=200)
    plz = models.CharField("PLZ", max_length=10)
    ort = models.CharField("Ort", max_length=100)
    land = models.CharField("Land (ISO-Code)", max_length=2, default="DE")
    email = models.EmailField("E-Mail", blank=True, help_text="Empfänger für Rechnungen, Pflicht für XRechnung")
    telefon = models.CharField("Telefon", max_length=50, blank=True)
    ust_id = models.CharField("USt-IdNr. des Kunden", max_length=20, blank=True)
    leitweg_id = models.CharField(
        "Leitweg-ID / Käuferreferenz", max_length=50, blank=True, help_text="Bei Behörden Pflicht; sonst optional"
    )
    zahlungsziel_tage = models.PositiveIntegerField("Zahlungsziel (Tage)", null=True, blank=True)
    rechnungsformat = models.CharField("Rechnungsformat", max_length=10, choices=FORMATE, default=FORMAT_ZUGFERD)
    notizen = models.TextField("Interne Notizen", blank=True)
    angelegt = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Kunde"
        verbose_name_plural = "Kunden"
        ordering = ["nummer"]

    def __str__(self):
        return f"{self.nummer} {self.anzeigename}".strip()

    def save(self, *args, **kwargs):
        if not self.nummer:
            self.nummer = Nummernkreis.naechste(Nummernkreis.ART_KUNDE)
        super().save(*args, **kwargs)

    def clean(self):
        if self.typ == self.TYP_FIRMA and not self.firma:
            raise ValidationError({"firma": "Bitte den Firmennamen angeben."})
        if self.typ == self.TYP_PRIVAT and not self.nachname:
            raise ValidationError({"nachname": "Bitte den Nachnamen angeben."})
        if self.rechnungsformat == self.FORMAT_XRECHNUNG and not self.email:
            raise ValidationError({"email": "Für XRechnung ist eine E-Mail-Adresse des Kunden Pflicht."})

    def get_absolute_url(self):
        return reverse("kunde_detail", args=[self.pk])

    @property
    def personenname(self):
        return " ".join(p for p in [self.vorname, self.nachname] if p)

    @property
    def anzeigename(self):
        return self.firma or self.personenname

    @property
    def adresse_zeilen(self):
        zeilen = []
        if self.firma:
            zeilen.append(self.firma)
            if self.personenname:
                zeilen.append(f"{self.anrede} {self.personenname}".strip())
        else:
            zeilen.append(f"{self.anrede} {self.personenname}".strip())
        if self.zusatz:
            zeilen.append(self.zusatz)
        zeilen += [self.strasse, f"{self.plz} {self.ort}"]
        if self.land != "DE":
            zeilen.append(self.land)
        return zeilen

    def umsatz(self):
        summe = Decimal("0")
        for r in self.rechnungen.exclude(status=Rechnung.STATUS_ENTWURF):
            summe += -r.summe_netto if r.art == Rechnung.ART_GUTSCHRIFT else r.summe_netto
        return summe


class Artikel(models.Model):
    nummer = models.CharField("Artikelnummer", max_length=50, unique=True)
    bezeichnung = models.CharField("Bezeichnung", max_length=200)
    beschreibung = models.TextField("Beschreibung", blank=True)
    einheit = models.CharField("Einheit", max_length=3, choices=EINHEITEN, default="C62")
    preis_netto = models.DecimalField("Preis netto", max_digits=12, decimal_places=2)
    ust_satz = models.DecimalField("USt-Satz", max_digits=5, decimal_places=2, choices=UST_SAETZE, default=Decimal("19.00"))
    aktiv = models.BooleanField("Aktiv", default=True)

    class Meta:
        verbose_name = "Artikel"
        verbose_name_plural = "Artikel"
        ordering = ["nummer"]

    def __str__(self):
        return f"{self.nummer} {self.bezeichnung}"

    def get_absolute_url(self):
        return reverse("artikel_liste")


class Textbaustein(models.Model):
    BEREICHE = [
        ("einleitung", "Rechnung: Einleitung"),
        ("schluss", "Rechnung: Schlusstext"),
        ("brief", "Brief"),
        ("email", "E-Mail-Text"),
    ]
    name = models.CharField("Name", max_length=100)
    bereich = models.CharField("Verwendung", max_length=20, choices=BEREICHE)
    text = models.TextField("Text")

    class Meta:
        verbose_name = "Textbaustein"
        verbose_name_plural = "Textbausteine"
        ordering = ["bereich", "name"]

    def __str__(self):
        return self.name


class FestgeschriebenError(ValidationError):
    pass


class Rechnung(models.Model):
    ART_RECHNUNG = "380"
    ART_GUTSCHRIFT = "381"
    # Stornos sind Gutschriften (381) mit Bezug auf die Originalrechnung, Beträge bleiben positiv (EN 16931)
    ARTEN = [(ART_RECHNUNG, "Rechnung"), (ART_GUTSCHRIFT, "Gutschrift")]

    STATUS_ENTWURF = "entwurf"
    STATUS_OFFEN = "offen"
    STATUS_BEZAHLT = "bezahlt"
    STATUS_STORNIERT = "storniert"
    STATI = [
        (STATUS_ENTWURF, "Entwurf"),
        (STATUS_OFFEN, "Offen"),
        (STATUS_BEZAHLT, "Bezahlt"),
        (STATUS_STORNIERT, "Storniert"),
    ]

    nummer = models.CharField("Rechnungsnummer", max_length=50, unique=True, null=True, blank=True)
    art = models.CharField("Art", max_length=3, choices=ARTEN, default=ART_RECHNUNG)
    status = models.CharField("Status", max_length=10, choices=STATI, default=STATUS_ENTWURF)
    kunde = models.ForeignKey(Kunde, on_delete=models.PROTECT, related_name="rechnungen", verbose_name="Kunde")
    datum = models.DateField("Rechnungsdatum", default=timezone.localdate)
    leistung_von = models.DateField("Leistungsdatum / Leistung von", default=timezone.localdate)
    leistung_bis = models.DateField("Leistung bis", null=True, blank=True, help_text="Nur bei Leistungszeitraum")
    faellig_am = models.DateField("Fällig am", null=True, blank=True)
    kaeufer_referenz = models.CharField(
        "Käuferreferenz / Leitweg-ID", max_length=100, blank=True, help_text="Leer = Leitweg-ID oder Kundennummer"
    )
    bestellnummer = models.CharField("Bestellnummer des Kunden", max_length=100, blank=True)
    betreff = models.CharField("Betreff", max_length=200, blank=True)
    einleitung = models.TextField("Einleitung", blank=True)
    schlusstext = models.TextField("Schlusstext", blank=True)
    befreiungsgrund = models.CharField(
        "Hinweis zur Steuerbefreiung", max_length=300, blank=True, help_text="Pflicht bei 0 % (z. B. Reverse Charge)"
    )
    bezug = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="korrekturen", verbose_name="Bezieht sich auf"
    )
    # Festgeschriebene Angaben (werden beim Abschließen gespeichert und danach nicht mehr verändert)
    empfaenger_adresse = models.TextField(blank=True)
    pdf = models.FileField(upload_to="rechnungen/%Y/", blank=True)
    xml = models.FileField(upload_to="rechnungen/%Y/", blank=True)
    format = models.CharField(max_length=10, choices=Kunde.FORMATE, blank=True)
    festgeschrieben_am = models.DateTimeField(null=True, blank=True)
    versendet_am = models.DateTimeField(null=True, blank=True)
    angelegt = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Rechnung"
        verbose_name_plural = "Rechnungen"
        ordering = ["-datum", "-nummer"]

    def __str__(self):
        return self.nummer or f"Entwurf {self.pk}"

    def get_absolute_url(self):
        return reverse("rechnung_detail", args=[self.pk])

    @property
    def titel(self):
        if self.art == self.ART_GUTSCHRIFT:
            return "Stornorechnung" if self.bezug_id else "Gutschrift"
        return "Rechnung"

    @property
    def ist_entwurf(self):
        return self.status == self.STATUS_ENTWURF

    def pruefe_aenderbar(self):
        if not self.ist_entwurf:
            raise FestgeschriebenError("Abgeschlossene Rechnungen können nicht mehr geändert werden. Bitte stornieren.")

    # --- Beträge ---------------------------------------------------------
    def steuergruppen(self):
        """Summen je Steuerkategorie und Satz, wie sie auch in der E-Rechnung stehen."""
        gruppen = {}
        for p in self.positionen.all():
            key = (p.ust_kategorie, p.ust_satz)
            gruppen.setdefault(key, Decimal("0"))
            gruppen[key] += p.netto
        ergebnis = []
        for (kat, satz), netto in sorted(gruppen.items(), key=lambda x: (-x[0][1], x[0][0])):
            netto = runden(netto)
            ergebnis.append({"kategorie": kat, "satz": satz, "netto": netto, "steuer": runden(netto * satz / 100)})
        return ergebnis

    @property
    def summe_netto(self):
        return runden(sum((p.netto for p in self.positionen.all()), Decimal("0")))

    @property
    def summe_steuer(self):
        return runden(sum((g["steuer"] for g in self.steuergruppen()), Decimal("0")))

    @property
    def summe_brutto(self):
        return self.summe_netto + self.summe_steuer

    @property
    def bezahlt(self):
        return runden(sum((z.betrag for z in self.zahlungen.all()), Decimal("0")))

    @property
    def offen(self):
        return self.summe_brutto - self.bezahlt

    @property
    def ist_ueberfaellig(self):
        return self.status == self.STATUS_OFFEN and self.faellig_am and self.faellig_am < timezone.localdate()

    @property
    def tage_ueberfaellig(self):
        if not self.ist_ueberfaellig:
            return 0
        return (timezone.localdate() - self.faellig_am).days

    @property
    def letzte_mahnstufe(self):
        m = self.mahnungen.order_by("-stufe").first()
        return m.stufe if m else 0

    @property
    def referenz(self):
        return self.kaeufer_referenz or self.kunde.leitweg_id or self.kunde.nummer

    def text_ersetzen(self, text):
        werte = {
            "nummer": self.nummer or "",
            "faellig_am": self.faellig_am.strftime("%d.%m.%Y") if self.faellig_am else "",
            "datum": self.datum.strftime("%d.%m.%Y"),
            "betrag": f"{self.summe_brutto:,.2f} €".replace(",", "X").replace(".", ",").replace("X", "."),
            "kunde": self.kunde.anzeigename,
        }
        for k, v in werte.items():
            text = text.replace("{" + k + "}", v)
        return text


class Position(models.Model):
    rechnung = models.ForeignKey(Rechnung, on_delete=models.CASCADE, related_name="positionen")
    reihenfolge = models.PositiveIntegerField(default=0)
    artikel = models.ForeignKey(Artikel, null=True, blank=True, on_delete=models.SET_NULL, verbose_name="Artikel")
    bezeichnung = models.CharField("Bezeichnung", max_length=200)
    beschreibung = models.TextField("Beschreibung", blank=True)
    menge = models.DecimalField("Menge", max_digits=12, decimal_places=3, default=Decimal("1"))
    einheit = models.CharField("Einheit", max_length=3, choices=EINHEITEN, default="C62")
    einzelpreis = models.DecimalField("Einzelpreis netto", max_digits=12, decimal_places=2)
    rabatt = models.DecimalField("Rabatt %", max_digits=5, decimal_places=2, default=Decimal("0"))
    ust_kategorie = models.CharField("Steuerart", max_length=2, choices=UST_KATEGORIEN, default="S")
    ust_satz = models.DecimalField("USt-Satz", max_digits=5, decimal_places=2, choices=UST_SAETZE, default=Decimal("19.00"))

    class Meta:
        ordering = ["reihenfolge", "pk"]
        verbose_name = "Position"
        verbose_name_plural = "Positionen"

    def __str__(self):
        return self.bezeichnung

    @property
    def netto_vor_rabatt(self):
        return runden(self.menge * self.einzelpreis)

    @property
    def rabatt_betrag(self):
        return runden(self.netto_vor_rabatt * self.rabatt / 100)

    @property
    def netto(self):
        return self.netto_vor_rabatt - self.rabatt_betrag

    @property
    def einheit_text(self):
        return EINHEIT_TEXT.get(self.einheit, self.einheit)


class Zahlung(models.Model):
    rechnung = models.ForeignKey(Rechnung, on_delete=models.CASCADE, related_name="zahlungen")
    datum = models.DateField("Datum", default=timezone.localdate)
    betrag = models.DecimalField("Betrag", max_digits=12, decimal_places=2)
    notiz = models.CharField("Notiz", max_length=200, blank=True)

    class Meta:
        ordering = ["datum"]
        verbose_name = "Zahlung"
        verbose_name_plural = "Zahlungen"


class Mahnstufe(models.Model):
    stufe = models.PositiveSmallIntegerField("Stufe", unique=True, choices=[(i, str(i)) for i in range(1, 5)])
    bezeichnung = models.CharField("Bezeichnung", max_length=100)
    tage_nach_faelligkeit = models.PositiveIntegerField(
        "Fällig ab (Tage nach Fälligkeit)", help_text="Ab so vielen Tagen Verzug wird diese Stufe vorgeschlagen"
    )
    gebuehr = models.DecimalField("Mahngebühr", max_digits=8, decimal_places=2, default=Decimal("0"))
    frist_tage = models.PositiveIntegerField("Neue Zahlungsfrist (Tage)", default=7)
    text = models.TextField(
        "Text", help_text="Platzhalter: {nummer}, {datum}, {betrag}, {faellig_am}, {neue_frist}, {kunde}"
    )

    class Meta:
        ordering = ["stufe"]
        verbose_name = "Mahnstufe"
        verbose_name_plural = "Mahnstufen"

    def __str__(self):
        return f"{self.stufe}. {self.bezeichnung}"


class Mahnung(models.Model):
    nummer = models.CharField("Mahnungsnummer", max_length=50, unique=True)
    rechnung = models.ForeignKey(Rechnung, on_delete=models.PROTECT, related_name="mahnungen")
    stufe = models.PositiveSmallIntegerField("Stufe")
    datum = models.DateField("Datum", default=timezone.localdate)
    gebuehr = models.DecimalField("Mahngebühr", max_digits=8, decimal_places=2, default=Decimal("0"))
    offener_betrag = models.DecimalField("Offener Rechnungsbetrag", max_digits=12, decimal_places=2)
    neue_frist = models.DateField("Zahlen bis")
    text = models.TextField("Text")
    pdf = models.FileField(upload_to="mahnungen/%Y/", blank=True)
    versendet_am = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-datum", "-stufe"]
        verbose_name = "Mahnung"
        verbose_name_plural = "Mahnungen"

    def __str__(self):
        return self.nummer

    @property
    def gesamt(self):
        return self.offener_betrag + self.gebuehr


class Brief(models.Model):
    nummer = models.CharField("Briefnummer", max_length=50, unique=True, blank=True)
    kunde = models.ForeignKey(Kunde, on_delete=models.PROTECT, related_name="briefe", verbose_name="Empfänger")
    datum = models.DateField("Datum", default=timezone.localdate)
    betreff = models.CharField("Betreff", max_length=200)
    text = models.TextField("Text")
    pdf = models.FileField(upload_to="briefe/%Y/", blank=True)
    versendet_am = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-datum", "-pk"]
        verbose_name = "Brief"
        verbose_name_plural = "Briefe"

    def __str__(self):
        return f"{self.nummer} {self.betreff}"

    def save(self, *args, **kwargs):
        if not self.nummer:
            self.nummer = Nummernkreis.naechste(Nummernkreis.ART_BRIEF, self.datum)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("brief_detail", args=[self.pk])


class Eingangsrechnung(models.Model):
    """Empfangene E-Rechnung (XRechnung oder ZUGFeRD), im Original unverändert archiviert."""

    datei = models.FileField("Datei", upload_to="eingang/%Y/")
    dateiname = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64, unique=True)
    format = models.CharField("Format", max_length=40)
    xml = models.TextField(blank=True)
    lieferant = models.CharField("Lieferant", max_length=200, blank=True)
    lieferant_ust_id = models.CharField(max_length=30, blank=True)
    nummer = models.CharField("Rechnungsnummer", max_length=100, blank=True)
    datum = models.DateField("Rechnungsdatum", null=True, blank=True)
    faellig_am = models.DateField("Fällig am", null=True, blank=True)
    waehrung = models.CharField(max_length=3, default="EUR")
    netto = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    steuer = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    brutto = models.DecimalField("Betrag", max_digits=12, decimal_places=2, null=True, blank=True)
    iban = models.CharField(max_length=34, blank=True)
    daten = models.JSONField(default=dict)
    bezahlt = models.BooleanField("Bezahlt", default=False)
    notiz = models.TextField("Notiz", blank=True)
    eingegangen = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-eingegangen"]
        verbose_name = "Eingangsrechnung"
        verbose_name_plural = "Eingangsrechnungen"

    def __str__(self):
        return f"{self.lieferant} {self.nummer}"

    def get_absolute_url(self):
        return reverse("eingang_detail", args=[self.pk])
