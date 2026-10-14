"""Liest empfangene E-Rechnungen ein: XRechnung (UBL oder CII) und ZUGFeRD/Factur-X (PDF mit eingebettetem XML)."""

import io
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from lxml import etree

CII = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
}
UBL = {
    "inv": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cn": "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}
XML_NAMEN = ("factur-x.xml", "zugferd-invoice.xml", "xrechnung.xml", "zugferd_invoice.xml")


class EinleseFehler(Exception):
    pass


@dataclass
class Ergebnis:
    format: str
    xml: str
    nummer: str = ""
    datum: date | None = None
    faellig_am: date | None = None
    lieferant: str = ""
    lieferant_ust_id: str = ""
    waehrung: str = "EUR"
    netto: Decimal | None = None
    steuer: Decimal | None = None
    brutto: Decimal | None = None
    iban: str = ""
    profil: str = ""
    positionen: list = field(default_factory=list)
    lieferant_adresse: str = ""
    kaeufer: str = ""


def xml_aus_pdf(daten: bytes) -> bytes:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(daten))
        anhaenge = reader.attachments
    except Exception as e:  # beschädigtes PDF
        raise EinleseFehler(f"Das PDF konnte nicht gelesen werden ({e}).")
    for name, inhalte in anhaenge.items():
        if name.lower() in XML_NAMEN or name.lower().endswith(".xml"):
            return inhalte[0]
    raise EinleseFehler("Das PDF enthält keine eingebettete E-Rechnung (kein ZUGFeRD/Factur-X).")


def _dez(text):
    if text is None:
        return None
    try:
        return Decimal(text.strip())
    except InvalidOperation:
        return None


def _cii_datum(text):
    if not text:
        return None
    try:
        return datetime.strptime(text.strip()[:8], "%Y%m%d").date()
    except ValueError:
        return None


def _iso_datum(text):
    if not text:
        return None
    try:
        return date.fromisoformat(text.strip()[:10])
    except ValueError:
        return None


def _text(baum, pfad, ns):
    r = baum.xpath(pfad, namespaces=ns)
    if not r:
        return ""
    wert = r[0]
    return (wert if isinstance(wert, str) else (wert.text or "")).strip()


def _lese_cii(baum, xml) -> Ergebnis:
    t = lambda p: _text(baum, p, CII)  # noqa: E731
    profil = t("//rsm:ExchangedDocumentContext/ram:GuidelineSpecifiedDocumentContextParameter/ram:ID")
    fmt = "XRechnung (CII)" if "xrechnung" in profil else "ZUGFeRD/Factur-X (CII)"
    sm = "//ram:ApplicableHeaderTradeSettlement/ram:SpecifiedTradeSettlementHeaderMonetarySummation/"
    seller = "//ram:ApplicableHeaderTradeAgreement/ram:SellerTradeParty/"
    adr = [t(seller + "ram:PostalTradeAddress/ram:LineOne"),
           " ".join(filter(None, [t(seller + "ram:PostalTradeAddress/ram:PostcodeCode"),
                                  t(seller + "ram:PostalTradeAddress/ram:CityName")]))]
    e = Ergebnis(
        format=fmt,
        xml=xml,
        profil=profil,
        nummer=t("//rsm:ExchangedDocument/ram:ID"),
        datum=_cii_datum(t("//rsm:ExchangedDocument/ram:IssueDateTime/udt:DateTimeString")),
        faellig_am=_cii_datum(t("//ram:SpecifiedTradePaymentTerms/ram:DueDateDateTime/udt:DateTimeString")),
        lieferant=t(seller + "ram:Name"),
        lieferant_ust_id=t(seller + "ram:SpecifiedTaxRegistration/ram:ID[@schemeID='VA']"),
        lieferant_adresse=", ".join(a for a in adr if a),
        kaeufer=t("//ram:ApplicableHeaderTradeAgreement/ram:BuyerTradeParty/ram:Name"),
        waehrung=t("//ram:InvoiceCurrencyCode") or "EUR",
        netto=_dez(t(sm + "ram:TaxBasisTotalAmount")),
        steuer=_dez(t(sm + "ram:TaxTotalAmount")),
        brutto=_dez(t(sm + "ram:GrandTotalAmount")),
        iban=t("//ram:PayeePartyCreditorFinancialAccount/ram:IBANID"),
    )
    for li in baum.xpath("//ram:IncludedSupplyChainTradeLineItem", namespaces=CII):
        lt = lambda p: _text(li, p, CII)  # noqa: E731
        e.positionen.append(
            {
                "bezeichnung": lt("ram:SpecifiedTradeProduct/ram:Name"),
                "menge": lt("ram:SpecifiedLineTradeDelivery/ram:BilledQuantity"),
                "einheit": _text(li, "ram:SpecifiedLineTradeDelivery/ram:BilledQuantity/@unitCode", CII),
                "einzelpreis": lt("ram:SpecifiedLineTradeAgreement/ram:NetPriceProductTradePrice/ram:ChargeAmount"),
                "ust_satz": lt("ram:SpecifiedLineTradeSettlement/ram:ApplicableTradeTax/ram:RateApplicablePercent"),
                "netto": lt(
                    "ram:SpecifiedLineTradeSettlement/ram:SpecifiedTradeSettlementLineMonetarySummation/ram:LineTotalAmount"
                ),
            }
        )
    return e


def _lese_ubl(baum, xml) -> Ergebnis:
    t = lambda p: _text(baum, p, UBL)  # noqa: E731
    gutschrift = baum.tag.endswith("CreditNote")
    profil = t("/*/cbc:CustomizationID")
    seller = "/*/cac:AccountingSupplierParty/cac:Party/"
    adr = [t(seller + "cac:PostalAddress/cbc:StreetName"),
           " ".join(filter(None, [t(seller + "cac:PostalAddress/cbc:PostalZone"),
                                  t(seller + "cac:PostalAddress/cbc:CityName")]))]
    e = Ergebnis(
        format=("XRechnung (UBL)" if "xrechnung" in profil else "UBL") + (" Gutschrift" if gutschrift else ""),
        xml=xml,
        profil=profil,
        nummer=t("/*/cbc:ID"),
        datum=_iso_datum(t("/*/cbc:IssueDate")),
        faellig_am=_iso_datum(t("/*/cbc:DueDate") or t("/*/cac:PaymentMeans/cbc:PaymentDueDate")),
        lieferant=t(seller + "cac:PartyLegalEntity/cbc:RegistrationName") or t(seller + "cac:PartyName/cbc:Name"),
        lieferant_ust_id=t(seller + "cac:PartyTaxScheme/cbc:CompanyID"),
        lieferant_adresse=", ".join(a for a in adr if a),
        kaeufer=t("/*/cac:AccountingCustomerParty/cac:Party/cac:PartyLegalEntity/cbc:RegistrationName"),
        waehrung=t("/*/cbc:DocumentCurrencyCode") or "EUR",
        netto=_dez(t("/*/cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount")),
        steuer=_dez(t("/*/cac:TaxTotal/cbc:TaxAmount")),
        brutto=_dez(t("/*/cac:LegalMonetaryTotal/cbc:TaxInclusiveAmount")),
        iban=t("/*/cac:PaymentMeans/cac:PayeeFinancialAccount/cbc:ID"),
    )
    for li in baum.xpath("/*/cac:InvoiceLine | /*/cac:CreditNoteLine", namespaces=UBL):
        lt = lambda p: _text(li, p, UBL)  # noqa: E731
        mengen = li.xpath("cbc:InvoicedQuantity | cbc:CreditedQuantity", namespaces=UBL)
        e.positionen.append(
            {
                "bezeichnung": lt("cac:Item/cbc:Name"),
                "menge": (mengen[0].text or "").strip() if mengen else "",
                "einheit": mengen[0].get("unitCode", "") if mengen else "",
                "einzelpreis": lt("cac:Price/cbc:PriceAmount"),
                "ust_satz": lt("cac:Item/cac:ClassifiedTaxCategory/cbc:Percent"),
                "netto": lt("cbc:LineExtensionAmount"),
            }
        )
    return e


def einlesen(daten: bytes, dateiname: str = "") -> Ergebnis:
    if daten[:5] == b"%PDF-":
        xml = xml_aus_pdf(daten)
    else:
        xml = daten
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        baum = etree.fromstring(xml, parser=parser)
    except etree.XMLSyntaxError as e:
        raise EinleseFehler(f"Die Datei ist kein gültiges XML ({e}).")
    text = xml.decode("utf-8", errors="replace")
    if baum.tag == f"{{{CII['rsm']}}}CrossIndustryInvoice":
        return _lese_cii(baum, text)
    if baum.tag in (f"{{{UBL['inv']}}}Invoice", f"{{{UBL['cn']}}}CreditNote"):
        return _lese_ubl(baum.getroottree().getroot(), text)
    raise EinleseFehler("Unbekanntes Format. Unterstützt werden XRechnung (UBL/CII) und ZUGFeRD/Factur-X.")
