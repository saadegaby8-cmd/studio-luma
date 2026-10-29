"""MIS LUGARES: escenarios reales a partir de coordenadas.

El generador de imágenes no busca en un mapa: "-34.9089, -55.0449" le llega como dos números y, como
mucho, inventa "una playa". Para que la foto salga EN ese lugar, la app lo traduce antes:

1. Qué lugar es: nombre y dirección por OpenStreetMap (Nominatim, gratis).
2. Cómo se ve: fotos reales de libre uso sacadas a unos cientos de metros (Wikimedia Commons, gratis),
   o las fotos que sube ella. Viajan al generador rotuladas "FOTO REAL DEL LUGAR".
3. Claude mira esas fotos y escribe cómo es el lugar y varios RINCONES distintos (así las tomas de un
   set no salen todas con el mismo fondo).
4. El SOL de verdad: con las coordenadas y la fecha se calcula dónde está el sol en el momento pedido
   (amanecer, golden hour, atardecer…), sin ninguna API.
"""
import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

UA = {"User-Agent": "StudioLuma/1.0 (fotos de producto; contacto en la app)"}

MOMENTOS = {
    "": ("Sin calcular", None),
    "amanecer": ("🌅 Amanecer", ("sube", 2.0)),
    "manana": ("☀️ Mañana", ("sube", 25.0)),
    "mediodia": ("🌞 Mediodía", ("max", None)),
    "tarde": ("🌤 Tarde", ("baja", 25.0)),
    "golden": ("✨ Golden hour", ("baja", 7.0)),
    "atardecer": ("🌇 Atardecer (sol en el horizonte)", ("baja", 1.5)),
}


# ── Coordenadas ──
def parse_coords(txt: str) -> Optional[Tuple[float, float]]:
    """Acepta "-34.908869, -55.044906", grados-minutos-segundos ("34°54′32″ S 55°02′42″ O") o un
    link de Google Maps (…/@-34.90,-55.04,17z…)."""
    t = str(txt or "").strip()
    if not t:
        return None
    m = re.search(r"@(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)", t) or re.search(
        r"[?&](?:q|ll|query)=(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)", t)
    if m:
        return _validas(float(m.group(1)), float(m.group(2)))
    dms = re.findall(r"(\d+(?:[.,]\d+)?)\s*[°º]\s*(?:(\d+(?:[.,]\d+)?)\s*['′’])?\s*(?:(\d+(?:[.,]\d+)?)\s*(?:\"|″|”|''|′′))?\s*([NSEOW])",
                     t, re.I)
    if len(dms) >= 2:
        vals = []
        for g, mi, se, h in dms[:2]:
            v = float(g.replace(",", ".")) + float((mi or "0").replace(",", ".")) / 60 + float((se or "0").replace(",", ".")) / 3600
            vals.append((-v if h.upper() in ("S", "O", "W") else v, h.upper()))
        lat = next((v for v, h in vals if h in ("N", "S")), None)
        lon = next((v for v, h in vals if h in ("E", "O", "W")), None)
        if lat is not None and lon is not None:
            return _validas(lat, lon)
    nums = re.findall(r"-?\d+(?:\.\d+)?", t.replace(",", " "))
    if len(nums) >= 2:
        return _validas(float(nums[0]), float(nums[1]))
    return None


def _validas(lat: float, lon: float) -> Optional[Tuple[float, float]]:
    return (round(lat, 6), round(lon, 6)) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


# ── El sol ──
def sol(lat: float, lon: float, t_utc: datetime) -> Tuple[float, float]:
    """(elevación, azimut) del sol en grados; azimut desde el norte en sentido horario (algoritmo NOAA)."""
    doy = t_utc.timetuple().tm_yday
    hora = t_utc.hour + t_utc.minute / 60 + t_utc.second / 3600
    g = 2 * math.pi / 365 * (doy - 1 + (hora - 12) / 24)
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                    - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    tst = hora * 60 + eqt + 4 * lon
    ha = math.radians(tst / 4 - 180)
    la = math.radians(lat)
    cz = math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)
    cz = max(-1.0, min(1.0, cz))
    elev = 90 - math.degrees(math.acos(cz))
    az = (math.degrees(math.atan2(math.sin(ha), math.cos(ha) * math.sin(la) - math.tan(decl) * math.cos(la))) + 180) % 360
    return elev, az


def momento_utc(lat: float, lon: float, dia: date, momento: str) -> Optional[datetime]:
    """La hora (UTC) del momento pedido en ese día y lugar."""
    spec = (MOMENTOS.get(momento) or ("", None))[1]
    if not spec:
        return None
    # el día "local" según la longitud (sin depender del huso horario)
    t0 = datetime(dia.year, dia.month, dia.day, tzinfo=timezone.utc) - timedelta(hours=lon / 15)
    puntos = [(t0 + timedelta(minutes=m), sol(lat, lon, t0 + timedelta(minutes=m))[0]) for m in range(0, 1441, 3)]
    tipo, objetivo = spec
    if tipo == "max":
        return max(puntos, key=lambda p: p[1])[0]
    imax = max(range(len(puntos)), key=lambda i: puntos[i][1])
    tramo = puntos[:imax + 1] if tipo == "sube" else puntos[imax:]
    if puntos[imax][1] < objetivo:            # el sol no llega tan alto ese día (invierno, latitudes altas)
        return puntos[imax][0]
    for (ta, ea), (tb, eb) in zip(tramo, tramo[1:]):
        if (ea - objetivo) * (eb - objetivo) <= 0:
            return ta
    return None


_RUMBOS = ["norte", "nor-noreste", "noreste", "este-noreste", "este", "este-sudeste", "sudeste", "sur-sudeste",
           "sur", "sur-sudoeste", "sudoeste", "oeste-sudoeste", "oeste", "oeste-noroeste", "noroeste", "nor-noroeste"]


def rumbo(az: float) -> str:
    return _RUMBOS[int((az % 360) / 22.5 + 0.5) % 16]


def texto_sol(lat: float, lon: float, dia: date, momento: str) -> str:
    t = momento_utc(lat, lon, dia, momento)
    if not t:
        return ""
    e, az = sol(lat, lon, t)
    if e < 0:
        return ""
    if e < 3:
        luz = "luz naranja y rojiza, rasante, sombras larguísimas, el cielo encendido cerca del sol"
    elif e < 12:
        luz = "golden hour: luz dorada y cálida, baja, sombras largas y suaves, brillo dorado en el pelo y la piel"
    elif e < 35:
        luz = "luz cálida y clara, sombras medianas"
    else:
        luz = "sol alto de día: luz blanca y dura, sombras cortas debajo de los objetos"
    return (f"SOL REAL DE ESE LUGAR ({MOMENTOS[momento][0].split(' ', 1)[-1].lower()}, {dia.strftime('%d/%m')}): "
            f"a {round(e)}° sobre el horizonte, hacia el {rumbo(az)} (azimut {round(az)}°); {luz}. Las sombras caen "
            f"hacia el {rumbo(az + 180)}. Que la luz y las sombras de la foto sean éstas.")


# ── Qué lugar es y fotos reales ──
async def que_lugar(lat: float, lon: float) -> Dict[str, str]:
    try:
        async with httpx.AsyncClient(timeout=15, headers=UA) as c:
            r = await c.get("https://nominatim.openstreetmap.org/reverse",
                            params={"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 16, "accept-language": "es"})
            j = r.json() if r.status_code == 200 else {}
    except Exception:
        j = {}
    a = j.get("address") or {}
    nombre = (j.get("name") or a.get("tourism") or a.get("leisure") or a.get("natural") or a.get("suburb")
              or a.get("village") or a.get("town") or a.get("city") or "")
    zona = ", ".join(x for x in (a.get("village") or a.get("town") or a.get("city") or "", a.get("state") or "",
                                 a.get("country") or "") if x and x != nombre)
    return {"nombre": str(nombre)[:80], "zona": zona[:120], "direccion": str(j.get("display_name") or "")[:240]}


def _limpio(html: str) -> str:
    return re.sub(r"<[^>]+>", "", str(html or "")).strip()[:80]


async def fotos_cerca(lat: float, lon: float, radio: int = 800, n: int = 24) -> List[Dict[str, str]]:
    """Fotos de libre uso de Wikimedia Commons sacadas cerca de esas coordenadas."""
    params = {"action": "query", "generator": "geosearch", "ggscoord": f"{lat}|{lon}", "ggsradius": int(radio),
              "ggslimit": int(n), "ggsnamespace": 6, "prop": "imageinfo|coordinates",
              "iiprop": "url|extmetadata|mime", "iiurlwidth": 640, "format": "json"}
    try:
        async with httpx.AsyncClient(timeout=20, headers=UA) as c:
            r = await c.get("https://commons.wikimedia.org/w/api.php", params=params)
            j = r.json() if r.status_code == 200 else {}
    except Exception:
        return []
    out = []
    for p in ((j.get("query") or {}).get("pages") or {}).values():
        ii = (p.get("imageinfo") or [{}])[0]
        if not str(ii.get("mime", "")).startswith("image/jpeg") or not ii.get("thumburl"):
            continue
        meta = ii.get("extmetadata") or {}
        co = (p.get("coordinates") or [{}])[0]
        dist = None
        if co.get("lat") is not None:
            dist = round(math.hypot((co["lat"] - lat) * 111000, (co["lon"] - lon) * 111000 * math.cos(math.radians(lat))))
        out.append({"thumb": ii["thumburl"],
                    "grande": ii["thumburl"].replace("/640px-", "/1280px-") if "/640px-" in ii["thumburl"] else ii["thumburl"],
                    "titulo": str(p.get("title", "")).replace("File:", "")[:90],
                    "licencia": _limpio((meta.get("LicenseShortName") or {}).get("value")),
                    "autor": _limpio((meta.get("Artist") or {}).get("value")), "distancia": dist})
    out.sort(key=lambda x: x["distancia"] if x["distancia"] is not None else 99999)
    return out


async def bajar(url: str) -> Optional[bytes]:
    if not re.match(r"^https://upload\.wikimedia\.org/", str(url or "")):
        return None                         # sólo de Commons: no se baja cualquier URL que llegue
    try:
        async with httpx.AsyncClient(timeout=30, headers=UA, follow_redirects=True) as c:
            r = await c.get(url)
            return r.content if r.status_code == 200 and len(r.content) < 15_000_000 else None
    except Exception:
        return None


SISTEMA_SCOUT = (
    "You are a location scout for fashion and swimwear photo shoots. You receive real photos of ONE place "
    "(name and coordinates given). Describe the PLACE only (never people). Answer in Spanish (Rioplatense), "
    "as JSON: {\"nombre\": short place name, \"descripcion\": 2-4 sentences of what the place looks like for an "
    "image generator — architecture/materials/colors, ground, rocks, vegetation, water and horizon, the overall "
    "mood; concrete and visual, no history, no adjectives like 'famous'; \"rincones\": 4 to 6 DIFFERENT spots "
    "or viewpoints of this same place where a model could be photographed so each photo has a different "
    "background (one short sentence each: what is behind her and what she stands/sits/leans on); "
    "\"mar\": where the sea/water is relative to the place if visible (e.g. 'al oeste, enfrente de las "
    "terrazas'), else \"\"}."
)
