from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from .models import Artikel, Eingangsrechnung, Kunde, Position, Rechnung, Zahlung


class KundeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Kunde
        exclude = ["angelegt"]
        read_only_fields = ["nummer"]

    def validate(self, daten):
        werte = {f.name: getattr(self.instance, f.name) for f in Kunde._meta.fields} if self.instance else {}
        try:
            Kunde(**{**werte, **daten}).clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.message_dict)
        return daten


class ArtikelSerializer(serializers.ModelSerializer):
    class Meta:
        model = Artikel
        fields = "__all__"


class PositionSerializer(serializers.ModelSerializer):
    netto = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Position
        exclude = ["rechnung", "reihenfolge"]


class ZahlungSerializer(serializers.ModelSerializer):
    class Meta:
        model = Zahlung
        exclude = ["rechnung"]


class RechnungSerializer(serializers.ModelSerializer):
    positionen = PositionSerializer(many=True)
    zahlungen = ZahlungSerializer(many=True, read_only=True)
    summe_netto = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    summe_steuer = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    summe_brutto = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    offen = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Rechnung
        fields = [
            "id", "nummer", "art", "status", "kunde", "datum", "leistung_von", "leistung_bis", "faellig_am",
            "kaeufer_referenz", "bestellnummer", "betreff", "einleitung", "schlusstext", "befreiungsgrund", "bezug",
            "format", "festgeschrieben_am", "versendet_am", "positionen", "zahlungen", "summe_netto", "summe_steuer",
            "summe_brutto", "offen",
        ]
        read_only_fields = ["nummer", "art", "status", "bezug", "format", "festgeschrieben_am", "versendet_am"]

    def validate(self, daten):
        if self.instance and not self.instance.ist_entwurf:
            raise serializers.ValidationError("Abgeschlossene Rechnungen können nicht geändert werden.")
        return daten

    def _positionen_speichern(self, rechnung, positionen):
        rechnung.positionen.all().delete()
        for i, p in enumerate(positionen):
            Position.objects.create(rechnung=rechnung, reihenfolge=i, **p)

    @transaction.atomic
    def create(self, daten):
        positionen = daten.pop("positionen")
        rechnung = Rechnung.objects.create(**daten)
        self._positionen_speichern(rechnung, positionen)
        return rechnung

    @transaction.atomic
    def update(self, rechnung, daten):
        positionen = daten.pop("positionen", None)
        for k, v in daten.items():
            setattr(rechnung, k, v)
        rechnung.save()
        if positionen is not None:
            self._positionen_speichern(rechnung, positionen)
        return rechnung


class EingangsrechnungSerializer(serializers.ModelSerializer):
    class Meta:
        model = Eingangsrechnung
        exclude = ["xml"]
        read_only_fields = [f.name for f in Eingangsrechnung._meta.fields if f.name not in ("bezahlt", "notiz")]
