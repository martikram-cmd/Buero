from decimal import Decimal

from django import template

register = template.Library()


def _de(wert, stellen=2):
    if wert is None or wert == "":
        return ""
    text = f"{Decimal(wert):,.{stellen}f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


@register.filter
def euro(wert):
    return f"{_de(wert)} €" if wert not in (None, "") else ""


@register.filter
def zahl(wert, stellen=2):
    return _de(wert, int(stellen))


@register.filter
def menge(wert):
    d = Decimal(wert).normalize()
    stellen = max(0, -d.as_tuple().exponent)
    return _de(d, stellen)


@register.filter
def iban(wert):
    w = (wert or "").replace(" ", "")
    return " ".join(w[i : i + 4] for i in range(0, len(w), 4))


@register.filter
def befreiung(text):
    from ..services.erechnung import BEFREIUNG

    if text in BEFREIUNG:
        return BEFREIUNG[text][1]
    return text


@register.filter
def status_klasse(status):
    return {"entwurf": "grau", "offen": "gelb", "bezahlt": "gruen", "storniert": "rot"}.get(status, "grau")
