"""Erzeugt E-Rechnungen im Format UN/CEFACT CII (EN 16931).

Dieselbe XML-Struktur dient für beide Formate:
- ZUGFeRD 2.x Profil EN 16931: XML wird in ein PDF/A-3 eingebettet
- XRechnung 3.0 (CII-Syntax): XML wird eigenständig versendet
"""

from decimal import Decimal

from lxml import etree

from ..models import Firma, Kunde, Rechnung

NS = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "qdt": "urn:un:unece:uncefact:data:standard:QualifiedDataType:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
}

GUIDELINE_ZUGFERD = "urn:cen.eu:en16931:2017"
GUIDELINE_XRECHNUNG = "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0"
PROZESS_XRECHNUNG = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"

# Standard-Begründungen und Codes für steuerfreie Kategorien (VATEX-Codeliste)
BEFREIUNG = {
    "AE": ("VATEX-EU-AE", "Steuerschuldnerschaft des Leistungsempfängers (§ 13b UStG)"),
    "K": ("VATEX-EU-IC", "Steuerfreie innergemeinschaftliche Lieferung/Leistung"),
    "G": ("VATEX-EU-G", "Steuerfreie Ausfuhrlieferung"),
    "E": (None, "Steuerbefreit"),
}


def _el(parent, tag, text=None, **attrs):
    prefix, name = tag.split(":")
    e = etree.SubElement(parent, f"{{{NS[prefix]}}}{name}", **attrs)
    if text is not None:
        e.text = str(text)
    return e


def _betrag(wert):
    return f"{Decimal(wert):.2f}"


def _menge(wert):
    return f"{Decimal(wert).normalize():f}"


def _datum(parent, tag, datum):
    wrapper = _el(parent, tag)
    _el(wrapper, "udt:DateTimeString", datum.strftime("%Y%m%d"), format="102")
    return wrapper


def pflichtangaben_pruefen(rechnung: Rechnung, firma: Firma, xrechnung: bool):
    """Gibt eine Liste fehlender Angaben zurück (leer = alles da)."""
    fehler = []
    if not firma:
        return ["Firmendaten fehlen (Einstellungen → Firma)."]
    if not (firma.ust_id or firma.steuernummer):
        fehler.append("Firma: Steuernummer oder USt-IdNr. fehlt.")
    if not firma.iban:
        fehler.append("Firma: IBAN fehlt (für die Zahlungsangaben).")
    for feld, name in [("kontakt_name", "Ansprechpartner"), ("telefon", "Telefon"), ("email", "E-Mail")]:
        if not getattr(firma, feld):
            fehler.append(f"Firma: {name} fehlt.")
    if not rechnung.positionen.exists():
        fehler.append("Die Rechnung hat keine Positionen.")
    kunde = rechnung.kunde
    if xrechnung and not kunde.email:
        fehler.append("Kunde: E-Mail-Adresse fehlt (Pflicht für XRechnung).")
    kategorien = {p.ust_kategorie for p in rechnung.positionen.all()}
    if kategorien & {"AE", "K"} and not kunde.ust_id:
        fehler.append("Kunde: USt-IdNr. fehlt (Pflicht bei Reverse Charge und innergemeinschaftlicher Leistung).")
    if kategorien & {"AE", "K"} and not firma.ust_id:
        fehler.append("Firma: USt-IdNr. fehlt (Pflicht bei Reverse Charge und innergemeinschaftlicher Leistung).")
    for p in rechnung.positionen.all():
        if p.ust_kategorie == "S" and p.ust_satz <= 0:
            fehler.append(f"Position „{p.bezeichnung}“: Bei Regelsteuersatz muss der Satz größer als 0 sein.")
        if p.ust_kategorie != "S" and p.ust_satz != 0:
            fehler.append(f"Position „{p.bezeichnung}“: Bei steuerfreien Leistungen muss der Satz 0 % sein.")
    return fehler


def _partei(parent, tag, name, strasse, plz, ort, land, email=None, ust_id=None, steuernummer=None, kontakt=None):
    p = _el(parent, tag)
    _el(p, "ram:Name", name)
    if kontakt:
        k = _el(p, "ram:DefinedTradeContact")
        _el(k, "ram:PersonName", kontakt["name"])
        tel = _el(k, "ram:TelephoneUniversalCommunication")
        _el(tel, "ram:CompleteNumber", kontakt["telefon"])
        mail = _el(k, "ram:EmailURIUniversalCommunication")
        _el(mail, "ram:URIID", kontakt["email"])
    a = _el(p, "ram:PostalTradeAddress")
    _el(a, "ram:PostcodeCode", plz)
    _el(a, "ram:LineOne", strasse)
    _el(a, "ram:CityName", ort)
    _el(a, "ram:CountryID", land)
    if email:
        u = _el(p, "ram:URIUniversalCommunication")
        _el(u, "ram:URIID", email, schemeID="EM")
    if ust_id:
        t = _el(p, "ram:SpecifiedTaxRegistration")
        _el(t, "ram:ID", ust_id, schemeID="VA")
    if steuernummer:
        t = _el(p, "ram:SpecifiedTaxRegistration")
        _el(t, "ram:ID", steuernummer, schemeID="FC")
    return p


def erzeuge_xml(rechnung: Rechnung, xrechnung: bool = False) -> bytes:
    firma = Firma.laden()
    kunde: Kunde = rechnung.kunde
    root = etree.Element(f"{{{NS['rsm']}}}CrossIndustryInvoice", nsmap=NS)

    # Kontext
    ctx = _el(root, "rsm:ExchangedDocumentContext")
    if xrechnung:
        bp = _el(ctx, "ram:BusinessProcessSpecifiedDocumentContextParameter")
        _el(bp, "ram:ID", PROZESS_XRECHNUNG)
    gl = _el(ctx, "ram:GuidelineSpecifiedDocumentContextParameter")
    _el(gl, "ram:ID", GUIDELINE_XRECHNUNG if xrechnung else GUIDELINE_ZUGFERD)

    # Kopf
    doc = _el(root, "rsm:ExchangedDocument")
    _el(doc, "ram:ID", rechnung.nummer)
    _el(doc, "ram:TypeCode", rechnung.art)
    _datum(doc, "ram:IssueDateTime", rechnung.datum)
    for text in [rechnung.einleitung, rechnung.schlusstext]:
        text = rechnung.text_ersetzen(text or "").strip()
        if text:
            n = _el(doc, "ram:IncludedNote")
            _el(n, "ram:Content", text)
    reg = []
    if firma.handelsregister:
        reg.append(firma.handelsregister)
    if firma.inhaber:
        reg.append(f"Inhaber/Geschäftsführung: {firma.inhaber}")
    if reg:
        n = _el(doc, "ram:IncludedNote")
        _el(n, "ram:Content", "; ".join(reg))
        _el(n, "ram:SubjectCode", "REG")

    tx = _el(root, "rsm:SupplyChainTradeTransaction")

    # Positionen
    for nr, p in enumerate(rechnung.positionen.all(), start=1):
        li = _el(tx, "ram:IncludedSupplyChainTradeLineItem")
        ld = _el(li, "ram:AssociatedDocumentLineDocument")
        _el(ld, "ram:LineID", nr)
        prod = _el(li, "ram:SpecifiedTradeProduct")
        if p.artikel_id:
            _el(prod, "ram:SellerAssignedID", p.artikel.nummer)
        _el(prod, "ram:Name", p.bezeichnung)
        if p.beschreibung:
            _el(prod, "ram:Description", p.beschreibung)
        ag = _el(li, "ram:SpecifiedLineTradeAgreement")
        np_ = _el(ag, "ram:NetPriceProductTradePrice")
        _el(np_, "ram:ChargeAmount", _betrag(p.einzelpreis))
        dl = _el(li, "ram:SpecifiedLineTradeDelivery")
        _el(dl, "ram:BilledQuantity", _menge(p.menge), unitCode=p.einheit)
        st = _el(li, "ram:SpecifiedLineTradeSettlement")
        tax = _el(st, "ram:ApplicableTradeTax")
        _el(tax, "ram:TypeCode", "VAT")
        _el(tax, "ram:CategoryCode", p.ust_kategorie)
        _el(tax, "ram:RateApplicablePercent", _betrag(p.ust_satz))
        if p.rabatt_betrag:
            ac = _el(st, "ram:SpecifiedTradeAllowanceCharge")
            ind = _el(ac, "ram:ChargeIndicator")
            _el(ind, "udt:Indicator", "false")
            _el(ac, "ram:CalculationPercent", _betrag(p.rabatt))
            _el(ac, "ram:BasisAmount", _betrag(p.netto_vor_rabatt))
            _el(ac, "ram:ActualAmount", _betrag(p.rabatt_betrag))
            _el(ac, "ram:ReasonCode", "95")
            _el(ac, "ram:Reason", "Rabatt")
        sm = _el(st, "ram:SpecifiedTradeSettlementLineMonetarySummation")
        _el(sm, "ram:LineTotalAmount", _betrag(p.netto))

    # Vereinbarung (Verkäufer, Käufer)
    agr = _el(tx, "ram:ApplicableHeaderTradeAgreement")
    _el(agr, "ram:BuyerReference", rechnung.referenz)
    _partei(
        agr,
        "ram:SellerTradeParty",
        firma.name,
        firma.strasse,
        firma.plz,
        firma.ort,
        firma.land,
        email=firma.email,
        ust_id=firma.ust_id or None,
        steuernummer=firma.steuernummer or None,
        kontakt={"name": firma.kontakt_name, "telefon": firma.telefon, "email": firma.email},
    )
    _partei(
        agr,
        "ram:BuyerTradeParty",
        kunde.anzeigename,
        kunde.strasse,
        kunde.plz,
        kunde.ort,
        kunde.land,
        email=kunde.email or None,
        ust_id=kunde.ust_id or None,
    )
    if rechnung.bestellnummer:
        bo = _el(agr, "ram:BuyerOrderReferencedDocument")
        _el(bo, "ram:IssuerAssignedID", rechnung.bestellnummer)

    # Lieferung
    dlv = _el(tx, "ram:ApplicableHeaderTradeDelivery")
    if not rechnung.leistung_bis:
        ev = _el(dlv, "ram:ActualDeliverySupplyChainEvent")
        _datum(ev, "ram:OccurrenceDateTime", rechnung.leistung_von)

    # Abrechnung
    stl = _el(tx, "ram:ApplicableHeaderTradeSettlement")
    _el(stl, "ram:PaymentReference", rechnung.nummer)
    _el(stl, "ram:InvoiceCurrencyCode", "EUR")
    pm = _el(stl, "ram:SpecifiedTradeSettlementPaymentMeans")
    _el(pm, "ram:TypeCode", "58")
    acc = _el(pm, "ram:PayeePartyCreditorFinancialAccount")
    _el(acc, "ram:IBANID", firma.iban.replace(" ", ""))
    _el(acc, "ram:AccountName", firma.name)
    if firma.bic:
        inst = _el(pm, "ram:PayeeSpecifiedCreditorFinancialInstitution")
        _el(inst, "ram:BICID", firma.bic)

    for g in rechnung.steuergruppen():
        t = _el(stl, "ram:ApplicableTradeTax")
        _el(t, "ram:CalculatedAmount", _betrag(g["steuer"]))
        _el(t, "ram:TypeCode", "VAT")
        code, standardtext = BEFREIUNG.get(g["kategorie"], (None, None))
        if g["kategorie"] != "S":
            _el(t, "ram:ExemptionReason", rechnung.befreiungsgrund or standardtext)
        _el(t, "ram:BasisAmount", _betrag(g["netto"]))
        _el(t, "ram:CategoryCode", g["kategorie"])
        if code:
            _el(t, "ram:ExemptionReasonCode", code)
        _el(t, "ram:RateApplicablePercent", _betrag(g["satz"]))

    if rechnung.leistung_bis:
        per = _el(stl, "ram:BillingSpecifiedPeriod")
        _datum(per, "ram:StartDateTime", rechnung.leistung_von)
        _datum(per, "ram:EndDateTime", rechnung.leistung_bis)

    terms = _el(stl, "ram:SpecifiedTradePaymentTerms")
    if rechnung.faellig_am:
        _el(terms, "ram:Description", f"Zahlbar bis {rechnung.faellig_am:%d.%m.%Y} ohne Abzug")
        _datum(terms, "ram:DueDateDateTime", rechnung.faellig_am)
    else:
        _el(terms, "ram:Description", "Zahlbar sofort ohne Abzug")

    netto = rechnung.summe_netto
    steuer = rechnung.summe_steuer
    brutto = rechnung.summe_brutto
    sm = _el(stl, "ram:SpecifiedTradeSettlementHeaderMonetarySummation")
    _el(sm, "ram:LineTotalAmount", _betrag(netto))
    _el(sm, "ram:TaxBasisTotalAmount", _betrag(netto))
    _el(sm, "ram:TaxTotalAmount", _betrag(steuer), currencyID="EUR")
    _el(sm, "ram:GrandTotalAmount", _betrag(brutto))
    _el(sm, "ram:DuePayableAmount", _betrag(brutto))

    if rechnung.bezug_id:
        ref = _el(stl, "ram:InvoiceReferencedDocument")
        _el(ref, "ram:IssuerAssignedID", rechnung.bezug.nummer)
        fd = _el(ref, "ram:FormattedIssueDateTime")
        _el(fd, f"qdt:DateTimeString", rechnung.bezug.datum.strftime("%Y%m%d"), format="102")

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def schema_pruefen(xml: bytes):
    """Prüft das XML gegen das offizielle XSD (Factur-X/ZUGFeRD EN 16931). Gibt Fehlerliste zurück."""
    import os

    import drafthorse

    pfad = os.path.join(os.path.dirname(drafthorse.__file__), "schema", "FACTUR-X_EN16931.xsd")
    schema = etree.XMLSchema(etree.parse(pfad))
    dok = etree.fromstring(xml)
    if schema.validate(dok):
        return []
    return [str(e) for e in schema.error_log]
