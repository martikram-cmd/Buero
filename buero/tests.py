import shutil
import tempfile
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token

from .models import Artikel, Firma, Kunde, Mahnstufe, Position, Rechnung
from .services import dokumente, einlesen, erechnung

MEDIA = tempfile.mkdtemp()


def firma_anlegen():
    return Firma.objects.create(
        name="Muster Technik GmbH", inhaber="Max Muster", strasse="Hauptstraße 1", plz="10115", ort="Berlin",
        kontakt_name="Max Muster", telefon="+49 30 123456", email="info@muster-technik.example",
        ust_id="DE123456789", steuernummer="30/123/45678", iban="DE02120300000000202051", bic="BYLADEM1001",
        bank_name="Testbank",
    )


def kunde_anlegen(**extra):
    daten = dict(
        typ=Kunde.TYP_FIRMA, firma="Beispiel AG", anrede="Frau", vorname="Erika", nachname="Mustermann",
        strasse="Ringstraße 5", plz="80331", ort="München", email="buchhaltung@beispiel.example",
    )
    daten.update(extra)
    return Kunde.objects.create(**daten)


def rechnung_anlegen(kunde, **extra):
    r = Rechnung.objects.create(kunde=kunde, betreff="Wartung", einleitung="Vielen Dank.", schlusstext="Bitte zahlen bis {faellig_am}.", **extra)
    Position.objects.create(rechnung=r, bezeichnung="Wartung Server", menge=Decimal("3.5"), einheit="HUR", einzelpreis=Decimal("95.00"))
    Position.objects.create(rechnung=r, bezeichnung="Fachbuch", menge=2, einheit="C62", einzelpreis=Decimal("39.90"), ust_satz=Decimal("7.00"), rabatt=Decimal("10"))
    return r


@override_settings(MEDIA_ROOT=MEDIA, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class RechnungTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.firma = firma_anlegen()
        self.kunde = kunde_anlegen()

    def test_betraege(self):
        r = rechnung_anlegen(self.kunde)
        # 3,5 h × 95 = 332,50 (19 %); 2 × 39,90 = 79,80 − 7,98 Rabatt = 71,82 (7 %)
        self.assertEqual(r.summe_netto, Decimal("404.32"))
        gruppen = {g["satz"]: g for g in r.steuergruppen()}
        self.assertEqual(gruppen[Decimal("19.00")]["steuer"], Decimal("63.18"))
        self.assertEqual(gruppen[Decimal("7.00")]["steuer"], Decimal("5.03"))
        self.assertEqual(r.summe_brutto, Decimal("472.53"))

    def test_abschliessen_zugferd(self):
        r = dokumente.abschliessen(rechnung_anlegen(self.kunde))
        jahr = timezone.localdate().year
        self.assertEqual(r.nummer, f"RE-{jahr}-0001")
        self.assertEqual(r.status, Rechnung.STATUS_OFFEN)
        self.assertEqual(r.faellig_am, r.datum + timedelta(days=14))
        with r.pdf.open("rb") as f:
            pdf = f.read()
        erg = einlesen.einlesen(pdf)
        self.assertEqual(erg.nummer, r.nummer)
        self.assertEqual(erg.brutto, Decimal("472.53"))
        self.assertEqual(erg.lieferant, "Muster Technik GmbH")
        self.assertEqual(len(erg.positionen), 2)
        # zweite Rechnung bekommt die nächste Nummer
        r2 = dokumente.abschliessen(rechnung_anlegen(self.kunde))
        self.assertEqual(r2.nummer, f"RE-{jahr}-0002")

    def test_xrechnung_schema(self):
        self.kunde.rechnungsformat = Kunde.FORMAT_XRECHNUNG
        self.kunde.leitweg_id = "991-12345-67"
        self.kunde.save()
        r = dokumente.abschliessen(rechnung_anlegen(self.kunde, leistung_bis=date.today() + timedelta(days=30)))
        self.assertTrue(r.xml)
        with r.xml.open("rb") as f:
            xml = f.read()
        self.assertEqual(erechnung.schema_pruefen(xml), [])
        self.assertIn(b"xrechnung_3.0", xml)
        self.assertIn(b"991-12345-67", xml)
        erg = einlesen.einlesen(xml)
        self.assertEqual(erg.format, "XRechnung (CII)")

    def test_festgeschrieben(self):
        r = dokumente.abschliessen(rechnung_anlegen(self.kunde))
        with self.assertRaises(Exception):
            r.pruefe_aenderbar()
        with self.assertRaises(ValidationError):
            dokumente.abschliessen(r)

    def test_geschaeftskunde_nur_pdf_abgelehnt(self):
        self.kunde.rechnungsformat = Kunde.FORMAT_PDF
        self.kunde.save()
        with self.assertRaises(ValidationError):
            dokumente.abschliessen(rechnung_anlegen(self.kunde))

    def test_pflichtangaben(self):
        self.firma.iban = ""
        self.firma.save()
        r = rechnung_anlegen(self.kunde)
        with self.assertRaises(ValidationError):
            dokumente.abschliessen(r)
        r.refresh_from_db()
        self.assertIsNone(r.nummer)

    def test_reverse_charge(self):
        self.kunde.ust_id = "ATU12345678"
        self.kunde.land = "AT"
        self.kunde.save()
        r = Rechnung.objects.create(kunde=self.kunde)
        Position.objects.create(rechnung=r, bezeichnung="Beratung", menge=1, einzelpreis=Decimal("1000"), ust_kategorie="AE", ust_satz=Decimal("0"))
        r = dokumente.abschliessen(r)
        self.assertEqual(r.summe_brutto, Decimal("1000.00"))
        with r.pdf.open("rb") as f:
            xml = einlesen.xml_aus_pdf(f.read())
        self.assertIn(b"VATEX-EU-AE", xml)
        self.assertEqual(erechnung.schema_pruefen(xml), [])

    def test_storno(self):
        r = dokumente.abschliessen(rechnung_anlegen(self.kunde))
        storno = dokumente.stornieren(r)
        r.refresh_from_db()
        self.assertEqual(r.status, Rechnung.STATUS_STORNIERT)
        self.assertEqual(storno.art, Rechnung.ART_GUTSCHRIFT)
        self.assertEqual(storno.bezug, r)
        self.assertEqual(storno.summe_brutto, r.summe_brutto)
        with storno.pdf.open("rb") as f:
            xml = einlesen.xml_aus_pdf(f.read())
        self.assertIn(b"<ram:TypeCode>381</ram:TypeCode>", xml)
        self.assertIn(r.nummer.encode(), xml)
        with self.assertRaises(ValidationError):
            dokumente.stornieren(r)

    def test_zahlung_und_mahnung(self):
        r = rechnung_anlegen(self.kunde, datum=date.today() - timedelta(days=40))
        r = dokumente.abschliessen(r)
        self.assertTrue(r.ist_ueberfaellig)
        vorschlag = dokumente.mahnung_vorschlag(r)
        self.assertEqual(vorschlag.stufe, 1)
        m = dokumente.mahnung_erstellen(r, vorschlag)
        self.assertTrue(m.pdf)
        self.assertIn(r.nummer, m.text)
        self.assertEqual(dokumente.mahnung_vorschlag(r).stufe, 2)
        r.zahlungen.create(betrag=Decimal("100"))
        dokumente.zahlung_pruefen(r)
        self.assertEqual(r.status, Rechnung.STATUS_OFFEN)
        r.zahlungen.create(betrag=r.offen)
        dokumente.zahlung_pruefen(r)
        self.assertEqual(r.status, Rechnung.STATUS_BEZAHLT)

    def test_girocode(self):
        self.assertTrue(dokumente.girocode(self.firma, Decimal("12.34"), "RE-1").startswith("data:image/svg+xml"))
        self.assertIsNone(dokumente.girocode(self.firma, Decimal("0"), "RE-1"))


UBL_BEISPIEL = b"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
 xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
 xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
 <cbc:CustomizationID>urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0</cbc:CustomizationID>
 <cbc:ID>L-2026-77</cbc:ID><cbc:IssueDate>2026-10-01</cbc:IssueDate><cbc:DueDate>2026-10-15</cbc:DueDate>
 <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
 <cac:AccountingSupplierParty><cac:Party><cac:PostalAddress><cbc:StreetName>Weg 2</cbc:StreetName><cbc:CityName>Hamburg</cbc:CityName><cbc:PostalZone>20095</cbc:PostalZone></cac:PostalAddress>
 <cac:PartyTaxScheme><cbc:CompanyID>DE987654321</cbc:CompanyID></cac:PartyTaxScheme>
 <cac:PartyLegalEntity><cbc:RegistrationName>Lieferant KG</cbc:RegistrationName></cac:PartyLegalEntity></cac:Party></cac:AccountingSupplierParty>
 <cac:PaymentMeans><cbc:PaymentMeansCode>58</cbc:PaymentMeansCode><cac:PayeeFinancialAccount><cbc:ID>DE02120300000000202051</cbc:ID></cac:PayeeFinancialAccount></cac:PaymentMeans>
 <cac:TaxTotal><cbc:TaxAmount currencyID="EUR">19.00</cbc:TaxAmount></cac:TaxTotal>
 <cac:LegalMonetaryTotal><cbc:TaxExclusiveAmount currencyID="EUR">100.00</cbc:TaxExclusiveAmount><cbc:TaxInclusiveAmount currencyID="EUR">119.00</cbc:TaxInclusiveAmount></cac:LegalMonetaryTotal>
 <cac:InvoiceLine><cbc:ID>1</cbc:ID><cbc:InvoicedQuantity unitCode="C62">1</cbc:InvoicedQuantity><cbc:LineExtensionAmount currencyID="EUR">100.00</cbc:LineExtensionAmount>
 <cac:Item><cbc:Name>Toner</cbc:Name><cac:ClassifiedTaxCategory><cbc:Percent>19</cbc:Percent></cac:ClassifiedTaxCategory></cac:Item><cac:Price><cbc:PriceAmount currencyID="EUR">100.00</cbc:PriceAmount></cac:Price></cac:InvoiceLine>
</Invoice>"""


@override_settings(MEDIA_ROOT=MEDIA, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class OberflaecheTests(TestCase):
    def setUp(self):
        firma_anlegen()
        self.user = User.objects.create_user("martin", password="geheim-geheim")
        self.client.force_login(self.user)
        self.kunde = kunde_anlegen()

    def test_login_pflicht(self):
        self.client.logout()
        antwort = self.client.get("/rechnungen/")
        self.assertEqual(antwort.status_code, 302)
        self.assertTrue(antwort["Location"].startswith("/login/"))

    def test_seiten(self):
        r = dokumente.abschliessen(rechnung_anlegen(self.kunde))
        Artikel.objects.create(nummer="A1", bezeichnung="Stunde", preis_netto=Decimal("95"), einheit="HUR")
        for url in ["/", "/rechnungen/", "/rechnungen/neu/", f"/rechnungen/{r.pk}/", "/kunden/", f"/kunden/{self.kunde.pk}/",
                    "/kunden/neu/", "/artikel/", "/mahnwesen/", "/briefe/", "/briefe/neu/", "/eingang/", "/einstellungen/",
                    "/einstellungen/firma/", f"/rechnungen/{r.pk}/senden/", f"/rechnungen/{r.pk}/datei/pdf/"]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_rechnung_ueber_formular(self):
        daten = {
            "kunde": self.kunde.pk, "datum": "2026-10-14", "leistung_von": "2026-10-10", "betreff": "Test",
            "einleitung": "", "schlusstext": "", "positionen-TOTAL_FORMS": "1", "positionen-INITIAL_FORMS": "0",
            "positionen-MIN_NUM_FORMS": "1", "positionen-MAX_NUM_FORMS": "1000",
            "positionen-0-bezeichnung": "Beratung", "positionen-0-menge": "2", "positionen-0-einheit": "HUR",
            "positionen-0-einzelpreis": "100.00", "positionen-0-rabatt": "0", "positionen-0-ust_kategorie": "S",
            "positionen-0-ust_satz": "19.00", "abschliessen": "1",
        }
        antwort = self.client.post("/rechnungen/neu/", daten)
        self.assertEqual(antwort.status_code, 302)
        r = Rechnung.objects.get()
        self.assertEqual(r.status, Rechnung.STATUS_OFFEN)
        self.assertEqual(r.summe_brutto, Decimal("238.00"))
        antwort = self.client.post(f"/rechnungen/{r.pk}/senden/", {"an": "kunde@example.org", "betreff": "Rechnung", "text": "Hallo"})
        self.assertEqual(antwort.status_code, 302)
        from django.core import mail

        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(mail.outbox[0].attachments[0][0].startswith(r.nummer))

    def test_brief(self):
        antwort = self.client.post("/briefe/neu/", {"kunde": self.kunde.pk, "datum": "2026-10-14", "betreff": "Terminbestätigung", "text": "Hiermit bestätigen wir."})
        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(self.client.get(antwort["Location"]).status_code, 200)

    def test_eingang_ubl(self):
        antwort = self.client.post("/eingang/", {"datei": SimpleUploadedFile("rechnung.xml", UBL_BEISPIEL, "application/xml")})
        self.assertEqual(antwort.status_code, 302)
        e = self.client.get(antwort["Location"])
        self.assertContains(e, "Lieferant KG")
        self.assertContains(e, "Toner")
        doppelt = self.client.post("/eingang/", {"datei": SimpleUploadedFile("rechnung.xml", UBL_BEISPIEL)}, follow=True)
        self.assertContains(doppelt, "bereits")


@override_settings(MEDIA_ROOT=MEDIA)
class ApiTests(TestCase):
    def setUp(self):
        firma_anlegen()
        user = User.objects.create_user("api")
        self.token = Token.objects.create(user=user)
        self.h = {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    def test_ohne_token(self):
        self.assertEqual(self.client.get("/api/kunden/").status_code, 401)

    def test_ablauf(self):
        k = self.client.post("/api/kunden/", {"typ": "firma", "firma": "API GmbH", "strasse": "A 1", "plz": "12345", "ort": "Bonn", "email": "a@b.de"}, content_type="application/json", **self.h)
        self.assertEqual(k.status_code, 201, k.content)
        r = self.client.post(
            "/api/rechnungen/",
            {"kunde": k.json()["id"], "positionen": [{"bezeichnung": "Lizenz", "menge": "1", "einzelpreis": "50.00"}]},
            content_type="application/json", **self.h,
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["summe_brutto"], "59.50")
        rid = r.json()["id"]
        a = self.client.post(f"/api/rechnungen/{rid}/abschliessen/", **self.h)
        self.assertEqual(a.status_code, 200, a.content)
        self.assertTrue(a.json()["nummer"].startswith("RE-"))
        self.assertEqual(self.client.get(f"/api/rechnungen/{rid}/pdf/", **self.h).status_code, 200)
        aendern = self.client.patch(f"/api/rechnungen/{rid}/", {"betreff": "neu"}, content_type="application/json", **self.h)
        self.assertEqual(aendern.status_code, 400)
        z = self.client.post(f"/api/rechnungen/{rid}/zahlungen/", {"datum": "2026-10-14", "betrag": "59.50"}, content_type="application/json", **self.h)
        self.assertEqual(z.json()["status"], "bezahlt")
        self.assertEqual(self.client.get("/api/schema/", **self.h).status_code, 200)

    def test_eingang_api(self):
        a = self.client.post("/api/eingangsrechnungen/hochladen/", {"datei": SimpleUploadedFile("x.xml", UBL_BEISPIEL)}, **self.h)
        self.assertEqual(a.status_code, 201, a.content)
        self.assertEqual(a.json()["brutto"], "119.00")
