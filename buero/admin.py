from django.contrib import admin

from . import models

for m in [models.Firma, models.Nummernkreis, models.Kunde, models.Artikel, models.Textbaustein, models.Mahnstufe,
          models.Brief, models.Eingangsrechnung]:
    admin.site.register(m)


class PositionInline(admin.TabularInline):
    model = models.Position
    extra = 0


@admin.register(models.Rechnung)
class RechnungAdmin(admin.ModelAdmin):
    list_display = ["nummer", "datum", "kunde", "status"]
    list_filter = ["status", "art"]
    inlines = [PositionInline]

    def has_change_permission(self, request, obj=None):
        # Festgeschriebene Rechnungen sind auch in der Verwaltung schreibgeschützt (GoBD)
        return obj is None or obj.ist_entwurf

    def has_delete_permission(self, request, obj=None):
        return obj is None or obj.ist_entwurf
