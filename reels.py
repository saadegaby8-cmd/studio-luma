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
import datetime as _dt
import html as _html
import json
import os
import re
import subprocess
import time
import uuid as _uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response

from imagenes_ia import (
    CURRENT_SUB,
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
from videos_luma import _duracion_video, _ffmpeg_bin, _spawn

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

ROUTE_PREFIX = os.environ.get("REELS_PREFIX", "/reels").rstrip("/")
API = ROUTE_PREFIX + "/api"
VERSION = "1.1.0"   # subí este número cada vez que cambiamos el archivo

OMNI_MODEL = os.getenv("REELS_OMNI_MODEL", "fal-ai/bytedance/omnihuman/v1.5")
PRECIO_OMNI_SEG = 0.16          # US$ por segundo de video hablado (fal, OmniHuman 1.5)
OMNI_TIMEOUT = 25 * 60          # por tramo
OMNI_MAX_SEG = 28               # audio por tramo (1080p admite 30 s; 720p, 60 s)
RESOLUCIONES = ("720p", "1080p")
DURACIONES = (25, 35, 45)
TONOS = ("cercana", "canchera", "seria", "divertida")
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
            "video": False, "propios": []}


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


def _system_guion(doc: Dict[str, Any], reel: Dict[str, Any]) -> str:
    dur = int(reel.get("duracion") or 35)
    n_tramos = 5 if dur <= 35 else 7
    palabras = int(dur * 2.3)
    tono = reel.get("tono") or "cercana"
    g = _g(doc)
    return (
        f"Sos guionista de reels de Instagram para {doc.get('marca') or 'una marca de ropa'}. "
        f"La que habla es {doc.get('nombre') or 'la influencer de la marca'}, {g['persona']}, "
        "la influencer de la marca (persona digital). Habla en primera persona, en castellano "
        "rioplatense con voseo, como si le hablara a una amiga, sin emojis y sin hashtags.\n\n"
        f"SU FICHA:\n{_ficha_texto(doc)}\n\nTono pedido para este reel: {tono}.\n\n"
        f"EL PRODUCTO:\n{_producto_texto(reel)}\n\n"
        f"ARMÁ UN GUION DE {dur} SEGUNDOS ({palabras} palabras aproximadamente) en exactamente "
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
        "- Frases cortas, ritmo de reel. Nada de 'hola chicas' ni muletillas. Sin comillas ni "
        "paréntesis adentro del texto.\n"
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
    for t in (data.get("tramos") or [])[:MAX_TRAMOS]:
        if isinstance(t, dict) and _texto(t.get("texto")):
            tramos.append(_tramo_nuevo(str(t.get("tipo") or "avatar"), t.get("texto"), t.get("muestra")))
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


async def _generar_voz(doc: Dict[str, Any], reel: Dict[str, Any], i: int) -> float:
    t = reel["tramos"][i]
    if not t.get("texto"):
        raise HTTPException(400, f"El tramo {i + 1} no tiene texto.")
    await _cobrar(COSTO_TTS)
    mp3 = await _tts_mp3(t["texto"], doc.get("voz") or "Kore", doc)
    await budget_record("reel_voz", "mp3", COSTO_TTS, 1,
                        note=f"{doc.get('nombre', '')} reel tramo {i + 1}")
    p = _audio_path(reel["id"], i)
    p.write_bytes(mp3)
    await kv.set(_k_audio(reel["id"], i), base64.b64encode(mp3).decode())
    dur = round(_duracion_video(p), 2) or round(len(t["texto"].split()) / 2.4, 2)
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


def _prompt_escena(doc: Dict[str, Any], reel: Dict[str, Any], i: int, n_refs: int,
                   n_prendas: int) -> str:
    g = _g(doc)
    amb = AMBIENTES.get(reel.get("ambiente") or "local", AMBIENTES["local"])
    k = sum(1 for t in reel["tramos"][:i] if t.get("tipo") == "avatar")
    enc = _ENCUADRES_ESCENA[k % len(_ENCUADRES_ESCENA)]
    prod = (reel.get("producto") or {}).get("titulo") or "la prenda"
    outfit = _texto(reel.get("outfit"), 200) or "ropa de todos los días, prolija y sencilla (una remera o camisa lisa)"
    partes = [
        f"Cuadro de un REEL vertical 9:16 para Instagram: {doc.get('nombre') or 'la protagonista'}, "
        f"{g['persona']}, influencer de la marca {doc.get('marca') or ''}, HABLANDO A CÁMARA como "
        "quien graba un video con el celular para presentar un producto.",
        _bloque_identidad_pj(doc, n_refs),
        f"ESCENARIO: {amb}. Es un lugar real, con profundidad y cosas de verdad alrededor.",
        f"ENCUADRE: {enc}. La cámara a la altura de sus ojos, ella bien centrada y ocupando "
        "buena parte del cuadro, boca cerrada o apenas entreabierta, expresión natural y "
        "cercana, mirada al lente.",
        f"ROPA DE ELLA: {outfit}. NO tiene puesta la prenda del producto.",
    ]
    if n_prendas:
        partes.append(
            f"EL PRODUCTO (no negociable): sobre el mostrador, a su lado y bien visible, está "
            f"apoyada la prenda de las FOTOS REALES DEL PRODUCTO (imágenes {n_refs + 1} a "
            f"{n_refs + n_prendas}): {prod}. Es EXACTAMENTE esa prenda —mismo diseño, mismo "
            "color, mismos detalles—, doblada prolija o extendida, como un producto que se "
            "está mostrando. No la rediseñes ni le cambies el color."
        )
    partes.append(
        "Foto real tomada con un celular, luz pareja y cálida del lugar, piel con textura real, "
        "sin desenfoque exagerado. Sin texto, sin logos, sin marcas de agua, sin otras personas."
    )
    return "\n\n".join(partes)


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
    prompt = _prompt_escena(doc, reel, i, len(refs), len(prendas))
    parts: List[Dict[str, Any]] = [{"text": prompt}]
    for j, (et, b64) in enumerate(refs):
        parts.append({"text": f"IMAGEN {j + 1} (referencia de identidad: {et}):"})
        parts.append(_img_part(b64))
    for j, b64 in enumerate(prendas):
        parts.append({"text": f"IMAGEN {len(refs) + j + 1} (foto real del producto, va apoyado en el mostrador):"})
        parts.append(_img_part(b64))
    img = await gemini_generate(parts, settings, aspect="9:16", image_size="2K")
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


_ENC_VIDEO = ["-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30"]
_ENC_AUDIO = ["-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "128k"]


def _prompt_omni(doc: Dict[str, Any]) -> str:
    g = _g(doc)
    return (f"The {g['woman']} talks to the camera like an influencer presenting a product in a "
            f"store, natural and warm, small hand gestures, eye contact with the lens. "
            f"{g['she'].capitalize()} keeps the same clothes, hair and background. Static "
            "handheld-feel camera. No text.")


async def _video_avatar(cli: httpx.AsyncClient, key: str, jid: str, doc: Dict[str, Any],
                        reel: Dict[str, Any], i: int) -> float:
    """Escena + audio del tramo → OmniHuman → tramo_i.mp4 (1080x1920, con NUESTRA voz)."""
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
    await _job_set(jid, {"paso": f"Subiendo la escena y la voz del tramo {i + 1}…"})
    img_url = await _fal_subir(cli, key, base64.b64decode(esc), "image/jpeg", f"reel_{rid}_esc{i}.jpg")
    au_url = await _fal_subir(cli, key, audio.read_bytes(), "audio/mpeg", f"reel_{rid}_au{i}.mp3")
    payload = {"image_url": img_url, "audio_url": au_url,
               "resolution": reel.get("resolucion") if reel.get("resolucion") in RESOLUCIONES else "720p",
               "turbo_mode": False, "prompt": _prompt_omni(doc)}
    await _fal_enviar(cli, headers, OMNI_MODEL, payload, jid, ("turbo_mode", "prompt", "resolution"))
    job = await kv.get(_k_job(jid)) or {}
    inicio = float(job.get("fal_inicio") or time.time())
    ultimo = ""
    while True:
        if time.time() - inicio > OMNI_TIMEOUT:
            raise RuntimeError(f"OmniHuman no terminó el tramo {i + 1} en {OMNI_TIMEOUT // 60} minutos.")
        rs = await cli.get(job["fal_status_url"], headers=headers)
        d = rs.json() if rs.status_code == 200 else {}
        st = d.get("status", "")
        if st in ("COMPLETED", "Completed", "succeeded", "OK"):
            break
        if st in ("FAILED", "Error", "CANCELLED"):
            raise RuntimeError(f"OmniHuman falló en el tramo {i + 1}: {rs.text[:300]}")
        if st == "IN_QUEUE":
            pos = d.get("queue_position")
            paso = f"Tramo {i + 1}: en la cola de fal" + (f", puesto {pos}" if pos is not None else "") + "…"
        else:
            paso = f"Tramo {i + 1}: OmniHuman está haciendo hablar a {doc.get('nombre') or 'la protagonista'} ({dur:.0f} s de audio)…"
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
        raise RuntimeError(f"OmniHuman no devolvió video: {json.dumps(res)[:300]}")
    dl = await cli.get(vurl, follow_redirects=True)
    if dl.status_code != 200:
        raise RuntimeError(f"fal descarga HTTP {dl.status_code}")
    crudo = _dir(rid) / f"omni_{i}.mp4"
    crudo.write_bytes(dl.content)
    # Normalizado: 1080x1920, 30 fps, y NUESTRA voz (la misma pista que oye la usuaria).
    salida = _dir(rid) / f"tramo_{i}.mp4"
    await asyncio.to_thread(_run, [
        _ff(), "-y", "-i", str(crudo), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
        "-vf", f"scale={ANCHO}:{ALTO}:force_original_aspect_ratio=increase,crop={ANCHO}:{ALTO},fps=30,format=yuv420p",
        *_ENC_VIDEO, *_ENC_AUDIO, "-t", f"{dur:.2f}", "-movflags", "+faststart", str(salida)])
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


async def _video_producto(reel: Dict[str, Any], i: int, fotos: List[Path], desde: int) -> int:
    """Tomas de producto con la voz del tramo: los VIDEOS PROPIOS de la usuaria si los
    subió (recortados al largo de la voz, repartido entre ellos), y si no, flashes de
    las fotos del producto (2,5 a 3,5 s cada uno). Devuelve el índice de la próxima
    foto a usar (así los tramos no repiten)."""
    rid = reel["id"]
    t = reel["tramos"][i]
    audio = await _asegurar_audio_en_disco(rid, i)
    dur = float(t.get("dur") or _duracion_video(audio) or 5.0)
    d = _dir(rid)
    salida = d / f"tramo_{i}.mp4"
    propios = [p for p in (t.get("propios") or []) if _propio_path(rid, str(p.get("id"))).exists()]
    if propios:
        cada = dur / len(propios)
        lineas = []
        for k, p in enumerate(propios):
            fuente = _propio_path(rid, str(p["id"]))
            clip = d / f"propio_{i}_{k}_cut.mp4"
            # Si el video es más corto que su parte, se repite hasta llenarla.
            await asyncio.to_thread(_run, [
                _ff(), "-y", "-stream_loop", "-1", "-i", str(fuente), "-t", f"{cada:.2f}",
                "-an", *_ENC_VIDEO, str(clip)], 600)
            lineas.append(f"file '{clip.name}'")
        lista = d / f"lista_{i}.txt"
        lista.write_text("\n".join(lineas) + "\n", encoding="utf-8")
        await asyncio.to_thread(_run, [
            _ff(), "-y", "-f", "concat", "-safe", "0", "-i", lista.name, "-i", audio.name,
            "-map", "0:v", "-map", "1:a", "-c:v", "copy", *_ENC_AUDIO, "-t", f"{dur:.2f}",
            "-movflags", "+faststart", salida.name], cwd=d)
        return desde
    if not fotos:
        # Sin fotos: un fondo oscuro con la voz (los subtítulos llevan el texto).
        await asyncio.to_thread(_run, [
            _ff(), "-y", "-f", "lavfi", "-i", f"color=c=0x131218:s={ANCHO}x{ALTO}:r=30",
            "-i", str(audio), "-map", "0:v", "-map", "1:a", "-t", f"{dur:.2f}",
            *_ENC_VIDEO, *_ENC_AUDIO, "-movflags", "+faststart", str(salida)])
        return desde
    n = max(1, min(len(fotos), int(round(dur / 3.0)) or 1))
    cada = dur / n
    lista = d / f"lista_{i}.txt"
    lineas = []
    for k in range(n):
        foto = fotos[(desde + k) % len(fotos)]
        clip = d / f"flash_{i}_{k}.mp4"
        await asyncio.to_thread(_clip_zoom, foto, cada, clip, (desde + k) % 2 == 0)
        lineas.append(f"file '{clip.name}'")
    lista.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    await asyncio.to_thread(_run, [
        _ff(), "-y", "-f", "concat", "-safe", "0", "-i", lista.name, "-i", audio.name,
        "-map", "0:v", "-map", "1:a", "-c:v", "copy", *_ENC_AUDIO, "-t", f"{dur:.2f}",
        "-movflags", "+faststart", salida.name], cwd=d)
    return desde + n


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


async def _armar_reel(reel: Dict[str, Any]) -> Path:
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
    vf = "format=yuv420p"
    if reel.get("subtitulos", True):
        (d / "reel.srt").write_text(_armar_srt(reel, duraciones), encoding="utf-8")
        vf = f"subtitles=reel.srt:force_style='{_ESTILO_SUB}',format=yuv420p"
    salida = d / "reel.mp4"
    await asyncio.to_thread(_run, [
        _ff(), "-y", "-f", "concat", "-safe", "0", "-i", "final.txt", "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-r", "30",
        *_ENC_AUDIO, "-movflags", "+faststart", "reel.mp4"], timeout=900, cwd=d)
    return salida


async def _procesar_reel(jid: str, rid: str, sub: Optional[str], solo: Optional[int] = None) -> None:
    set_current_sub(sub)
    try:
        reel = await _reel(rid)
        doc = await _doc(reel["pid"])
        key = await _fal_key()
        if not key:
            raise RuntimeError("Falta la API key de fal (Fotos → Ajustes → Motor FLUX, o FAL_KEY en Railway).")
        await _job_set(jid, {"estado": "generando", "paso": "Preparando las fotos del producto…"})
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
                    costo_total += await _video_avatar(cli, key, jid, doc, reel, i)
                else:
                    await _job_set(jid, {"paso": f"Tramo {i + 1} de {len(reel['tramos'])}: flashes del producto…"})
                    desde = await _video_producto(reel, i, fotos, desde)
                t["video"] = True
                reel["costo_total"] = round(costo_total, 3)
                await _guardar_reel(reel)
        await _job_set(jid, {"paso": "Pegando los tramos y quemando los subtítulos…"})
        salida = await _armar_reel(reel)
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
        else:
            seg += 15
    return seg


def _costo_estimado(reel: Dict[str, Any], solo: Optional[int] = None) -> float:
    c = 0.0
    for i, t in enumerate(reel["tramos"]):
        if solo is not None and i != solo:
            continue
        if t.get("tipo") == "avatar" and not (t.get("video") and solo is None):
            c += PRECIO_OMNI_SEG * float(t.get("dur") or 6)
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
            "duraciones": DURACIONES, "resoluciones": RESOLUCIONES, "precio_omni_seg": PRECIO_OMNI_SEG,
            "max_tramos": MAX_TRAMOS, "personajes": PJ_PREFIX, "personajes_api": PJ_API}


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
    doc = await _doc(pid)
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
        "tono": payload.get("tono") if payload.get("tono") in TONOS else (doc.get("tono") and "cercana") or "cercana",
        "ambiente": payload.get("ambiente") if payload.get("ambiente") in AMBIENTES else "local",
        "outfit": _texto(payload.get("outfit"), 200),
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
    for k in ("tono", "duracion", "ambiente", "outfit"):
        if k in payload:
            if k == "tono" and payload[k] in TONOS:
                reel[k] = payload[k]
            elif k == "duracion" and payload[k] in DURACIONES:
                reel[k] = int(payload[k])
            elif k == "ambiente" and payload[k] in AMBIENTES:
                reel[k] = payload[k]
            elif k == "outfit":
                reel[k] = _texto(payload[k], 200)
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
        if prev and nt["tipo"] == "producto":
            nt["propios"] = list(prev.get("propios") or [])   # los videos propios se quedan
            if nt["propios"] and prev.get("texto") != texto:
                nt["video"] = False
        nuevos.append(nt)
    if len(nuevos) < 1:
        raise HTTPException(400, "El guion necesita al menos un tramo con texto.")
    reel["tramos"] = nuevos
    if "titulo" in payload:
        reel["titulo"] = _texto(payload["titulo"], 80) or reel.get("titulo")
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
        if "outfit" in payload:
            reel["outfit"] = _texto(payload["outfit"], 200)
        if payload.get("ambiente") in AMBIENTES:
            reel["ambiente"] = payload["ambiente"]
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
                     {"rid": rid, "costo": costo, "titulo": titulo[:60]})
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


@router.get(API + "/job/{jid}")
async def api_job(jid: str) -> Dict[str, Any]:
    job = await kv.get(_k_job(jid))
    if not job:
        raise HTTPException(404, "Ese trabajo no está.")
    if job.get("estado") in ("en_cola", "generando") and time.time() - float(job.get("latido") or job.get("inicio") or 0) > 180:
        job = await _job_set(jid, {"estado": "error", "error": "El servidor se reinició antes de terminar. "
                                                              "Volvé a generar: los tramos que ya estaban listos se conservan."})
        try:
            reel = await _reel(str(job.get("rid") or ""))
            if reel.get("estado") == "generando":
                reel["estado"] = "error"
                reel["error"] = job["error"]
                await _guardar_reel(reel)
        except Exception:
            pass
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
    <div class="row3">
      <div><label>Tono</label><select id="rTono"></select></div>
      <div><label>Dónde está ella</label><select id="rAmb"></select></div>
      <div><label>Duración</label><select id="rDur"></select></div>
    </div>
    <label>Cómo está vestida ella (opcional)</label><input id="rOutfit" placeholder="ej: remera negra lisa y jean; o el uniforme del local">
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
    <p class="hint">Una foto vertical por cada tramo en que habla: ella en el lugar elegido, la prenda al lado sobre el mostrador. Aprobá o rehacé cada una; también podés subir la tuya.</p>
    <div id="escenas"></div>
  </div>

  <!-- PASO 4 -->
  <div class="card" id="paso4" style="display:none">
    <h2>4) Generar el reel</h2>
    <div class="row3">
      <div><label>Calidad del video de ella</label><select id="rRes"><option value="720p">720p (más rápido)</option><option value="1080p">1080p</option></select></div>
      <div><label>Subtítulos</label><select id="rSubs"><option value="si">Sí, quemados</option><option value="no">No</option></select></div>
      <div><label>&nbsp;</label><button class="go" id="btnGenerar">🎞️ Generar reel</button></div>
    </div>
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
  sel.onchange = () => { PID = sel.value; cargarLista(); };
  PID = sel.value || null;
  $("#rTono").innerHTML = CFG.tonos.map(t => `<option value="${t}">${t[0].toUpperCase() + t.slice(1)}</option>`).join("");
  $("#rAmb").innerHTML = Object.entries(CFG.ambientes).map(([k, v]) => `<option value="${k}">${esc(v[0].toUpperCase() + v.slice(1))}</option>`).join("");
  $("#rDur").innerHTML = CFG.duraciones.map(d => `<option value="${d}" ${d === 35 ? "selected" : ""}>${d} segundos</option>`).join("");
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

function paso(n){ $$(".pasos .p").forEach(p => { const k = +p.dataset.p; p.classList.toggle("on", k === n); }); [1,2,3,4].forEach(k => $("#paso" + k).style.display = (k <= n) ? "" : "none"); $("#paso" + n).scrollIntoView({behavior: "smooth", block: "start"}); }
$$(".pasos .p").forEach(p => p.onclick = () => { const k = +p.dataset.p; if(k === 1 || (REEL && (k === 2 ? REEL.tramos.length : k === 3 ? REEL.tramos.some(t => t.audio) : REEL.tramos.length))) paso(k); });

$("#btnNuevo").onclick = () => { if(!PID) return toast("Primero creá un personaje en la pestaña Personajes."); REEL = null; FOTOS = [];
  ["url","pTitulo","pPrecio","pDesc","pTalles","pColores","pNotas","rOutfit"].forEach(id => $("#" + id).value = ""); $("#pFotos").innerHTML = ""; $("#p1Est").textContent = "";
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
    const comun = {tono: $("#rTono").value, ambiente: $("#rAmb").value, duracion: +$("#rDur").value, outfit: $("#rOutfit").value};
    if(!REEL){ const d = await post("/" + PID + "/nuevo", Object.assign({producto: prod, fotos: FOTOS, fuente_url: $("#url").value.trim()}, comun)); REEL = d.reel; }
    const d2 = await post("/reel/" + REEL.id + "/guion", Object.assign({notas: prod.notas}, comun)); REEL = d2.reel;
    pintarTramos(); paso(2); cargarLista();
  }catch(e){ toast(e.message, 5000); } ocupado($("#btnGuion"), false); };

function pintarTramos(){
  $("#rTitulo").value = REEL.titulo || "";
  $("#tramos").innerHTML = REEL.tramos.map((t, i) => `<div class="tramo ${t.tipo}" data-i="${i}">
    <div class="top"><span class="n">Tramo ${i + 1}</span>
      <select class="tipo"><option value="avatar" ${t.tipo === "avatar" ? "selected" : ""}>👩 Ella a cámara</option><option value="producto" ${t.tipo === "producto" ? "selected" : ""}>🧺 Producto (sin gente)</option></select>
      <span class="dur">${t.audio ? "🎙️ " + t.dur + " s" : "sin voz"}${t.audio ? ` <button class="sm" onclick="oir(${i})">▶</button>` : ""}</span>
      <button class="sm" onclick="quitarTramo(${i})">🗑</button></div>
    <textarea class="texto" rows="2">${esc(t.texto)}</textarea>
    <input class="muestra" placeholder="Qué se ve en pantalla (sólo en los de producto)" value="${esc(t.muestra || "")}" style="margin-top:6px;${t.tipo === "producto" ? "" : "display:none"}">
    <div class="propios" style="margin-top:8px;${t.tipo === "producto" ? "" : "display:none"}">
      <div class="hint" style="margin:0 0 4px">🎥 <b>Tus videos reales</b> para este tramo (primeros planos, costuras, tela). Si subís, reemplazan a los flashes de fotos; se recortan al vertical y al largo de la voz.</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
        ${(t.propios || []).map(p => `<span class="pill">${esc(p.nombre || "video")} · ${p.dur} s <a href="${API}/reel/${REEL.id}/tramo/${i}/video_propio/${p.id}" target="_blank">▶</a> <a href="#" onclick="borrarPropio(${i},'${p.id}');return false">✕</a></span>`).join("")}
        ${(t.propios || []).length < ${MAX_PROPIOS_JS} ? `<label class="sm" style="margin:0"><input type="file" accept="video/*" multiple style="display:none" onchange="subirPropios(${i},this)"><button class="sm" onclick="this.previousElementSibling.click()">⬆️ Subir mis videos</button></label>` : ""}
      </div>
    </div>
  </div>`).join("");
  $$("#tramos .tipo").forEach(s => s.onchange = () => { const c = s.closest(".tramo"); c.className = "tramo " + s.value; c.querySelector(".muestra").style.display = s.value === "producto" ? "" : "none"; c.querySelector(".propios").style.display = s.value === "producto" ? "" : "none"; });
  const tot = REEL.tramos.reduce((a, t) => a + (t.dur || 0), 0);
  $("#p2Est").textContent = REEL.tramos.every(t => t.audio) && REEL.tramos.length ? `Duración total: ${tot.toFixed(1)} s` : "";
}
function leerTramos(){ return $$("#tramos .tramo").map(c => ({tipo: c.querySelector(".tipo").value, texto: c.querySelector(".texto").value, muestra: c.querySelector(".muestra").value})); }
function quitarTramo(i){ const ts = leerTramos(); ts.splice(i, 1); REEL.tramos = ts.map((t, k) => Object.assign({dur: 0, audio: false, escena: false, video: false}, REEL.tramos[k] && REEL.tramos[k].texto === t.texto ? REEL.tramos[k] : {}, t)); pintarTramos(); }
$("#btnAddTramo").onclick = () => { REEL.tramos = leerTramos().map((t, k) => Object.assign({dur: 0, audio: false, escena: false, video: false}, REEL.tramos[k] || {}, t)); REEL.tramos.push({tipo: "producto", texto: "", muestra: "", dur: 0, audio: false, escena: false, video: false}); pintarTramos(); };
async function guardarTramos(){ const d = await post("/reel/" + REEL.id + "/tramos", {tramos: leerTramos(), titulo: $("#rTitulo").value}); REEL = d.reel; pintarTramos(); }
$("#btnGuardarTramos").onclick = async () => { try{ await guardarTramos(); toast("Guion guardado."); }catch(e){ toast(e.message, 5000); } };
$("#btnVoces").onclick = async () => { ocupado($("#btnVoces"), true, "Grabando las voces…");
  try{ await guardarTramos(); const d = await post("/reel/" + REEL.id + "/voces", {todas: true}); REEL = d.reel; pintarTramos(); pintarEscenas(); paso(3); toast(`Voces listas: ${d.dur_total} s en total.`); }
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
      <div style="flex:1;min-width:200px"><div style="display:flex;gap:6px;flex-wrap:wrap"><button class="go sm" id="gen${i}">${t.escena ? "🔁 Rehacer" : "✨ Generar escena"}</button><label class="sm" style="margin:0"><input type="file" accept="image/*" id="sub${i}" style="display:none"><button class="sm" onclick="document.getElementById('sub${i}').click()">⬆️ Subir la mía</button></label></div>
      <p class="hint" id="est${i}">${t.escena ? "Lista." : "Todavía no tiene escena."}</p></div></div>`;
    E.appendChild(d);
    d.querySelector("#gen" + i).onclick = async () => { const b = d.querySelector("#gen" + i); ocupado(b, true, "Nano Banana…");
      try{ const r = await post("/reel/" + REEL.id + "/escena/" + i, {outfit: $("#rOutfit").value, ambiente: $("#rAmb").value}); REEL = r.reel; const im = d.querySelector("#esc" + i); im.src = r.src; im.style.display = ""; d.querySelector("#est" + i).textContent = "Lista."; b._t = "🔁 Rehacer"; listoParaReel(); }
      catch(e){ toast(e.message, 6000); } ocupado(b, false); };
    d.querySelector("#sub" + i).onchange = async e => { const f = e.target.files[0]; if(!f) return;
      try{ const src = await achicar(f, 1920); const r = await post("/reel/" + REEL.id + "/escena/" + i, {imagen: src}); REEL = r.reel; const im = d.querySelector("#esc" + i); im.src = src; im.style.display = ""; d.querySelector("#est" + i).textContent = "Lista (subida)."; listoParaReel(); }
      catch(err){ toast(err.message, 6000); } };
  });
  listoParaReel();
}
function listoParaReel(){
  const ok = REEL.tramos.length && REEL.tramos.every(t => t.audio && (t.tipo !== "avatar" || t.escena));
  $("#paso4").style.display = ok ? "" : "none";
  if(ok){ const seg = REEL.tramos.filter(t => t.tipo === "avatar").reduce((a, t) => a + (t.dur || 0), 0);
    $("#p4Est").textContent = `${REEL.tramos.length} tramos · ${REEL.tramos.reduce((a, t) => a + (t.dur || 0), 0).toFixed(1)} s · ella habla ${seg.toFixed(1)} s → OmniHuman ≈ US$ ${(seg * CFG.precio_omni_seg).toFixed(2)}. Tarda entre 5 y 15 minutos.`;
    $("#rRes").value = REEL.resolucion || "720p"; $("#rSubs").value = REEL.subtitulos === false ? "no" : "si"; if(REEL.video) pintarResultado(); }
}
$("#btnGenerar").onclick = async () => { await generar(null); };
async function generar(solo){
  ocupado($("#btnGenerar"), true, "Arrancando…");
  try{ await post("/reel/" + REEL.id + "/tramos", {tramos: leerTramos(), titulo: $("#rTitulo").value, resolucion: $("#rRes").value, subtitulos: $("#rSubs").value !== "no"}).then(d => { REEL = d.reel; });
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
    $("#rTono").value = REEL.tono; $("#rAmb").value = REEL.ambiente; $("#rDur").value = REEL.duracion; $("#rOutfit").value = REEL.outfit || ""; pintarFotos();
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
