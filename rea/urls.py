from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

admin.site.site_header = "REA Verwaltung"

urlpatterns = [
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("passwort/", auth_views.PasswordChangeView.as_view(success_url="/einstellungen/"), name="password_change"),
    path("admin/", admin.site.urls),
    path("api/", include("buero.api_urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/doku/", SpectacularSwaggerView.as_view(url_name="schema"), name="api_doku"),
    path("", include("buero.urls")),
]
