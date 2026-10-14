from decimal import Decimal

from django.db import migrations

STUFEN = [
    (1, "Zahlungserinnerung", 7, Decimal("0.00"), 7,
     "sicher ist es Ihrer Aufmerksamkeit entgangen: Unsere Rechnung {nummer} vom {datum} über {betrag} war am {faellig_am} fällig.\n\n"
     "Bitte überweisen Sie den offenen Betrag bis zum {neue_frist}. Sollten Sie die Zahlung bereits veranlasst haben, betrachten Sie dieses Schreiben bitte als gegenstandslos."),
    (2, "1. Mahnung", 21, Decimal("5.00"), 7,
     "leider konnten wir bis heute keinen Zahlungseingang zu unserer Rechnung {nummer} vom {datum} feststellen.\n\n"
     "Bitte überweisen Sie den offenen Betrag zuzüglich Mahngebühr bis spätestens {neue_frist}."),
    (3, "2. Mahnung", 35, Decimal("10.00"), 7,
     "trotz unserer Erinnerung ist die Rechnung {nummer} vom {datum} weiterhin offen.\n\n"
     "Wir fordern Sie hiermit nochmals auf, den Gesamtbetrag bis zum {neue_frist} zu begleichen."),
    (4, "Letzte Mahnung", 49, Decimal("15.00"), 5,
     "die Rechnung {nummer} vom {datum} ist trotz mehrfacher Mahnung nicht bezahlt.\n\n"
     "Sollte der Gesamtbetrag nicht bis zum {neue_frist} bei uns eingehen, übergeben wir die Forderung ohne weitere Ankündigung zur Beitreibung."),
]


def anlegen(apps, schema_editor):
    Mahnstufe = apps.get_model("buero", "Mahnstufe")
    for stufe, name, tage, gebuehr, frist, text in STUFEN:
        Mahnstufe.objects.get_or_create(
            stufe=stufe,
            defaults={"bezeichnung": name, "tage_nach_faelligkeit": tage, "gebuehr": gebuehr, "frist_tage": frist,
                      "text": "Sehr geehrte Damen und Herren,\n\n" + text + "\n\nMit freundlichen Grüßen"},
        )


class Migration(migrations.Migration):
    dependencies = [("buero", "0001_start")]
    operations = [migrations.RunPython(anlegen, migrations.RunPython.noop)]
