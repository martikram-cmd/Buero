from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, parsers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Artikel, Eingangsrechnung, Kunde, Rechnung
from .serializers import (
    ArtikelSerializer, EingangsrechnungSerializer, KundeSerializer, RechnungSerializer, ZahlungSerializer,
)
from .services import dokumente, einlesen


def _fehler(e):
    return Response({"fehler": e.messages if isinstance(e, ValidationError) else [str(e)]}, status=status.HTTP_400_BAD_REQUEST)


class KundeViewSet(viewsets.ModelViewSet):
    queryset = Kunde.objects.all()
    serializer_class = KundeSerializer
    search_fields = ["nummer", "firma", "nachname"]

    def destroy(self, request, *args, **kwargs):
        if self.get_object().rechnungen.exists():
            return Response({"fehler": ["Kunden mit Rechnungen können nicht gelöscht werden."]}, status=400)
        return super().destroy(request, *args, **kwargs)


class ArtikelViewSet(viewsets.ModelViewSet):
    queryset = Artikel.objects.all()
    serializer_class = ArtikelSerializer


class RechnungViewSet(viewsets.ModelViewSet):
    queryset = Rechnung.objects.select_related("kunde").prefetch_related("positionen", "zahlungen")
    serializer_class = RechnungSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        if s := self.request.query_params.get("status"):
            qs = qs.filter(status=s)
        if k := self.request.query_params.get("kunde"):
            qs = qs.filter(kunde_id=k)
        return qs

    def destroy(self, request, *args, **kwargs):
        if not self.get_object().ist_entwurf:
            return Response({"fehler": ["Nur Entwürfe können gelöscht werden."]}, status=400)
        return super().destroy(request, *args, **kwargs)

    @extend_schema(request=None, responses=RechnungSerializer)
    @action(detail=True, methods=["post"])
    def abschliessen(self, request, pk=None):
        try:
            r = dokumente.abschliessen(self.get_object())
        except ValidationError as e:
            return _fehler(e)
        return Response(RechnungSerializer(r).data)

    @extend_schema(request=None, responses=RechnungSerializer)
    @action(detail=True, methods=["post"])
    def stornieren(self, request, pk=None):
        try:
            storno = dokumente.stornieren(self.get_object())
        except ValidationError as e:
            return _fehler(e)
        return Response(RechnungSerializer(storno).data, status=201)

    @extend_schema(request=ZahlungSerializer, responses=RechnungSerializer)
    @action(detail=True, methods=["post"])
    def zahlungen(self, request, pk=None):
        r = self.get_object()
        if r.status not in (Rechnung.STATUS_OFFEN, Rechnung.STATUS_BEZAHLT):
            return Response({"fehler": ["Zahlungen nur zu offenen Rechnungen."]}, status=400)
        s = ZahlungSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        s.save(rechnung=r)
        r = Rechnung.objects.get(pk=r.pk)
        dokumente.zahlung_pruefen(r)
        return Response(RechnungSerializer(r).data, status=201)

    @extend_schema(responses={(200, "application/pdf"): bytes})
    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        r = self.get_object()
        if not r.pdf:
            raise Http404
        return FileResponse(r.pdf.open("rb"), content_type="application/pdf")

    @extend_schema(responses={(200, "application/xml"): bytes})
    @action(detail=True, methods=["get"])
    def xml(self, request, pk=None):
        r = self.get_object()
        if not r.xml:
            raise Http404
        return FileResponse(r.xml.open("rb"), content_type="application/xml")


class EingangsrechnungViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    queryset = Eingangsrechnung.objects.all()
    serializer_class = EingangsrechnungSerializer
    parser_classes = [parsers.JSONParser, parsers.MultiPartParser]

    @extend_schema(
        request={"multipart/form-data": {"type": "object", "properties": {"datei": {"type": "string", "format": "binary"}}}},
        responses=EingangsrechnungSerializer,
    )
    @action(detail=False, methods=["post"])
    def hochladen(self, request):
        from .views import eingangsrechnung_speichern

        datei = request.FILES.get("datei")
        if not datei:
            return Response({"fehler": ["Feld 'datei' fehlt."]}, status=400)
        try:
            e = eingangsrechnung_speichern(datei.read(), datei.name)
        except (ValidationError, einlesen.EinleseFehler) as f:
            return _fehler(f)
        return Response(EingangsrechnungSerializer(e).data, status=201)
