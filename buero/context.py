from .models import Firma


def firma(request):
    return {"firma": Firma.laden()}
