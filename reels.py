# -*- coding: utf-8 -*-
"""
reels.py — Reels de Instagram con un Personaje (pestaña 🎞️ Reels)
==================================================================

Módulo STANDALONE de Studio Luma (mismo molde que personajes.py).

Qué hace
--------
Arma un reel vertical (1080x1920) donde el Personaje —la influencer de la
marca— habla a cámara desde el local, y entre medio aparecen tomas de la
prenda SIN gente mientras ella sigue hablando. La clave es separar la voz de
la imagen:

  1. GUION. Gemini escribe el guion en tramos alternados (ella / producto) con
     la ficha del Personaje y los datos de la prenda (de un link de Tiendanube
     o Mercado Libre, o escritos a mano). La usuaria lo corrige.
  2. VOZ. Cada tramo va al TTS con la voz del Personaje: así se sabe la
     duración exacta de cada tramo antes de gastar en video.
  3. ESCENAS. Para cada tramo de ella, una foto vertical de ella en el local
     con la prenda apoyada al lado (Nano Banana, con su hoja de identidad).
  4. VIDEO. Las escenas van a OmniHuman 1.5 (fal) con su tramo de audio: ella
     habla con labios, cara y manos sincronizados. Las tomas de producto son
     flashes de las fotos de la prenda con zoom lento (ffmpeg, costo cero).
  5. ARMADO. ffmpeg pega los tramos, subtítulos quemados y salida 1080x1920.

Cómo se engancha (main.py):

    from reels import router as reels_router
    app.include_router(reels_router)

UI: GET /reels?pid=<personaje>

Variables de entorno
--------------------
  REELS_PREFIX     (opcional) -> default "/reels"
  REELS_OMNI_MODEL (opcional) -> default fal-ai/bytedance/omnihuman/v1.5
"""

import asyncio
import base64
import io
import datetime as _dt
import html as _html
import json
import math
import os
import re
import subprocess
import time
import uuid as _uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response

from imagenes_ia import (
    CURRENT_SUB,
    _LENC_KW,
    _compress_ref,
    _img_part,
    _pfx,
    _pricing,
    _strip_data_url,
    budget_record,
    gemini_generate,
    get_settings,
    kv,
    set_current_sub,
)
from personajes import (
    API as PJ_API,
    COSTO_TTS,
    PJ_DIR,
    ROUTE_PREFIX as PJ_PREFIX,
    VOCES,
    _bind,
    _bloque_identidad_pj,
    _cobrar,
    _doc,
    _fal_enviar,
    _fal_key,
    _fal_subir,
    _ficha_texto,
    _g,
    _gemini_json,
    _guardar_en_drive,
    _job_nuevo,
    _job_set,
    _k_job,
    _refs_identidad,
    _slug,
    _tts_mp3,
)
from videos_luma import FAL_MODELS, PRECIO_SEG, RESOLUCION_FAL, _duracion_video, _ffmpeg_bin, _spawn

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

ROUTE_PREFIX = os.environ.get("REELS_PREFIX", "/reels").rstrip("/")
API = ROUTE_PREFIX + "/api"
VERSION = "2.5.0"   # subí este número cada vez que cambiamos el archivo

OMNI_MODEL = os.getenv("REELS_OMNI_MODEL", "fal-ai/bytedance/omnihuman/v1.5")
PRECIO_OMNI_SEG = 0.16          # US$ por segundo de video hablado (fal, OmniHuman 1.5)
OMNI_TIMEOUT = 25 * 60          # por tramo
OMNI_MAX_SEG = 28               # audio por tramo (1080p admite 30 s; 720p, 60 s)
RESOLUCIONES = ("720p", "1080p")
DURACIONES = (25, 35, 45)
TONOS = ("chetita", "canchera", "cercana", "divertida", "seria")
# Cómo lee cada tono (la consigna que va delante del texto en la voz de Gemini).
# Nada de locutora: es una chica joven grabando con el celular.
ESTILOS_VOZ = {
    "chetita": "chetita de Palermo: relajada y con onda, las vocales un poco alargadas, la "
               "entonación sube al final de las frases, media nasal, como hablándoles a sus "
               "seguidores en una story mientras hace otra cosa",
    "canchera": "canchera y con onda, rápida y con energía, como si les contara algo copado "
                "a sus amigas, con alguna risita chiquita si pega",
    "cercana": "cercana y natural, tranquila pero con onda, como hablándole a una amiga",
    "divertida": "divertida y con mucha energía, jugando con las palabras, riéndose un poco",
    "seria": "clara y segura, sin exagerar, como una vendedora joven que sabe lo que dice",
}
# Cómo es el RITMO de cada tono. Medido sobre un reel real que mandó la usuaria como
# referencia (una influencer de 19 o 20): habla 20 segundos con 2 huecos de 150 ms y
# ninguno de más de 0,30 s. La voz que sacábamos tenía 19 huecos y uno de 0,65 s.
RITMOS_VOZ = {
    "chetita": "Hablá RÁPIDO y DE CORRIDO, encadenando una frase con la otra sin pausa: "
               "arrancás la que sigue antes de que se apague la anterior. Casi no respirás y "
               "no hacés silencios entre oraciones. Mucha energía, sin bajar nunca del todo.",
    "canchera": "Hablá rápido y con energía, con pausas mínimas, sin arrastrar las frases.",
    "cercana": "Ritmo tranquilo y parejo, con pausas naturales y alguna respiración entre frases.",
    "divertida": "Rápido y saltado, cambiando de velocidad, con alguna risita en el medio.",
    "seria": "Ritmo parejo y claro, con pausas cortas donde van las comas.",
}
# Energía de la voz: la voz de Gemini sale de una mujer adulta (F0 medida: 190 Hz) y la
# referencia de la usuaria está en 242 Hz. Después del TTS, la voz pasa por rubberband
# (sube el tono y la velocidad sin romper el timbre) y por silenceremove, que le corta los
# silencios largos. Va ANTES de OmniHuman, así los labios sincronizan con lo que se oye.
_SIN_PAUSAS = "silenceremove=stop_periods=-1:stop_duration=0.10:stop_threshold=-26dB:stop_silence=0.06"
# `formant=preserved` es la diferencia entre "la misma mujer hablando más agudo" y "una
# mujer más chiquita": sin eso, rubberband le encoge también el tracto vocal y la voz sale
# con ese timbre procesado de dibujito. Con las formantes quietas se puede subir menos el
# tono y suena más joven igual.
ENERGIAS_VOZ = {
    "natural": {"nombre": "Tal cual sale (con sus pausas)", "af": "", "palabras_seg": 2.3},
    "corrido": {"nombre": "De corrido (misma voz, sin pausas) · recomendada",
                "af": f"{_SIN_PAUSAS},rubberband=tempo=1.04", "palabras_seg": 2.5},
    "joven": {"nombre": "Un toque más joven (+5% de tono)",
              "af": f"{_SIN_PAUSAS},rubberband=pitch=1.05:formant=preserved:tempo=1.04",
              "palabras_seg": 2.55},
    "muy_joven": {"nombre": "Bastante más joven (+10% de tono)",
                  "af": f"{_SIN_PAUSAS},rubberband=pitch=1.10:formant=preserved:tempo=1.06",
                  "palabras_seg": 2.7},
}
ENERGIA_DEFAULT = "corrido"

# Cómo ESCRIBE cada tono (esto va al guionista, no a la voz).
GUION_TONOS = {
    "chetita": "Escribí como una influencer chetita de Palermo hablándole a sus seguidores: "
               "muletillas de verdad ('o sea', 'tipo', 'nada', 'literal', 'obvio', 'la verdad "
               "que', 'igual'), los adjetivos de ella ('divino', 'divina', 'hermoso', "
               "'increíble', 'me muero', 'amo', 'obsesionada', 'una locura'), algún anglicismo "
               "de moda ('outfit', 'look', 'básico', 'comfy', 'must'), diminutivos ('un "
               "toquecito', 'chiquito'). Puede arrancar con 'Chicos' o 'Bueno, chicos'. Alguna "
               "frase que arranca y se corrige sola, como habla la gente de verdad.",
    "canchera": "Escribí canchera y directa, con expresiones rioplatenses naturales ('mirá', "
                "'posta', 're', 'un montón', 'tremendo') sin abusar. Frases cortas, al hueso.",
    "cercana": "Escribí como si le hablaras a una amiga: simple, cálida, sin vender. "
               "Contá tu experiencia en primera persona.",
    "divertida": "Escribí con humor liviano, alguna exageración graciosa y complicidad, "
                 "sin chistes forzados.",
    "seria": "Escribí claro y concreto, con los datos del producto adelante, sin muletillas "
             "ni adornos. Tono de quien sabe de la prenda.",
}
# El look de la imagen de ella: prompt de la escena + filtro ffmpeg sobre su video.
LOOKS = {
    "celular": "Celular (quemada, blandita, con neblina)",
    "celular_fuerte": "Celular fuerte (más quemada y borrosa)",
    "limpio": "Limpia (foto prolija, sin filtro)",
}
AMBIENTES = {
    "local": "el interior de un local de ropa chico y cálido: percheros con prendas, un "
             "mostrador de madera clara, luz de vidriera",
    "deposito": "un depósito de ropa real: estanterías con cajas de cartón etiquetadas, "
                "bolsas con prendas, un mostrador improvisado, luz de tubo cálida",
    "showroom": "un showroom luminoso y prolijo: pared clara, un mostrador blanco, un "
                "perchero con pocas prendas, luz natural de ventana",
    "casa": "su casa, un living luminoso: un sillón claro, una mesa ratona como mostrador, "
            "luz natural de ventana",
}
MAX_TRAMOS = 9

# ETAPA 2 ────────────────────────────────────────────────────────────────────
# Tomas de producto con IA: la foto real de la prenda (recortada a 9:16) a un
# motor image-to-video de fal, los mismos que ya usa Videos. Sale un clip de 5 o
# 10 s con un paneo lento sobre la prenda; si el tramo dura más, se repite.
MOTORES_IA = {
    "seedance": "Seedance Lite · 720p",
    "wan": "Wan 2.6 Flash · 1080p",
    "ltx23_fast": "LTX 2.3 Fast · 1080p",
    "seedance_pro": "Seedance Pro · 1080p",
}
MOTOR_IA_DEFAULT = "seedance"
IA_TIMEOUT = 12 * 60
# Música de fondo: pistas de la usuaria (mp3/m4a/wav), convertidas a mp3 y guardadas
# en el KV (sobreviven a los deploys). Se mezclan bajo la voz, en loop, con fade final.
MAX_PISTAS = 8
MAX_PISTA_MB = 20
PISTA_MAX_SEG = 300
MUSICA_VOL_DEFAULT = 22        # % (0 a 100): 22 queda claramente debajo de la voz
# Dos maneras de usar la pista. "Encima" es la música del reel, limpia, como en cualquier
# edición. "Local" la hace sonar como si viniera del parlante del negocio donde ella está:
# el parlante chico se come los graves y los agudos, y el ambiente le mete una reverb
# cortita. Es lo que se oye de fondo en un video grabado de verdad en un local.
MUSICA_MODOS = {"encima": "Encima del video (limpia)",
                "local": "Como si sonara en el local (de fondo)"}
_AF_MUSICA_LOCAL = "highpass=f=180,lowpass=f=3800,aecho=0.8:0.7:40|75:0.25|0.18"
MUSICA_VOL_LOCAL = 14          # de fondo va más bajo que una música encima
# Precio, talles y llamado a la acción sobre el video (van en el mismo ASS que los
# subtítulos, así que no hace falta drawtext, que este ffmpeg no trae).
CTA_DEFAULT = "Escribinos por DM"
# Plantillas: llenan las opciones del reel y le dan un enfoque al guion.
PLANTILLAS = {
    "lanzamiento": {
        "nombre": "Lanzamiento", "desc": "Llegó algo nuevo: entusiasmo, qué tiene de distinto, dónde conseguirlo.",
        "tono": "chetita", "ambiente": "local", "duracion": 35, "look": "celular", "mic": True,
        "mostrar_precio": True, "mostrar_talles": True, "cta": "Escribinos por DM",
        "ia_producto": False,
        "consigna": "Es un LANZAMIENTO: el gancho dice que acaba de llegar y por qué es distinto; "
                    "los tramos de producto muestran lo nuevo (tela, diseño, colores); el cierre "
                    "invita a escribir antes de que se agote.",
    },
    "oferta": {
        "nombre": "Oferta / promo", "desc": "Precio o descuento al frente, urgencia, cómo aprovecharla.",
        "tono": "canchera", "ambiente": "deposito", "duracion": 25, "look": "celular", "mic": True,
        "mostrar_precio": True, "mostrar_talles": False, "cta": "Hasta agotar stock · DM",
        "ia_producto": False,
        "consigna": "Es una OFERTA: el precio o el descuento aparece en el primer tramo y se repite "
                    "en el cierre; hay urgencia real (por tiempo o por stock) sin inventar datos; "
                    "frases cortas, ritmo rápido.",
    },
    "detalle": {
        "nombre": "Detalle de producto", "desc": "Tela, calce, costuras: para mostrar la prenda de cerca.",
        "tono": "chetita", "ambiente": "showroom", "duracion": 45, "look": "celular", "mic": True,
        "mostrar_precio": True, "mostrar_talles": True, "cta": "Talles y colores por DM",
        "ia_producto": True,
        "consigna": "Es un reel de DETALLE: más tramos de producto que de ella; cada tramo de "
                    "producto se concentra en una sola cosa (la tela, el calce, las tiras, las "
                    "costuras, los colores) y 'muestra' pide un primer plano de eso.",
    },
    "cotidiano": {
        "nombre": "Un día con la prenda", "desc": "Situaciones de uso, opinión personal, tono de amiga.",
        "tono": "cercana", "ambiente": "casa", "duracion": 35, "look": "celular", "mic": False,
        "mostrar_precio": False, "mostrar_talles": False, "cta": "Contame qué te parece",
        "ia_producto": False,
        "consigna": "Es un reel COTIDIANO: ella cuenta cómo y cuándo la usa (entrenar, dormir, "
                    "salir), qué le gusta y qué le cambió; el producto aparece de paso, sin tono "
                    "de venta; el cierre es una pregunta o una invitación a charlar.",
    },
}
MAX_FOTOS_PRODUCTO = 6
MAX_PROPIOS = 3                 # videos propios por tramo de producto
MAX_VIDEO_MB = 150
PROPIO_MAX_SEG = 60
ANCHO, ALTO = 1080, 1920
REEL_DIR = PJ_DIR / "reels"
REEL_DIR.mkdir(parents=True, exist_ok=True)
MAX_REELS = 30
LEER_TIMEOUT = 25


# ─────────────────────────────────────────────────────────────────────────────
# ALMACENAMIENTO
# ─────────────────────────────────────────────────────────────────────────────

def _k_reel(rid: str) -> str:
    return _pfx() + f"reel:{rid}"


def _k_idx(pid: str) -> str:
    return _pfx() + f"reel:idx:{pid}"


def _k_pfoto(rid: str, n: int) -> str:
    return _pfx() + f"reel:{rid}:pf:{n}"


def _k_escena(rid: str, i: int) -> str:
    return _pfx() + f"reel:{rid}:esc:{i}"


def _k_audio(rid: str, i: int) -> str:
    return _pfx() + f"reel:{rid}:au:{i}"


def _dir(rid: str) -> Path:
    d = REEL_DIR / rid
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ahora() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _texto(v: Any, tope: int = 600) -> str:
    return str(v or "").strip()[:tope]


async def _reel(rid: str) -> Dict[str, Any]:
    r = await kv.get(_k_reel(rid))
    if not isinstance(r, dict):
        raise HTTPException(404, "Ese reel no existe (o es de otra cuenta).")
    return r


async def _guardar_reel(reel: Dict[str, Any]) -> None:
    reel["actualizado"] = _ahora()
    ok = await kv.set(_k_reel(reel["id"]), reel)
    if not ok:
        raise HTTPException(500, f"No se pudo guardar el reel ({kv.backend}). {kv.last_error or ''}")


async def _idx(pid: str) -> List[str]:
    lst = await kv.get(_k_idx(pid))
    return [x for x in lst if isinstance(x, str)] if isinstance(lst, list) else []


def _tramo_nuevo(tipo: str = "avatar", texto: str = "", muestra: str = "") -> Dict[str, Any]:
    return {"tipo": "producto" if tipo == "producto" else "avatar", "texto": _texto(texto, 400),
            "muestra": _texto(muestra, 200), "dur": 0.0, "audio": False, "escena": False,
            "video": False, "propios": [], "detalle": "", "encuadre": "", "ia": False}


def _encuadre_idx(t: Dict[str, Any]) -> Optional[int]:
    """El encuadre elegido a mano para esa escena (0..3), o None si es automático."""
    e = t.get("encuadre")
    return e if isinstance(e, int) and 0 <= e < len(_ENCUADRES_ESCENA) else None


_VOCES_OK = {v for lst in VOCES.values() for v, _ in lst}


def _voz_valida(v: Any) -> bool:
    return isinstance(v, str) and v in _VOCES_OK


def _aplicar_opciones(reel: Dict[str, Any], payload: Dict[str, Any]) -> None:
    """Las opciones de 'cómo es el reel' que puede mandar cualquier paso."""
    if payload.get("tono") in TONOS:
        reel["tono"] = payload["tono"]
    if payload.get("duracion") in DURACIONES:
        reel["duracion"] = int(payload["duracion"])
    if payload.get("ambiente") in AMBIENTES:
        reel["ambiente"] = payload["ambiente"]
    if "outfit" in payload:
        reel["outfit"] = _texto(payload["outfit"], 200)
    if "lugar" in payload:
        reel["lugar"] = _texto(payload["lugar"], 500)
    if "continuidad" in payload:
        reel["continuidad"] = payload["continuidad"] is not False
    if payload.get("camara") in CAMARAS:
        reel["camara"] = payload["camara"]
    if "voz_real" in payload:
        reel["voz_real"] = payload["voz_real"] is not False
    if payload.get("voz_energia") in ENERGIAS_VOZ:
        reel["voz_energia"] = payload["voz_energia"]
    if "mic" in payload:
        reel["mic"] = payload["mic"] is not False
    if payload.get("look") in LOOKS:
        reel["look"] = payload["look"]
    if "voz" in payload:
        reel["voz"] = payload["voz"] if _voz_valida(payload["voz"]) else ""
    if "plantilla" in payload:
        reel["plantilla"] = payload["plantilla"] if payload["plantilla"] in PLANTILLAS else ""
    if payload.get("motor_ia") in MOTORES_IA:
        reel["motor_ia"] = payload["motor_ia"]
    if "musica" in payload:
        reel["musica"] = _texto(payload["musica"], 16)
    if payload.get("musica_modo") in MUSICA_MODOS:
        reel["musica_modo"] = payload["musica_modo"]
    if "musica_desde" in payload:
        try:
            reel["musica_desde"] = max(0.0, round(float(payload["musica_desde"]), 2))
        except (TypeError, ValueError):
            pass
    if "musica_vol" in payload:
        try:
            reel["musica_vol"] = max(0, min(100, int(payload["musica_vol"])))
        except (TypeError, ValueError):
            pass
    for k in ("mostrar_precio", "mostrar_talles"):
        if k in payload:
            reel[k] = bool(payload[k])
    if "cta" in payload:
        reel["cta"] = _texto(payload["cta"], 60)


def _k_musica_idx() -> str:
    return _pfx() + "reels:musica"


def _k_musica(mid: str) -> str:
    return _pfx() + f"reels:musica:{mid}"


async def _pistas() -> List[Dict[str, Any]]:
    lst = await kv.get(_k_musica_idx())
    return [x for x in (lst or []) if isinstance(x, dict) and x.get("id")]


def _publico(reel: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(reel)
    out["tramos"] = [dict(t) for t in reel.get("tramos") or []]
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 1) LEER UN LINK (Tiendanube / Mercado Libre / cualquier página con datos)
# ─────────────────────────────────────────────────────────────────────────────

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0 Safari/537.36")


def _limpiar_html(s: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", s)).strip()


def _jsonld_productos(html_txt: str) -> List[Dict[str, Any]]:
    """Todos los objetos @type Product de los bloques ld+json de la página."""
    out: List[Dict[str, Any]] = []

    def visitar(x: Any) -> None:
        if isinstance(x, dict):
            t = x.get("@type")
            tipos = t if isinstance(t, list) else [t]
            if any(str(tt).lower() == "product" for tt in tipos if tt):
                out.append(x)
            for v in x.values():
                visitar(v)
        elif isinstance(x, list):
            for v in x:
                visitar(v)

    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                         html_txt, flags=re.S | re.I):
        raw = m.group(1).strip()
        try:
            visitar(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


def _meta(html_txt: str, *nombres: str) -> str:
    for n in nombres:
        m = re.search(r'<meta[^>]+(?:property|name)=["\']' + re.escape(n)
                      + r'["\'][^>]+content=["\']([^"\']*)["\']', html_txt, flags=re.I)
        if not m:
            m = re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']'
                          + re.escape(n) + r'["\']', html_txt, flags=re.I)
        if m and m.group(1).strip():
            return _html.unescape(m.group(1).strip())
    return ""


def _imagenes_de(x: Any) -> List[str]:
    if isinstance(x, str):
        return [x]
    if isinstance(x, dict):
        u = x.get("url") or x.get("contentUrl")
        return [u] if isinstance(u, str) else []
    if isinstance(x, list):
        out: List[str] = []
        for v in x:
            out += _imagenes_de(v)
        return out
    return []


def _precio_de(prod: Dict[str, Any]) -> str:
    of = prod.get("offers")
    ofs = of if isinstance(of, list) else [of]
    for o in ofs:
        if isinstance(o, dict):
            for k in ("price", "lowPrice"):
                if o.get(k) not in (None, ""):
                    mon = o.get("priceCurrency") or ""
                    return f"{mon} {o[k]}".strip()
    return ""


async def _leer_ml(cli: httpx.AsyncClient, url: str) -> Optional[Dict[str, Any]]:
    """Mercado Libre por su API pública (título, precio, fotos, talles/colores). Si
    la API pide token (401/403), devuelve None y se lee la página como cualquier otra."""
    m = re.search(r"(MLA|MLB|MLM|MLU|MLC|MCO|MPE)-?(\d{6,})", url, flags=re.I)
    if not m:
        return None
    iid = (m.group(1) + m.group(2)).upper()
    try:
        r = await cli.get(f"https://api.mercadolibre.com/items/{iid}")
        if r.status_code != 200:
            return None
        d = r.json()
        desc = ""
        try:
            rd = await cli.get(f"https://api.mercadolibre.com/items/{iid}/description")
            if rd.status_code == 200:
                desc = str(rd.json().get("plain_text") or "")
        except Exception:
            pass
        talles, colores = set(), set()
        for v in d.get("variations") or []:
            for c in v.get("attribute_combinations") or []:
                nid = str(c.get("id") or "").upper()
                val = str(c.get("value_name") or "").strip()
                if not val:
                    continue
                if "SIZE" in nid or "TALLE" in nid:
                    talles.add(val)
                elif "COLOR" in nid:
                    colores.add(val)
        for a in d.get("attributes") or []:
            nid = str(a.get("id") or "").upper()
            val = str(a.get("value_name") or "").strip()
            if val and nid in ("COLOR", "MAIN_COLOR"):
                colores.add(val)
        fotos = [p.get("secure_url") or p.get("url") for p in (d.get("pictures") or [])]
        return {"titulo": str(d.get("title") or ""), "descripcion": desc,
                "precio": (f"{d.get('currency_id') or ''} {d.get('price') or ''}").strip(),
                "talles": ", ".join(sorted(talles)), "colores": ", ".join(sorted(colores)),
                "fotos": [f for f in fotos if f], "fuente": "mercadolibre"}
    except Exception:
        return None


async def _leer_link(url: str) -> Dict[str, Any]:
    """Devuelve {titulo, descripcion, precio, talles, colores, fotos:[b64], fuente}."""
    url = url.strip()
    if not re.match(r"^https?://", url, flags=re.I):
        url = "https://" + url
    headers = {"User-Agent": _UA, "Accept-Language": "es-AR,es;q=0.9"}
    async with httpx.AsyncClient(timeout=LEER_TIMEOUT, follow_redirects=True, headers=headers) as cli:
        info = await _leer_ml(cli, url) if "mercadolibre" in url or "mercadolivre" in url else None
        if not info:
            try:
                r = await cli.get(url)
            except Exception as e:
                raise HTTPException(400, f"No pude abrir ese link: {e}")
            if r.status_code != 200:
                raise HTTPException(400, f"La página respondió {r.status_code}. Probá con otro link "
                                         "o cargá los datos a mano.")
            txt = r.text
            prods = _jsonld_productos(txt)
            p0 = prods[0] if prods else {}
            titulo = str(p0.get("name") or "") or _meta(txt, "og:title", "twitter:title")
            if not titulo:
                mt = re.search(r"<title[^>]*>(.*?)</title>", txt, flags=re.S | re.I)
                titulo = _html.unescape(mt.group(1).strip()) if mt else ""
            desc = _limpiar_html(str(p0.get("description") or "")) or _meta(
                txt, "og:description", "description", "twitter:description")
            fotos: List[str] = []
            for u in _imagenes_de(p0.get("image")) + [_meta(txt, "og:image")]:
                if u and u not in fotos:
                    fotos.append(u)
            info = {"titulo": titulo, "descripcion": desc, "precio": _precio_de(p0),
                    "talles": "", "colores": "", "fotos": fotos,
                    "fuente": "tiendanube" if "tiendanube" in txt.lower() or "nuvemshop" in txt.lower()
                    else "web"}
        # Fotos: se bajan y se guardan chicas (van a Nano Banana y a los flashes del reel).
        b64s: List[str] = []
        for u in (info.get("fotos") or [])[:MAX_FOTOS_PRODUCTO]:
            try:
                if u.startswith("//"):
                    u = "https:" + u
                rr = await cli.get(u)
                if rr.status_code == 200 and len(rr.content) > 2000 and len(rr.content) < 12_000_000:
                    b64s.append(_compress_ref(rr.content, max_dim=1600, q=90))
            except Exception:
                continue
    return {"titulo": _texto(info.get("titulo"), 160), "descripcion": _texto(info.get("descripcion"), 1500),
            "precio": _texto(info.get("precio"), 40), "talles": _texto(info.get("talles"), 120),
            "colores": _texto(info.get("colores"), 120), "fotos": b64s, "fuente": info.get("fuente", "web")}


# ─────────────────────────────────────────────────────────────────────────────
# 2) GUION
# ─────────────────────────────────────────────────────────────────────────────

def _producto_texto(reel: Dict[str, Any]) -> str:
    p = reel.get("producto") or {}
    lineas = [f"Producto: {p.get('titulo') or 'una prenda de la marca'}"]
    for k, et in (("descripcion", "Descripción"), ("precio", "Precio"), ("talles", "Talles"),
                  ("colores", "Colores"), ("notas", "Lo que la dueña quiere destacar")):
        if p.get(k):
            lineas.append(f"{et}: {p[k]}")
    return "\n".join(lineas)


def _palabras_seg(reel: Dict[str, Any]) -> float:
    return ENERGIAS_VOZ[_energia(reel)]["palabras_seg"]


def _system_guion(doc: Dict[str, Any], reel: Dict[str, Any]) -> str:
    dur = int(reel.get("duracion") or 35)
    n_tramos = 5 if dur <= 35 else 7
    palabras = int(dur * _palabras_seg(reel))
    tono = reel.get("tono") or "cercana"
    g = _g(doc)
    return (
        f"Sos guionista de reels de Instagram para {doc.get('marca') or 'una marca de ropa'}. "
        f"La que habla es {doc.get('nombre') or 'la influencer de la marca'}, {g['persona']}, "
        "la influencer de la marca (persona digital). Habla en primera persona, en castellano "
        "rioplatense con voseo, como si le hablara a una amiga, sin emojis y sin hashtags.\n\n"
        f"SU FICHA:\n{_ficha_texto(doc)}\n\n"
        f"TONO Y MANERA DE HABLAR ({tono}): {GUION_TONOS.get(tono, GUION_TONOS['canchera'])}\n\n"
        f"EL PRODUCTO:\n{_producto_texto(reel)}\n\n"
        + (f"ENFOQUE DE ESTE REEL: {PLANTILLAS[reel['plantilla']]['consigna']}\n\n"
           if reel.get("plantilla") in PLANTILLAS else "")
        + f"ARMÁ UN GUION DE {dur} SEGUNDOS ({palabras} palabras aproximadamente) en exactamente "
        f"{n_tramos} tramos alternados: el primero y el último los dice ELLA A CÁMARA (tipo "
        "\"avatar\"); en el medio se alternan tramos de PRODUCTO (tipo \"producto\": la voz de "
        "ella sigue, pero en pantalla se ve la prenda sola, sin gente) y de ella. Reglas:\n"
        "- Tramo 1 (avatar): gancho de 2 oraciones cortas, dice qué llegó y por qué mirarlo. "
        "Máximo 18 palabras.\n"
        "- Tramos de producto: 18 a 28 palabras, hablan de la tela, el calce, los detalles, "
        "los colores, los talles y el precio con datos REALES del producto (no inventes datos "
        "que no estén).\n"
        "- Tramos de ella: 12 a 20 palabras, opinión personal, algo que le gusta, una situación "
        "de uso.\n"
        "- Último tramo (avatar): llamado a la acción concreto (escribir por DM, entrar al link "
        "de la bio, pasar por el local). Máximo 16 palabras.\n"
        "- Frases cortas, ritmo de reel, con las muletillas y las palabras del TONO de arriba. "
        "Que suene a alguien hablando, no a un texto leído: alguna frase corta sola, algún "
        "'eh' o 'nada' donde caiga natural. Nada de lenguaje de publicidad ni de frases hechas "
        "de vendedor. Sin comillas ni paréntesis adentro del texto.\n"
        "- En cada tramo de producto, 'muestra' dice en 5 a 10 palabras qué se ve en pantalla "
        "(ej: 'primer plano del encaje y las tiras').\n\n"
        "Respondé SOLO con un JSON:\n"
        "{\n"
        '  "titulo": "título corto del reel (3 a 6 palabras)",\n'
        '  "tramos": [ {"tipo": "avatar" | "producto", "texto": "lo que dice", "muestra": "qué se ve (solo en producto)"} ]\n'
        "}"
    )


async def _escribir_guion(doc: Dict[str, Any], reel: Dict[str, Any]) -> Dict[str, Any]:
    data = await _gemini_json(_system_guion(doc, reel),
                              [{"role": "user", "parts": [{"text": "Escribí el guion."}]}],
                              temperature=0.8)
    tramos: List[Dict[str, Any]] = []
    ia = bool(PLANTILLAS.get(reel.get("plantilla") or "", {}).get("ia_producto"))
    for t in (data.get("tramos") or [])[:MAX_TRAMOS]:
        if isinstance(t, dict) and _texto(t.get("texto")):
            nt = _tramo_nuevo(str(t.get("tipo") or "avatar"), t.get("texto"), t.get("muestra"))
            nt["ia"] = ia and nt["tipo"] == "producto"
            tramos.append(nt)
    if len(tramos) < 2:
        raise HTTPException(422, "El guion salió vacío. Probá de nuevo o escribilo a mano.")
    if tramos[0]["tipo"] != "avatar":
        tramos[0]["tipo"] = "avatar"
    if tramos[-1]["tipo"] != "avatar":
        tramos[-1]["tipo"] = "avatar"
    return {"titulo": _texto(data.get("titulo"), 80) or "Reel", "tramos": tramos}


# ─────────────────────────────────────────────────────────────────────────────
# 3) VOZ POR TRAMO
# ─────────────────────────────────────────────────────────────────────────────

def _audio_path(rid: str, i: int) -> Path:
    return _dir(rid) / f"tramo_{i}.mp3"


async def _asegurar_audio_en_disco(rid: str, i: int) -> Path:
    """El mp3 vive en el KV (sobrevive a un deploy); en disco se rehace si falta."""
    p = _audio_path(rid, i)
    if not p.exists():
        b64 = await kv.get(_k_audio(rid, i))
        if not b64:
            raise RuntimeError(f"Falta la voz del tramo {i + 1}: generá las voces de nuevo.")
        p.write_bytes(base64.b64decode(b64))
    return p


def _instruccion_voz(doc: Dict[str, Any], reel: Dict[str, Any]) -> str:
    """La consigna de lectura para la voz del reel: influencer joven, rioplatense marcado."""
    g = _g(doc)
    estilo = ESTILOS_VOZ.get(reel.get("tono") or "", ESTILOS_VOZ["chetita"])
    edad = "19 o 20" if _energia(reel) != "natural" else "unos 25"
    quien = (f"una influencer argentina de {edad} años" if g["she"] == "she"
             else f"un influencer argentino de {edad} años")
    ritmo = RITMOS_VOZ.get(reel.get("tono") or "", RITMOS_VOZ["chetita"])
    joven = "" if _energia(reel) == "natural" else (
        "Sos bien joven, de 19 o 20 años: voz aguda y liviana, nada de voz de señora. ")
    return (f"Sos {quien}, grabando un reel para Instagram con el celular, hablando a cámara. "
            "Acento rioplatense bien marcado (la 'y' y la 'll' suenan 'sh', entonación porteña), "
            "con voseo. NADA de tono de locutora ni de publicidad de radio: voz de persona "
            f"normal, {estilo}. {joven}{ritmo} Que suene a charla y no a lectura: algunas "
            "palabras más rápido que otras, alguna sílaba alargada, sin marcar cada palabra y "
            "sin sobreactuar. Decí exactamente este texto: ")


def _voz_reel(doc: Dict[str, Any], reel: Dict[str, Any]) -> str:
    """La voz elegida para el reel; si no eligió, la del personaje; si no, una joven."""
    return reel.get("voz") or doc.get("voz") or ("Puck" if _g(doc)["she"] == "he" else "Leda")


def _energia(reel: Dict[str, Any]) -> str:
    return reel.get("voz_energia") if reel.get("voz_energia") in ENERGIAS_VOZ else ENERGIA_DEFAULT


def _tratar_voz(mp3: bytes, af: str, d: Path, nombre: str) -> bytes:
    """Acelera la voz, le corta los silencios largos y, si se pide, le sube el tono sin
    encogerle las formantes (sin eso queda el efecto de cinta acelerada). Va antes de
    OmniHuman: los labios sincronizan con este audio, no con el que salió del TTS."""
    d.mkdir(parents=True, exist_ok=True)
    ent, sal = d / f"{nombre}_crudo.mp3", d / f"{nombre}_tratado.mp3"
    ent.write_bytes(mp3)
    try:
        _run([_ff(), "-y", "-i", str(ent), "-af", af, "-ar", "24000", "-b:a", "96k", str(sal)],
             timeout=180)
        return sal.read_bytes()
    finally:
        for x in (ent, sal):
            try:
                x.unlink()
            except OSError:
                pass


async def _generar_voz(doc: Dict[str, Any], reel: Dict[str, Any], i: int) -> float:
    t = reel["tramos"][i]
    if not t.get("texto"):
        raise HTTPException(400, f"El tramo {i + 1} no tiene texto.")
    await _cobrar(COSTO_TTS)
    mp3 = await _tts_mp3(t["texto"], _voz_reel(doc, reel), doc, instruccion=_instruccion_voz(doc, reel))
    af = ENERGIAS_VOZ[_energia(reel)]["af"]
    if af:
        try:
            mp3 = await asyncio.to_thread(_tratar_voz, mp3, af, _dir(reel["id"]), f"tts_{i}")
        except Exception as e:      # si ffmpeg falla, queda la voz tal cual salió del TTS
            print(f"[reels] no pude tratar la voz del tramo {i + 1}: {e}")
    await budget_record("reel_voz", "mp3", COSTO_TTS, 1,
                        note=f"{doc.get('nombre', '')} reel tramo {i + 1}")
    p = _audio_path(reel["id"], i)
    p.write_bytes(mp3)
    await kv.set(_k_audio(reel["id"], i), base64.b64encode(mp3).decode())
    dur = round(_duracion_video(p), 2) or round(len(t["texto"].split()) / _palabras_seg(reel), 2)
    t["dur"] = dur
    t["audio"] = True
    t["video"] = False     # la voz cambió: el video de ese tramo hay que rehacerlo
    return dur


# ─────────────────────────────────────────────────────────────────────────────
# 4) ESCENAS DE ELLA (foto vertical en el local, la prenda al lado)
# ─────────────────────────────────────────────────────────────────────────────

_ENCUADRES_ESCENA = [
    "plano medio de frente, de la cintura para arriba, mirando al lente, las manos apoyadas "
    "en el mostrador",
    "plano medio corto en leve 3/4, mirando al lente, una mano sobre la prenda del mostrador",
    "plano medio de frente, un poco más cerca, sosteniendo la prenda en alto con las dos "
    "manos para mostrarla a cámara",
    "plano americano en 3/4, apoyada en el mostrador, la prenda al lado, mirando al lente",
]
ENCUADRES_NOMBRES = [
    "Plano medio de frente, manos en el mostrador",
    "Plano medio corto en 3/4, una mano sobre la prenda",
    "Más cerca, mostrando la prenda en alto",
    "Plano americano en 3/4, apoyada en el mostrador",
]
# Con el micrófono en una mano, la otra hace lo que hacía antes.
_ENCUADRES_MIC = [
    "plano medio de frente, de la cintura para arriba, mirando al lente, la otra mano apoyada "
    "en el mostrador",
    "plano medio corto en leve 3/4, mirando al lente, la otra mano sobre la prenda del mostrador",
    "plano medio de frente, un poco más cerca, con la otra mano sosteniendo la prenda en alto "
    "para mostrarla a cámara",
    "plano americano en 3/4, apoyada en el mostrador, la prenda al lado, mirando al lente",
]
_MIC_ESCENA = ("MICRÓFONO (no negociable): sostiene con una mano, cerca de la boca, un micrófono "
               "inalámbrico chiquito NEGRO, de solapa (mini mic del tamaño de un dedo, sin cable "
               "ni mango largo), como usan las influencers para hablar a cámara.")
_LOOK_ESCENA = {
    "celular": (
        "IMAGEN: es un cuadro de VIDEO grabado con la cámara de un celular, no una foto de "
        "estudio: un poco sobreexpuesto (las luces quemadas, la ventana blanca), leve falta de "
        "nitidez, una neblina suave como de lente un poco sucio, colores de cámara frontal de "
        "celular, grano fino. Sin desenfoque de fondo profesional, sin retoque, sin look de "
        "estudio. Piel real. Sin texto, sin logos, sin marcas de agua, sin otras personas."
    ),
    "limpio": (
        "Foto real tomada con un celular, luz pareja y cálida del lugar, piel con textura real, "
        "sin desenfoque exagerado. Sin texto, sin logos, sin marcas de agua, sin otras personas."
    ),
}
_LOOK_ESCENA["celular_fuerte"] = _LOOK_ESCENA["celular"]


def _mic(reel: Dict[str, Any]) -> bool:
    return reel.get("mic") is not False


def _look(reel: Dict[str, Any]) -> str:
    return reel.get("look") if reel.get("look") in LOOKS else "celular"


def _es_lenceria(txt: str) -> bool:
    t = (txt or "").lower()
    return any(kw in t for kw in _LENC_KW)


# Cuando ella lleva puesta ropa interior o malla, el filtro de Gemini lee "selfie de
# celular en corpiño" como sugerente. Fotos nunca tiene ese problema porque encuadra
# todo como catálogo de tienda: acá va el mismo marco.
_MARCO_CATALOGO = (
    "CONTEXTO COMERCIAL (importante): es contenido de la tienda online de una marca de ropa "
    "interior; ella es la vendedora de la marca mostrando lo que vende. La ropa interior que "
    "lleva puesta se ve como en una foto de CATÁLOGO de e-commerce: prolija, elegante, sobria, "
    "nada sugerente ni sensual, pose de vendedora hablando (no pose de modelo), boca cerrada, "
    "sonrisa natural. Sin nada más que la prenda y el lugar."
)


def _prompt_escena(doc: Dict[str, Any], reel: Dict[str, Any], i: int, n_refs: int,
                   n_prendas: int, conservador: bool = False, n_ancla: int = 0) -> str:
    g = _g(doc)
    amb = AMBIENTES.get(reel.get("ambiente") or "local", AMBIENTES["local"])
    t = reel["tramos"][i]
    k = _encuadre_idx(t)
    if k is None:
        k = sum(1 for x in reel["tramos"][:i] if x.get("tipo") == "avatar")
    encs = _ENCUADRES_MIC if _mic(reel) else _ENCUADRES_ESCENA
    enc = encs[k % len(encs)]
    detalle = _texto(t.get("detalle"), 500)
    lugar = _texto(reel.get("lugar"), 500)
    prod = (reel.get("producto") or {}).get("titulo") or "la prenda"
    outfit = _texto(reel.get("outfit"), 200) or "ropa de todos los días, prolija y sencilla (una remera o camisa lisa)"
    lenceria = _es_lenceria(outfit)
    boca = "boca cerrada, sonrisa natural" if (lenceria or conservador) else "boca cerrada o apenas entreabierta"
    partes = [
        f"Cuadro de un REEL vertical 9:16 para Instagram: {doc.get('nombre') or 'la protagonista'}, "
        f"{g['persona']}, influencer de la marca {doc.get('marca') or ''}, HABLANDO A CÁMARA como "
        "quien graba un video con el celular para presentar un producto.",
        _bloque_identidad_pj(doc, n_refs),
        f"ESCENARIO: {amb}. Es un lugar real, con profundidad y cosas de verdad alrededor."
        + (f"\nCÓMO ES EL LUGAR (respetalo al pie de la letra): {lugar}" if lugar else ""),
        f"ENCUADRE: {enc}. La cámara a la altura de sus ojos, ella bien centrada y ocupando "
        f"buena parte del cuadro, {boca}, expresión natural y "
        "cercana, mirada al lente.",
        f"ROPA DE ELLA: {outfit}. NO tiene puesta la prenda del producto.",
    ]
    if n_ancla:
        partes.append(
            f"CONTINUIDAD CON EL RESTO DEL REEL (no negociable): la IMAGEN {n_refs + 1} es la "
            "PRIMERA ESCENA de este mismo reel. Es el mismo momento y el mismo lugar: mantené "
            "idénticos el ambiente, los muebles, los objetos del fondo, el color de las paredes, "
            "la luz, la ropa de ella, el peinado y el maquillaje. Lo ÚNICO que cambia es el "
            "encuadre y la pose que se piden acá."
        )
    if lenceria or conservador:
        partes.append(_MARCO_CATALOGO)
    if _mic(reel):
        partes.append(_MIC_ESCENA)
    if detalle:
        partes.append(f"DETALLES PEDIDOS PARA ESTA ESCENA (respetalos al pie de la letra): {detalle}")
    if n_prendas:
        partes.append(
            f"EL PRODUCTO (no negociable): a su lado y bien visible —sobre el mostrador, salvo "
            f"que el lugar o los detalles pidan otra cosa— está la prenda de las FOTOS REALES "
            f"DEL PRODUCTO (imágenes {n_refs + n_ancla + 1} a "
            f"{n_refs + n_ancla + n_prendas}): {prod}. Es EXACTAMENTE esa prenda —mismo diseño, mismo "
            "color, mismos detalles—, doblada prolija o extendida, como un producto que se "
            "está mostrando. No la rediseñes ni le cambies el color."
        )
    # En el reintento conservador va la versión limpia (sin "quemada, borrosa"), que es
    # la que menos le suena a selfie al filtro.
    partes.append(_LOOK_ESCENA["limpio" if conservador else _look(reel)])
    return "\n\n".join(partes)


def _system_preguntas(doc: Dict[str, Any], reel: Dict[str, Any], i: int,
                      con_ancla: bool = False) -> str:
    t = reel["tramos"][i]
    k = _encuadre_idx(t)
    enc = ENCUADRES_NOMBRES[k] if k is not None else "automático (va rotando entre cuatro)"
    amb = AMBIENTES.get(reel.get("ambiente") or "local", AMBIENTES["local"])
    prod = (reel.get("producto") or {}).get("titulo") or "la prenda"
    return (
        "Sos la directora de arte de un reel de Instagram de una marca de ropa. Antes de generar "
        "la foto de una escena en la que la influencer habla a cámara, le hacés a la dueña de la "
        "marca 3 o 4 preguntas CORTAS de aclaración sobre cosas que la foto no puede adivinar "
        "(qué hace con la prenda, expresión, qué tan cerca, qué hay alrededor, un accesorio, el "
        "pelo, hacia dónde mira, qué hace con la mano libre, etc.). Cada pregunta trae 2 a 4 "
        "opciones cortas y concretas; la primera es la que vos recomendás para ese tramo. No "
        "preguntes lo que ya está decidido abajo.\n\n"
        f"LO QUE DICE EN ESE TRAMO: {t.get('texto') or ''}\n"
        f"PRODUCTO: {prod}\nLUGAR: {amb}\nENCUADRE: {enc}\n"
        f"CÓMO ES EL LUGAR: {_texto(reel.get('lugar'), 500) or 'sin detalles'}\n"
        f"ROPA DE ELLA: {_texto(reel.get('outfit'), 200) or 'ropa de todos los días'}\n"
        f"MICRÓFONO CHIQUITO EN LA MANO: {'sí' if _mic(reel) else 'no'}\n"
        f"DETALLES YA PEDIDOS: {_texto(t.get('detalle'), 500) or 'ninguno'}\n"
        + ("ATENCIÓN: esta escena copia el lugar, la ropa, el peinado y la luz de la primera "
           "escena del reel, así que NO preguntes por nada de eso: preguntá sólo por la pose, "
           "la expresión, qué hace con las manos y con la prenda en ESTE tramo.\n"
           if con_ancla else "")
        + "\n"
        "Respondé SOLO con un JSON: {\"preguntas\": [{\"pregunta\": \"...\", \"opciones\": [\"...\", \"...\"]}]}"
    )


def _system_preguntas_lugar(ambiente: str, lugar: str, producto: Dict[str, Any]) -> str:
    amb = AMBIENTES.get(ambiente or "local", AMBIENTES["local"])
    return (
        "Sos la directora de arte de un reel de Instagram de una marca de ropa. Antes de generar "
        "las fotos del reel, le hacés a la dueña de la marca 3 o 4 preguntas CORTAS sobre CÓMO ES "
        "EL LUGAR donde va a grabar, para que las fotos no lo inventen: dónde está el producto "
        "(colgado en un perchero, apoyado en el mostrador, en una caja abierta, sobre un maniquí), "
        "qué se ve detrás de ella, cómo es el mueble donde apoya, la luz, si hay cartel o logo de "
        "la marca, qué tan lleno o vacío está. Cada pregunta trae 2 a 4 opciones cortas y "
        "concretas; la primera es la que vos recomendás. No preguntes por la ropa de ella, ni por "
        "la pose, ni por el encuadre: eso se decide después.\n\n"
        f"EL LUGAR ELEGIDO: {amb}\n"
        f"PRODUCTO: {_texto(producto.get('titulo'), 160) or 'una prenda'}\n"
        f"YA DICHO SOBRE EL LUGAR: {_texto(lugar, 500) or 'nada'}\n\n"
        "Respondé SOLO con un JSON: {\"preguntas\": [{\"pregunta\": \"...\", \"opciones\": [\"...\", \"...\"]}]}"
    )


def _leer_preguntas(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for q in (data.get("preguntas") or [])[:4]:
        if not isinstance(q, dict) or not _texto(q.get("pregunta")):
            continue
        ops = [_texto(o, 80) for o in (q.get("opciones") or []) if _texto(o, 80)][:4]
        if ops:
            out.append({"pregunta": _texto(q["pregunta"], 160), "opciones": ops})
    if not out:
        raise HTTPException(422, "No salieron preguntas. Probá de nuevo o escribí los detalles a mano.")
    return out


async def _preguntas_escena(doc: Dict[str, Any], reel: Dict[str, Any], i: int,
                            con_ancla: bool = False) -> List[Dict[str, Any]]:
    return _leer_preguntas(await _gemini_json(
        _system_preguntas(doc, reel, i, con_ancla),
        [{"role": "user", "parts": [{"text": "Hacé las preguntas."}]}], temperature=0.7))


async def _preguntas_lugar(ambiente: str, lugar: str, producto: Dict[str, Any]) -> List[Dict[str, Any]]:
    return _leer_preguntas(await _gemini_json(
        _system_preguntas_lugar(ambiente, lugar, producto),
        [{"role": "user", "parts": [{"text": "Hacé las preguntas."}]}], temperature=0.7))


async def _ancla_escena(reel: Dict[str, Any], i: int) -> Optional[str]:
    """La PRIMERA escena ya generada del reel (sin contar la que estamos rehaciendo): va
    como referencia para que el lugar, la ropa y la luz no cambien entre tramos."""
    if reel.get("continuidad") is False:
        return None
    for j, t in enumerate(reel.get("tramos") or []):
        if j == i or t.get("tipo") != "avatar" or not t.get("escena"):
            continue
        b = await kv.get(_k_escena(reel["id"], j))
        if b:
            return b
    return None


async def _generar_escena(doc: Dict[str, Any], reel: Dict[str, Any], i: int) -> str:
    refs = await _refs_identidad(doc)
    if not refs:
        raise HTTPException(400, "Este personaje todavía no tiene retrato aprobado: hacelo en su ficha.")
    settings = await get_settings()
    est = _pricing(settings).get("2K", 0.10)
    await _cobrar(est)
    prendas: List[str] = []
    for n in range(min(int((reel.get("producto") or {}).get("n_fotos") or 0), 3)):
        b = await kv.get(_k_pfoto(reel["id"], n))
        if b:
            prendas.append(b)
    ancla = await _ancla_escena(reel, i)
    n_ancla = 1 if ancla else 0

    def _parts(conservador: bool) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = [{"text": _prompt_escena(doc, reel, i, len(refs), len(prendas),
                                                             conservador, n_ancla)}]
        for j, (et, b64) in enumerate(refs):
            out.append({"text": f"IMAGEN {j + 1} (referencia de identidad: {et}):"})
            out.append(_img_part(b64))
        if ancla:
            out.append({"text": f"IMAGEN {len(refs) + 1} (LA PRIMERA ESCENA DE ESTE REEL: de acá "
                                "salen el lugar, el fondo, la luz, la ropa y el peinado):"})
            out.append(_img_part(ancla))
        for j, b64 in enumerate(prendas):
            out.append({"text": f"IMAGEN {len(refs) + n_ancla + j + 1} (foto real del producto, va apoyado en el mostrador):"})
            out.append(_img_part(b64))
        return out
    try:
        img = await gemini_generate(_parts(False), settings, aspect="9:16", image_size="2K")
    except HTTPException as e:
        if e.status_code != 422 or "bloqueó" not in str(e.detail):
            raise
        # Bloqueo del filtro: un reintento con el marco de catálogo y el look limpio.
        try:
            img = await gemini_generate(_parts(True), settings, aspect="9:16", image_size="2K")
        except HTTPException as e2:
            if e2.status_code == 422 and "bloqueó" in str(e2.detail):
                raise HTTPException(422, f"{e2.detail} · Probé dos veces (la segunda en modo catálogo). "
                                         "Qué suele destrabarlo: en 'Cómo está vestida' poné la prenda con "
                                         "nombre de catálogo (ej: 'conjunto de ropa interior negro y short') "
                                         "y sacá palabras de pose o de cuerpo; cambiá el modelo de imagen en "
                                         "Fotos → Ajustes (el Pro es más estricto que el Flash); o subí tu "
                                         "propia escena con '⬆️ Subir la mía'.")
            raise
    await budget_record("reel_escena", "2K", est, 1, note=f"{doc.get('nombre', '')} reel escena {i + 1}")
    b64 = _compress_ref(img, max_dim=1920, q=92)
    await kv.set(_k_escena(reel["id"], i), b64)
    reel["tramos"][i]["escena"] = True
    reel["tramos"][i]["video"] = False
    return b64


# ─────────────────────────────────────────────────────────────────────────────
# 5) VIDEO: OmniHuman para ella, flashes con zoom para el producto, armado ffmpeg
# ─────────────────────────────────────────────────────────────────────────────

def _ff() -> str:
    b = _ffmpeg_bin()
    if not b:
        raise RuntimeError("No hay ffmpeg en el servidor.")
    return b


def _run(cmd: List[str], timeout: int = 600, cwd: Optional[Path] = None) -> None:
    res = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=str(cwd) if cwd else None)
    if res.returncode != 0:
        raise RuntimeError("ffmpeg: " + res.stderr.decode(errors="ignore")[-400:])


async def _run_latiendo(jid: Optional[str], cmd: List[str], timeout: int = 600,
                        cwd: Optional[Path] = None) -> None:
    """ffmpeg en un hilo, mandando latidos al trabajo cada 20 s: un armado largo no
    parece un proceso muerto."""
    tarea = asyncio.create_task(asyncio.to_thread(_run, cmd, timeout, cwd))
    while True:
        try:
            await asyncio.wait_for(asyncio.shield(tarea), timeout=20)
            return
        except asyncio.TimeoutError:
            if jid:
                await _job_set(jid, {})


_ENC_VIDEO = ["-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30"]
_ENC_AUDIO = ["-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "128k"]


def _prompt_omni(doc: Dict[str, Any], reel: Optional[Dict[str, Any]] = None) -> str:
    g = _g(doc)
    mic = (f"{g['she'].capitalize()} holds a tiny black wireless clip-on microphone near "
           f"{g['her']} mouth with one hand the whole time and never puts it down. "
           if reel is None or _mic(reel) else "")
    return (f"The {g['woman']} talks to the camera like a young influencer filming a story on "
            f"{g['her']} phone: natural and upbeat, never posing. Alive micro-movements the whole "
            f"time — small head tilts and nods on the stressed words, eyebrows moving with what "
            f"{g['she']} says, natural blinking, a small smile that comes and goes, shoulders and "
            f"weight shifting slightly, free hand gesturing loosely while talking. "
            f"{mic}{g['she'].capitalize()} keeps the same clothes, hair and background. Handheld "
            "camera with a tiny natural drift, no zoom. No text.")


# Look de celular sobre el video de ella (ffmpeg): neblina de lente sucio (bloom en RGB,
# para que no tiña), luces quemadas y negros levantados (curves), un poco blanda (gblur)
# y grano fino (noise). Las fotos del producto y tus videos propios quedan como están.
_FILTRO_LOOK = {
    "celular": ("format=gbrp,split[a][b];[b]gblur=sigma=28[g];[a][g]blend=all_mode=screen:all_opacity=0.30,"
                "gblur=sigma=1.2,curves=all='0/0.06 0.5/0.60 0.82/0.97 1/1',"
                "eq=contrast=0.92:saturation=0.95,noise=alls=10:allf=t+u"),
    "celular_fuerte": ("format=gbrp,split[a][b];[b]gblur=sigma=34[g];[a][g]blend=all_mode=screen:all_opacity=0.45,"
                       "gblur=sigma=1.9,curves=all='0/0.10 0.45/0.60 0.75/0.97 1/1',"
                       "eq=contrast=0.88:saturation=0.90:brightness=0.03,noise=alls=12:allf=t+u"),
}


# Cámara en mano: OmniHuman devuelve el cuadro clavado (el fondo queda congelado píxel a
# píxel, y eso es lo que más delata que es IA). Se agranda un 5% y se recorta con un
# desplazamiento que va cambiando con el tiempo: dos senos de períodos distintos por eje,
# así el movimiento no se repite ni parece un vaivén.
_MANO_ESCALA = 1.05
_MANO_CROP = (f"crop={ANCHO}:{ALTO}:"
              "x='(in_w-out_w)/2+9*sin(2*PI*t/3.1)+5*sin(2*PI*t/1.7+1.2)':"
              "y='(in_h-out_h)/2+7*sin(2*PI*t/2.6+0.5)+4*cos(2*PI*t/1.3)'")
CAMARAS = {"mano": "En mano (se mueve sola, como un celular)", "fija": "Fija (clavada)"}

# Voz con aire de micrófono real: la voz de Gemini sale de estudio, limpia y pareja, y eso
# también suena a IA. La primera versión sonaba a radio AM y estaba medido por qué: le
# sacaba cuerpo en 260 Hz, le metía 2,5 dB en 3,4 kHz y le sumaba un eco de 24 ms que
# peina el espectro y deja ese timbre metálico. Quedó así: cuerpo en 1,3 kHz, una pizca de
# presencia en 3 kHz, menos filo en 7,5 kHz (ahí teníamos 4 dB de más contra la referencia
# que mandó la usuaria) y compresión suave. Sin eco.
_AF_VOZ = ("highpass=f=80,equalizer=f=1300:t=q:w=1.2:g=2,equalizer=f=3000:t=q:w=2:g=2,"
           "equalizer=f=7500:t=q:w=1.5:g=-4,"
           "acompressor=threshold=0.10:ratio=2.5:attack=15:release=200:makeup=1.8,"
           "alimiter=limit=0.95")


def _camara(reel: Dict[str, Any]) -> str:
    return reel.get("camara") if reel.get("camara") in CAMARAS else "mano"


def _af_voz(reel: Dict[str, Any]) -> List[str]:
    return [] if reel.get("voz_real") is False else ["-af", _AF_VOZ]


def _vf_avatar(reel: Dict[str, Any]) -> str:
    if _camara(reel) == "mano":
        w2, h2 = int(ANCHO * _MANO_ESCALA), int(ALTO * _MANO_ESCALA)
        base = f"scale={w2}:{h2}:force_original_aspect_ratio=increase,{_MANO_CROP},fps=30"
    else:
        base = f"scale={ANCHO}:{ALTO}:force_original_aspect_ratio=increase,crop={ANCHO}:{ALTO},fps=30"
    f = _FILTRO_LOOK.get(_look(reel))
    return (base + "," + f + "," if f else base + ",") + "format=yuv420p"


async def _fal_esperar_video(cli: httpx.AsyncClient, headers: Dict[str, str], jid: str,
                             job: Dict[str, Any], destino: Path, timeout: int, pref: str,
                             paso_txt: str, quien: str) -> None:
    """Espera el trabajo de fal que está en el job (status/result), baja el video a
    `destino` y limpia las URLs del job (ese pedido ya no hay que retomarlo)."""
    inicio = float(job.get("fal_inicio") or time.time())
    ultimo = ""
    while True:
        if time.time() - inicio > timeout:
            raise RuntimeError(f"{quien} no terminó ({pref.lower()}) en {timeout // 60} minutos.")
        rs = await cli.get(job["fal_status_url"], headers=headers)
        d = rs.json() if rs.status_code == 200 else {}
        st = d.get("status", "")
        if st in ("COMPLETED", "Completed", "succeeded", "OK"):
            break
        if st in ("FAILED", "Error", "CANCELLED"):
            raise RuntimeError(f"{quien} falló ({pref.lower()}): {rs.text[:300]}")
        if st == "IN_QUEUE":
            pos = d.get("queue_position")
            paso = f"{pref}: en la cola de fal" + (f", puesto {pos}" if pos is not None else "") + "…"
        else:
            paso = paso_txt
        await _job_set(jid, {"paso": paso} if paso != ultimo else {})
        ultimo = paso
        await asyncio.sleep(8)
    rr = await cli.get(job["fal_result_url"], headers=headers)
    if rr.status_code != 200:
        raise RuntimeError(f"fal result HTTP {rr.status_code}: {rr.text[:200]}")
    res = rr.json()
    vurl = (res.get("video") or {}).get("url") if isinstance(res.get("video"), dict) else None
    vurl = vurl or res.get("video_url") or res.get("url")
    if not vurl:
        raise RuntimeError(f"{quien} no devolvió video: {json.dumps(res)[:300]}")
    dl = await cli.get(vurl, follow_redirects=True)
    if dl.status_code != 200:
        raise RuntimeError(f"fal descarga HTTP {dl.status_code}")
    destino.write_bytes(dl.content)
    await _job_set(jid, {"fal_status_url": None, "fal_result_url": None})   # ya bajó


async def _video_avatar(cli: httpx.AsyncClient, key: str, jid: str, doc: Dict[str, Any],
                        reel: Dict[str, Any], i: int, reanudar: bool = False) -> float:
    """Escena + audio del tramo → OmniHuman → tramo_i.mp4 (1080x1920, con NUESTRA voz).
    Con `reanudar`, si el trabajo ya había mandado ESTE tramo a fal antes de que el
    server se reiniciara, no lo vuelve a mandar (ni a pagar): retoma la espera."""
    rid = reel["id"]
    t = reel["tramos"][i]
    esc = await kv.get(_k_escena(rid, i))
    if not esc:
        raise RuntimeError(f"El tramo {i + 1} no tiene escena: generala antes.")
    audio = await _asegurar_audio_en_disco(rid, i)
    dur = float(t.get("dur") or _duracion_video(audio) or 5.0)
    if dur > OMNI_MAX_SEG:
        raise RuntimeError(f"El tramo {i + 1} dura {dur:.0f} s: acortá el texto (máximo "
                           f"{OMNI_MAX_SEG} s por tramo de ella).")
    headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
    job = await kv.get(_k_job(jid)) or {}
    retomado = bool(reanudar and job.get("tramo_actual") == i and job.get("fal_status_url")
                    and job.get("fal_result_url"))
    if retomado:
        await _job_set(jid, {"paso": f"Tramo {i + 1}: retomando desde fal lo que ya estaba en marcha…"})
    else:
        await _job_set(jid, {"paso": f"Subiendo la escena y la voz del tramo {i + 1}…",
                             "tramo_actual": i, "fal_status_url": None, "fal_result_url": None})
        img_url = await _fal_subir(cli, key, base64.b64decode(esc), "image/jpeg", f"reel_{rid}_esc{i}.jpg")
        au_url = await _fal_subir(cli, key, audio.read_bytes(), "audio/mpeg", f"reel_{rid}_au{i}.mp3")
        payload = {"image_url": img_url, "audio_url": au_url,
                   "resolution": reel.get("resolucion") if reel.get("resolucion") in RESOLUCIONES else "720p",
                   "turbo_mode": False, "prompt": _prompt_omni(doc, reel)}
        await _fal_enviar(cli, headers, OMNI_MODEL, payload, jid, ("turbo_mode", "prompt", "resolution"))
        job = await kv.get(_k_job(jid)) or {}
    crudo = _dir(rid) / f"omni_{i}.mp4"
    await _fal_esperar_video(
        cli, headers, jid, job, crudo, OMNI_TIMEOUT, f"Tramo {i + 1}",
        f"Tramo {i + 1}: OmniHuman está haciendo hablar a {doc.get('nombre') or 'la protagonista'} ({dur:.0f} s de audio)…",
        "OmniHuman")
    # Normalizado: 1080x1920, 30 fps, y NUESTRA voz (la misma pista que oye la usuaria).
    salida = _dir(rid) / f"tramo_{i}.mp4"
    await _run_latiendo(jid, [
        _ff(), "-y", "-i", str(crudo), "-i", str(audio),
        "-filter_complex", "[0:v]" + _vf_avatar(reel) + "[v]", "-map", "[v]", "-map", "1:a:0",
        *_af_voz(reel), *_ENC_VIDEO, *_ENC_AUDIO, "-t", f"{dur:.2f}",
        "-movflags", "+faststart", str(salida)])
    costo = round(PRECIO_OMNI_SEG * dur, 3)
    await budget_record("reel_omnihuman", OMNI_MODEL, costo, 1,
                        note=f"{doc.get('nombre', '')} reel tramo {i + 1} ({dur:.0f} s)")
    return costo


async def _fotos_para_broll(reel: Dict[str, Any]) -> List[Path]:
    """Las fotos del producto en disco (para los flashes). Si no hay, las escenas."""
    rid = reel["id"]
    out: List[Path] = []
    n = int((reel.get("producto") or {}).get("n_fotos") or 0)
    for k in range(n):
        b = await kv.get(_k_pfoto(rid, k))
        if b:
            p = _dir(rid) / f"pf_{k}.jpg"
            p.write_bytes(base64.b64decode(b))
            out.append(p)
    if not out:
        for i, t in enumerate(reel.get("tramos") or []):
            if t.get("escena"):
                b = await kv.get(_k_escena(rid, i))
                if b:
                    p = _dir(rid) / f"esc_{i}.jpg"
                    p.write_bytes(base64.b64decode(b))
                    out.append(p)
    return out


def _clip_zoom(foto: Path, dur: float, salida: Path, acercar: bool) -> None:
    """Un flash de una foto con zoom lento (Ken Burns) a 1080x1920 y 30 fps."""
    frames = max(15, int(round(dur * 30)))
    paso = 0.22 / max(frames, 1)
    z = f"min(1+{paso:.6f}*on,1.22)" if acercar else f"max(1.22-{paso:.6f}*on,1)"
    vf = (f"scale={ANCHO * 1.2:.0f}:{ALTO * 1.2:.0f}:force_original_aspect_ratio=increase,"
          f"crop={ANCHO * 1.2:.0f}:{ALTO * 1.2:.0f},"
          f"zoompan=z='{z}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={ANCHO}x{ALTO}:fps=30,"
          "format=yuv420p")
    _run([_ff(), "-y", "-loop", "1", "-framerate", "30", "-i", str(foto), "-t", f"{dur:.2f}",
          "-vf", vf, *_ENC_VIDEO, "-an", str(salida)], timeout=300)


def _propio_path(rid: str, uid: str) -> Path:
    return _dir(rid) / f"propio_{uid}.mp4"


def _normalizar_propio(entrada: Path, salida: Path) -> float:
    """Un video real de la usuaria (celular, cualquier formato) → 9:16 recortado al
    centro, 1080x1920, 30 fps, sin audio (la voz de ella va encima), hasta 60 s."""
    vf = ("crop='if(gt(iw/ih,9/16),ih*9/16,iw)':'if(gt(iw/ih,9/16),ih,iw*16/9)',"
          f"scale={ANCHO}:{ALTO},fps=30,format=yuv420p")
    _run([_ff(), "-y", "-i", str(entrada), "-t", str(PROPIO_MAX_SEG), "-vf", vf, "-an",
          *_ENC_VIDEO, "-movflags", "+faststart", str(salida)], timeout=600)
    return round(_duracion_video(salida), 2)


def _foto_9_16(src: Path) -> bytes:
    """La foto del producto recortada al centro a 9:16 y a 1080x1920 (para que el
    clip de IA salga vertical sin deformar)."""
    from PIL import Image
    im = Image.open(src).convert("RGB")
    w, h = im.size
    if w / h > 9 / 16:
        nw = int(h * 9 / 16)
        x = (w - nw) // 2
        im = im.crop((x, 0, x + nw, h))
    else:
        nh = int(w * 16 / 9)
        y = (h - nh) // 2
        im = im.crop((0, y, w, y + nh))
    im = im.resize((ANCHO, ALTO))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def _motor_ia(reel: Dict[str, Any]) -> str:
    return reel.get("motor_ia") if reel.get("motor_ia") in MOTORES_IA else MOTOR_IA_DEFAULT


def _ia_clip_seg(dur: float) -> int:
    """fal saca clips de 5 o 10 s; si el tramo dura más, el clip se repite."""
    return 5 if dur <= 5.5 else 10


def _costo_ia(reel: Dict[str, Any], t: Dict[str, Any]) -> float:
    dur = float(t.get("dur") or len((t.get("texto") or "").split()) / 2.4 or 5)
    return round(PRECIO_SEG.get(_motor_ia(reel), 0.05) * _ia_clip_seg(dur), 3)


def _prompt_ia(reel: Dict[str, Any], t: Dict[str, Any]) -> str:
    prod = (reel.get("producto") or {}).get("titulo") or "the garment"
    muestra = _texto(t.get("muestra"), 200)
    return (f"Product close-up video for a fashion reel: {prod}. "
            + (f"What we see: {muestra}. " if muestra else "")
            + "Slow, smooth handheld push-in and gentle drift over the garment, revealing the "
              "fabric texture, seams and details. Keep the exact same garment, colors and design "
              "as the photo. Natural soft light, shot on a phone. No people, no hands, no text, "
              "no logos.")


async def _clip_ia(cli: httpx.AsyncClient, key: str, jid: str, reel: Dict[str, Any], i: int,
                   foto: Path, reanudar: bool = False) -> float:
    """Un clip image-to-video de la prenda (fal) para el tramo i → ia_{i}.mp4. Con
    `reanudar`, si ese tramo ya estaba en fal antes del reinicio, espera ese resultado."""
    rid = reel["id"]
    t = reel["tramos"][i]
    motor = _motor_ia(reel)
    dur = float(t.get("dur") or 5.0)
    seg = _ia_clip_seg(dur)
    headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
    job = await kv.get(_k_job(jid)) or {}
    retomado = bool(reanudar and job.get("tramo_actual") == i and job.get("fal_status_url")
                    and job.get("fal_result_url"))
    if retomado:
        await _job_set(jid, {"paso": f"Tramo {i + 1}: retomando el clip de IA que ya estaba en fal…"})
    else:
        await _job_set(jid, {"paso": f"Tramo {i + 1}: mandando la foto de la prenda a {MOTORES_IA[motor]}…",
                             "tramo_actual": i, "fal_status_url": None, "fal_result_url": None})
        img_url = await _fal_subir(cli, key, await asyncio.to_thread(_foto_9_16, foto), "image/jpeg",
                                   f"reel_{rid}_ia{i}.jpg")
        payload = {"prompt": _prompt_ia(reel, t), "image_url": img_url,
                   "resolution": RESOLUCION_FAL.get(motor, "720p"), "duration": str(seg)}
        await _fal_enviar(cli, headers, FAL_MODELS[motor], payload, jid, ("resolution", "duration"))
        job = await kv.get(_k_job(jid)) or {}
    destino = _dir(rid) / f"ia_{i}.mp4"
    await _fal_esperar_video(cli, headers, jid, job, destino, IA_TIMEOUT, f"Tramo {i + 1}",
                             f"Tramo {i + 1}: {MOTORES_IA[motor]} está filmando la prenda ({seg} s)…",
                             MOTORES_IA[motor])
    costo = round(PRECIO_SEG.get(motor, 0.05) * seg, 3)
    await budget_record("reel_ia", FAL_MODELS[motor], costo, 1,
                        note=f"reel tramo {i + 1} clip IA ({seg} s)")
    return costo


async def _video_producto(reel: Dict[str, Any], i: int, fotos: List[Path], desde: int,
                          jid: Optional[str] = None, cli: Optional[httpx.AsyncClient] = None,
                          key: str = "", reanudar: bool = False) -> Tuple[int, float]:
    """Tomas de producto con la voz del tramo: los VIDEOS PROPIOS de la usuaria si los
    subió (recortados al largo de la voz, repartido entre ellos); si no, un CLIP DE IA
    de la prenda si el tramo lo pide; y si no, flashes de las fotos del producto
    (2,5 a 3,5 s cada uno). Devuelve (índice de la próxima foto a usar, costo)."""
    rid = reel["id"]
    t = reel["tramos"][i]
    audio = await _asegurar_audio_en_disco(rid, i)
    dur = float(t.get("dur") or _duracion_video(audio) or 5.0)
    d = _dir(rid)
    salida = d / f"tramo_{i}.mp4"
    costo = 0.0
    propios = [p for p in (t.get("propios") or []) if _propio_path(rid, str(p.get("id"))).exists()]
    fuentes = [_propio_path(rid, str(p["id"])) for p in propios]
    if not fuentes and t.get("ia") and fotos and cli is not None and key:
        clip_ia = d / f"ia_{i}.mp4"
        if not (reanudar and clip_ia.exists() and _duracion_video(clip_ia) > 0):
            costo = await _clip_ia(cli, key, jid or "", reel, i, fotos[desde % len(fotos)], reanudar)
        fuentes = [clip_ia]
        desde += 1
    if fuentes:
        cada = dur / len(fuentes)
        lineas = []
        for k, fuente in enumerate(fuentes):
            clip = d / f"propio_{i}_{k}_cut.mp4"
            # Si el video (o el clip de IA) es más corto que su parte, se repite hasta llenarla.
            await _run_latiendo(jid, [
                _ff(), "-y", "-stream_loop", "-1", "-i", str(fuente), "-t", f"{cada:.2f}",
                "-an", *_ENC_VIDEO, str(clip)], 600)
            lineas.append(f"file '{clip.name}'")
        lista = d / f"lista_{i}.txt"
        lista.write_text("\n".join(lineas) + "\n", encoding="utf-8")
        await _run_latiendo(jid, [
            _ff(), "-y", "-f", "concat", "-safe", "0", "-i", lista.name, "-i", audio.name,
            "-map", "0:v", "-map", "1:a", "-c:v", "copy", *_af_voz(reel), *_ENC_AUDIO,
            "-t", f"{dur:.2f}", "-movflags", "+faststart", salida.name], cwd=d)
        return desde, costo
    if not fotos:
        # Sin fotos: un fondo oscuro con la voz (los subtítulos llevan el texto).
        await _run_latiendo(jid, [
            _ff(), "-y", "-f", "lavfi", "-i", f"color=c=0x131218:s={ANCHO}x{ALTO}:r=30",
            "-i", str(audio), "-map", "0:v", "-map", "1:a", "-t", f"{dur:.2f}",
            *_af_voz(reel), *_ENC_VIDEO, *_ENC_AUDIO, "-movflags", "+faststart", str(salida)])
        return desde, costo
    n = max(1, min(len(fotos), int(round(dur / 3.0)) or 1))
    cada = dur / n
    lista = d / f"lista_{i}.txt"
    lineas = []
    for k in range(n):
        foto = fotos[(desde + k) % len(fotos)]
        clip = d / f"flash_{i}_{k}.mp4"
        await asyncio.to_thread(_clip_zoom, foto, cada, clip, (desde + k) % 2 == 0)
        if jid:
            await _job_set(jid, {})
        lineas.append(f"file '{clip.name}'")
    lista.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    await _run_latiendo(jid, [
        _ff(), "-y", "-f", "concat", "-safe", "0", "-i", lista.name, "-i", audio.name,
        "-map", "0:v", "-map", "1:a", "-c:v", "copy", *_af_voz(reel), *_ENC_AUDIO,
        "-t", f"{dur:.2f}", "-movflags", "+faststart", salida.name], cwd=d)
    return desde + n, costo


def _srt_tiempo(seg: float) -> str:
    ms = int(round(seg * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _armar_srt(reel: Dict[str, Any], duraciones: List[float]) -> str:
    """Subtítulos por frases cortas (hasta 5 palabras), repartidos por cantidad de
    palabras dentro de cada tramo."""
    out: List[str] = []
    n = 1
    t0 = 0.0
    for t, dur in zip(reel["tramos"], duraciones):
        palabras = (t.get("texto") or "").split()
        if not palabras or dur <= 0:
            t0 += max(dur, 0)
            continue
        trozos: List[List[str]] = []
        actual: List[str] = []
        for w in palabras:
            actual.append(w)
            if len(actual) >= 5 or w.endswith((".", ",", "!", "?", ":", ";")) and len(actual) >= 3:
                trozos.append(actual)
                actual = []
        if actual:
            if trozos and len(actual) == 1:
                trozos[-1] += actual
            else:
                trozos.append(actual)
        total = sum(len(x) for x in trozos)
        cur = t0
        for tr in trozos:
            d = dur * len(tr) / total
            out.append(f"{n}\n{_srt_tiempo(cur)} --> {_srt_tiempo(cur + d - 0.02)}\n{' '.join(tr)}\n")
            n += 1
            cur += d
        t0 += dur
    return "\n".join(out) + "\n"


_ESTILO_SUB = ("FontName=DejaVu Sans,Bold=1,FontSize=11,PrimaryColour=&H00FFFFFF,"
               "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,Alignment=2,"
               "MarginV=44,MarginL=36,MarginR=36")


def _ass_tiempo(seg: float) -> str:
    cs = int(round(max(seg, 0) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s_, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s_:02d}.{cs:02d}"


def _ass_texto(t: str) -> str:
    return (t or "").replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ").strip()


# Estilos en unidades de 1080x1920. Sub = el mismo de siempre (abajo, borde negro);
# Sticker = caja negra translúcida arriba (precio y talles); CTA = caja clara arriba.
_ASS_CABECERA = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,DejaVu Sans,72,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,7,0,2,100,100,290,1
Style: Sticker,DejaVu Sans,58,&H00FFFFFF,&H00FFFFFF,&H00000000,&H66000000,-1,0,0,0,100,100,1,0,3,18,0,8,120,120,170,1
Style: CTA,DejaVu Sans,62,&H00141414,&H00FFFFFF,&H00FFFFFF,&H00F2F2F2,-1,0,0,0,100,100,1,0,3,20,0,8,120,120,170,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _trozos_sub(texto: str) -> List[List[str]]:
    """Frases cortas (hasta 5 palabras) para los subtítulos."""
    palabras = (texto or "").split()
    trozos: List[List[str]] = []
    actual: List[str] = []
    for w in palabras:
        actual.append(w)
        if len(actual) >= 5 or w.endswith((".", ",", "!", "?", ":", ";")) and len(actual) >= 3:
            trozos.append(actual)
            actual = []
    if actual:
        if trozos and len(actual) == 1:
            trozos[-1] += actual
        else:
            trozos.append(actual)
    return trozos


def _precio_sticker(precio: str) -> str:
    """'ARS 24900' / '24900' / '$24.900' / 'USD 19.5' → '$ 24.900' / 'US$ 19,50'.
    Si no se entiende el número, va tal cual."""
    m = re.search(r"\d[\d.,]*", precio)
    if not m:
        return precio
    crudo = m.group(0)
    moneda = "US$" if re.search(r"USD|US\$|U\$S", precio.upper()) else "$"
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", crudo):
        entero, dec = int(re.sub(r"[.,]", "", crudo)), ""
    elif re.fullmatch(r"\d+[.,]\d{1,2}", crudo):
        a, b = re.split(r"[.,]", crudo)
        entero, dec = int(a), b.ljust(2, "0")
    elif re.fullmatch(r"\d+", crudo):
        entero, dec = int(crudo), ""
    else:
        return precio
    txt = f"{entero:,}".replace(",", ".")
    return f"{moneda} {txt}" + (f",{dec}" if dec else "")


def _armar_ass(reel: Dict[str, Any], duraciones: List[float]) -> str:
    """Un solo ASS con los subtítulos (si están activados), el precio y los talles
    sobre los tramos de producto, y el llamado a la acción sobre el último tramo."""
    ev: List[str] = []
    prod = reel.get("producto") or {}
    sticker: List[str] = []
    if reel.get("mostrar_precio", True) and _texto(prod.get("precio"), 40):
        sticker.append(_precio_sticker(_texto(prod.get("precio"), 40)))
    if reel.get("mostrar_talles", True) and _texto(prod.get("talles"), 60):
        sticker.append(f"Talles {_texto(prod.get('talles'), 60)}")
    cta = _texto(reel.get("cta"), 60)
    t0 = 0.0
    n_tramos = len(reel["tramos"])
    for k, (t, dur) in enumerate(zip(reel["tramos"], duraciones)):
        if dur <= 0:
            continue
        t1 = t0 + dur
        if reel.get("subtitulos", True):
            trozos = _trozos_sub(t.get("texto") or "")
            total = sum(len(x) for x in trozos) or 1
            cur = t0
            for tr in trozos:
                d = dur * len(tr) / total
                ev.append(f"Dialogue: 0,{_ass_tiempo(cur)},{_ass_tiempo(cur + d - 0.02)},Sub,,0,0,0,,{_ass_texto(' '.join(tr))}")
                cur += d
        if t.get("tipo") == "producto" and sticker:
            ev.append(f"Dialogue: 1,{_ass_tiempo(t0)},{_ass_tiempo(t1 - 0.02)},Sticker,,0,0,0,,"
                      "{\\fad(200,200)}" + _ass_texto("\\N".join(sticker)))
        if k == n_tramos - 1 and cta:
            ev.append(f"Dialogue: 1,{_ass_tiempo(t0 + 0.4)},{_ass_tiempo(t1)},CTA,,0,0,0,,"
                      "{\\fad(250,0)}" + _ass_texto(cta))
        t0 = t1
    return _ASS_CABECERA + "\n".join(ev) + "\n"


def _musica_modo(reel: Dict[str, Any]) -> str:
    return reel.get("musica_modo") if reel.get("musica_modo") in MUSICA_MODOS else "encima"


async def _musica_en_disco(reel: Dict[str, Any], total: float,
                           jid: Optional[str] = None) -> Optional[Path]:
    """La pista lista para mezclar: arrancando en el segundo que eligió la usuaria, repetida
    hasta cubrir el reel, con el color del modo y el fade final ya aplicados."""
    mid = _texto(reel.get("musica"), 16)
    if not mid:
        return None
    b64 = await kv.get(_k_musica(mid))
    if not b64:
        return None
    d = _dir(reel["id"])
    entera = d / "musica_entera.mp3"
    entera.write_bytes(base64.b64decode(b64))
    largo = _duracion_video(entera)
    desde = max(0.0, float(reel.get("musica_desde") or 0))
    if largo and desde >= largo - 1:
        desde = 0.0                       # se pasó del final: vuelve al principio
    # El corte va a un archivo propio y después se repite con el demuxer concat: mezclar
    # -ss con -stream_loop deja el salto sólo en la primera vuelta y el -t cuenta mal.
    trozo = d / "musica_trozo.mp3"
    await _run_latiendo(jid, [
        _ff(), "-y", "-ss", f"{desde:.2f}", "-i", str(entera), "-ar", "44100",
        "-b:a", "128k", str(trozo)], 300)
    veces = max(1, math.ceil(total / max(_duracion_video(trozo), 0.5)))
    lista = d / "musica_lista.txt"
    lista.write_text("".join(f"file '{trozo.name}'\n" for _ in range(veces)), encoding="utf-8")
    af = [_AF_MUSICA_LOCAL] if _musica_modo(reel) == "local" else []
    af.append(f"afade=t=out:st={max(0.0, total - 2.0):.2f}:d=2")
    salida = d / "musica.mp3"
    await _run_latiendo(jid, [
        _ff(), "-y", "-f", "concat", "-safe", "0", "-i", lista.name, "-t", f"{total:.2f}",
        "-af", ",".join(af), "-ar", "44100", "-b:a", "128k", salida.name], 300, cwd=d)
    return salida


async def _armar_reel(reel: Dict[str, Any], jid: Optional[str] = None) -> Path:
    d = _dir(reel["id"])
    tramos = reel["tramos"]
    duraciones = []
    lineas = []
    for i, t in enumerate(tramos):
        p = d / f"tramo_{i}.mp4"
        if not p.exists():
            raise RuntimeError(f"Falta el video del tramo {i + 1}.")
        duraciones.append(float(t.get("dur") or _duracion_video(p) or 0))
        lineas.append(f"file 'tramo_{i}.mp4'")
    (d / "final.txt").write_text("\n".join(lineas) + "\n", encoding="utf-8")
    total = sum(duraciones)
    ass = _armar_ass(reel, duraciones)
    (d / "reel.ass").write_text(ass, encoding="utf-8")
    (d / "reel.srt").write_text(_armar_srt(reel, duraciones), encoding="utf-8")   # por si la quieren aparte
    vf = ("[0:v]subtitles=reel.ass,format=yuv420p[v]" if "Dialogue:" in ass
          else "[0:v]format=yuv420p[v]")
    entradas = ["-f", "concat", "-safe", "0", "-i", "final.txt"]
    vol = reel.get("musica_vol")
    vol = (MUSICA_VOL_LOCAL if _musica_modo(reel) == "local" else MUSICA_VOL_DEFAULT) if vol is None else vol
    vol = max(0, min(100, int(vol)))
    musica = await _musica_en_disco(reel, total, jid) if vol > 0 else None
    if musica:
        # La pista ya viene cortada, en loop y con fade; acá sólo va el volumen. La voz
        # manda: amix sin normalizar para que no le baje el nivel.
        entradas += ["-i", musica.name]
        vf += (f";[1:a]volume={vol / 100:.2f}[m];"
               f"[0:a][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]")
        mapa = ["-map", "[v]", "-map", "[a]"]
    else:
        mapa = ["-map", "[v]", "-map", "0:a"]
    await _run_latiendo(jid, [
        _ff(), "-y", *entradas, "-filter_complex", vf, *mapa, "-t", f"{total:.2f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-r", "30",
        *_ENC_AUDIO, "-movflags", "+faststart", "reel.mp4"], timeout=900, cwd=d)
    return d / "reel.mp4"


async def _procesar_reel(jid: str, rid: str, sub: Optional[str], solo: Optional[int] = None,
                         reanudar: bool = False) -> None:
    """Corre tramo por tramo y guarda cada uno apenas sale: si el server se reinicia,
    se retoma desde el tramo que faltaba (y si ese tramo ya estaba en fal, se espera
    ese resultado sin volver a pagarlo)."""
    set_current_sub(sub)
    try:
        reel = await _reel(rid)
        doc = await _doc(reel["pid"])
        key = await _fal_key()
        if not key:
            raise RuntimeError("Falta la API key de fal (Fotos → Ajustes → Motor FLUX, o FAL_KEY en Railway).")
        await _job_set(jid, {"estado": "generando",
                             "paso": ("Retomando después del reinicio…" if reanudar
                                      else "Preparando las fotos del producto…")})
        costo_total = float(reel.get("costo_total") or 0)
        fotos = await _fotos_para_broll(reel)
        desde = 0
        async with httpx.AsyncClient(timeout=180) as cli:
            for i, t in enumerate(reel["tramos"]):
                p = _dir(rid) / f"tramo_{i}.mp4"
                if t.get("video") and p.exists() and (solo is None or i != solo):
                    if t.get("tipo") == "producto":
                        desde += max(1, int(round(float(t.get("dur") or 3) / 3.0)) or 1)
                    continue
                if t.get("tipo") == "avatar":
                    await _job_set(jid, {"paso": f"Tramo {i + 1} de {len(reel['tramos'])}: ella hablando…"})
                    costo_total += await _video_avatar(cli, key, jid, doc, reel, i, reanudar=reanudar)
                else:
                    await _job_set(jid, {"paso": f"Tramo {i + 1} de {len(reel['tramos'])}: tomas del producto…"})
                    desde, c_ia = await _video_producto(reel, i, fotos, desde, jid, cli, key, reanudar)
                    costo_total += c_ia
                t["video"] = True
                reel["costo_total"] = round(costo_total, 3)
                await _guardar_reel(reel)
        await _job_set(jid, {"paso": "Pegando los tramos, la música y los textos…"})
        salida = await _armar_reel(reel, jid)
        reel["video"] = True
        reel["estado"] = "listo"
        reel["dur_total"] = round(sum(float(t.get("dur") or 0) for t in reel["tramos"]), 1)
        reel["costo_total"] = round(costo_total, 3)
        await _guardar_reel(reel)
        link = await _guardar_en_drive(f"reel-{_slug(doc.get('nombre', ''))}-{rid}.mp4",
                                       salida.read_bytes(), "video/mp4")
        if link:
            reel["drive"] = link
            await _guardar_reel(reel)
        await _job_set(jid, {"estado": "listo", "paso": "", "drive": link,
                             "costo": round(costo_total, 3)})
    except Exception as e:
        try:
            reel = await _reel(rid)
            reel["estado"] = "error"
            reel["error"] = str(e)[:600]
            await _guardar_reel(reel)
        except Exception:
            pass
        await _job_set(jid, {"estado": "error", "error": str(e)[:600]})


def _estimado_seg(reel: Dict[str, Any], solo: Optional[int] = None) -> int:
    seg = 40
    for i, t in enumerate(reel["tramos"]):
        if solo is not None and i != solo:
            continue
        if t.get("tipo") == "avatar":
            seg += 60 + int(12 * float(t.get("dur") or 6))
        elif t.get("ia") and not t.get("propios"):
            seg += 120
        else:
            seg += 15
    return seg


def _costo_estimado(reel: Dict[str, Any], solo: Optional[int] = None) -> float:
    c = 0.0
    for i, t in enumerate(reel["tramos"]):
        if solo is not None and i != solo:
            continue
        if t.get("video") and solo is None:
            continue
        if t.get("tipo") == "avatar":
            c += PRECIO_OMNI_SEG * float(t.get("dur") or 6)
        elif t.get("ia") and not t.get("propios"):
            c += _costo_ia(reel, t)
    return round(c, 2)


# ─────────────────────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────────────────────

# Igual que Personajes: cada pedido queda atado a la cuenta logueada (los datos de
# cada usuaria viven bajo su propio prefijo en el KV).
router = APIRouter(dependencies=[Depends(_bind)])


@router.get(ROUTE_PREFIX, response_class=HTMLResponse)
async def ui() -> HTMLResponse:
    return HTMLResponse(HTML_PAGE, headers={"Cache-Control": "no-store, no-cache, max-age=0, must-revalidate",
                                            "Pragma": "no-cache"})


@router.get(API + "/health")
async def api_health() -> Dict[str, Any]:
    return {"ok": True, "version": VERSION, "omni": OMNI_MODEL, "ffmpeg": bool(_ffmpeg_bin())}


@router.get(API + "/config")
async def api_config() -> Dict[str, Any]:
    return {"tonos": TONOS, "ambientes": {k: v.split(":")[0] for k, v in AMBIENTES.items()},
            "looks": LOOKS, "camaras": CAMARAS, "voces": VOCES,
            "energias": {k: {"nombre": v["nombre"], "palabras_seg": v["palabras_seg"]} for k, v in ENERGIAS_VOZ.items()},
            "energia_default": ENERGIA_DEFAULT, "encuadres": ENCUADRES_NOMBRES, "duraciones": DURACIONES,
            "motores_ia": {k: {"nombre": v, "precio_seg": PRECIO_SEG.get(k, 0.05)} for k, v in MOTORES_IA.items()},
            "motor_ia_default": MOTOR_IA_DEFAULT, "plantillas": PLANTILLAS, "cta_default": CTA_DEFAULT,
            "musica_vol_default": MUSICA_VOL_DEFAULT, "max_pistas": MAX_PISTAS,
            "musica_modos": MUSICA_MODOS, "musica_vol_local": MUSICA_VOL_LOCAL, "resoluciones": RESOLUCIONES, "precio_omni_seg": PRECIO_OMNI_SEG,
            "max_tramos": MAX_TRAMOS, "personajes": PJ_PREFIX, "personajes_api": PJ_API}


_TEXTO_PRUEBA = ("Bueno chicos, les tengo que mostrar esto porque es literal lo más lindo que "
                 "entró en la semana. Miren la tela, es un montón.")


@router.post(API + "/voz_prueba")
async def api_voz_prueba(payload: Dict[str, Any] = Body(default={})):
    """Un audio corto con la voz, el tono y la energía elegidos, para escuchar antes de
    grabar todo el guion. Cuesta lo que un mensaje de voz."""
    pid = _texto(payload.get("pid"), 20)
    doc = await _doc(pid) if pid else {"nombre": "", "genero": "mujer", "tono": ""}
    falso = {"tono": payload.get("tono") if payload.get("tono") in TONOS else "chetita",
             "voz": payload.get("voz") if _voz_valida(payload.get("voz")) else "",
             "voz_energia": payload.get("voz_energia") if payload.get("voz_energia") in ENERGIAS_VOZ else ENERGIA_DEFAULT}
    texto = _texto(payload.get("texto"), 300) or _TEXTO_PRUEBA
    await _cobrar(COSTO_TTS)
    mp3 = await _tts_mp3(texto, _voz_reel(doc, falso), doc, instruccion=_instruccion_voz(doc, falso))
    await budget_record("reel_voz", "mp3", COSTO_TTS, 1, note="prueba de voz de reels")
    af = ENERGIAS_VOZ[_energia(falso)]["af"]
    if af:
        try:
            mp3 = await asyncio.to_thread(_tratar_voz, mp3, af, REEL_DIR / "pruebas",
                                          _uuid.uuid4().hex[:8])
        except Exception as e:
            print(f"[reels] no pude tratar la voz de prueba: {e}")
    return Response(content=mp3, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


@router.post(API + "/lugar_preguntas")
async def api_lugar_preguntas(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Gemini pregunta cómo es el lugar (dónde va el producto, qué se ve atrás, la luz).
    No necesita un reel armado: sirve desde el paso 1."""
    return {"preguntas": await _preguntas_lugar(_texto(payload.get("ambiente"), 20),
                                                _texto(payload.get("lugar"), 500),
                                                payload.get("producto") or {})}


@router.post(API + "/leer_link")
async def api_leer_link(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    url = _texto(payload.get("url"), 500)
    if not url:
        raise HTTPException(400, "Pegá el link del producto.")
    info = await _leer_link(url)
    if not info["titulo"] and not info["fotos"]:
        raise HTTPException(422, "No encontré datos del producto en ese link. Cargalos a mano.")
    return {"producto": {k: info[k] for k in ("titulo", "descripcion", "precio", "talles", "colores", "fuente")},
            "fotos": ["data:image/jpeg;base64," + b for b in info["fotos"]]}


@router.get(API + "/{pid}/lista")
async def api_lista(pid: str) -> Dict[str, Any]:
    await _doc(pid)
    out = []
    for rid in await _idx(pid):
        r = await kv.get(_k_reel(rid))
        if isinstance(r, dict):
            out.append({k: r.get(k) for k in ("id", "titulo", "estado", "creado", "dur_total",
                                               "costo_total", "video")}
                       | {"producto": (r.get("producto") or {}).get("titulo", "")})
    return {"reels": out}


@router.post(API + "/{pid}/nuevo")
async def api_nuevo(pid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    await _doc(pid)          # valida que el personaje exista
    ids = await _idx(pid)
    if len(ids) >= MAX_REELS:
        raise HTTPException(400, f"Hasta {MAX_REELS} reels por personaje: borrá alguno.")
    prod_in = payload.get("producto") or {}
    producto = {k: _texto(prod_in.get(k), 1500 if k == "descripcion" else 160)
                for k in ("titulo", "descripcion", "precio", "talles", "colores", "notas")}
    if not producto["titulo"]:
        raise HTTPException(400, "El producto necesita al menos un título.")
    rid = _uuid.uuid4().hex[:10]
    fotos = [_strip_data_url(str(f)) for f in (payload.get("fotos") or []) if f][:MAX_FOTOS_PRODUCTO]
    n = 0
    for f in fotos:
        try:
            b64 = _compress_ref(base64.b64decode(f), max_dim=1600, q=90)
        except Exception:
            continue
        await kv.set(_k_pfoto(rid, n), b64)
        n += 1
    producto["n_fotos"] = n
    reel = {
        "id": rid, "pid": pid, "creado": _ahora(), "titulo": _texto(payload.get("titulo"), 80) or producto["titulo"][:60],
        "fuente_url": _texto(payload.get("fuente_url"), 500), "producto": producto,
        "tono": payload.get("tono") if payload.get("tono") in TONOS else "chetita",
        "ambiente": payload.get("ambiente") if payload.get("ambiente") in AMBIENTES else "local",
        "outfit": _texto(payload.get("outfit"), 200),
        "lugar": _texto(payload.get("lugar"), 500),
        "continuidad": payload.get("continuidad") is not False,
        "camara": payload.get("camara") if payload.get("camara") in CAMARAS else "mano",
        "voz_real": payload.get("voz_real") is not False,
        "voz_energia": payload.get("voz_energia") if payload.get("voz_energia") in ENERGIAS_VOZ else ENERGIA_DEFAULT,
        "mic": payload.get("mic") is not False,
        "look": payload.get("look") if payload.get("look") in LOOKS else "celular",
        "voz": payload.get("voz") if _voz_valida(payload.get("voz")) else "",
        "plantilla": payload.get("plantilla") if payload.get("plantilla") in PLANTILLAS else "",
        "motor_ia": payload.get("motor_ia") if payload.get("motor_ia") in MOTORES_IA else MOTOR_IA_DEFAULT,
        "musica": _texto(payload.get("musica"), 16), "musica_vol": MUSICA_VOL_DEFAULT,
        "musica_modo": payload.get("musica_modo") if payload.get("musica_modo") in MUSICA_MODOS else "encima",
        "musica_desde": 0.0,
        "mostrar_precio": payload.get("mostrar_precio") is not False,
        "mostrar_talles": payload.get("mostrar_talles") is not False,
        "cta": _texto(payload.get("cta"), 60) if "cta" in payload else CTA_DEFAULT,
        "duracion": int(payload.get("duracion")) if payload.get("duracion") in DURACIONES else 35,
        "resolucion": payload.get("resolucion") if payload.get("resolucion") in RESOLUCIONES else "720p",
        "subtitulos": bool(payload.get("subtitulos", True)),
        "tramos": [], "estado": "borrador", "video": False, "costo_total": 0.0,
    }
    await _guardar_reel(reel)
    await kv.set(_k_idx(pid), ([rid] + [x for x in ids if x != rid])[:MAX_REELS])
    return {"reel": _publico(reel)}


@router.get(API + "/reel/{rid}")
async def api_reel(rid: str) -> Dict[str, Any]:
    reel = await _reel(rid)
    if reel.get("estado") == "generando" and reel.get("job"):
        # Al abrir un reel que quedó "generando", el vigilante lo retoma si hace falta.
        job = await kv.get(_k_job(str(reel["job"])))
        if job:
            await _vigilar_job(job)
    return {"reel": _publico(reel), "costo_estimado": _costo_estimado(reel)}


@router.delete(API + "/reel/{rid}")
async def api_borrar(rid: str) -> Dict[str, Any]:
    reel = await _reel(rid)
    pid = reel["pid"]
    await kv.set(_k_idx(pid), [x for x in await _idx(pid) if x != rid])
    await kv.delete(_k_reel(rid))
    for i in range(len(reel.get("tramos") or [])):
        await kv.delete(_k_escena(rid, i))
        await kv.delete(_k_audio(rid, i))
    for n in range(int((reel.get("producto") or {}).get("n_fotos") or 0)):
        await kv.delete(_k_pfoto(rid, n))
    return {"ok": True}


@router.post(API + "/reel/{rid}/guion")
async def api_guion(rid: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    reel = await _reel(rid)
    doc = await _doc(reel["pid"])
    _aplicar_opciones(reel, payload)
    if "notas" in payload:
        reel["producto"]["notas"] = _texto(payload["notas"], 400)
    g = await _escribir_guion(doc, reel)
    reel["titulo"] = g["titulo"]
    reel["tramos"] = g["tramos"]
    reel["estado"] = "borrador"
    reel["video"] = False
    await _guardar_reel(reel)
    return {"reel": _publico(reel)}


@router.post(API + "/reel/{rid}/tramos")
async def api_tramos(rid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Guarda el guion corregido. Un tramo cuyo texto cambió pierde su voz y su video."""
    reel = await _reel(rid)
    nuevos: List[Dict[str, Any]] = []
    viejos = reel.get("tramos") or []
    for k, t in enumerate((payload.get("tramos") or [])[:MAX_TRAMOS]):
        if not isinstance(t, dict):
            continue
        texto = _texto(t.get("texto"), 400)
        if not texto:
            continue
        nt = _tramo_nuevo(str(t.get("tipo") or "avatar"), texto, t.get("muestra"))
        prev = viejos[k] if k < len(viejos) else None
        if prev and prev.get("texto") == texto and prev.get("tipo") == nt["tipo"]:
            for kk in ("dur", "audio", "escena", "video"):
                nt[kk] = prev.get(kk, nt[kk])
        elif prev and prev.get("tipo") == nt["tipo"] == "avatar":
            nt["escena"] = prev.get("escena", False)      # la escena sirve igual
        if prev and nt["tipo"] == "avatar":
            nt["detalle"] = _texto(prev.get("detalle"), 500)   # los detalles de la escena se quedan
            nt["encuadre"] = prev.get("encuadre", "")
        if nt["tipo"] == "producto":
            nt["ia"] = bool(t.get("ia"))
            if prev:
                nt["propios"] = list(prev.get("propios") or [])   # los videos propios se quedan
                if (nt["propios"] and prev.get("texto") != texto) or bool(prev.get("ia")) != nt["ia"]:
                    nt["video"] = False
        nuevos.append(nt)
    if len(nuevos) < 1:
        raise HTTPException(400, "El guion necesita al menos un tramo con texto.")
    reel["tramos"] = nuevos
    if "titulo" in payload:
        reel["titulo"] = _texto(payload["titulo"], 80) or reel.get("titulo")
    _aplicar_opciones(reel, payload)
    for k in ("resolucion", "subtitulos"):
        if k in payload:
            reel[k] = payload[k] if (k != "resolucion" or payload[k] in RESOLUCIONES) else reel.get(k)
    reel["video"] = False
    reel["estado"] = "borrador"
    await _guardar_reel(reel)
    return {"reel": _publico(reel)}


@router.post(API + "/reel/{rid}/voces")
async def api_voces(rid: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Voz de cada tramo (o de uno solo con 'tramo'). Devuelve las duraciones."""
    reel = await _reel(rid)
    doc = await _doc(reel["pid"])
    if not reel.get("tramos"):
        raise HTTPException(400, "Primero escribí el guion.")
    _aplicar_opciones(reel, payload)
    solo = payload.get("tramo")
    for i, t in enumerate(reel["tramos"]):
        if solo is not None and int(solo) != i:
            continue
        if t.get("audio") and solo is None and not payload.get("todas"):
            continue
        await _generar_voz(doc, reel, i)
        await _guardar_reel(reel)
    reel["video"] = False
    reel["estado"] = "borrador"
    await _guardar_reel(reel)
    return {"reel": _publico(reel), "dur_total": round(sum(float(t.get("dur") or 0) for t in reel["tramos"]), 1)}


@router.get(API + "/reel/{rid}/audio/{i}")
async def api_audio(rid: str, i: int):
    reel = await _reel(rid)
    if i < 0 or i >= len(reel.get("tramos") or []):
        raise HTTPException(404, "Ese tramo no existe.")
    b64 = await kv.get(_k_audio(rid, i))
    if not b64:
        raise HTTPException(404, "Ese tramo todavía no tiene voz.")
    return Response(content=base64.b64decode(b64), media_type="audio/mpeg",
                    headers={"Cache-Control": "no-store"})


def _aplicar_detalle(t: Dict[str, Any], payload: Dict[str, Any]) -> None:
    if "detalle" in payload:
        t["detalle"] = _texto(payload["detalle"], 500)
    if "encuadre" in payload:
        e = payload["encuadre"]
        t["encuadre"] = e if isinstance(e, int) and 0 <= e < len(ENCUADRES_NOMBRES) else ""


def _tramo_avatar(reel: Dict[str, Any], i: int) -> Dict[str, Any]:
    if i < 0 or i >= len(reel.get("tramos") or []):
        raise HTTPException(404, "Ese tramo no existe.")
    if reel["tramos"][i].get("tipo") != "avatar":
        raise HTTPException(400, "Las escenas son sólo para los tramos en los que ella habla.")
    return reel["tramos"][i]


@router.post(API + "/reel/{rid}/escena/{i}/detalle")
async def api_escena_detalle(rid: str, i: int, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Guarda los detalles y el encuadre de esa escena sin generar nada."""
    reel = await _reel(rid)
    _aplicar_detalle(_tramo_avatar(reel, i), payload)
    await _guardar_reel(reel)
    return {"reel": _publico(reel)}


@router.post(API + "/reel/{rid}/escena/{i}/preguntas")
async def api_escena_preguntas(rid: str, i: int, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Gemini pregunta 3 o 4 aclaraciones sobre esa escena, con opciones."""
    reel = await _reel(rid)
    t = _tramo_avatar(reel, i)
    _aplicar_opciones(reel, payload)
    _aplicar_detalle(t, payload)
    doc = await _doc(reel["pid"])
    preguntas = await _preguntas_escena(doc, reel, i, bool(await _ancla_escena(reel, i)))
    await _guardar_reel(reel)
    return {"preguntas": preguntas, "reel": _publico(reel)}


@router.post(API + "/reel/{rid}/escena/{i}")
async def api_escena(rid: str, i: int, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Genera (o sube, con 'imagen') la escena de ella para el tramo i."""
    reel = await _reel(rid)
    if i < 0 or i >= len(reel.get("tramos") or []):
        raise HTTPException(404, "Ese tramo no existe.")
    if reel["tramos"][i].get("tipo") != "avatar":
        raise HTTPException(400, "Las escenas son sólo para los tramos en los que ella habla.")
    doc = await _doc(reel["pid"])
    if payload.get("imagen"):
        try:
            b64 = _compress_ref(base64.b64decode(_strip_data_url(str(payload["imagen"]))), max_dim=1920, q=92)
        except Exception:
            raise HTTPException(400, "No pude leer la imagen.")
        await kv.set(_k_escena(rid, i), b64)
        reel["tramos"][i]["escena"] = True
        reel["tramos"][i]["video"] = False
    else:
        _aplicar_opciones(reel, payload)
        _aplicar_detalle(reel["tramos"][i], payload)
        b64 = await _generar_escena(doc, reel, i)
    reel["video"] = False
    reel["estado"] = "borrador"
    await _guardar_reel(reel)
    return {"reel": _publico(reel), "src": "data:image/jpeg;base64," + b64}


@router.get(API + "/reel/{rid}/escena/{i}")
async def api_escena_img(rid: str, i: int):
    await _reel(rid)
    b64 = await kv.get(_k_escena(rid, i))
    if not b64:
        raise HTTPException(404, "Ese tramo todavía no tiene escena.")
    return Response(content=base64.b64decode(b64), media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@router.get(API + "/reel/{rid}/foto/{n}")
async def api_pfoto(rid: str, n: int):
    await _reel(rid)
    b64 = await kv.get(_k_pfoto(rid, n))
    if not b64:
        raise HTTPException(404, "No hay esa foto.")
    return Response(content=base64.b64decode(b64), media_type="image/jpeg",
                    headers={"Cache-Control": "max-age=300"})


def _normalizar_pista(entrada: Path, salida: Path) -> float:
    """Cualquier audio → mp3 128k, hasta 5 minutos."""
    _run([_ff(), "-y", "-i", str(entrada), "-t", str(PISTA_MAX_SEG), "-vn", "-ac", "2", "-ar", "44100",
          "-b:a", "128k", str(salida)], timeout=300)
    return round(_duracion_video(salida), 2)


@router.get(API + "/musica")
async def api_musica_lista() -> Dict[str, Any]:
    return {"pistas": await _pistas(), "max": MAX_PISTAS}


@router.post(API + "/musica")
async def api_musica_subir(audio: UploadFile = File(...)) -> Dict[str, Any]:
    """Sube una pista a la biblioteca de música de la cuenta (vale para todos los reels)."""
    pistas = await _pistas()
    if len(pistas) >= MAX_PISTAS:
        raise HTTPException(400, f"Hasta {MAX_PISTAS} pistas: borrá alguna.")
    raw = await audio.read()
    if not raw:
        raise HTTPException(400, "El archivo vino vacío.")
    if len(raw) > MAX_PISTA_MB * 1024 * 1024:
        raise HTTPException(400, f"La pista pesa más de {MAX_PISTA_MB} MB.")
    mid = _uuid.uuid4().hex[:10]
    REEL_DIR.mkdir(parents=True, exist_ok=True)
    crudo = REEL_DIR / f"pista_{mid}{Path(audio.filename or 'a.mp3').suffix.lower() or '.mp3'}"
    mp3 = REEL_DIR / f"pista_{mid}.mp3"
    crudo.write_bytes(raw)
    try:
        dur = await asyncio.to_thread(_normalizar_pista, crudo, mp3)
        if dur <= 0:
            raise HTTPException(400, "No pude leer ese audio.")
        await kv.set(_k_musica(mid), base64.b64encode(mp3.read_bytes()).decode())
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"No pude convertir '{audio.filename}': {str(e)[-200:]}")
    finally:
        for p in (crudo, mp3):
            try:
                p.unlink()
            except OSError:
                pass
    pista = {"id": mid, "nombre": _texto(audio.filename, 80) or "pista", "dur": dur}
    await kv.set(_k_musica_idx(), pistas + [pista])
    return {"pista": pista, "pistas": pistas + [pista]}


@router.get(API + "/musica/{mid}")
async def api_musica_oir(mid: str):
    b64 = await kv.get(_k_musica(re.sub(r"[^a-f0-9]", "", mid)))
    if not b64:
        raise HTTPException(404, "Esa pista no está.")
    return Response(content=base64.b64decode(b64), media_type="audio/mpeg",
                    headers={"Cache-Control": "max-age=3600"})


@router.delete(API + "/musica/{mid}")
async def api_musica_borrar(mid: str) -> Dict[str, Any]:
    pistas = [p for p in await _pistas() if p.get("id") != mid]
    await kv.set(_k_musica_idx(), pistas)
    await kv.delete(_k_musica(mid))
    return {"pistas": pistas}


@router.post(API + "/reel/{rid}/tramo/{i}/video_propio")
async def api_video_propio(rid: str, i: int, videos: List[UploadFile] = File(...)) -> Dict[str, Any]:
    """Videos REALES de la usuaria (primeros planos de la prenda, costuras, tela) para un
    tramo de producto: reemplazan a los flashes de fotos en ese tramo."""
    reel = await _reel(rid)
    if i < 0 or i >= len(reel.get("tramos") or []):
        raise HTTPException(404, "Ese tramo no existe.")
    t = reel["tramos"][i]
    if t.get("tipo") != "producto":
        raise HTTPException(400, "Los videos propios van en los tramos de producto (los que no tienen a ella).")
    propios = list(t.get("propios") or [])
    if len(propios) + len(videos or []) > MAX_PROPIOS:
        raise HTTPException(400, f"Hasta {MAX_PROPIOS} videos por tramo.")
    d = _dir(rid)
    for up in (videos or [])[:MAX_PROPIOS]:
        raw = await up.read()
        if not raw:
            continue
        if len(raw) > MAX_VIDEO_MB * 1024 * 1024:
            raise HTTPException(400, f"'{up.filename}' pesa más de {MAX_VIDEO_MB} MB: recortalo o comprimilo.")
        uid = _uuid.uuid4().hex[:8]
        crudo = d / f"subido_{uid}{Path(up.filename or 'v.mp4').suffix.lower() or '.mp4'}"
        crudo.write_bytes(raw)
        try:
            dur = await asyncio.to_thread(_normalizar_propio, crudo, _propio_path(rid, uid))
        except Exception as e:
            raise HTTPException(400, f"No pude leer '{up.filename}': {str(e)[-200:]}")
        finally:
            try:
                crudo.unlink()
            except OSError:
                pass
        if dur <= 0:
            raise HTTPException(400, f"'{up.filename}' quedó vacío al convertirlo.")
        propios.append({"id": uid, "nombre": _texto(up.filename, 80), "dur": dur})
    t["propios"] = propios
    t["video"] = False
    reel["video"] = False
    reel["estado"] = "borrador"
    await _guardar_reel(reel)
    return {"reel": _publico(reel)}


@router.delete(API + "/reel/{rid}/tramo/{i}/video_propio/{uid}")
async def api_video_propio_borrar(rid: str, i: int, uid: str) -> Dict[str, Any]:
    reel = await _reel(rid)
    if i < 0 or i >= len(reel.get("tramos") or []):
        raise HTTPException(404, "Ese tramo no existe.")
    t = reel["tramos"][i]
    t["propios"] = [p for p in (t.get("propios") or []) if p.get("id") != uid]
    try:
        _propio_path(rid, uid).unlink()
    except OSError:
        pass
    t["video"] = False
    reel["video"] = False
    await _guardar_reel(reel)
    return {"reel": _publico(reel)}


@router.get(API + "/reel/{rid}/tramo/{i}/video_propio/{uid}")
async def api_video_propio_ver(rid: str, i: int, uid: str):
    await _reel(rid)
    p = _propio_path(rid, re.sub(r"[^a-f0-9]", "", uid))
    if not p.exists():
        raise HTTPException(404, "Ese video ya no está en el servidor: subilo de nuevo.")
    return FileResponse(str(p), media_type="video/mp4")


def _listo_para_generar(reel: Dict[str, Any], solo: Optional[int] = None) -> None:
    if not reel.get("tramos"):
        raise HTTPException(400, "Primero escribí el guion.")
    for i, t in enumerate(reel["tramos"]):
        if solo is not None and i != solo:
            continue
        if not t.get("audio"):
            raise HTTPException(400, f"Al tramo {i + 1} le falta la voz: generá las voces.")
        if t.get("tipo") == "avatar" and not t.get("escena"):
            raise HTTPException(400, f"Al tramo {i + 1} le falta la escena de ella.")
        if t.get("tipo") == "avatar" and float(t.get("dur") or 0) > OMNI_MAX_SEG:
            raise HTTPException(400, f"El tramo {i + 1} dura {float(t['dur']):.0f} s: acortá el texto "
                                     f"(máximo {OMNI_MAX_SEG} s por tramo de ella).")


@router.post(API + "/reel/{rid}/generar")
async def api_generar(rid: str, request: Request, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Genera el reel completo (o rehace un tramo con 'tramo') en segundo plano."""
    reel = await _reel(rid)
    await _doc(reel["pid"])        # el personaje tiene que existir y ser de esta cuenta
    if not await _fal_key():
        raise HTTPException(400, "Falta la API key de fal (Fotos → Ajustes → Motor FLUX, o FAL_KEY en Railway).")
    solo = payload.get("tramo")
    solo = int(solo) if solo is not None else None
    if solo is not None and (solo < 0 or solo >= len(reel.get("tramos") or [])):
        raise HTTPException(404, "Ese tramo no existe.")
    _listo_para_generar(reel, solo)
    if solo is not None:
        reel["tramos"][solo]["video"] = False
    costo = _costo_estimado(reel, solo)
    await _cobrar(costo)
    jid = _uuid.uuid4().hex[:10]
    reel["estado"] = "generando"
    reel["job"] = jid
    reel["error"] = ""
    await _guardar_reel(reel)
    titulo = ("Reel: " + (reel.get("titulo") or "")) if solo is None else f"Reel: rehacer tramo {solo + 1}"
    await _job_nuevo(jid, reel["pid"], "reel", _estimado_seg(reel, solo),
                     {"rid": rid, "solo": solo, "costo": costo, "titulo": titulo[:60]})
    _spawn(_procesar_reel(jid, rid, CURRENT_SUB.get(), solo))
    return {"ok": True, "job": jid, "costo": costo}


@router.get(API + "/reel/{rid}/video")
async def api_video(rid: str):
    reel = await _reel(rid)
    p = _dir(rid) / "reel.mp4"
    if not p.exists():
        if reel.get("drive"):
            raise HTTPException(410, "El reel ya no está en el disco del servidor; quedó en tu Drive.")
        raise HTTPException(404, "Todavía no está el reel.")
    return FileResponse(str(p), media_type="video/mp4",
                        filename=f"reel-{_slug(reel.get('titulo', ''))}.mp4")


@router.get(API + "/reel/{rid}/tramo/{i}/video")
async def api_tramo_video(rid: str, i: int):
    await _reel(rid)
    p = _dir(rid) / f"tramo_{i}.mp4"
    if not p.exists():
        raise HTTPException(404, "Ese tramo todavía no tiene video.")
    return FileResponse(str(p), media_type="video/mp4", filename=f"tramo_{i + 1}.mp4")


SIN_LATIDO = 150          # seg sin latido = el proceso que lo llevaba ya no existe
REINTENTOS_MAX = 3


async def _vigilar_job(job: Dict[str, Any]) -> Dict[str, Any]:
    """Si el trabajo quedó 'generando' sin latido (el server se reinició por un deploy,
    por ejemplo), lo RETOMA: los tramos ya hechos se conservan y, si un tramo de ella
    estaba en fal, se espera ese resultado sin volver a pagarlo."""
    if not isinstance(job, dict) or job.get("estado") not in ("en_cola", "generando"):
        return job
    quieto = time.time() - float(job.get("latido") or job.get("inicio") or 0)
    if quieto < SIN_LATIDO:
        return job
    jid, rid = str(job.get("id") or ""), str(job.get("rid") or "")
    intentos = int(job.get("reintentos") or 0)
    if rid and intentos < REINTENTOS_MAX:
        job = await _job_set(jid, {"reintentos": intentos + 1,
                                   "paso": "El server se reinició: retomando donde quedó…"})
        _spawn(_procesar_reel(jid, rid, CURRENT_SUB.get(), job.get("solo"), reanudar=True))
        return job
    job = await _job_set(jid, {"estado": "error",
                               "error": "El servidor se reinició varias veces antes de terminar. "
                                        "Volvé a generar: los tramos que ya estaban listos se conservan."})
    try:
        reel = await _reel(rid)
        if reel.get("estado") == "generando":
            reel["estado"] = "error"
            reel["error"] = job["error"]
            await _guardar_reel(reel)
    except Exception:
        pass
    return job


@router.get(API + "/job/{jid}")
async def api_job(jid: str) -> Dict[str, Any]:
    job = await kv.get(_k_job(jid))
    if not job:
        raise HTTPException(404, "Ese trabajo no está.")
    job = await _vigilar_job(job)
    return {k: job.get(k) for k in ("id", "estado", "paso", "inicio", "estimado_seg", "costo", "error", "rid", "drive")} | {"ahora": time.time()}


# ─────────────────────────────────────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────────────────────────────────────

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="es-AR">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Studio Luma · Reels</title>
<link href="https://fonts.googleapis.com/css2?family=Bodoni+Moda:wght@600&family=Jost:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root{
    --ink:#ecebf1; --ink-soft:#96919f; --line:#2c2a34;
    --ivory:#131218; --card:#1b1a21; --card-2:#232128;
    --rose:#c9a86b; --rose-deep:#d8b878; --ok:#5fae86; --bad:#e0736f;
    --shadow:0 1px 2px rgba(0,0,0,.5),0 12px 34px rgba(0,0,0,.4);
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--ivory);color:var(--ink);font-family:Jost,system-ui,sans-serif;font-size:16px;line-height:1.55;-webkit-font-smoothing:antialiased}
  a{color:var(--rose-deep);text-decoration:none}
  header{padding:16px 18px 12px;border-bottom:1px solid var(--line);background:rgba(19,18,24,.9);backdrop-filter:blur(8px);position:sticky;top:0;z-index:20}
  .brandrow{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
  .mono{width:42px;height:42px;border-radius:11px;border:1px solid var(--rose);display:flex;align-items:center;justify-content:center;flex:none;background:linear-gradient(150deg,#221f27,#161419);font-family:'Bodoni Moda',serif;font-weight:600;font-size:20px;color:var(--rose-deep)}
  .brand{font-family:'Bodoni Moda',serif;font-size:24px;font-weight:600;line-height:1}
  .brand small{display:block;font-family:Jost;font-size:12px;font-weight:400;color:var(--ink-soft);margin-top:4px;letter-spacing:.06em}
  .links{margin-left:auto;display:flex;gap:8px;flex-wrap:wrap}
  .links a{font-size:13px;padding:7px 13px;border:1px solid var(--line);border-radius:999px;background:var(--card)}
  main{max-width:1080px;margin:0 auto;padding:16px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:18px;box-shadow:var(--shadow);margin-bottom:16px}
  h2{font-family:'Bodoni Moda',serif;font-weight:600;font-size:22px;margin:0 0 6px}
  h3{font-size:15px;margin:14px 0 6px;color:var(--rose-deep);font-weight:500}
  .hint{color:var(--ink-soft);font-size:13px;margin:4px 0 8px}
  label{display:block;font-size:13px;color:var(--ink-soft);margin:10px 0 4px}
  input,select,textarea{width:100%;background:var(--card-2);color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:10px 12px;font:inherit;font-size:15px}
  input:focus,select:focus,textarea:focus{outline:none;border-color:var(--rose)}
  textarea{resize:vertical}
  button{background:var(--card-2);color:var(--ink);border:1px solid var(--line);border-radius:999px;padding:9px 16px;font:inherit;font-size:14px;cursor:pointer}
  button:hover{border-color:var(--rose)} button:disabled{opacity:.5;cursor:default}
  button.go{background:linear-gradient(150deg,var(--rose-deep),var(--rose));color:#17140d;border:none;font-weight:500;padding:11px 18px}
  button.sm{padding:6px 12px;font-size:13px}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:10px} .row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
  @media(max-width:640px){.row,.row3{grid-template-columns:1fr}}
  .pasos{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0 14px}
  .pasos .p{padding:7px 13px;border-radius:999px;border:1px solid var(--line);background:var(--card-2);font-size:13px;color:var(--ink-soft)}
  .pasos .p.on{background:var(--rose);color:#17140d;border-color:var(--rose);font-weight:500}
  .pasos .p.ok{border-color:var(--ok);color:var(--ok)}
  .tramo{border:1px solid var(--line);border-radius:14px;padding:12px;margin:10px 0;background:var(--card-2)}
  .tramo.avatar{border-left:4px solid var(--rose)} .tramo.producto{border-left:4px solid var(--ok)}
  .tramo .top{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:6px}
  .tramo .top select{width:auto} .tramo .top .n{font-weight:500;color:var(--rose-deep)}
  .tramo .top .dur{margin-left:auto;font-size:13px;color:var(--ink-soft)}
  .esc{display:flex;gap:12px;align-items:flex-start;flex-wrap:wrap;margin-top:8px}
  .esc img{width:120px;aspect-ratio:9/16;object-fit:cover;border-radius:10px;border:1px solid var(--line);background:#000}
  .esc textarea{min-height:56px;font-size:13px} .esc select{font-size:13px}
  .preg{margin-top:8px;padding:8px 10px;border:1px dashed var(--line);border-radius:10px;font-size:13px}
  .ayuda{margin:6px 0 2px;padding:8px 12px;border:1px solid var(--line);border-radius:10px;font-size:13px}
  .ayuda summary{cursor:pointer;color:var(--rose-deep)}
  .ayuda p{margin:8px 0} .ayuda ul{margin:6px 0 6px 18px;padding:0} .ayuda li{margin:4px 0}
  .ayuda .aviso{border-left:2px solid var(--bad);padding-left:10px}
  .preg .q{margin:6px 0 4px;font-weight:500} .preg .ops{display:flex;gap:6px;flex-wrap:wrap}
  .chip{border:1px solid var(--line);border-radius:999px;padding:3px 10px;font-size:12px;cursor:pointer;background:transparent;color:var(--ink)}
  .chip.on{background:var(--rose-deep);color:#fff;border-color:var(--rose-deep)}
  .fotos{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}
  .fotos img{width:72px;height:72px;object-fit:cover;border-radius:8px;border:1px solid var(--line)}
  .pill{display:inline-block;background:rgba(201,168,107,.14);color:var(--rose-deep);border-radius:999px;padding:3px 10px;font-size:12px;margin:2px 4px 2px 0}
  .ok{color:var(--ok)} .bad{color:var(--bad)}
  .spin{display:inline-block;width:14px;height:14px;border:2px solid var(--line);border-top-color:var(--rose);border-radius:50%;animation:sp .8s linear infinite;vertical-align:-2px;margin-right:6px}
  @keyframes sp{to{transform:rotate(360deg)}}
  .toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:#2a2731;border:1px solid var(--rose);color:var(--ink);padding:10px 16px;border-radius:12px;font-size:14px;display:none;z-index:50;max-width:92vw}
  .toast.on{display:block}
  video{width:100%;max-width:360px;aspect-ratio:9/16;background:#000;border-radius:14px;border:1px solid var(--line)}
  .lista .it{display:flex;gap:10px;align-items:center;padding:8px 0;border-bottom:1px solid var(--line);flex-wrap:wrap}
  .lista .it:last-child{border-bottom:none}
  .pjsel{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
  .pjsel img{width:44px;height:44px;border-radius:50%;object-fit:cover;border:1px solid var(--line)}
  .errbox{background:rgba(224,115,111,.12);border:1px solid var(--bad);border-radius:10px;padding:10px 12px;font-size:14px;margin:8px 0}
</style>
</head>
<body>
<header>
  <div class="brandrow">
    <div class="mono">SL</div>
    <div class="brand">Reels<small>STUDIO LUMA · V%%VERSION%%</small></div>
    <div class="links"><a href="%%HOME%%">← Fotos</a><a href="%%PERSONAJES%%">👤 Personajes</a><a href="%%VIDEOS%%">🎬 Videos</a></div>
  </div>
</header>
<main>

<div class="card">
  <h2>Reel de Instagram con tu Personaje</h2>
  <p class="hint">Ella se presenta y cuenta la prenda desde el local; entre medio aparecen tomas de la prenda sola mientras su voz sigue. Vos corregís el guion, aprobás las escenas y recién ahí se genera.</p>
  <div class="pjsel">
    <img id="pjImg" alt="" style="display:none">
    <div style="flex:1;min-width:200px"><label style="margin-top:0">Personaje</label><select id="pjSel"></select></div>
    <div><label style="margin-top:0">&nbsp;</label><button class="go" id="btnNuevo">➕ Reel nuevo</button></div>
  </div>
  <div class="lista" id="lista" style="margin-top:12px"></div>
</div>

<div id="editor" style="display:none">
  <div class="pasos">
    <div class="p on" data-p="1">1 · Producto</div><div class="p" data-p="2">2 · Guion y voz</div>
    <div class="p" data-p="3">3 · Escenas</div><div class="p" data-p="4">4 · Reel</div>
  </div>

  <!-- PASO 1 -->
  <div class="card" id="paso1">
    <h2>1) El producto</h2>
    <label>Link del producto (Tiendanube o Mercado Libre) — opcional</label>
    <div style="display:flex;gap:8px;flex-wrap:wrap"><input id="url" placeholder="https://tu-tienda.mitiendanube.com/productos/..." style="flex:1;min-width:220px"><button id="btnLeer">Leer el link</button></div>
    <p class="hint">Trae título, descripción, precio y fotos. Si no tenés link, cargá los datos a mano y subí las fotos de la prenda.</p>
    <div class="row"><div><label>Título</label><input id="pTitulo"></div><div><label>Precio</label><input id="pPrecio" placeholder="$ 24.900"></div></div>
    <label>Descripción</label><textarea id="pDesc" rows="4"></textarea>
    <div class="row"><div><label>Talles</label><input id="pTalles" placeholder="85 al 100"></div><div><label>Colores</label><input id="pColores" placeholder="negro, nude, bordó"></div></div>
    <label>Qué querés destacar (opcional)</label><input id="pNotas" placeholder="ej: es el más pedido, viene con bolsita de regalo, edición limitada">
    <label>Fotos de la prenda (para las tomas de producto y para que aparezca en el mostrador)</label>
    <div class="fotos" id="pFotos"></div>
    <input type="file" id="pFile" accept="image/*" multiple>
    <h3>Cómo es el reel</h3>
    <label>Plantilla</label><select id="rPlantilla"></select>
    <p class="hint" id="plantillaDesc">Elegí una plantilla y se llenan las opciones de abajo (después podés cambiar lo que quieras). El guion sigue su enfoque.</p>
    <div class="row3">
      <div><label>Tono</label><select id="rTono"></select></div>
      <div><label>Dónde está ella</label><select id="rAmb"></select></div>
      <div><label>Duración</label><select id="rDur"></select></div>
    </div>
    <label>Cómo es el lugar (opcional)</label><textarea id="rLugar" rows="2" placeholder="ej: el producto colgado en un perchero de caño, cajas apiladas atrás, cartel de la marca en la pared"></textarea>
    <div style="display:flex;gap:10px;flex-wrap:wrap;margin:6px 0"><button class="sm" id="btnLugarPreg">❓ Preguntame sobre el lugar</button><span class="hint" style="margin:0">Dónde va el producto, qué se ve atrás, la luz.</span></div>
    <div id="pregsLugar"></div>
    <label>Cómo está vestida ella (opcional)</label><input id="rOutfit" placeholder="ej: remera negra lisa y jean; o el uniforme del local">
    <div class="row3">
      <div><label>Micrófono chiquito en la mano</label><select id="rMic"><option value="si">Sí, mini mic negro</option><option value="no">No</option></select></div>
      <div><label>Look de la imagen de ella</label><select id="rLook"></select></div>
      <div><label>Voz <button class="sm" id="btnVozPrueba" style="padding:1px 8px;font-size:11px">▶ probar</button></label><select id="rVoz"></select></div>
    </div>
    <div class="row3">
      <div><label>Cámara</label><select id="rCam"></select></div>
      <div><label>Aire de micrófono real en la voz</label><select id="rVozReal"><option value="si">Sí (recomendado)</option><option value="no">No, voz limpia</option></select></div>
      <div><label>Energía de la voz</label><select id="rEnergia"></select></div>
    </div>
    <p class="hint">El <b>tono</b> manda cómo <b>escribe</b> el guion y cómo <b>habla</b>: <i>chetita</i> = influencer de Palermo (o sea, tipo, divino, amo); <i>canchera</i> = más directa. La voz arranca en <i>Leda · joven</i>; si cambiás voz o tono, volvé a generar las voces. El <b>look celular</b> deja su video quemado, blandito y con neblina (las fotos del producto y tus videos quedan como están). <b>Cámara en mano</b> le suma un movimiento chiquito que no se repite: OmniHuman devuelve el cuadro clavado, y el fondo congelado es lo que más delata que es IA. El <b>aire de micrófono</b> le saca a la voz el brillo de estudio y le pone cuerpo de mini mic en un local. La <b>energía</b> le corta a la voz los silencios largos para que hable de corrido; las opciones con "+ tono" además la hacen más joven sin encogerle el timbre (subir el tono a lo bruto es lo que deja ese efecto de cinta acelerada). Tocá <b>▶ probar</b> para escuchar la combinación de voz, tono y energía antes de grabar el guion entero: sale menos de un centavo.</p>
    <div style="display:flex;gap:10px;margin-top:14px;flex-wrap:wrap"><button class="go" id="btnGuion">✍️ Escribir el guion</button><span class="hint" id="p1Est"></span></div>
  </div>

  <!-- PASO 2 -->
  <div class="card" id="paso2" style="display:none">
    <h2>2) Guion y voz</h2>
    <p class="hint">Corregí lo que quieras. Los tramos <b>de ella</b> salen a cámara; los <b>de producto</b> muestran la prenda sola con su voz encima. Después generá las voces: ahí ves cuánto dura cada tramo.</p>
    <label>Título del reel</label><input id="rTitulo">
    <div id="tramos"></div>
    <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:10px">
      <button class="sm" id="btnAddTramo">➕ Sumar tramo</button>
      <button id="btnGuardarTramos">💾 Guardar cambios</button>
      <button class="go" id="btnVoces">🎙️ Generar voces</button>
      <span class="hint" id="p2Est"></span>
    </div>
  </div>

  <!-- PASO 3 -->
  <div class="card" id="paso3" style="display:none">
    <h2>3) Escenas de ella</h2>
    <p class="hint">Una foto vertical por cada tramo en que habla: ella en el lugar elegido, la prenda al lado sobre el mostrador. Elegí el encuadre, agregá detalles (o tocá <b>❓ Preguntame</b> para que Gemini te pregunte lo que la foto no puede adivinar), generá, y aprobá o rehacé cada una; también podés subir la tuya.</p>
    <label class="hint" style="display:flex;gap:8px;align-items:flex-start;margin:0 0 10px"><input type="checkbox" id="rCont" checked style="width:auto;margin:2px 0 0"><span><b>Seguir la primera escena.</b> Las que generes después copian de la primera el lugar, el fondo, la luz, la ropa y el peinado: sólo cambia el encuadre y la pose. Destildalo si querés que cada una sea libre.</span></label>
    <div id="escenas"></div>
    <div id="falta"></div>
  </div>

  <!-- PASO 4 -->
  <div class="card" id="paso4" style="display:none">
    <h2>4) Generar el reel</h2>
    <div class="row3">
      <div><label>Calidad del video de ella</label><select id="rRes"><option value="720p">720p (más rápido)</option><option value="1080p">1080p</option></select></div>
      <div><label>Subtítulos</label><select id="rSubs"><option value="si">Sí, quemados</option><option value="no">No</option></select></div>
      <div><label>Motor de los clips IA de producto</label><select id="rMotor"></select></div>
    </div>
    <div class="row3">
      <div><label>Música de fondo</label><select id="rMusica"></select></div>
      <div><label>Cómo suena</label><select id="rMusModo"></select></div>
      <div><label>&nbsp;</label><label class="sm" style="margin:0"><input type="file" accept="audio/*" id="rMusFile" style="display:none"><button class="sm" onclick="document.getElementById('rMusFile').click()">⬆️ Subir una pista</button> <button class="sm" id="btnMusBorrar">🗑</button></label></div>
    </div>
    <div class="row3">
      <div><label>Arranca en el segundo <span id="rMusDesdeTxt" class="hint" style="margin:0"></span></label><input type="number" id="rMusDesde" min="0" step="1" value="0"></div>
      <div><label>Volumen de la música <span id="rMusVolTxt"></span></label><input type="range" id="rMusVol" min="0" max="100" step="1" style="width:100%"></div>
      <div><label>&nbsp;</label><button class="sm" id="btnMusOir">▶ escuchar desde ahí</button></div>
    </div>
    <div class="row3">
      <div><label>Precio sobre el video</label><select id="rPrecio"><option value="si">Sí, en los tramos de producto</option><option value="no">No</option></select></div>
      <div><label>Talles sobre el video</label><select id="rTalles"><option value="si">Sí, en los tramos de producto</option><option value="no">No</option></select></div>
      <div><label>Llamado a la acción (último tramo)</label><input id="rCta" maxlength="60" placeholder="ej: Escribinos por DM"></div>
    </div>
    <p class="hint">Las pistas quedan guardadas en tu cuenta para todos los reels. La música se repite hasta cubrir el reel, se va apagando al final y siempre queda debajo de la voz. Con <b>"Arranca en el segundo"</b> elegís qué parte del tema entra: puso 45 y empieza en el estribillo. Tocá <b>▶ escuchar desde ahí</b> para buscar el punto. El precio y los talles salen de los datos del producto del paso 1.</p>
    <details class="ayuda"><summary>🎵 No tengo temas descargados, ¿de dónde saco una canción?</summary>
      <p><b>Lo más fácil, y lo que mejor funciona en Instagram: no la pongas acá.</b> Generá el reel con "Sin música", subilo a Instagram y ahí, en el editor, tocá 🎵 <i>Música</i>. Esos temas tienen licencia (no te silencian el reel), están los que suenan de moda (ayuda al alcance) y con el control de volumen le bajás la música para que se escuche tu voz.</p>
      <p><b>Si la querés pegada al video</b> (para WhatsApp, la web o TikTok), necesitás un mp3 que puedas usar. Dos lugares gratis:</p>
      <ul>
        <li><b>pixabay.com/music</b> — buscás, tocás Download y baja el mp3. Sin cuenta y sin dar créditos.</li>
        <li><b>Biblioteca de audio de YouTube</b> — entrás a YouTube Studio con tu cuenta de Google, menú "Biblioteca de audio". Fijate en la columna que dice si hay que dar crédito.</li>
      </ul>
      <p>Buscá en inglés que hay mucho más: <i>fashion</i>, <i>chill</i>, <i>upbeat</i>, <i>lofi</i>, <i>french house</i>. Bajás el mp3 y lo subís acá con "⬆️ Subir una pista".</p>
      <p class="aviso"><b>Ojo:</b> no bajes un tema conocido de YouTube o Spotify para meterlo acá. Instagram reconoce la música y te puede silenciar o bajar el reel. Para un tema famoso, el camino es ponerlo desde Instagram.</p>
    </details>
    <div style="margin-top:10px"><button class="go" id="btnGenerar">🎞️ Generar reel</button></div>
    <p class="hint" id="p4Est"></p>
    <div id="jobEstado"></div>
    <div id="resultado" style="display:none">
      <video id="video" controls playsinline></video>
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:10px">
        <a id="descargar" class="pill" href="#" download>⬇️ Descargar</a><span id="driveLink"></span>
      </div>
      <h3>Rehacer un tramo</h3>
      <div id="rehacer" style="display:flex;gap:6px;flex-wrap:wrap"></div>
    </div>
  </div>
</div>

</main>
<div class="toast" id="toast"></div>
<script>
const API = "%%API%%"; const PJ_API = "%%PJ_API%%";
const $ = s => document.querySelector(s), $$ = s => Array.from(document.querySelectorAll(s));
function esc(s){ return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
function toast(m, ms){ const t = $("#toast"); t.textContent = m; t.classList.add("on"); clearTimeout(t._t); t._t = setTimeout(() => t.classList.remove("on"), ms || 3400); }
async function api(path, opts, base){
  const r = await fetch((base || API) + path, Object.assign({headers: {"Content-Type": "application/json"}}, opts || {}));
  if(r.status === 401){ location.href = "/auth/login"; throw new Error("login"); }
  const ct = r.headers.get("content-type") || "";
  if(!ct.includes("json")){ if(!r.ok) throw new Error("HTTP " + r.status); return r; }
  const d = await r.json(); if(!r.ok) throw new Error(d.detail || d.error || ("HTTP " + r.status)); return d;
}
const post = (p, body) => api(p, {method: "POST", body: JSON.stringify(body || {})});
function ocupado(btn, on, txt){ btn.disabled = on; if(on){ btn._t = btn.innerHTML; btn.innerHTML = '<span class="spin"></span>' + (txt || "Un momento…"); } else if(btn._t){ btn.innerHTML = btn._t; } }
function achicar(file, maxDim){ return new Promise((res, rej) => { const img = new Image(); img.onload = () => { try{ URL.revokeObjectURL(img.src); }catch(e){}
  let w = img.naturalWidth, h = img.naturalHeight; if(Math.max(w, h) > maxDim){ const s = maxDim / Math.max(w, h); w = Math.round(w * s); h = Math.round(h * s); }
  const c = document.createElement("canvas"); c.width = w; c.height = h; c.getContext("2d").drawImage(img, 0, 0, w, h); res(c.toDataURL("image/jpeg", 0.9)); };
  img.onerror = () => rej(new Error("No se pudo leer la imagen")); img.src = URL.createObjectURL(file); }); }
function fmt(s){ s = Math.max(0, Math.round(s)); return (s >= 60 ? Math.floor(s / 60) + " min " : "") + (s % 60) + " s"; }

let CFG = {}, PJS = [], PID = null, REEL = null, FOTOS = [], JOB = null, JOB_T = null;

async function init(){
  CFG = await api("/config");
  const d = await api("/lista", null, PJ_API); PJS = d.personajes || [];
  const sel = $("#pjSel");
  sel.innerHTML = PJS.map(p => `<option value="${p.id}">${esc(p.nombre)}${p.tiene_retrato ? "" : " (sin retrato)"}</option>`).join("") || '<option value="">Primero creá un personaje</option>';
  const q = new URLSearchParams(location.search).get("pid");
  if(q && PJS.some(p => p.id === q)) sel.value = q;
  sel.onchange = () => { PID = sel.value; pintarVoces(); cargarLista(); };
  PID = sel.value || null; pintarVoces();
  $("#rTono").innerHTML = CFG.tonos.map(t => `<option value="${t}">${t[0].toUpperCase() + t.slice(1)}</option>`).join("");
  $("#rAmb").innerHTML = Object.entries(CFG.ambientes).map(([k, v]) => `<option value="${k}">${esc(v[0].toUpperCase() + v.slice(1))}</option>`).join("");
  $("#rDur").innerHTML = CFG.duraciones.map(d => `<option value="${d}" ${d === 35 ? "selected" : ""}>${d} segundos</option>`).join("");
  $("#rLook").innerHTML = Object.entries(CFG.looks).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  $("#rCam").innerHTML = Object.entries(CFG.camaras).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  $("#rEnergia").innerHTML = Object.entries(CFG.energias).map(([k, v]) => `<option value="${k}" ${k === CFG.energia_default ? "selected" : ""}>${esc(v.nombre)}</option>`).join("");
  $("#rEnergia").onchange = () => { if(REEL && REEL.tramos) pintarTramos(); };
  $("#rPlantilla").innerHTML = `<option value="">Sin plantilla (a mano)</option>` + Object.entries(CFG.plantillas).map(([k, v]) => `<option value="${k}">${esc(v.nombre)}</option>`).join("");
  $("#rPlantilla").onchange = () => aplicarPlantilla($("#rPlantilla").value);
  $("#rMotor").innerHTML = Object.entries(CFG.motores_ia).map(([k, v]) => `<option value="${k}" ${k === CFG.motor_ia_default ? "selected" : ""}>${esc(v.nombre)} · ${usd(v.precio_seg)}/s (clip de 5 o 10 s)</option>`).join("");
  $("#rMotor").onchange = () => { if(REEL) pintarTramos(); listoParaReel(); };
  $("#rMusVol").value = CFG.musica_vol_default; $("#rMusVol").oninput = () => $("#rMusVolTxt").textContent = $("#rMusVol").value + "%"; $("#rMusVolTxt").textContent = CFG.musica_vol_default + "%";
  $("#rMusModo").innerHTML = Object.entries(CFG.musica_modos).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  $("#rMusModo").onchange = () => { const v = $("#rMusModo").value === "local" ? CFG.musica_vol_local : CFG.musica_vol_default; $("#rMusVol").value = v; $("#rMusVolTxt").textContent = v + "%"; };
  $("#rMusica").onchange = pintarLargoPista;
  $("#rCta").value = CFG.cta_default;
  await cargarMusica();
  await cargarLista();
}
async function cargarLista(){
  const L = $("#lista"); if(!PID){ L.innerHTML = ""; return; }
  const img = $("#pjImg"); img.src = PJ_API + "/" + PID + "/img/retrato?t=" + Date.now(); img.style.display = ""; img.onerror = () => img.style.display = "none";
  try{
    const d = await api("/" + PID + "/lista");
    L.innerHTML = (d.reels || []).length ? d.reels.map(r => `<div class="it"><b>${esc(r.titulo || "Reel")}</b><span class="hint" style="margin:0">${esc(r.producto || "")} · ${esc(r.estado)}${r.dur_total ? " · " + r.dur_total + " s" : ""}${r.costo_total ? " · US$ " + r.costo_total : ""}</span>
      <span style="margin-left:auto;display:flex;gap:6px"><button class="sm" onclick="abrirReel('${r.id}')">Abrir</button><button class="sm" onclick="borrarReel('${r.id}')">🗑</button></span></div>`).join("")
      : '<p class="hint">Todavía no hay reels de este personaje.</p>';
  }catch(e){ L.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; }
}
async function borrarReel(rid){ if(!confirm("¿Borrar este reel?")) return; try{ await api("/reel/" + rid, {method: "DELETE"}); if(REEL && REEL.id === rid){ REEL = null; $("#editor").style.display = "none"; } cargarLista(); }catch(e){ toast(e.message); } }

function paso(n){ $$(".pasos .p").forEach(p => { const k = +p.dataset.p; p.classList.toggle("on", k === n); }); [1,2,3,4].forEach(k => $("#paso" + k).style.display = (k <= n) ? "" : "none");
  if(n < 4) listoParaReel();   // si el reel ya está listo, el paso 4 queda a la vista igual
  $("#paso" + n).scrollIntoView({behavior: "smooth", block: "start"}); }
$$(".pasos .p").forEach(p => p.onclick = () => { const k = +p.dataset.p;
  if(k === 4 && REEL && REEL.tramos && faltaParaReel().length){ paso(3); return toast("Falta " + faltaParaReel()[0] + ".", 6000); }
  if(k === 1 || (REEL && (k === 2 ? REEL.tramos.length : k === 3 ? REEL.tramos.some(t => t.audio) : REEL.tramos.length))) paso(k); });

$("#btnNuevo").onclick = () => { if(!PID) return toast("Primero creá un personaje en la pestaña Personajes."); REEL = null; FOTOS = [];
  ["url","pTitulo","pPrecio","pDesc","pTalles","pColores","pNotas","rOutfit","rLugar"].forEach(id => $("#" + id).value = ""); $("#pregsLugar").innerHTML = ""; $("#rCont").checked = true; $("#rPlantilla").value = ""; aplicarPlantilla(""); $("#pFotos").innerHTML = ""; $("#p1Est").textContent = "";
  $("#editor").style.display = ""; paso(1); };

$("#btnLeer").onclick = async () => { const u = $("#url").value.trim(); if(!u) return toast("Pegá el link.");
  ocupado($("#btnLeer"), true, "Leyendo…");
  try{ const d = await post("/leer_link", {url: u}); const p = d.producto;
    $("#pTitulo").value = p.titulo || ""; $("#pPrecio").value = p.precio || ""; $("#pDesc").value = p.descripcion || ""; $("#pTalles").value = p.talles || ""; $("#pColores").value = p.colores || "";
    FOTOS = d.fotos || []; pintarFotos(); toast(`Leído (${p.fuente}): ${FOTOS.length} foto(s).`);
  }catch(e){ toast(e.message, 5000); } ocupado($("#btnLeer"), false); };
function pintarFotos(){ $("#pFotos").innerHTML = FOTOS.map((f, i) => `<span style="position:relative"><img src="${f}"><button class="sm" style="position:absolute;top:-6px;right:-6px;padding:0 6px" onclick="FOTOS.splice(${i},1);pintarFotos()">×</button></span>`).join(""); }
$("#pFile").onchange = async e => { for(const f of Array.from(e.target.files || []).slice(0, 6)){ try{ FOTOS.push(await achicar(f, 1600)); }catch(err){ toast(err.message); } } e.target.value = ""; pintarFotos(); };

$("#btnGuion").onclick = async () => {
  const titulo = $("#pTitulo").value.trim(); if(!titulo) return toast("Ponele un título al producto.");
  ocupado($("#btnGuion"), true, "Escribiendo el guion…");
  try{
    const prod = {titulo, precio: $("#pPrecio").value, descripcion: $("#pDesc").value, talles: $("#pTalles").value, colores: $("#pColores").value, notas: $("#pNotas").value};
    const comun = opciones();
    if(!REEL){ const d = await post("/" + PID + "/nuevo", Object.assign({producto: prod, fotos: FOTOS, fuente_url: $("#url").value.trim()}, comun)); REEL = d.reel; }
    const d2 = await post("/reel/" + REEL.id + "/guion", Object.assign({notas: prod.notas}, comun)); REEL = d2.reel;
    pintarTramos(); paso(2); cargarLista();
  }catch(e){ toast(e.message, 5000); } ocupado($("#btnGuion"), false); };

function opciones(){ return {tono: $("#rTono").value, ambiente: $("#rAmb").value, duracion: +$("#rDur").value, outfit: $("#rOutfit").value, lugar: $("#rLugar").value, continuidad: $("#rCont").checked, mic: $("#rMic").value !== "no", look: $("#rLook").value, camara: $("#rCam").value, voz_real: $("#rVozReal").value !== "no", voz_energia: $("#rEnergia").value, voz: $("#rVoz").value,
  plantilla: $("#rPlantilla").value, motor_ia: $("#rMotor").value, musica: $("#rMusica").value, musica_vol: +$("#rMusVol").value, musica_modo: $("#rMusModo").value, musica_desde: +$("#rMusDesde").value || 0, mostrar_precio: $("#rPrecio").value !== "no", mostrar_talles: $("#rTalles").value !== "no", cta: $("#rCta").value}; }
function aplicarPlantilla(k){ const p = CFG.plantillas[k]; $("#plantillaDesc").textContent = p ? p.desc + " El guion sigue este enfoque." : "Elegí una plantilla y se llenan las opciones de abajo (después podés cambiar lo que quieras). El guion sigue su enfoque.";
  if(!p) return; $("#rTono").value = p.tono; $("#rAmb").value = p.ambiente; $("#rDur").value = p.duracion; $("#rLook").value = p.look; $("#rMic").value = p.mic ? "si" : "no";
  $("#rPrecio").value = p.mostrar_precio ? "si" : "no"; $("#rTalles").value = p.mostrar_talles ? "si" : "no"; $("#rCta").value = p.cta; }
let PISTAS = [];
async function cargarMusica(sel){ try{ const d = await api("/musica"); const cur = sel !== undefined ? sel : $("#rMusica").value; PISTAS = d.pistas || [];
    $("#rMusica").innerHTML = `<option value="">Sin música</option>` + PISTAS.map(p => `<option value="${p.id}">${esc(p.nombre)} · ${Math.round(p.dur)} s</option>`).join(""); $("#rMusica").value = cur || ""; pintarLargoPista(); }catch(e){} }
function pintarLargoPista(){ const p = PISTAS.find(x => x.id === $("#rMusica").value);
  $("#rMusDesdeTxt").textContent = p ? `(el tema dura ${Math.round(p.dur)} s)` : "";
  if(p) $("#rMusDesde").max = Math.max(0, Math.floor(p.dur) - 1); }
$("#rMusFile").onchange = async e => { const f = e.target.files[0]; if(!f) return; toast("Subiendo y convirtiendo la pista…", 8000);
  try{ const fd = new FormData(); fd.append("audio", f, f.name); const r = await fetch(API + "/musica", {method: "POST", body: fd}); const d = await r.json(); if(!r.ok) throw new Error(d.detail || ("HTTP " + r.status)); await cargarMusica(d.pista.id); toast("Pista lista: " + d.pista.nombre); }
  catch(err){ toast(err.message, 6000); } e.target.value = ""; };
$("#btnMusOir").onclick = () => { const id = $("#rMusica").value; if(!id) return toast("Elegí una pista.");
  if(window._musA){ window._musA.pause(); window._musA = null; return; }
  const a = new Audio(API + "/musica/" + id); a.volume = 0.5;
  a.addEventListener("loadedmetadata", () => { a.currentTime = Math.min(+$("#rMusDesde").value || 0, Math.max(0, a.duration - 1)); a.play(); });
  a.addEventListener("ended", () => { window._musA = null; });
  window._musA = a; };
$("#btnMusBorrar").onclick = async () => { const id = $("#rMusica").value; if(!id) return toast("Elegí una pista."); if(!confirm("¿Borrar esta pista de tu biblioteca?")) return; try{ await api("/musica/" + id, {method: "DELETE"}); await cargarMusica(""); }catch(e){ toast(e.message); } };
function pintarVoces(){ const pj = PJS.find(p => p.id === PID); const g = (pj && pj.genero === "hombre") ? "hombre" : "mujer"; const actual = $("#rVoz").value;
  $("#rVoz").innerHTML = `<option value="">La del personaje</option>` + (CFG.voces[g] || []).map(([v, et]) => `<option value="${v}">${esc(et)}</option>`).join(""); $("#rVoz").value = actual || (g === "hombre" ? "Puck" : "Leda"); }

function pintarTramos(){
  $("#rTitulo").value = REEL.titulo || "";
  $("#tramos").innerHTML = REEL.tramos.map((t, i) => `<div class="tramo ${t.tipo}" data-i="${i}">
    <div class="top"><span class="n">Tramo ${i + 1}</span>
      <select class="tipo"><option value="avatar" ${t.tipo === "avatar" ? "selected" : ""}>👩 Ella a cámara</option><option value="producto" ${t.tipo === "producto" ? "selected" : ""}>🧺 Producto (sin gente)</option></select>
      <span class="dur">${durTxt(t)}${t.audio ? ` <button class="sm" onclick="oir(${i})">▶</button>` : ""}</span>
      <button class="sm" onclick="quitarTramo(${i})">🗑</button></div>
    <textarea class="texto" rows="2">${esc(t.texto)}</textarea>
    <input class="muestra" placeholder="Qué se ve en pantalla (sólo en los de producto)" value="${esc(t.muestra || "")}" style="margin-top:6px;${t.tipo === "producto" ? "" : "display:none"}">
    <div class="tomas" style="margin-top:6px;${t.tipo === "producto" ? "" : "display:none"}"><label style="margin:0 0 4px">Tomas de este tramo (si subís tus videos, mandan ellos)</label>
      <select class="ia"><option value="" ${t.ia ? "" : "selected"}>📷 Flashes de las fotos · sin costo</option><option value="1" ${t.ia ? "selected" : ""}>🎬 Video IA de la prenda con ${esc(motorNombre())} · ${usd(costoIa(t))}</option></select></div>
    <div class="propios" style="margin-top:8px;${t.tipo === "producto" ? "" : "display:none"}">
      <div class="hint" style="margin:0 0 4px">🎥 <b>Tus videos reales</b> para este tramo (primeros planos, costuras, tela). Si subís, reemplazan a los flashes de fotos; se recortan al vertical y al largo de la voz.</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
        ${(t.propios || []).map(p => `<span class="pill">${esc(p.nombre || "video")} · ${p.dur} s <a href="${API}/reel/${REEL.id}/tramo/${i}/video_propio/${p.id}" target="_blank">▶</a> <a href="#" onclick="borrarPropio(${i},'${p.id}');return false">✕</a></span>`).join("")}
        ${(t.propios || []).length < ${MAX_PROPIOS_JS} ? `<label class="sm" style="margin:0"><input type="file" accept="video/*" multiple style="display:none" onchange="subirPropios(${i},this)"><button class="sm" onclick="this.previousElementSibling.click()">⬆️ Subir mis videos</button></label>` : ""}
      </div>
    </div>
  </div>`).join("");
  $$("#tramos .tipo").forEach(s => s.onchange = () => { const c = s.closest(".tramo"); c.className = "tramo " + s.value; ["muestra", "propios", "tomas"].forEach(k => c.querySelector("." + k).style.display = s.value === "producto" ? "" : "none"); });
  $$("#tramos .ia").forEach(s => s.onchange = () => { const i = +s.closest(".tramo").dataset.i; REEL.tramos[i].ia = !!s.value; REEL.tramos[i].video = false; pintarTramos(); });
  listoParaReel();
  const rc = resumenCosto(REEL.tramos), conVoz = REEL.tramos.length && REEL.tramos.every(t => t.audio);
  $("#p2Est").innerHTML = REEL.tramos.length ? `${conVoz ? "" : "Estimado por palabras · "}Ella habla <b>${rc.ella} s</b> → OmniHuman <b>${usd(rc.costo)}</b> · producto ${rc.prod} s${rc.ia ? ` (clips IA <b>${usd(rc.ia)}</b>)` : " sin costo"} · total ${rc.total} s · <b>${usd(rc.costo + rc.ia)}</b>` : "";
}
const MAX_SEG_ELLA = 28;
function palabrasSeg(){ const e = CFG.energias[($("#rEnergia") && $("#rEnergia").value) || CFG.energia_default]; return (e && e.palabras_seg) || 2.4; }
function segEst(t){ return t.audio ? (t.dur || 0) : Math.round((t.texto || "").split(/\s+/).filter(Boolean).length / palabrasSeg() * 10) / 10; }
function usd(x){ return "US$ " + (Math.round(x * 100) / 100).toFixed(2); }
function durTxt(t){ const s = segEst(t); const pre = t.audio ? "🎙️ " : "≈ ";
  if(t.tipo === "avatar"){ const mal = s > MAX_SEG_ELLA; return `<b style="color:${mal ? "var(--bad)" : "var(--rose-deep)"}">${pre}${s} s de ella · ${usd(s * CFG.precio_omni_seg)}</b>${mal ? " ⚠ pasa los " + MAX_SEG_ELLA + " s: acortá" : ""}${t.audio ? "" : " (estimado)"}`; }
  if(t.ia && !(t.propios || []).length) return `${pre}${s} s · <b style="color:var(--rose-deep)">clip IA ${usd(costoIa(t))}</b>${t.audio ? "" : " (estimado)"}`;
  return `${pre}${s} s · sin costo${t.audio ? "" : " (estimado)"}`; }
function motorKey(){ const v = $("#rMotor") && $("#rMotor").value; return v || (REEL && REEL.motor_ia) || CFG.motor_ia_default; }
function motorNombre(){ const m = CFG.motores_ia[motorKey()]; return m ? m.nombre : motorKey(); }
function costoIa(t){ if((t.propios || []).length) return 0; const m = CFG.motores_ia[motorKey()] || {precio_seg: 0.05}; const s = segEst(t); return Math.round(m.precio_seg * (s <= 5.5 ? 5 : 10) * 1000) / 1000; }
function resumenCosto(ts){ const ella = ts.filter(t => t.tipo === "avatar").reduce((a, t) => a + segEst(t), 0); const prod = ts.filter(t => t.tipo !== "avatar").reduce((a, t) => a + segEst(t), 0);
  const ia = ts.filter(t => t.tipo !== "avatar" && t.ia).reduce((a, t) => a + costoIa(t), 0);
  return {ella: Math.round(ella * 10) / 10, prod: Math.round(prod * 10) / 10, costo: Math.round(ella * CFG.precio_omni_seg * 100) / 100, ia: Math.round(ia * 100) / 100, total: Math.round((ella + prod) * 10) / 10}; }
function leerTramos(){ return $$("#tramos .tramo").map(c => ({tipo: c.querySelector(".tipo").value, texto: c.querySelector(".texto").value, muestra: c.querySelector(".muestra").value, ia: !!c.querySelector(".ia").value})); }
function quitarTramo(i){ const ts = leerTramos(); ts.splice(i, 1); REEL.tramos = ts.map((t, k) => Object.assign({dur: 0, audio: false, escena: false, video: false}, REEL.tramos[k] && REEL.tramos[k].texto === t.texto ? REEL.tramos[k] : {}, t)); pintarTramos(); }
$("#btnAddTramo").onclick = () => { REEL.tramos = leerTramos().map((t, k) => Object.assign({dur: 0, audio: false, escena: false, video: false}, REEL.tramos[k] || {}, t)); REEL.tramos.push({tipo: "producto", texto: "", muestra: "", dur: 0, audio: false, escena: false, video: false}); pintarTramos(); };
async function guardarTramos(){ const d = await post("/reel/" + REEL.id + "/tramos", {tramos: leerTramos(), titulo: $("#rTitulo").value}); REEL = d.reel; pintarTramos(); }
$("#btnGuardarTramos").onclick = async () => { try{ await guardarTramos(); toast("Guion guardado."); }catch(e){ toast(e.message, 5000); } };
$("#btnVoces").onclick = async () => { ocupado($("#btnVoces"), true, "Grabando las voces…");
  try{ await guardarTramos(); const d = await post("/reel/" + REEL.id + "/voces", Object.assign({todas: true}, opciones())); REEL = d.reel; pintarTramos(); pintarEscenas(); paso(3); toast(`Voces listas: ${d.dur_total} s en total.`); }
  catch(e){ toast(e.message, 6000); } ocupado($("#btnVoces"), false); };
function oir(i){ const a = new Audio(API + "/reel/" + REEL.id + "/audio/" + i + "?t=" + Date.now()); a.play(); }
async function subirPropios(i, input){ const files = Array.from(input.files || []); if(!files.length) return;
  try{ await guardarTramos(); const fd = new FormData(); files.forEach(f => fd.append("videos", f, f.name));
    toast("Subiendo y convirtiendo " + files.length + " video(s)… puede tardar un minuto.", 8000);
    const r = await fetch(API + "/reel/" + REEL.id + "/tramo/" + i + "/video_propio", {method: "POST", body: fd});
    const d = await r.json(); if(!r.ok) throw new Error(d.detail || ("HTTP " + r.status));
    REEL = d.reel; pintarTramos(); toast("Video(s) listo(s) para el tramo " + (i + 1) + ".");
  }catch(e){ toast(e.message, 6000); } input.value = ""; }
async function borrarPropio(i, uid){ try{ const d = await api("/reel/" + REEL.id + "/tramo/" + i + "/video_propio/" + uid, {method: "DELETE"}); REEL = d.reel; pintarTramos(); }catch(e){ toast(e.message); } }

function pintarEscenas(){
  const E = $("#escenas"); E.innerHTML = "";
  REEL.tramos.forEach((t, i) => { if(t.tipo !== "avatar") return;
    const d = document.createElement("div"); d.className = "tramo avatar";
    d.innerHTML = `<div class="top"><span class="n">Tramo ${i + 1}</span><span class="hint" style="margin:0">${esc(t.texto)}</span></div>
      <div class="esc"><img id="esc${i}" src="${t.escena ? API + "/reel/" + REEL.id + "/escena/" + i + "?t=" + Date.now() : ""}" style="${t.escena ? "" : "display:none"}">
      <div style="flex:1;min-width:200px">
      <label style="margin-top:0">Encuadre</label><select id="enc${i}"><option value="">Automático (va rotando)</option>${CFG.encuadres.map((n, k) => `<option value="${k}" ${t.encuadre === k ? "selected" : ""}>${esc(n)}</option>`).join("")}</select>
      <label>Detalles de esta escena (opcional)</label><textarea id="det${i}" placeholder="ej: sonriendo, con el pack en la mano libre, el pelo suelto, más cerca de cámara">${esc(t.detalle || "")}</textarea>
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px"><button class="sm" id="preg${i}">❓ Preguntame</button><button class="go sm" id="gen${i}">${t.escena ? "🔁 Rehacer" : "✨ Generar escena"}</button><label class="sm" style="margin:0"><input type="file" accept="image/*" id="sub${i}" style="display:none"><button class="sm" onclick="document.getElementById('sub${i}').click()">⬆️ Subir la mía</button></label></div>
      <div id="pregs${i}"></div>
      <p class="hint" id="est${i}">${t.escena ? "Lista." : "Todavía no tiene escena."}</p></div></div>`;
    E.appendChild(d);
    const detalleDe = () => ({detalle: d.querySelector("#det" + i).value, encuadre: d.querySelector("#enc" + i).value === "" ? "" : +d.querySelector("#enc" + i).value});
    const guardarDetalle = async () => { try{ const r = await post("/reel/" + REEL.id + "/escena/" + i + "/detalle", detalleDe()); REEL = r.reel; }catch(e){} };
    d.querySelector("#det" + i).onchange = guardarDetalle; d.querySelector("#enc" + i).onchange = guardarDetalle;
    d.querySelector("#preg" + i).onclick = async () => { const b = d.querySelector("#preg" + i); ocupado(b, true, "Pensando…");
      try{ const r = await post("/reel/" + REEL.id + "/escena/" + i + "/preguntas", Object.assign(opciones(), detalleDe())); REEL = r.reel; pintarPreguntas(d, i, r.preguntas); }
      catch(e){ toast(e.message, 6000); } ocupado(b, false); };
    d.querySelector("#gen" + i).onclick = async () => { const b = d.querySelector("#gen" + i); ocupado(b, true, "Nano Banana…");
      try{ const r = await post("/reel/" + REEL.id + "/escena/" + i, Object.assign(opciones(), detalleDe())); REEL = r.reel; const im = d.querySelector("#esc" + i); im.src = r.src; im.style.display = ""; d.querySelector("#est" + i).textContent = "Lista."; b._t = "🔁 Rehacer"; listoParaReel(); }
      catch(e){ toast(e.message, 6000); } ocupado(b, false); };
    d.querySelector("#sub" + i).onchange = async e => { const f = e.target.files[0]; if(!f) return;
      try{ const src = await achicar(f, 1920); const r = await post("/reel/" + REEL.id + "/escena/" + i, {imagen: src}); REEL = r.reel; const im = d.querySelector("#esc" + i); im.src = src; im.style.display = ""; d.querySelector("#est" + i).textContent = "Lista (subida)."; listoParaReel(); }
      catch(err){ toast(err.message, 6000); } };
  });
  listoParaReel();
}
function pintarPreguntas(d, i, preguntas){ pintarChips(d.querySelector("#pregs" + i), d.querySelector("#det" + i), preguntas, "los detalles de la escena"); }
function pintarChips(P, ta, preguntas, donde){
  P.innerHTML = `<div class="preg"><span class="hint" style="margin:0">Elegí una opción por pregunta (o escribí la tuya): se suma a ${donde}.</span>` + preguntas.map((q, k) => `<div class="q">${esc(q.pregunta)}</div><div class="ops">${q.opciones.map((o, j) => `<button class="chip" data-k="${k}" data-o="${esc(o)}">${esc(o)}</button>`).join("")}<input placeholder="otra…" data-k="${k}" style="width:140px;padding:3px 8px;font-size:12px;margin:0"></div>`).join("") + `</div>`;
  const resp = {};
  const volcar = () => { const base = (ta.value || "").split(" · ").filter(x => x && !x.startsWith("»")); const nuevos = Object.entries(resp).filter(([, v]) => v).map(([k, v]) => "» " + preguntas[k].pregunta.replace(/^¿|\?$/g, "") + ": " + v); ta.value = base.concat(nuevos).join(" · "); ta.dispatchEvent(new Event("change")); };
  P.querySelectorAll(".chip").forEach(c => c.onclick = () => { const k = c.dataset.k; P.querySelectorAll(`.chip[data-k="${k}"]`).forEach(x => x.classList.remove("on")); c.classList.add("on"); resp[k] = c.dataset.o; volcar(); });
  P.querySelectorAll("input[data-k]").forEach(inp => inp.onchange = () => { const k = inp.dataset.k; P.querySelectorAll(`.chip[data-k="${k}"]`).forEach(x => x.classList.remove("on")); resp[k] = inp.value.trim(); volcar(); });
}
$("#btnVozPrueba").onclick = async () => { const b = $("#btnVozPrueba"); ocupado(b, true, "…");
  try{ const r = await fetch(API + "/voz_prueba", {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({pid: PID, voz: $("#rVoz").value, tono: $("#rTono").value, voz_energia: $("#rEnergia").value})});
    if(!r.ok) throw new Error((await r.json()).detail || ("HTTP " + r.status));
    const a = new Audio(URL.createObjectURL(await r.blob())); a.play(); }
  catch(e){ toast(e.message, 6000); } ocupado(b, false); };
$("#btnLugarPreg").onclick = async () => { const b = $("#btnLugarPreg"); ocupado(b, true, "Pensando…");
  try{ const d = await post("/lugar_preguntas", {ambiente: $("#rAmb").value, lugar: $("#rLugar").value, producto: {titulo: $("#pTitulo").value}});
    pintarChips($("#pregsLugar"), $("#rLugar"), d.preguntas, "cómo es el lugar"); }
  catch(e){ toast(e.message, 6000); } ocupado(b, false); };
// Qué le falta al reel para poder generarse. Antes el paso 4 simplemente no aparecía y
// no había forma de saber por qué: ahora lo dice con nombre y apellido.
function faltaParaReel(){
  const faltan = [];
  (REEL && REEL.tramos || []).forEach((t, i) => {
    if(!t.audio) faltan.push(`la voz del tramo ${i + 1}`);
    else if(t.tipo === "avatar" && !t.escena) faltan.push(`la escena del tramo ${i + 1}`);
    else if(t.tipo === "avatar" && (t.dur || 0) > MAX_SEG_ELLA) faltan.push(`acortar el tramo ${i + 1}: dura ${(t.dur || 0).toFixed(1)} s y el máximo de ella es ${MAX_SEG_ELLA}`);
  });
  return faltan;
}
function listoParaReel(){
  if(!REEL || !REEL.tramos) return;
  const faltan = faltaParaReel();
  const ok = REEL.tramos.length && !faltan.length;
  $("#falta").innerHTML = ok ? "" : (REEL.tramos.length
    ? `<div class="errbox" style="margin-top:12px"><b>Todavía no puedo generar el reel.</b><br>Falta: ${faltan.map(esc).join(" · ")}.<br><span class="hint" style="margin:4px 0 0">Las voces se generan en el paso 2 y las escenas acá arriba, una por cada tramo en que ella habla.</span></div>`
    : "");
  $("#paso4").style.display = ok ? "" : "none";
  if(ok){ const rc = resumenCosto(REEL.tramos);
    $("#p4Est").innerHTML = `<table style="border-collapse:collapse;font-size:13px;margin:6px 0">${REEL.tramos.map((t, i) => `<tr><td style="padding:2px 10px 2px 0">Tramo ${i + 1}</td><td style="padding:2px 10px 2px 0">${t.tipo === "avatar" ? "👩 ella" : "🧺 producto"}</td><td style="padding:2px 10px 2px 0;text-align:right">${(t.dur || 0).toFixed(1)} s</td><td style="padding:2px 0;text-align:right">${t.tipo === "avatar" ? usd((t.dur || 0) * CFG.precio_omni_seg) : (t.ia && !(t.propios || []).length ? "clip IA " + usd(costoIa(t)) : "—")}</td></tr>`).join("")}
      <tr style="border-top:1px solid var(--line)"><td colspan="2" style="padding:4px 10px 2px 0"><b>Total</b></td><td style="padding:4px 10px 2px 0;text-align:right"><b>${rc.total} s</b></td><td style="padding:4px 0;text-align:right"><b>${usd(rc.costo)}</b></td></tr></table>
      <span class="hint">OmniHuman cobra ${usd(CFG.precio_omni_seg)} por segundo de ella hablando (${rc.ella} s). Producto, voz y armado no suman. Tarda entre 5 y 15 minutos.</span>`;
    $("#rRes").value = REEL.resolucion || "720p"; $("#rSubs").value = REEL.subtitulos === false ? "no" : "si"; if(REEL.video) pintarResultado(); }
}
$("#btnGenerar").onclick = async () => { await generar(null); };
async function generar(solo){
  const ts = solo == null ? REEL.tramos : [REEL.tramos[solo]];
  const rc = resumenCosto(ts);
  if(!confirm((solo == null ? "Generar el reel completo" : "Rehacer el tramo " + (solo + 1)) + ": ella habla " + rc.ella + " s → OmniHuman " + usd(rc.costo) + (rc.ia ? " + clips IA de producto " + usd(rc.ia) : "") + " = " + usd(rc.costo + rc.ia) + ". ¿Seguimos?")) return;
  ocupado($("#btnGenerar"), true, "Arrancando…");
  try{ await post("/reel/" + REEL.id + "/tramos", Object.assign({tramos: leerTramos(), titulo: $("#rTitulo").value, resolucion: $("#rRes").value, subtitulos: $("#rSubs").value !== "no"}, opciones())).then(d => { REEL = d.reel; });
    const d = await post("/reel/" + REEL.id + "/generar", solo == null ? {} : {tramo: solo}); JOB = d.job; $("#resultado").style.display = "none"; seguirJob(); }
  catch(e){ toast(e.message, 6000); ocupado($("#btnGenerar"), false); }
}
function relojHtml(j, ahora){ const t = Math.max(0, Math.round((ahora || Date.now() / 1000) - (j.inicio || 0))); const est = j.estimado_seg || 0;
  return `Van ${fmt(t)}${est ? " · estimado " + fmt(est) + (t > est ? " (se pasó, sigue vivo)" : "") : ""}`; }
async function seguirJob(){ clearTimeout(JOB_T);
  try{ const j = await api("/job/" + JOB);
    if(j.estado === "listo"){ $("#jobEstado").innerHTML = `<p class="ok">✅ Reel listo${j.costo ? " · US$ " + j.costo : ""}</p>`; ocupado($("#btnGenerar"), false); const r = await api("/reel/" + REEL.id); REEL = r.reel; pintarResultado(); cargarLista(); return; }
    if(j.estado === "error"){ $("#jobEstado").innerHTML = `<div class="errbox">${esc(j.error || "Falló")}</div>`; ocupado($("#btnGenerar"), false); const r = await api("/reel/" + REEL.id); REEL = r.reel; if(REEL.video) pintarResultado(); return; }
    $("#jobEstado").innerHTML = `<p class="hint"><span class="spin"></span>${esc(j.paso || "En cola…")}<br>${relojHtml(j, j.ahora)}</p>`;
  }catch(e){ $("#jobEstado").innerHTML = `<div class="errbox">${esc(e.message)}</div>`; }
  JOB_T = setTimeout(seguirJob, 5000);
}
function pintarResultado(){ $("#resultado").style.display = ""; const v = $("#video"); v.src = API + "/reel/" + REEL.id + "/video?t=" + Date.now(); $("#descargar").href = v.src;
  $("#driveLink").innerHTML = REEL.drive ? `<a class="pill" href="${esc(REEL.drive)}" target="_blank">☁️ En Drive</a>` : "";
  $("#rehacer").innerHTML = REEL.tramos.map((t, i) => `<button class="sm" onclick="generar(${i})">Tramo ${i + 1} (${t.tipo === "avatar" ? "ella" : "producto"})</button>`).join(""); }

async function abrirReel(rid){
  try{ const d = await api("/reel/" + rid); REEL = d.reel; FOTOS = []; for(let n = 0; n < (REEL.producto.n_fotos || 0); n++) FOTOS.push(API + "/reel/" + rid + "/foto/" + n);
    const p = REEL.producto; $("#url").value = REEL.fuente_url || ""; $("#pTitulo").value = p.titulo || ""; $("#pPrecio").value = p.precio || ""; $("#pDesc").value = p.descripcion || ""; $("#pTalles").value = p.talles || ""; $("#pColores").value = p.colores || ""; $("#pNotas").value = p.notas || "";
    $("#rTono").value = REEL.tono; $("#rAmb").value = REEL.ambiente; $("#rDur").value = REEL.duracion; $("#rOutfit").value = REEL.outfit || ""; $("#rMic").value = REEL.mic === false ? "no" : "si"; $("#rLook").value = REEL.look || "celular"; $("#rVoz").value = REEL.voz || ""; $("#rLugar").value = REEL.lugar || ""; $("#rCam").value = REEL.camara || "mano"; $("#rVozReal").value = REEL.voz_real === false ? "no" : "si"; $("#rEnergia").value = REEL.voz_energia || CFG.energia_default; $("#rCont").checked = REEL.continuidad !== false; $("#pregsLugar").innerHTML = ""; $("#rPlantilla").value = REEL.plantilla || ""; aplicarPlantilla(""); $("#rPlantilla").value = REEL.plantilla || "";
    $("#rMotor").value = REEL.motor_ia || CFG.motor_ia_default; $("#rMusica").value = REEL.musica || ""; $("#rMusModo").value = REEL.musica_modo || "encima"; $("#rMusDesde").value = REEL.musica_desde || 0; pintarLargoPista();
    $("#rMusVol").value = REEL.musica_vol == null ? CFG.musica_vol_default : REEL.musica_vol; $("#rMusVolTxt").textContent = $("#rMusVol").value + "%";
    $("#rPrecio").value = REEL.mostrar_precio === false ? "no" : "si"; $("#rTalles").value = REEL.mostrar_talles === false ? "no" : "si"; $("#rCta").value = REEL.cta == null ? CFG.cta_default : REEL.cta; pintarFotos();
    $("#editor").style.display = ""; $("#jobEstado").innerHTML = ""; $("#resultado").style.display = "none";
    if(REEL.tramos.length){ pintarTramos(); pintarEscenas(); paso(REEL.video ? 4 : (REEL.tramos.some(t => t.audio) ? 3 : 2)); } else paso(1);
    if(REEL.estado === "generando" && REEL.job){ JOB = REEL.job; seguirJob(); }
    if(REEL.estado === "error" && REEL.error) $("#jobEstado").innerHTML = `<div class="errbox">${esc(REEL.error)}</div>`;
  }catch(e){ toast(e.message, 5000); }
}
init().catch(e => toast(e.message, 6000));
</script>
</body>
</html>
"""

_HOME = os.environ.get("IMAGENES_PREFIX", "/imagenes").rstrip("/") or "/"
_VIDEOS = os.environ.get("VIDEOS_PREFIX", "/videos").rstrip("/") or "/videos"
HTML_PAGE = (HTML_PAGE.replace("%%API%%", API).replace("%%PJ_API%%", PJ_API)
             .replace("%%PERSONAJES%%", PJ_PREFIX or "/personajes").replace("%%HOME%%", _HOME)
             .replace("%%VIDEOS%%", _VIDEOS).replace("%%VERSION%%", VERSION)
             .replace("${MAX_PROPIOS_JS}", str(MAX_PROPIOS)))
