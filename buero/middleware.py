from urllib.parse import quote

from django.shortcuts import redirect

OFFEN = ("/login/", "/static/", "/api/")


class LoginRequiredMiddleware:
    """Alle Seiten nur nach Anmeldung. Die API prüft selbst (Token oder Sitzung)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated and not request.path.startswith(OFFEN):
            return redirect(f"/login/?next={quote(request.get_full_path())}")
        return self.get_response(request)
