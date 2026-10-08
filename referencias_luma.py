# -*- coding: utf-8 -*-
"""
referencias_luma.py — El KIT DE REFERENCIAS de Studio Luma: "Mis lugares" y "Mis prendas"
=========================================================================================

Hasta ahora el lugar de un reel era sólo TEXTO ("su dormitorio", "un local chico"), así que
cada reel inventaba otro cuarto y entre toma y toma a veces cambiaba. Acá cada cuenta guarda
sus LUGARES de verdad: 1 a 4 vistas del MISMO lugar, vacío (sin gente), y los reels lo usan
como referencia de imagen (la foto clave lo copia y Kling lo recibe como @Image1).

Dos maneras de armar un lugar:
- con FOTOS REALES (el local, el depósito, el probador de la marca): lo más realista;
- GENERADO por la IA (Nano Banana) a partir de una descripción: la primera vista sale del
  texto y las demás de esa primera, así es el mismo lugar.

Las vistas: plano abierto (todo el lugar), plano medio, un rincón/detalle y el espejo (para
probadores y dormitorios). Cada toma usa la que coincide con su plano.

MIS PRENDAS (la ficha de producto): cada prenda se carga UNA vez (nombre, descripción y sus
colores, con 1 a 3 fotos cada uno) y, por personaje, su PROBADOR: el cuerpo de ella sin cabeza
con ese color puesto, de frente, de espalda y de 3/4, sobre gris. Acá se ve, se aprueba o se
rehace (con lo que hay que corregir). Filmado, Reels y Cambio de conjunto eligen la prenda de
la lista y usan ese probador tal cual, sin volver a pagarlo.

Se guarda en el KV de la cuenta (base64), igual que las fotos de los personajes.
"""

import asyncio
import base64
import io
import os
import time
import uuid as _uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response

from imagenes_ia import (
    _compress_ref,
    _strip_data_url,
    _pfx,
    budget_check,
    budget_record,
    gemini_generate,
    get_settings,
    kv,
    session_sub_from_request,
    set_current_sub,
)

VERSION = "1.1.0"
ROUTE_PREFIX = os.environ.get("REFERENCIAS_PREFIX", "/referencias").rstrip("/")
API = ROUTE_PREFIX + "/api"

MAX_LUGARES = 12
VISTAS: Dict[str, Dict[str, str]] = {
    "abierto": {"nombre": "Plano abierto (todo el lugar)",
                "en": "a WIDE view of the whole place from the doorway, eye level"},
    "medio": {"nombre": "Plano medio",
              "en": "a MEDIUM view of the main area of the same place, eye level, a few steps closer"},
    "rincon": {"nombre": "Un rincón / detalle",
               "en": "a CLOSER view of one corner of the same place (furniture and decoration detail)"},
    "espejo": {"nombre": "El espejo",
               "en": "a view of the full-length mirror of the same place (nobody reflected in it)"},
}
COSTO_VISTA = 0.04          # Nano Banana, una imagen (aprox.)


def _k_idx() -> str:
    return _pfx() + "refs:lugares"


def _k_img(lid: str, vista: str) -> str:
    return _pfx() + f"refs:lugar:{lid}:{vista}"


async def lugares() -> List[Dict[str, Any]]:
    d = await kv.get(_k_idx())
    return [x for x in (d or []) if isinstance(x, dict) and x.get("id")]


async def lugar(lid: str) -> Optional[Dict[str, Any]]:
    return next((x for x in await lugares() if x["id"] == lid), None)


async def vista_b64(lid: str, vista: str) -> Optional[str]:
    return await kv.get(_k_img(lid, vista))


async def vista_para(lid: str, plano: str = "") -> Optional[str]:
    """La vista del lugar que mejor va con el plano de una toma (abierto para los planos
    enteros; medio para los demás). Si esa no está, la que haya."""
    meta = await lugar(lid)
    if not meta:
        return None
    hay = list(meta.get("vistas") or [])
    cerca = plano in ("primer", "detalle", "macro", "cerca", "pecho")
    if plano == "espejo":
        orden = ["espejo", "medio", "abierto", "rincon"]
    elif cerca:
        orden = ["medio", "rincon", "abierto", "espejo"]
    else:
        orden = ["abierto", "medio", "espejo", "rincon"]
    for v in orden + hay:
        if v in hay:
            b = await vista_b64(lid, v)
            if b:
                return b
    return None


async def _guardar_idx(lst: List[Dict[str, Any]]) -> None:
    await kv.set(_k_idx(), lst[:MAX_LUGARES])


def _a_jpeg(data: bytes) -> str:
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=94)
    return _compress_ref(buf.getvalue(), max_dim=1600, q=90)


_PROMPT_PRIMERA = (
    "Photorealistic photo taken with a phone of this real place: {desc}. The place is EMPTY: no "
    "people, no mannequins, no body parts, nobody in mirrors. Shot as {vista}. Natural real light, "
    "real textures, lived-in and believable (not a 3D render, not a showroom catalogue), vertical "
    "9:16 framing. No text, no logos, no watermark."
)
_PROMPT_OTRA = (
    "The reference image is a real place. Photograph EXACTLY the same place — same walls, floor, "
    "furniture, objects, colours, light and time of day — now as {vista}. It must be unmistakably "
    "the same room. EMPTY: no people, no mannequins, nobody in mirrors. Phone photo, natural light, "
    "vertical 9:16. No text, no logos, no watermark."
)


async def _generar_vista(desc: str, vista: str, base: Optional[str]) -> str:
    settings = dict(await get_settings())
    v_en = VISTAS[vista]["en"]
    if base:
        parts = [{"text": _PROMPT_OTRA.format(vista=v_en)},
                 {"inlineData": {"mimeType": "image/jpeg", "data": base}}]
    else:
        parts = [{"text": _PROMPT_PRIMERA.format(desc=desc, vista=v_en)}]
    ok, motivo, _, _ = await budget_check(COSTO_VISTA)
    if not ok:
        raise HTTPException(402, motivo)
    img = await gemini_generate(parts, settings, "9:16", "2K", save_prompt=False)
    await budget_record("referencias_lugar", "nano", COSTO_VISTA, 1, note=f"Mis lugares: vista {vista}")
    return _compress_ref(img, max_dim=1600, q=90)


# ─────────────────────────────────────────────────────────────────────────────
# MIS PRENDAS (ficha de producto + probador por personaje)
# ─────────────────────────────────────────────────────────────────────────────

MAX_PRENDAS = 40
MAX_COLORES_PRENDA = 8
MAX_FOTOS_COLOR = 3
VISTAS_PROBADOR = ["frente", "espalda", "3/4"]


def _k_prendas() -> str:
    return _pfx() + "refs:prendas"


def _k_pfoto(fid: str, ci: int, n: int) -> str:
    return _pfx() + f"refs:prenda:{fid}:{ci}:foto:{n}"


def _k_pb(fid: str, ci: int, pid: str) -> str:
    """El probador de ese color para ese personaje: {imgs, aprobado, estado, error, ts}."""
    return _pfx() + f"refs:prenda:{fid}:{ci}:probador:{pid}"


async def prendas() -> List[Dict[str, Any]]:
    d = await kv.get(_k_prendas())
    return [x for x in (d or []) if isinstance(x, dict) and x.get("id")]


async def prenda(fid: str) -> Optional[Dict[str, Any]]:
    return next((x for x in await prendas() if x["id"] == fid), None)


async def fotos_color(fid: str, ci: int) -> List[str]:
    """Las fotos de un color de una prenda, tal cual se guardaron (las usan los módulos)."""
    x = await prenda(fid)
    if not x or not (0 <= ci < len(x.get("colores") or [])):
        return []
    n = int(x["colores"][ci].get("n") or 0)
    return [b for b in [await kv.get(_k_pfoto(fid, ci, i)) for i in range(n)] if b]


async def probador_de(fid: str, ci: int, pid: str) -> List[str]:
    """El probador ya hecho de ese color para ese personaje ([] si no hay)."""
    r = await kv.get(_k_pb(fid, int(ci), pid))
    return list(r.get("imgs") or []) if isinstance(r, dict) else []


async def guardar_probador(fid: str, ci: int, pid: str, imgs: List[str], aprobado: bool = False) -> None:
    await kv.set(_k_pb(fid, int(ci), pid), {"imgs": imgs, "aprobado": aprobado, "estado": "", "error": "",
                                             "ts": time.time()})


def ficha_valida(v: Any) -> Optional[List[Any]]:
    """[id de la prenda, color] si viene bien formado (lo que guardan los reels)."""
    if isinstance(v, (list, tuple)) and len(v) == 2 and str(v[0]).isalnum() and len(str(v[0])) <= 16:
        try:
            return [str(v[0]), int(v[1])]
        except (TypeError, ValueError):
            return None
    return None


async def _guardar_prendas(lst: List[Dict[str, Any]]) -> None:
    await kv.set(_k_prendas(), lst[:MAX_PRENDAS])


async def _guardar_fotos(fid: str, ci: int, fotos: List[Any]) -> int:
    n = 0
    for f in fotos[:MAX_FOTOS_COLOR]:
        try:
            b = await asyncio.to_thread(_a_jpeg, base64.b64decode(_strip_data_url(str(f))))
        except Exception:
            raise HTTPException(400, "No pude leer una de las fotos.")
        await kv.set(_k_pfoto(fid, ci, n), b)
        n += 1
    return n


# ─────────────────────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────────────────────

async def _bind(request: Request) -> None:
    set_current_sub(session_sub_from_request(request))


router = APIRouter(dependencies=[Depends(_bind)])


def _publico(x: Dict[str, Any]) -> Dict[str, Any]:
    return {**x, "urls": {v: f"{API}/lugares/{x['id']}/vista/{v}?t={int(x.get('ts', 0))}" for v in x.get("vistas", [])}}


@router.get(API + "/lugares")
async def api_lugares() -> Dict[str, Any]:
    return {"lugares": [_publico(x) for x in await lugares()], "vistas": {k: v["nombre"] for k, v in VISTAS.items()},
            "max": MAX_LUGARES, "costo_vista": COSTO_VISTA}


@router.post(API + "/lugares")
async def api_crear(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    nombre = str(payload.get("nombre") or "").strip()[:60]
    if not nombre:
        raise HTTPException(400, "Ponele un nombre al lugar (ej.: Mi local, El probador).")
    lst = await lugares()
    if len(lst) >= MAX_LUGARES:
        raise HTTPException(400, f"Llegaste al máximo de {MAX_LUGARES} lugares: borrá alguno.")
    x = {"id": _uuid.uuid4().hex[:10], "nombre": nombre, "desc": str(payload.get("desc") or "").strip()[:600],
         "vistas": [], "ts": time.time()}
    await _guardar_idx(lst + [x])
    return {"lugar": _publico(x)}


@router.put(API + "/lugares/{lid}")
async def api_editar(lid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    lst = await lugares()
    x = next((y for y in lst if y["id"] == lid), None)
    if not x:
        raise HTTPException(404, "Ese lugar no existe.")
    if "nombre" in payload:
        x["nombre"] = str(payload["nombre"] or "").strip()[:60] or x["nombre"]
    if "desc" in payload:
        x["desc"] = str(payload["desc"] or "").strip()[:600]
    await _guardar_idx(lst)
    return {"lugar": _publico(x)}


@router.post(API + "/lugares/{lid}/vista/{vista}")
async def api_subir_vista(lid: str, vista: str, archivo: UploadFile = File(...)) -> Dict[str, Any]:
    """Una foto REAL del lugar para esa vista (reemplaza la que hubiera)."""
    if vista not in VISTAS:
        raise HTTPException(400, "Vista desconocida.")
    lst = await lugares()
    x = next((y for y in lst if y["id"] == lid), None)
    if not x:
        raise HTTPException(404, "Ese lugar no existe.")
    data = await archivo.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(400, "La foto pesa más de 20 MB.")
    try:
        b64 = await asyncio.to_thread(_a_jpeg, data)
    except Exception:
        raise HTTPException(400, "No pude leer esa foto.")
    await kv.set(_k_img(lid, vista), b64)
    if vista not in x["vistas"]:
        x["vistas"].append(vista)
    x["ts"] = time.time()
    await _guardar_idx(lst)
    return {"lugar": _publico(x)}


@router.post(API + "/lugares/{lid}/generar")
async def api_generar(lid: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Genera con IA las vistas que faltan (o las pedidas). La primera sale de la descripción;
    las demás, de la primera (el mismo lugar)."""
    lst = await lugares()
    x = next((y for y in lst if y["id"] == lid), None)
    if not x:
        raise HTTPException(404, "Ese lugar no existe.")
    pedidas = [v for v in (payload.get("vistas") or ["abierto", "medio", "rincon"]) if v in VISTAS]
    if not x.get("vistas") and not x.get("desc"):
        raise HTTPException(400, "Escribí cómo es el lugar (o subí una foto) para poder generarlo.")
    base = None
    for v in ("abierto", "medio", "rincon", "espejo"):
        if v in x["vistas"]:
            base = await vista_b64(lid, v)
            break
    hechas = []
    for v in pedidas:
        if v in x["vistas"] and not payload.get("rehacer"):
            continue
        b64 = await _generar_vista(x.get("desc") or x["nombre"], v, base)
        await kv.set(_k_img(lid, v), b64)
        if v not in x["vistas"]:
            x["vistas"].append(v)
        base = base or b64
        hechas.append(v)
    x["ts"] = time.time()
    await _guardar_idx(lst)
    return {"lugar": _publico(x), "hechas": hechas}


@router.get(API + "/lugares/{lid}/vista/{vista}")
async def api_ver_vista(lid: str, vista: str):
    b = await vista_b64(lid, vista)
    if not b:
        raise HTTPException(404, "Esa vista no existe.")
    return Response(base64.b64decode(b), media_type="image/jpeg")


@router.delete(API + "/lugares/{lid}/vista/{vista}")
async def api_borrar_vista(lid: str, vista: str) -> Dict[str, Any]:
    lst = await lugares()
    x = next((y for y in lst if y["id"] == lid), None)
    if not x:
        raise HTTPException(404, "Ese lugar no existe.")
    await kv.delete(_k_img(lid, vista))
    x["vistas"] = [v for v in x["vistas"] if v != vista]
    await _guardar_idx(lst)
    return {"lugar": _publico(x)}


@router.delete(API + "/lugares/{lid}")
async def api_borrar(lid: str) -> Dict[str, Any]:
    lst = await lugares()
    x = next((y for y in lst if y["id"] == lid), None)
    if x:
        for v in x.get("vistas") or []:
            await kv.delete(_k_img(lid, v))
    await _guardar_idx([y for y in lst if y["id"] != lid])
    return {"ok": True}


def _prenda_publica(x: Dict[str, Any]) -> Dict[str, Any]:
    t = int(x.get("ts", 0))
    return {**x, "colores": [{**c, "urls": [f"{API}/prendas/{x['id']}/foto/{ci}/{n}.jpg?t={t}" for n in range(int(c.get("n") or 0))]}
                             for ci, c in enumerate(x.get("colores") or [])]}


@router.get(API + "/prendas")
async def api_prendas(pid: str = "") -> Dict[str, Any]:
    """La lista de prendas; con ?pid=, el estado del probador de cada color para ese personaje."""
    out = []
    for x in await prendas():
        px = _prenda_publica(x)
        if pid:
            for ci, c in enumerate(px["colores"]):
                r = await kv.get(_k_pb(x["id"], ci, pid))
                r = r if isinstance(r, dict) else {}
                if r.get("estado") == "haciendo" and _k_pb(x["id"], ci, pid) not in _HACIENDO:
                    r.update({"estado": "error", "error": "Se cortó (se reinició el servidor): tocá Rehacer."})
                t = int(r.get("ts", 0))
                c["probador"] = {"n": len(r.get("imgs") or []), "aprobado": bool(r.get("aprobado")),
                                 "estado": r.get("estado") or "", "error": r.get("error") or "",
                                 "urls": [f"{API}/prendas/{x['id']}/probador/{ci}/{pid}/{i}.jpg?t={t}"
                                          for i in range(len(r.get("imgs") or []))]}
        out.append(px)
    return {"prendas": out, "max": MAX_PRENDAS, "max_colores": MAX_COLORES_PRENDA, "vistas_probador": VISTAS_PROBADOR}


@router.post(API + "/prendas")
async def api_prenda_nueva(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    nombre = str(payload.get("nombre") or "").strip()[:80]
    if not nombre:
        raise HTTPException(400, "Ponele un nombre a la prenda (ej.: Conjunto encaje Sofía).")
    lst = await prendas()
    if len(lst) >= MAX_PRENDAS:
        raise HTTPException(400, f"Llegaste al máximo de {MAX_PRENDAS} prendas: borrá alguna.")
    colores_in = [c for c in (payload.get("colores") or []) if isinstance(c, dict) and c.get("fotos")][:MAX_COLORES_PRENDA]
    if not colores_in:
        raise HTTPException(400, "Subí al menos un color con sus fotos (mejor frente y espalda).")
    fid = _uuid.uuid4().hex[:10]
    colores = []
    for ci, c in enumerate(colores_in):
        n = await _guardar_fotos(fid, ci, c.get("fotos") or [])
        colores.append({"nombre": str(c.get("nombre") or "").strip()[:40] or f"Color {ci + 1}", "n": n})
    x = {"id": fid, "nombre": nombre, "desc": str(payload.get("desc") or "").strip()[:1500],
         "colores": colores, "ts": time.time()}
    await _guardar_prendas(lst + [x])
    return {"prenda": _prenda_publica(x)}


@router.put(API + "/prendas/{fid}")
async def api_prenda_editar(fid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    lst = await prendas()
    x = next((y for y in lst if y["id"] == fid), None)
    if not x:
        raise HTTPException(404, "Esa prenda no existe.")
    if "nombre" in payload:
        x["nombre"] = str(payload["nombre"] or "").strip()[:80] or x["nombre"]
    if "desc" in payload:
        x["desc"] = str(payload["desc"] or "").strip()[:1500]
    await _guardar_prendas(lst)
    return {"prenda": _prenda_publica(x)}


@router.post(API + "/prendas/{fid}/color")
async def api_prenda_color(fid: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Suma un color (al final: los colores no se reordenan, los reels los nombran por número)."""
    lst = await prendas()
    x = next((y for y in lst if y["id"] == fid), None)
    if not x:
        raise HTTPException(404, "Esa prenda no existe.")
    if len(x["colores"]) >= MAX_COLORES_PRENDA:
        raise HTTPException(400, f"Hasta {MAX_COLORES_PRENDA} colores por prenda.")
    if not payload.get("fotos"):
        raise HTTPException(400, "Subí las fotos de ese color.")
    ci = len(x["colores"])
    n = await _guardar_fotos(fid, ci, payload.get("fotos") or [])
    x["colores"].append({"nombre": str(payload.get("nombre") or "").strip()[:40] or f"Color {ci + 1}", "n": n})
    x["ts"] = time.time()
    await _guardar_prendas(lst)
    return {"prenda": _prenda_publica(x)}


@router.get(API + "/prendas/{fid}/foto/{ci}/{n}.jpg")
async def api_prenda_foto(fid: str, ci: int, n: int):
    b = await kv.get(_k_pfoto(fid, ci, n))
    if not b:
        raise HTTPException(404, "Esa foto no existe.")
    return Response(base64.b64decode(b), media_type="image/jpeg")


@router.delete(API + "/prendas/{fid}")
async def api_prenda_borrar(fid: str) -> Dict[str, Any]:
    lst = await prendas()
    x = next((y for y in lst if y["id"] == fid), None)
    if x:
        for ci, c in enumerate(x.get("colores") or []):
            for n in range(int(c.get("n") or 0)):
                await kv.delete(_k_pfoto(fid, ci, n))
    await _guardar_prendas([y for y in lst if y["id"] != fid])
    return {"ok": True}


_HACIENDO: Dict[str, float] = {}


async def _hacer_probador(sub: Optional[str], fid: str, ci: int, pid: str, correccion: str) -> None:
    set_current_sub(sub)
    import filmado as _film            # acá adentro: filmado importa este módulo
    from personajes import _doc, _refs_identidad
    k = _k_pb(fid, ci, pid)
    try:
        doc = await _doc(pid)
        refs = await _refs_identidad(doc)
        fotos = await fotos_color(fid, ci)
        if not fotos:
            raise RuntimeError("Ese color no tiene fotos.")
        pb = await _film.probador_para(doc, fotos, refs, forzar=True, correccion=correccion)
        if not pb:
            raise RuntimeError("El personaje no tiene cuerpo entero de frente en su hoja: hacelo en su ficha.")
        await guardar_probador(fid, ci, pid, pb)
    except Exception as e:
        r = await kv.get(k)
        r = r if isinstance(r, dict) else {}
        r.update({"estado": "error", "error": str(getattr(e, "detail", "") or e)[:300]})
        await kv.set(k, r)
    finally:
        _HACIENDO.pop(k, None)


@router.post(API + "/prendas/{fid}/probador/{ci}")
async def api_probador_hacer(fid: str, ci: int, request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Hace (o rehace, con lo que hay que corregir) el probador de ese color para un personaje."""
    pid = str(payload.get("pid") or "")
    x = await prenda(fid)
    if not x or not (0 <= ci < len(x.get("colores") or [])):
        raise HTTPException(404, "Esa prenda o ese color no existe.")
    if not pid:
        raise HTTPException(400, "Elegí el personaje.")
    k = _k_pb(fid, ci, pid)
    if k in _HACIENDO:
        raise HTTPException(409, "Ya se está haciendo ese probador.")
    r = await kv.get(k)
    r = r if isinstance(r, dict) else {"imgs": []}
    r.update({"estado": "haciendo", "error": "", "aprobado": False})
    await kv.set(k, r)
    _HACIENDO[k] = time.time()
    from videos_luma import _spawn
    _spawn(_hacer_probador(session_sub_from_request(request), fid, ci, pid,
                           str(payload.get("correccion") or "").strip()[:400]))
    return {"ok": True}


@router.post(API + "/prendas/{fid}/probador/{ci}/aprobar")
async def api_probador_aprobar(fid: str, ci: int, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    k = _k_pb(fid, ci, str(payload.get("pid") or ""))
    r = await kv.get(k)
    if not isinstance(r, dict) or not r.get("imgs"):
        raise HTTPException(404, "Todavía no hay probador para aprobar.")
    r["aprobado"] = payload.get("aprobado") is not False
    await kv.set(k, r)
    return {"ok": True}


@router.get(API + "/prendas/{fid}/probador/{ci}/{pid}/{i}.jpg")
async def api_probador_img(fid: str, ci: int, pid: str, i: int):
    imgs = await probador_de(fid, ci, pid)
    if not (0 <= i < len(imgs)):
        raise HTTPException(404, "Esa vista del probador no existe.")
    return Response(base64.b64decode(imgs[i]), media_type="image/jpeg")


@router.get(ROUTE_PREFIX, response_class=HTMLResponse)
async def ui() -> HTMLResponse:
    return HTMLResponse(PAGINA.replace("%%API%%", API).replace("%%VERSION%%", VERSION))


PAGINA = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Referencias · Studio Luma</title>
<link href="https://fonts.googleapis.com/css2?family=Bodoni+Moda:wght@500;600&family=Jost:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root{--ink:#ecebf1;--ink-soft:#96919f;--line:#2c2a34;--ivory:#131218;--card:#1b1a21;--card-2:#232128;--rose:#c9a86b;--rose-deep:#d8b878;--ok:#5fae86;--bad:#e0736f}
  *{box-sizing:border-box} body{margin:0;background:var(--ivory);color:var(--ink);font-family:Jost,system-ui,sans-serif;font-size:16px;line-height:1.55}
  main{max-width:1080px;margin:0 auto;padding:16px} .card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:18px;margin-bottom:16px}
  h2{font-family:'Bodoni Moda',serif;font-weight:600;font-size:22px;margin:0 0 6px} h3{font-size:16px;margin:0 0 4px;font-weight:500}
  .hint{color:var(--ink-soft);font-size:13px;margin:4px 0 8px} label{display:block;font-size:13px;color:var(--ink-soft);margin:10px 0 4px}
  input,textarea,select{width:100%;background:var(--card-2);color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:10px 12px;font:inherit;font-size:15px}
  button{background:var(--card-2);color:var(--ink);border:1px solid var(--line);border-radius:999px;padding:9px 16px;font:inherit;font-size:14px;cursor:pointer}
  button.go{background:linear-gradient(150deg,var(--rose-deep),var(--rose));color:#17140d;border:none;font-weight:500} button:disabled{opacity:.5}
  button.sm{padding:6px 12px;font-size:13px}
  .vistas{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:10px} @media(max-width:640px){.vistas{grid-template-columns:repeat(2,1fr)}}
  .v{border:1px dashed var(--line);border-radius:12px;padding:8px;text-align:center;font-size:12px;color:var(--ink-soft);background:var(--card-2)}
  .v img{width:100%;aspect-ratio:9/16;object-fit:cover;border-radius:8px;display:block;margin-bottom:6px}
  .v .vacio{width:100%;aspect-ratio:9/16;border-radius:8px;display:flex;align-items:center;justify-content:center;background:var(--card);margin-bottom:6px;font-size:26px}
  .fila{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px} a{color:var(--rose-deep)}
  .mal{color:var(--bad)} .bien{color:var(--ok)}
  .tabs{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px} .tabs button.on{background:linear-gradient(150deg,var(--rose-deep),var(--rose));color:#17140d;border:none}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:10px} @media(max-width:640px){.row{grid-template-columns:1fr}}
  .thumbs{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px} .thumbs img{width:56px;height:74px;object-fit:cover;border-radius:6px;border:1px solid var(--line)}
  .color{border:1px solid var(--line);border-radius:14px;padding:12px;margin-top:10px;background:var(--card-2)}
  .pb{display:grid;grid-template-columns:repeat(3,minmax(0,150px));gap:8px;margin-top:8px}
  .pb figure{margin:0;text-align:center;font-size:12px;color:var(--ink-soft)} .pb img{width:100%;aspect-ratio:3/4;object-fit:cover;border-radius:8px;background:#777;display:block}
  .spin{display:inline-block;width:12px;height:12px;border:2px solid var(--ink-soft);border-top-color:transparent;border-radius:50%;animation:g 1s linear infinite;vertical-align:middle;margin-right:6px} @keyframes g{to{transform:rotate(360deg)}}
</style></head><body><main>
<div class="card" id="cab">
  <h2>Referencias <small style="font-family:Jost;font-size:12px;color:var(--ink-soft)">v%%VERSION%%</small></h2>
  <p class="hint">Lo que los reels usan para salir reales y siempre iguales: <b>tus lugares</b> (fotos del lugar vacío) y <b>tus prendas</b>
  con su <b>probador</b> (su cuerpo sin cabeza con la prenda puesta, sobre gris). Se cargan una vez y se eligen en Filmado, Reels y Cambio de conjunto.</p>
  <p class="hint" id="volver"><a href="/">← Fotos</a> · <a href="/reels">🎞️ Reels</a> · <a href="/reels?modo=filmado">🎬 Filmado</a></p>
  <div class="tabs"><button data-t="lugares" class="on">📍 Mis lugares</button><button data-t="prendas">👗 Mis prendas y probador</button></div>
</div>

<section id="t_lugares">
<div class="card">
  <h3>Nuevo lugar</h3>
  <p class="hint">Cada lugar tiene hasta 4 vistas del <b>mismo lugar, vacío (sin gente)</b>: plano abierto, plano medio, un rincón y el espejo.
  👉 Lo más realista: <b>fotos reales</b> con el celular, de pie, a la altura de los ojos, con buena luz. Si no tenés, describilo y la IA lo genera (unos US$0,04 por vista).</p>
  <label>Nombre</label><input id="nombre" maxlength="60" placeholder="Mi local, El probador, Depósito…">
  <label>Cómo es (opcional si subís fotos; obligatorio para generarlo)</label>
  <textarea id="desc" rows="3" placeholder="Ej.: un local de lencería chico y cálido, paredes blanco tiza, exhibidores de madera clara con corpiños colgados, un probador con cortina de lino beige y un espejo grande, luz cálida"></textarea>
  <div class="fila"><button class="go" id="crear">＋ Crear lugar</button><span class="hint" id="est"></span></div>
</div>
<div id="lista"></div>
</section>

<section id="t_prendas" style="display:none">
<div class="card">
  <h3>Nueva prenda</h3>
  <p class="hint">Cargala una vez con sus colores (1 a 3 fotos por color: frente, espalda y detalle). Después, para cada modelo, hacé su <b>probador</b>:
  su cuerpo sin cabeza con ese color puesto, de frente, espalda y 3/4, sobre gris (~US$0,21 por color, una vez). Si no te gusta, lo rehacés diciendo qué corregir.</p>
  <div class="row"><div><label>Nombre</label><input id="pr_nombre" maxlength="80" placeholder="Conjunto encaje Sofía"></div>
  <div><label>Descripción (opcional: tela, detalles, calce)</label><input id="pr_desc" maxlength="600" placeholder="Encaje elastizado, aro, bombacha colaless"></div></div>
  <div id="pr_colores"></div>
  <div class="fila"><button class="sm" id="pr_otro">＋ Otro color</button><button class="go" id="pr_crear">＋ Crear prenda</button><span class="hint" id="pr_est"></span></div>
</div>
<div class="card"><label style="margin-top:0">Probadores de la modelo</label><select id="pj"></select>
  <p class="hint">El probador es de cada modelo (es su cuerpo). Los reels usan el de la modelo que elijas ahí.</p></div>
<div id="pr_lista"></div>
</section>
<script>
const API = "%%API%%"; let VISTAS = {}, COSTO = 0.04, PRENDAS = [], NUEVOS = [], VP = ["frente", "espalda", "3/4"], POLL = null;
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function api(p, o = {}) { const r = await fetch(API + p, o); let d = {}; try { d = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error(d.detail || ("HTTP " + r.status)); return d; }
const json = (method, body) => ({method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(body || {})});
const EMBED = new URLSearchParams(location.search).get("embed");
function avisar(){ if(EMBED) parent.postMessage({cambiosAlto: document.documentElement.scrollHeight, de: "referencias"}, "*"); }
if(EMBED){ new ResizeObserver(avisar).observe(document.body); document.getElementById("volver").style.display = "none"; }
function tab(t){ document.querySelectorAll(".tabs button").forEach(b => b.classList.toggle("on", b.dataset.t === t));
  $("#t_lugares").style.display = t === "lugares" ? "" : "none"; $("#t_prendas").style.display = t === "prendas" ? "" : "none";
  try{ history.replaceState(null, "", location.search + "#" + t); }catch(e){} if(t === "prendas") cargarPrendas(); }
document.querySelectorAll(".tabs button").forEach(b => b.onclick = () => tab(b.dataset.t));

// ── Mis lugares ──
function pintar(lst) {
  $("#lista").innerHTML = lst.length ? "" : '<div class="card"><p class="hint">Todavía no tenés lugares.</p></div>';
  lst.forEach(x => {
    const c = document.createElement("div"); c.className = "card";
    c.innerHTML = `<h3>${esc(x.nombre)}</h3><p class="hint">${esc(x.desc || "Sin descripción")}</p>
      <div class="vistas">${Object.entries(VISTAS).map(([k, n]) => `<div class="v">${x.urls[k] ? `<img src="${x.urls[k]}">` : `<div class="vacio">＋</div>`}
        <div>${esc(n)}</div><div class="fila" style="justify-content:center;margin-top:6px">
        <label style="margin:0"><input type="file" accept="image/*" data-sub="${k}" style="display:none"><span style="cursor:pointer;color:var(--rose-deep)">📷 ${x.urls[k] ? "Cambiar" : "Subir foto"}</span></label>
        ${x.urls[k] ? `<span style="cursor:pointer" data-del="${k}" title="Borrar">🗑</span>` : ""}</div></div>`).join("")}</div>
      <div class="fila"><button data-gen="1">✨ Generar con IA las vistas que faltan (~US$${COSTO.toFixed(2)} c/u)</button>
      <button data-borrar="1">Borrar lugar</button><span class="hint" data-est></span></div>`;
    const est = c.querySelector("[data-est]");
    c.querySelectorAll("[data-sub]").forEach(inp => inp.onchange = async () => {
      const f = inp.files[0]; if (!f) return; est.textContent = "Subiendo…";
      const fd = new FormData(); fd.append("archivo", f);
      try { await api(`/lugares/${x.id}/vista/${inp.dataset.sub}`, {method: "POST", body: fd}); cargar(); } catch (e) { est.innerHTML = `<span class="mal">${esc(e.message)}</span>`; } });
    c.querySelectorAll("[data-del]").forEach(b => b.onclick = async () => { if (!confirm("¿Borrar esta vista?")) return;
      try { await api(`/lugares/${x.id}/vista/${b.dataset.del}`, {method: "DELETE"}); cargar(); } catch (e) { est.textContent = e.message; } });
    c.querySelector("[data-gen]").onclick = async ev => { ev.target.disabled = true; est.textContent = "Generando… (unos segundos por vista)";
      try { const d = await api(`/lugares/${x.id}/generar`, json("POST", {vistas: Object.keys(VISTAS).filter(k => k !== "espejo")}));
        est.textContent = d.hechas.length ? `Listo: ${d.hechas.length} vista(s).` : "No faltaba ninguna."; cargar(); }
      catch (e) { est.innerHTML = `<span class="mal">${esc(e.message)}</span>`; } ev.target.disabled = false; };
    c.querySelector("[data-borrar]").onclick = async () => { if (!confirm(`¿Borrar "${x.nombre}"?`)) return; await api(`/lugares/${x.id}`, {method: "DELETE"}); cargar(); };
    $("#lista").appendChild(c);
  });
}
async function cargar() { try { const d = await api("/lugares"); VISTAS = d.vistas; COSTO = d.costo_vista; pintar(d.lugares); } catch (e) { $("#lista").innerHTML = `<div class="card mal">${esc(e.message)}</div>`; } }
$("#crear").onclick = async () => {
  try { await api("/lugares", json("POST", {nombre: $("#nombre").value, desc: $("#desc").value}));
    $("#nombre").value = ""; $("#desc").value = ""; $("#est").textContent = "Creado ✓ — ahora subí sus fotos o generalas abajo."; cargar(); }
  catch (e) { $("#est").innerHTML = `<span class="mal">${esc(e.message)}</span>`; } };

// ── Mis prendas ──
function leer(f){ return new Promise((ok, mal) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = mal; r.readAsDataURL(f); }); }
function pintarNuevos(){
  $("#pr_colores").innerHTML = NUEVOS.map((c, i) => `<div class="color"><div class="row"><div><label style="margin-top:0">Color ${i + 1}</label><input data-cn="${i}" value="${esc(c.nombre)}" placeholder="negro, bordó, nude…"></div>
    <div><label style="margin-top:0">Fotos (1 a 3)</label><input type="file" accept="image/*" multiple data-cf="${i}"></div></div>
    <div class="thumbs">${c.fotos.map(s => `<img src="${s}">`).join("")}</div>${i ? `<button class="sm" data-cx="${i}" style="margin-top:6px">Quitar</button>` : ""}</div>`).join("");
  document.querySelectorAll("[data-cn]").forEach(x => x.oninput = () => { NUEVOS[+x.dataset.cn].nombre = x.value; });
  document.querySelectorAll("[data-cf]").forEach(x => x.onchange = async e => { NUEVOS[+x.dataset.cf].fotos = await Promise.all(Array.from(e.target.files).slice(0, 3).map(leer)); pintarNuevos(); });
  document.querySelectorAll("[data-cx]").forEach(x => x.onclick = () => { NUEVOS.splice(+x.dataset.cx, 1); pintarNuevos(); });
}
$("#pr_otro").onclick = () => { if(NUEVOS.length < 8){ NUEVOS.push({nombre: "", fotos: []}); pintarNuevos(); } };
$("#pr_crear").onclick = async ev => { ev.target.disabled = true; $("#pr_est").textContent = "Guardando…";
  try { await api("/prendas", json("POST", {nombre: $("#pr_nombre").value, desc: $("#pr_desc").value, colores: NUEVOS.filter(c => c.fotos.length)}));
    $("#pr_nombre").value = ""; $("#pr_desc").value = ""; NUEVOS = [{nombre: "", fotos: []}]; pintarNuevos();
    $("#pr_est").textContent = "Creada ✓ — ahora hacé su probador abajo."; cargarPrendas(); }
  catch (e) { $("#pr_est").innerHTML = `<span class="mal">${esc(e.message)}</span>`; } ev.target.disabled = false; };
function pintarPrendas(){
  const pid = $("#pj").value; let haciendo = false;
  $("#pr_lista").innerHTML = PRENDAS.length ? "" : '<div class="card"><p class="hint">Todavía no cargaste prendas.</p></div>';
  PRENDAS.forEach(x => {
    const c = document.createElement("div"); c.className = "card";
    c.innerHTML = `<h3>${esc(x.nombre)}</h3><p class="hint">${esc(x.desc || "")}</p>` + x.colores.map((col, ci) => { const pb = col.probador || {};
      if(pb.estado === "haciendo") haciendo = true;
      const estado = pb.estado === "haciendo" ? `<span class="hint"><span class="spin"></span>Haciendo el probador… (1 a 3 minutos)</span>`
        : pb.estado === "error" ? `<span class="mal">No salió: ${esc(pb.error)}</span>`
        : pb.n ? (pb.aprobado ? `<span class="bien">✓ Aprobado: los reels usan éste</span>` : `<span class="hint">Revisalo: ¿es la prenda exacta y le calza bien? (los reels usan el último que hiciste)</span>`) : `<span class="hint">Sin probador para esta modelo.</span>`;
      return `<div class="color"><div class="fila" style="margin-top:0;justify-content:space-between"><b>${esc(col.nombre)}</b>${estado}</div>
        <div class="thumbs">${col.urls.map(u => `<a href="${u}" target="_blank"><img src="${u}"></a>`).join("")}</div>
        ${pb.n ? `<div class="pb">${(pb.urls || []).map((u, i) => `<figure><a href="${u}" target="_blank"><img src="${u}"></a>${esc(VP[i] || "")}</figure>`).join("")}</div>` : ""}
        ${pid ? `<div class="fila">${pb.estado === "haciendo" ? "" : pb.n ? `${pb.aprobado ? "" : `<button class="go sm" data-ok="${ci}">✓ Aprobar</button>`}
            <input data-cor="${ci}" placeholder="Qué corregir (ej.: el encaje del corpiño es más fino, la bombacha es colaless)" style="flex:1;min-width:220px">
            <button class="sm" data-re="${ci}">↻ Rehacer (~US$0,21)</button>` : `<button class="go sm" data-hacer="${ci}">🧍 Hacer probador (~US$0,21)</button>`}</div>` : ""}</div>`; }).join("")
      + `<div class="fila"><label style="margin:0"><input type="file" accept="image/*" multiple data-add style="display:none"><span style="cursor:pointer;color:var(--rose-deep)">＋ Sumar un color (sus fotos)</span></label>
         <button class="sm" data-borrar>Borrar prenda</button><span class="hint" data-est></span></div>`;
    const est = c.querySelector("[data-est]");
    const hacer = async (ci, correccion) => { try { await api(`/prendas/${x.id}/probador/${ci}`, json("POST", {pid, correccion})); cargarPrendas(); } catch (e) { est.innerHTML = `<span class="mal">${esc(e.message)}</span>`; } };
    c.querySelectorAll("[data-hacer]").forEach(b => b.onclick = () => { b.disabled = true; hacer(+b.dataset.hacer, ""); });
    c.querySelectorAll("[data-re]").forEach(b => b.onclick = () => { b.disabled = true; hacer(+b.dataset.re, c.querySelector(`[data-cor="${b.dataset.re}"]`).value); });
    c.querySelectorAll("[data-ok]").forEach(b => b.onclick = async () => { try { await api(`/prendas/${x.id}/probador/${b.dataset.ok}/aprobar`, json("POST", {pid})); cargarPrendas(); } catch (e) { est.textContent = e.message; } });
    c.querySelector("[data-add]").onchange = async e => { const fotos = await Promise.all(Array.from(e.target.files).slice(0, 3).map(leer)); if(!fotos.length) return;
      const nombre = prompt("¿Qué color es?") || ""; try { await api(`/prendas/${x.id}/color`, json("POST", {nombre, fotos})); cargarPrendas(); } catch (err) { est.innerHTML = `<span class="mal">${esc(err.message)}</span>`; } };
    c.querySelector("[data-borrar]").onclick = async () => { if (!confirm(`¿Borrar "${x.nombre}"? Los reels ya hechos no cambian.`)) return; await api(`/prendas/${x.id}`, {method: "DELETE"}); cargarPrendas(); };
    $("#pr_lista").appendChild(c);
  });
  clearTimeout(POLL); if(haciendo) POLL = setTimeout(cargarPrendas, 6000);
}
async function cargarPrendas(){ try { const pid = $("#pj").value; const d = await api("/prendas" + (pid ? "?pid=" + encodeURIComponent(pid) : ""));
  PRENDAS = d.prendas; VP = d.vistas_probador || VP; pintarPrendas(); } catch (e) { $("#pr_lista").innerHTML = `<div class="card mal">${esc(e.message)}</div>`; } }
$("#pj").onchange = cargarPrendas;
(async () => {
  NUEVOS = [{nombre: "", fotos: []}]; pintarNuevos();
  try { const pj = await (await fetch("/personajes/api/lista")).json(); $("#pj").innerHTML = (pj.personajes || []).map(p => `<option value="${esc(p.id)}">${esc(p.nombre)}</option>`).join("") || '<option value="">Primero creá un personaje</option>'; } catch (e) {}
  cargar();
  tab(location.hash === "#prendas" ? "prendas" : "lugares");
})();
</script></main></body></html>"""
