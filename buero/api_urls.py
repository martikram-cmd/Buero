from rest_framework.routers import DefaultRouter

from . import api

router = DefaultRouter()
router.register("kunden", api.KundeViewSet)
router.register("artikel", api.ArtikelViewSet)
router.register("rechnungen", api.RechnungViewSet)
router.register("eingangsrechnungen", api.EingangsrechnungViewSet)

urlpatterns = router.urls
