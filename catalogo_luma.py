"""
================================================================================
CATÁLOGO — el catálogo digital para el celular, que se hojea como un libro
================================================================================
Se arma en /catalogo (menú aparte):

- LA PORTADA: una foto elegida por la usuaria, con su título y su leyenda.
- CADA PRODUCTO es una doble página: a la IZQUIERDA tres fotos pegadas como figuritas, con el
  artículo, el nombre y los talles en que viene; a la DERECHA la foto grupal a página entera.
- EL VISOR pasa las hojas como un libro (deslizando, tocando los bordes o con las flechas). En el
  celular parado se ve una página por vez; acostado o en la compu, el libro abierto de a dos.
- COMPARTIR: "Publicar" le da un link (/c/…) que se abre SIN iniciar sesión, para mandarlo a las
  clientas. Sólo entra quien tiene el link; se despublica cuando quiera y el link deja de andar.

Todo se guarda en Redis (el servidor no tiene disco fijo): el catálogo y cada foto, ya achicada.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import secrets
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from imagenes_ia import _compress_ref, _pfx, kv, session_sub_from_request, set_current_sub

router = APIRouter()
VERSION = "1.5.3"
ESTILOS = ("figuritas", "editorial", "trio", "plena")      # cómo se arma la página izquierda de cada producto
PREF = "/catalogo"
API = PREF + "/api"
PUB = "/c"                       # el link público (abierto en main._OPEN_PREFIXES)
MAX_CATALOGOS, MAX_PRODUCTOS = 30, 80
_ID = re.compile(r"[0-9a-f]{6,32}")


def _k(cid: str) -> str:
    return _pfx() + f"catalogo:{cid}"


def _k_idx() -> str:
    return _pfx() + "catalogos"


def _k_img(cid: str, iid: str) -> str:
    return _pfx() + f"catalogo:{cid}:img:{iid}"


def _k_pub(token: str) -> str:
    return f"catalogo_pub:{token}"           # global: lleva a (cuenta, catálogo)


_LOCKS: Dict[str, asyncio.Lock] = {}


async def _cat(cid: str) -> Dict[str, Any]:
    if not _ID.fullmatch(cid or ""):
        raise HTTPException(404, "No encuentro ese catálogo.")
    c = await kv.get(_k(cid))
    if not isinstance(c, dict):
        raise HTTPException(404, "No encuentro ese catálogo.")
    return c


def _t(v: Any, tope: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:tope]


def _iid(v: Any, validas: set) -> str:
    v = str(v or "")
    return v if v in validas else ""


def _lay_limpio(v: Any) -> Optional[Dict[str, Dict[str, float]]]:
    """El acomodo a mano de una página: dónde va, de qué tamaño y con qué giro cada cosa (título, datos y cada foto).
    Todo en fracciones de la página, así vale igual en cualquier pantalla."""
    if not isinstance(v, dict):
        return None
    out: Dict[str, Dict[str, float]] = {}
    topes = {"x": (-0.5, 1.2), "y": (-0.5, 1.2), "w": (0.08, 1.5), "s": (0.4, 3.0), "r": (-180.0, 180.0), "z": (1.0, 99.0)}
    for k in ("cab", "dat", "f0", "f1", "f2"):
        e = v.get(k)
        if not isinstance(e, dict):
            continue
        d = {}
        for campo, (lo, hi) in topes.items():
            try:
                if e.get(campo) is not None:
                    d[campo] = round(max(lo, min(hi, float(e[campo]))), 4)
            except (TypeError, ValueError):
                pass
        if "x" in d and "y" in d and "w" in d:
            out[k] = d
    bg = v.get("bg")
    if out and isinstance(bg, dict):      # el fondo de ESA página: color (vacío = el de las fotos) y cuánto grano
        f: Dict[str, Any] = {}
        c = str(bg.get("c") or "").strip().lower()
        if re.fullmatch(r"#[0-9a-f]{6}", c):
            f["c"] = c
        try:
            if bg.get("g") is not None:
                f["g"] = round(max(0.0, min(1.0, float(bg["g"]))), 3)
        except (TypeError, ValueError):
            pass
        img = str(bg.get("img") or "")
        if re.fullmatch(r"[0-9a-f]{12}", img):      # un fondo propio (una imagen subida al catálogo)
            f["img"] = img
            f["osc"] = bool(bg.get("osc"))           # si es oscura, las letras van en blanco
        if f:
            out["bg"] = f       # type: ignore[assignment]
    return out or None


def _publico(c: Dict[str, Any]) -> Dict[str, Any]:
    """Lo que ve quien abre el catálogo: nada de la cuenta, sólo el contenido."""
    return {"nombre": c.get("nombre", ""), "portada": c.get("portada") or {},
            "productos": [{k: p.get(k) for k in ("articulo", "nombre", "talles", "colores", "precio", "figus", "grupal", "estilo", "lay")}
                          for p in c.get("productos") or []],
            "contacto": c.get("contacto", ""), "fondo": c.get("fondo") or "auto"}


# ─────────────────────────────────────────────────────────────────────────────
# ARMADO (con sesión)
# ─────────────────────────────────────────────────────────────────────────────

@router.get(API + "/lista")
async def api_lista(request: Request) -> Dict[str, Any]:
    set_current_sub(session_sub_from_request(request))
    out = []
    for cid in (await kv.get(_k_idx())) or []:
        c = await kv.get(_k(cid))
        if isinstance(c, dict):
            out.append({"id": cid, "nombre": c.get("nombre"), "productos": len(c.get("productos") or []),
                        "publicado": bool(c.get("token")), "actualizado": c.get("actualizado", "")})
    return {"catalogos": out}


@router.post(API + "/nuevo")
async def api_nuevo(request: Request, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    set_current_sub(session_sub_from_request(request))
    idx = (await kv.get(_k_idx())) or []
    if len(idx) >= MAX_CATALOGOS:
        raise HTTPException(400, f"Hasta {MAX_CATALOGOS} catálogos: borrá alguno.")
    cid = secrets.token_hex(5)
    c = {"id": cid, "nombre": _t(payload.get("nombre"), 80) or "Catálogo nuevo",
         "portada": {"img": "", "titulo": "", "leyenda": ""}, "productos": [], "imgs": [], "contacto": "",
         "token": "", "creado": time.strftime("%Y-%m-%d %H:%M"), "actualizado": time.strftime("%Y-%m-%d %H:%M")}
    await kv.set(_k(cid), c)
    await kv.set(_k_idx(), [cid] + idx)
    return {"catalogo": c}


@router.get(API + "/{cid}")
async def api_get(cid: str, request: Request) -> Dict[str, Any]:
    set_current_sub(session_sub_from_request(request))
    return {"catalogo": await _cat(cid)}


@router.post(API + "/{cid}")
async def api_guardar(cid: str, request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Guarda los textos y qué foto va en cada lugar. Las fotos que ya no se usan se borran."""
    set_current_sub(session_sub_from_request(request))
    async with _LOCKS.setdefault(cid, asyncio.Lock()):
        c = await _cat(cid)
        validas = set(c.get("imgs") or [])
        if "nombre" in payload:
            c["nombre"] = _t(payload.get("nombre"), 80) or c["nombre"]
        if "contacto" in payload:
            c["contacto"] = _t(payload.get("contacto"), 120)
        if "fondo" in payload:      # el papel de las páginas: "auto" (el fondo de las fotos de cada producto), "papel" o un color
            f = str(payload.get("fondo") or "").strip().lower()
            c["fondo"] = f if (f in ("auto", "papel") or re.fullmatch(r"#[0-9a-f]{6}", f)) else "auto"
        if isinstance(payload.get("portada"), dict):
            p = payload["portada"]
            c["portada"] = {"img": _iid(p.get("img"), validas), "titulo": _t(p.get("titulo"), 60),
                            "leyenda": _t(p.get("leyenda"), 160),
                            "tono": "oscuro" if p.get("tono") == "oscuro" else "claro"}
        if isinstance(payload.get("productos"), list):
            prods = []
            for p in payload["productos"][:MAX_PRODUCTOS]:
                if not isinstance(p, dict):
                    continue
                figus = [_iid(x, validas) for x in (list(p.get("figus") or []) + ["", "", ""])[:3]]
                prods.append({"id": str(p.get("id")) if _ID.fullmatch(str(p.get("id") or "")) else secrets.token_hex(4),
                              "articulo": _t(p.get("articulo"), 40), "nombre": _t(p.get("nombre"), 80),
                              "talles": _t(p.get("talles"), 120), "colores": _t(p.get("colores"), 120),
                              "precio": _t(p.get("precio"), 40), "figus": figus,
                              "estilo": p.get("estilo") if p.get("estilo") in ESTILOS else "figuritas",
                              "grupal": _iid(p.get("grupal"), validas)})
                _l = _lay_limpio(p.get("lay"))
                if _l and prods[-1]["estilo"] != "plena":
                    if (_l.get("bg") or {}).get("img") not in validas:
                        (_l.get("bg") or {}).pop("img", None)
                    prods[-1]["lay"] = _l
            c["productos"] = prods
        usadas = {c["portada"].get("img")} | {x for p in c["productos"] for x in p["figus"] + [p["grupal"]]}
        usadas |= {((p.get("lay") or {}).get("bg") or {}).get("img") for p in c["productos"]}      # los fondos propios
        for iid in validas - usadas:
            await kv.delete(_k_img(cid, iid))
        c["imgs"] = sorted(validas & usadas)
        c["actualizado"] = time.strftime("%Y-%m-%d %H:%M")
        await kv.set(_k(cid), c)
    return {"catalogo": c}


@router.post(API + "/{cid}/lay")
async def api_lay(cid: str, request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Guarda (o borra, con lay vacío) el acomodo a mano de la página de UN producto."""
    set_current_sub(session_sub_from_request(request))
    async with _LOCKS.setdefault(cid, asyncio.Lock()):
        c = await _cat(cid)
        try:
            pr = c["productos"][int(payload.get("i"))]
        except (TypeError, ValueError, IndexError):
            raise HTTPException(404, "No encuentro ese producto.")
        lay = _lay_limpio(payload.get("lay"))
        if lay and (lay.get("bg") or {}).get("img") not in (c.get("imgs") or []):
            (lay.get("bg") or {}).pop("img", None)
        if lay:
            pr["lay"] = lay
        else:
            pr.pop("lay", None)
        c["actualizado"] = time.strftime("%Y-%m-%d %H:%M")
        await kv.set(_k(cid), c)
    return {"ok": True, "lay": lay}


@router.post(API + "/{cid}/img")
async def api_subir(cid: str, request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Una foto (data URL). Se achica a 1600 px y se guarda; devuelve su id para ponerla en una página."""
    set_current_sub(session_sub_from_request(request))
    m = re.match(r"data:image/[a-zA-Z+.-]+;base64,(.+)$", str(payload.get("img") or ""), re.S)
    if not m:
        raise HTTPException(400, "Eso no es una imagen.")
    try:
        crudo = base64.b64decode(m.group(1))
        chico = _compress_ref(crudo, max_dim=1600, q=88)
    except Exception:
        raise HTTPException(400, "No pude leer esa imagen.")
    if len(crudo) > 25_000_000:
        raise HTTPException(413, "La imagen pesa demasiado.")
    async with _LOCKS.setdefault(cid, asyncio.Lock()):
        c = await _cat(cid)
        if len(c.get("imgs") or []) >= MAX_PRODUCTOS * 4 + 4:
            raise HTTPException(400, "Este catálogo ya tiene demasiadas fotos.")
        iid = secrets.token_hex(6)
        await kv.set(_k_img(cid, iid), chico)
        c["imgs"] = list(c.get("imgs") or []) + [iid]
        await kv.set(_k(cid), c)
    return {"iid": iid}


async def _jpg(cid: str, iid: str) -> Response:
    if not _ID.fullmatch(iid or ""):
        raise HTTPException(404, "No está esa foto.")
    b = await kv.get(_k_img(cid, iid))
    if not b:
        raise HTTPException(404, "No está esa foto.")
    return Response(base64.b64decode(b), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.get(API + "/{cid}/img/{iid}.jpg")
async def api_img(cid: str, iid: str, request: Request) -> Response:
    set_current_sub(session_sub_from_request(request))
    await _cat(cid)
    return await _jpg(cid, iid)


@router.post(API + "/{cid}/publicar")
async def api_publicar(cid: str, request: Request, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Prende o apaga el link público. Al apagarlo, el link viejo deja de andar para siempre."""
    sub = session_sub_from_request(request)
    set_current_sub(sub)
    async with _LOCKS.setdefault(cid, asyncio.Lock()):
        c = await _cat(cid)
        if payload.get("on") is False:
            if c.get("token"):
                await kv.delete(_k_pub(c["token"]))
            c["token"] = ""
        elif not c.get("token"):
            c["token"] = secrets.token_urlsafe(12)
            await kv.set(_k_pub(c["token"]), {"sub": sub, "cid": cid})
        await kv.set(_k(cid), c)
    return {"catalogo": c}


@router.delete(API + "/{cid}")
async def api_borrar(cid: str, request: Request) -> Dict[str, Any]:
    set_current_sub(session_sub_from_request(request))
    c = await _cat(cid)
    for iid in c.get("imgs") or []:
        await kv.delete(_k_img(cid, iid))
    if c.get("token"):
        await kv.delete(_k_pub(c["token"]))
    await kv.delete(_k(cid))
    await kv.set(_k_idx(), [x for x in ((await kv.get(_k_idx())) or []) if x != cid])
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# EL LINK PÚBLICO (sin sesión): sólo lee, y sólo ese catálogo
# ─────────────────────────────────────────────────────────────────────────────

async def _de_token(token: str) -> Dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,40}", token or ""):
        raise HTTPException(404, "Este catálogo no existe o ya no está publicado.")
    ref = await kv.get(_k_pub(token))
    if not isinstance(ref, dict) or not ref.get("sub") or not ref.get("cid"):
        raise HTTPException(404, "Este catálogo no existe o ya no está publicado.")
    set_current_sub(ref["sub"])
    c = await kv.get(_k(ref["cid"]))
    if not isinstance(c, dict) or c.get("token") != token:
        raise HTTPException(404, "Este catálogo no existe o ya no está publicado.")
    return c


@router.get(PUB + "/{token}", response_class=HTMLResponse)
async def pub_ver(token: str) -> HTMLResponse:
    c = await _de_token(token)
    return HTMLResponse(VISOR.replace("%%DATA%%", f"{PUB}/{token}/data").replace("%%IMG%%", f"{PUB}/{token}/img/")
                        .replace("%%TITULO%%", _html(c.get("nombre") or "Catálogo")).replace("%%VOLVER%%", ""))


@router.get(PUB + "/{token}/data")
async def pub_data(token: str) -> Dict[str, Any]:
    return {"catalogo": _publico(await _de_token(token))}


@router.get(PUB + "/{token}/img/{iid}.jpg")
async def pub_img(token: str, iid: str) -> Response:
    c = await _de_token(token)
    if iid not in (c.get("imgs") or []):
        raise HTTPException(404, "No está esa foto.")
    return await _jpg(c["id"], iid)


def _html(t: str) -> str:
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# ─────────────────────────────────────────────────────────────────────────────
# PANTALLAS
# ─────────────────────────────────────────────────────────────────────────────

@router.get(PREF, response_class=HTMLResponse)
async def pagina() -> HTMLResponse:
    return HTMLResponse(EDITOR.replace("%%API%%", API).replace("%%PREF%%", PREF).replace("%%PUB%%", PUB)
                        .replace("%%VERSION%%", VERSION))


@router.get(PREF + "/ver", response_class=HTMLResponse)
async def pagina_ver(id: str = "") -> HTMLResponse:
    if not _ID.fullmatch(id or ""):
        raise HTTPException(404, "No encuentro ese catálogo.")
    return HTMLResponse(VISOR.replace("%%DATA%%", f"{API}/{id}").replace("%%IMG%%", f"{API}/{id}/img/")
                        .replace("%%TITULO%%", "Catálogo").replace("%%VOLVER%%", f"{PREF}?id={id}"))


EDITOR = r"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Catálogo · Studio Luma</title>
<link href="https://fonts.googleapis.com/css2?family=Jost:wght@400;500;600&family=Playfair+Display:wght@500;600&display=swap" rel="stylesheet">
<style>
:root{--bg:#141318;--card:#1c1b22;--card-2:#26242d;--line:#3a3842;--ink:#eee;--ink-soft:#aaa;--rose-deep:#e9c58b}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);font-family:Jost,system-ui,sans-serif;font-size:14px}
main{max-width:980px;margin:0 auto;padding:16px} h1{font-family:"Playfair Display",serif;font-size:24px;margin:4px 0}
h2{font-size:16px;margin:0 0 8px} a{color:var(--rose-deep)} .hint{color:var(--ink-soft);font-size:12.5px} .mal{color:#f08080} .bien{color:#9fd49f}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin:12px 0}
button,.btn{background:var(--card-2);color:var(--ink);border:1px solid var(--line);border-radius:9px;padding:7px 12px;font:inherit;cursor:pointer;text-decoration:none;display:inline-block}
button.go,.btn.go{background:var(--rose-deep);color:#1c1b22;border:0;font-weight:600} button.sm{padding:3px 8px;font-size:12.5px}
input,textarea,select{width:100%;background:#141318;color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:8px;font:inherit}
label{display:block;font-size:12.5px;color:var(--ink-soft);margin:8px 0 3px}
.fila{display:flex;gap:8px;align-items:center;flex-wrap:wrap} .g2{display:grid;grid-template-columns:1fr 1fr;gap:10px} .g3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
.slot{position:relative;background:#141318 center/cover no-repeat;border:1.5px dashed var(--line);border-radius:10px;aspect-ratio:4/5;display:flex;align-items:center;justify-content:center;text-align:center;color:var(--ink-soft);font-size:12px;cursor:pointer;overflow:hidden}
.slot.con{border-style:solid} .slot .x{position:absolute;top:4px;right:4px;background:rgba(0,0,0,.65);color:#fff;border:0;border-radius:50%;width:24px;height:24px;padding:0;line-height:1}
.slot input{display:none} .fotos{display:grid;grid-template-columns:repeat(3,1fr) 1.6fr;gap:8px;margin-top:8px}
.portada{display:grid;grid-template-columns:180px 1fr;gap:14px} .portada .slot{aspect-ratio:4/5}
@media(max-width:640px){.g3{grid-template-columns:1fr 1fr}.portada{grid-template-columns:120px 1fr}.fotos{grid-template-columns:repeat(3,1fr)}.fotos .slot.grupal{grid-column:1/-1;aspect-ratio:16/10}}
</style></head><body><main>
<div class="fila"><a href="/">◀ Fotos</a><span class="hint">Catálogo v%%VERSION%%</span></div>
<div id="app"><p class="hint">Cargando…</p></div>
</main><script>
const API = "%%API%%", PREF = "%%PREF%%", PUB = "%%PUB%%";
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let C = null, tG = null;
async function api(u, op){ const r = await fetch(API + u, op); const j = await r.json().catch(() => ({})); if(!r.ok) throw new Error(j.detail || r.status); return j; }
const post = (u, b) => api(u, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(b || {})});
const img = iid => iid ? `${API}/${C.id}/img/${iid}.jpg` : "";
function achicar(file){ return new Promise((ok, mal) => { const im = new Image(), u = URL.createObjectURL(file);
  im.onload = () => { const k = Math.min(1, 1800 / Math.max(im.width, im.height)), c = document.createElement("canvas"); c.width = Math.round(im.width * k); c.height = Math.round(im.height * k);
    c.getContext("2d").drawImage(im, 0, 0, c.width, c.height); URL.revokeObjectURL(u); ok(c.toDataURL("image/jpeg", 0.9)); };
  im.onerror = () => mal(new Error("No pude leer esa imagen.")); im.src = u; }); }
function guardar(ya){ clearTimeout(tG); $("#est") && ($("#est").textContent = "…"); tG = setTimeout(async () => {
  try{ C = (await post("/" + C.id, {nombre: C.nombre, contacto: C.contacto, portada: C.portada, productos: C.productos})).catalogo; $("#est") && ($("#est").innerHTML = '<span class="bien">✓ guardado</span>'); }
  catch(e){ $("#est") && ($("#est").innerHTML = `<span class="mal">${esc(e.message)}</span>`); } }, ya ? 0 : 700); }
function slot(clave, iid, texto, extra){ return `<label class="slot ${iid ? "con" : ""} ${extra || ""}" style="${iid ? `background-image:url('${img(iid)}')` : ""}" data-slot="${clave}">
  ${iid ? `<button class="x" data-quitar="${clave}" title="Quitar">✕</button>` : esc(texto)}<input type="file" accept="image/*" data-file="${clave}"></label>`; }
function leer(clave){ const [a, b, c] = clave.split(":"); if(a === "portada") return C.portada.img; const p = C.productos[+b]; return a === "grupal" ? p.grupal : p.figus[+c]; }
function poner(clave, iid){ const [a, b, c] = clave.split(":"); if(a === "portada") C.portada.img = iid; else { const p = C.productos[+b]; if(a === "grupal") p.grupal = iid; else p.figus[+c] = iid; } }
function pintar(){
  const link = C.token ? location.origin + PUB + "/" + C.token : "";
  $("#app").innerHTML = `<h1>📖 Catálogo</h1>
    <div class="card"><div class="g2"><div><label>Nombre del catálogo</label><input data-c="nombre" value="${esc(C.nombre)}"></div>
        <div><label>Contacto al final (WhatsApp, Instagram…)</label><input data-c="contacto" value="${esc(C.contacto || "")}" placeholder="ej: @luma.intima · 11 5555-5555"></div>
        <div><label>Fondo de las páginas</label><select id="fondoSel"><option value="auto">Como el fondo de las fotos de cada producto (automático)</option><option value="papel">Papel marfil</option><option value="color">Un color que elijo yo</option></select></div>
        <div id="fondoColW"><label>Color</label><input type="color" id="fondoCol" style="height:38px;padding:2px"></div></div>
      <div class="fila" style="margin-top:10px"><a class="btn go" href="${PREF}/ver?id=${C.id}" target="_blank">👁 Ver y acomodar el catálogo</a>
        ${C.token ? `<button id="copiar">🔗 Copiar el link para clientas</button><button id="despub">Despublicar</button>` : `<button id="pub">🌐 Publicar (link para clientas)</button>`}
        <span class="hint" id="est"></span><span style="flex:1"></span><button id="otros">Mis catálogos</button></div>
      ${C.token ? `<div class="hint" style="margin-top:6px">Publicado: <a href="${link}" target="_blank">${esc(link)}</a> · lo abre cualquiera que tenga el link, sin iniciar sesión.</div>` : `<div class="hint" style="margin-top:6px">Sin publicar: sólo lo ves vos.</div>`}</div>
    <div class="card"><h2>La portada</h2><div class="portada">${slot("portada", C.portada.img, "Tocá para elegir la foto de portada")}
      <div><label>Título</label><input data-p="titulo" value="${esc(C.portada.titulo)}" placeholder="ej: Colección Verano 2027">
        <label>Leyenda</label><textarea data-p="leyenda" rows="3" placeholder="ej: Básicos sin costuras · venta por mayor y menor">${esc(C.portada.leyenda)}</textarea>
        <label>Color de las letras</label><select data-p="tono"><option value="claro" ${C.portada.tono !== "oscuro" ? "selected" : ""}>Blancas (foto oscura)</option><option value="oscuro" ${C.portada.tono === "oscuro" ? "selected" : ""}>Negras (foto clara)</option></select>
        <p class="hint">La foto va a página entera en 4:5. Si la foto ya trae el logo, dejá el título y la leyenda vacíos y no se escribe nada encima.</p></div></div></div>
    ${C.productos.map((p, i) => `<div class="card"><div class="fila" style="justify-content:space-between"><h2>Producto ${i + 1}</h2>
        <span><button class="sm" data-sube="${i}" ${i ? "" : "disabled"}>↑</button> <button class="sm" data-baja="${i}" ${i < C.productos.length - 1 ? "" : "disabled"}>↓</button> <button class="sm" data-borra="${i}">🗑</button></span></div>
      <div class="g3"><div><label>Artículo</label><input data-k="articulo" data-i="${i}" value="${esc(p.articulo)}" placeholder="ej: M326"></div>
        <div><label>Nombre</label><input data-k="nombre" data-i="${i}" value="${esc(p.nombre)}" placeholder="ej: Top sin costuras"></div>
        <div><label>Talles que vienen</label><input data-k="talles" data-i="${i}" value="${esc(p.talles)}" placeholder="ej: S · M · L · XL"></div>
        <div><label>Colores (opcional)</label><input data-k="colores" data-i="${i}" value="${esc(p.colores)}" placeholder="ej: negro, blanco, beige"></div>
        <div><label>Precio (opcional)</label><input data-k="precio" data-i="${i}" value="${esc(p.precio)}" placeholder="ej: $19.800"></div></div>
      <div class="g2"><div><label>Estilo de la página izquierda</label><select data-k="estilo" data-i="${i}">${[["figuritas", "Figuritas pegadas (3 fotos inclinadas con cinta)"], ["editorial", "Editorial (1 foto grande + 2 chicas, prolijo)"], ["trio", "Trío (3 fotos en fila, con mucho aire)"], ["plena", "Foto a página entera (la 1ª foto, datos abajo)"]].map(o => `<option value="${o[0]}" ${(p.estilo || "figuritas") === o[0] ? "selected" : ""}>${o[1]}</option>`).join("")}</select></div></div>
      <label>Página izquierda: hasta 3 fotos · Página derecha: la foto grupal (todas en 4:5)${p.lay ? ' · <span class="bien">✋ acomodada a mano</span>' : ""}</label>
      <div class="fotos">${[0, 1, 2].map(k => slot(`figu:${i}:${k}`, p.figus[k], `Foto ${k + 1}`)).join("")}${slot(`grupal:${i}`, p.grupal, "Foto grupal", "grupal")}</div></div>`).join("")}
    <p><button class="go" id="mas">＋ Agregar producto</button></p>`;
  { const f = C.fondo || "auto", sel = $("#fondoSel"), col = $("#fondoCol"), esHex = /^#/.test(f);
    sel.value = esHex ? "color" : f; col.value = esHex ? f : "#9a8f86"; $("#fondoColW").style.display = esHex ? "" : "none";
    sel.onchange = () => { C.fondo = sel.value === "color" ? col.value : sel.value; $("#fondoColW").style.display = sel.value === "color" ? "" : "none"; guardar(); };
    col.oninput = () => { C.fondo = col.value; guardar(); }; }
  $$("[data-c]").forEach(x => x.oninput = () => { C[x.dataset.c] = x.value; guardar(); });
  $$("[data-p]").forEach(x => x.oninput = x.onchange = () => { C.portada[x.dataset.p] = x.value; guardar(); });
  $$("[data-k]").forEach(x => x.oninput = x.onchange = () => { C.productos[+x.dataset.i][x.dataset.k] = x.value; guardar(); });
  $$("[data-file]").forEach(x => x.onchange = async () => { const f = x.files[0]; if(!f) return; const lab = x.closest(".slot"); lab.textContent = "Subiendo…";
    try{ const r = await post(`/${C.id}/img`, {img: await achicar(f)}); poner(x.dataset.file, r.iid); guardar(true); setTimeout(pintar, 400); }catch(e){ alert(e.message); pintar(); } });
  $$("[data-quitar]").forEach(b => b.onclick = e => { e.preventDefault(); poner(b.dataset.quitar, ""); guardar(true); pintar(); });
  $$("[data-borra]").forEach(b => b.onclick = () => { if(!confirm("¿Borrar este producto del catálogo?")) return; C.productos.splice(+b.dataset.borra, 1); guardar(true); pintar(); });
  $$("[data-sube]").forEach(b => b.onclick = () => { const i = +b.dataset.sube; [C.productos[i - 1], C.productos[i]] = [C.productos[i], C.productos[i - 1]]; guardar(true); pintar(); });
  $$("[data-baja]").forEach(b => b.onclick = () => { const i = +b.dataset.baja; [C.productos[i + 1], C.productos[i]] = [C.productos[i], C.productos[i + 1]]; guardar(true); pintar(); });
  $("#mas").onclick = () => { C.productos.push({id: "", articulo: "", nombre: "", talles: "", colores: "", precio: "", figus: ["", "", ""], grupal: "", estilo: (C.productos[C.productos.length - 1] || {}).estilo || "figuritas"}); guardar(true); pintar(); window.scrollTo(0, document.body.scrollHeight); };
  if($("#pub")) $("#pub").onclick = async () => { if(!confirm("Publicar crea un link que cualquiera con el link puede abrir sin iniciar sesión. ¿Publicar?")) return; C = (await post(`/${C.id}/publicar`, {on: true})).catalogo; pintar(); };
  if($("#despub")) $("#despub").onclick = async () => { if(!confirm("El link deja de funcionar. ¿Despublicar?")) return; C = (await post(`/${C.id}/publicar`, {on: false})).catalogo; pintar(); };
  if($("#copiar")) $("#copiar").onclick = async () => { try{ await navigator.clipboard.writeText(link); $("#est").innerHTML = '<span class="bien">✓ link copiado</span>'; }catch(e){ prompt("Copiá el link:", link); } };
  $("#otros").onclick = () => { history.replaceState(null, "", PREF); lista(); };
}
async function lista(){
  const d = await api("/lista");
  $("#app").innerHTML = `<h1>📖 Catálogo</h1><p class="hint">El catálogo digital para el celular, que se hojea como un libro: portada, y por cada producto una página con sus fotos, el artículo y los talles (en el estilo que elijas: figuritas, editorial, trío o página entera) y la foto grupal a la derecha. Todas las hojas en 4:5.</p>
    <div class="card"><div class="fila"><input id="nNom" placeholder="Nombre del catálogo nuevo (ej: Verano 2027)" style="flex:1;min-width:200px"><button class="go" id="crear">＋ Crear catálogo</button></div></div>
    <div class="card"><h2>Mis catálogos</h2>${(d.catalogos || []).map(c => `<div class="fila" style="padding:7px 0;border-top:1px solid var(--line)"><a href="${PREF}?id=${c.id}" style="flex:1">📖 ${esc(c.nombre)}</a>
      <span class="hint">${c.productos} productos${c.publicado ? " · 🌐 publicado" : ""}</span><button class="sm" data-del="${c.id}">🗑</button></div>`).join("") || '<p class="hint">Todavía no armaste ninguno.</p>'}</div>`;
  $("#crear").onclick = async () => { const r = await post("/nuevo", {nombre: $("#nNom").value}); location.href = `${PREF}?id=${r.catalogo.id}`; };
  $$("[data-del]").forEach(b => b.onclick = async () => { if(!confirm("¿Borrar este catálogo y sus fotos? No se puede deshacer.")) return; await api("/" + b.dataset.del, {method: "DELETE"}); lista(); });
}
(async () => { const id = new URLSearchParams(location.search).get("id");
  try{ if(id){ C = (await api("/" + id)).catalogo; pintar(); } else await lista(); }catch(e){ $("#app").innerHTML = `<p class="mal">${esc(e.message)}</p><p><a href="${PREF}">Mis catálogos</a></p>`; } })();
</script></body></html>"""


# EL VISOR: el libro. Una página por vez con el celular parado; abierto de a dos acostado o en la compu.
VISOR = r"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>%%TITULO%%</title>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@300;400;500&family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;1,400;1,500&display=swap" rel="stylesheet">
<style>
:root{--papel:#F6F1E9;--papel2:#EDE5DA;--tinta:#1c1917;--suave:#8a7f74;--rosa:#C9A59A;--fondo:#211d1b;--serif:"Cormorant Garamond",Georgia,serif;--sans:Montserrat,system-ui,sans-serif}
*{box-sizing:border-box} html,body{margin:0;height:100%;background:var(--fondo);overflow:hidden;font-family:var(--sans);font-weight:400;color:var(--tinta);-webkit-user-select:none;user-select:none;touch-action:pan-y}
#mesa{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;perspective:2200px}
#libro{position:relative;display:flex;filter:drop-shadow(0 18px 40px rgba(0,0,0,.45))}
.cara{position:relative;width:var(--w);height:var(--h);overflow:hidden;background:var(--papel)}
.vacia{background:transparent}
.borde{position:absolute;inset:0;border:calc(var(--pw)*.011) solid #fff;pointer-events:none;z-index:4}   /* el filete blanco de cada hoja */
.lomo.izq .borde{border-right-width:calc(var(--pw)*.0055)} .lomo.der .borde{border-left-width:calc(var(--pw)*.0055)}   /* en el centro se juntan dos: cada uno la mitad, así queda igual que el de afuera */
.hoja{position:absolute;top:0;width:var(--w);height:var(--h);transform-style:preserve-3d;transition:transform .75s cubic-bezier(.3,.1,.2,1);z-index:5}
.hoja > .cara{position:absolute;inset:0;backface-visibility:hidden;-webkit-backface-visibility:hidden}
.hoja > .dorso{transform:rotateY(180deg)}
.pg{position:absolute;inset:0;display:flex;flex-direction:column}
.portada{background:#1d1716 center/cover no-repeat;color:#fff;justify-content:flex-end;text-align:center}
.portada.contx::after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,0) 40%,rgba(0,0,0,.58))}
.portada.oscuro{color:var(--tinta)} .portada.oscuro::after{display:none}
.portada .tx{position:relative;z-index:1;padding:calc(var(--w)*.08);padding-bottom:calc(var(--w)*.12)}
.portada h1{font-family:var(--sans);font-weight:300;text-transform:uppercase;letter-spacing:.32em;margin:0 -.32em calc(var(--w)*.035) 0;font-size:calc(var(--w)*.082);line-height:1.2}
.portada p{display:flex;align-items:center;justify-content:center;gap:calc(var(--w)*.035);font-size:calc(var(--w)*.03);font-weight:400;letter-spacing:.3em;text-transform:uppercase;margin:0;line-height:1.6}
.portada p::before,.portada p::after{content:"";flex:1;max-width:calc(var(--w)*.12);height:1px;background:currentColor;opacity:.8}
.figus{padding:calc(var(--w)*.06) calc(var(--w)*.07) calc(var(--w)*.07);background:var(--pp,var(--papel));color:var(--tinta);--raya:rgba(28,25,23,.3);--suave:rgba(28,25,23,.62)}
.figus.osc{--tinta:#fff;--suave:rgba(255,255,255,.74);--raya:rgba(255,255,255,.4)} .e-plena.figus{--tinta:#1c1917;--suave:#8a7f74;--raya:rgba(28,25,23,.25)}
/* grano fuerte de película sobre el PAPEL (las fotos van arriba, limpias) */
.figus:not(.e-plena)::before,.fin::before{content:"";position:absolute;inset:0;pointer-events:none;opacity:var(--go,.42);mix-blend-mode:overlay;background-image:var(--grano);background-size:var(--gs) var(--gs)}
.figus:not(.e-plena) > *,.fin > *{position:relative;z-index:1}
/* ACOMODO LIBRE: cada cosa donde ella la dejó (posición y ancho en % de la página) */
.figus.libre{padding:0} .figus.libre > .el{position:absolute;touch-action:none}
.libre .fig{aspect-ratio:4/5} .libre .fig.marco{border:calc(var(--pw)*.014) solid #fff;box-shadow:0 calc(var(--pw)*.01) calc(var(--pw)*.035) rgba(40,30,25,.24)}
.libre .fig.marco::before{content:"";position:absolute;left:50%;top:calc(var(--pw)*-.03);width:34%;height:calc(var(--pw)*.04);transform:translateX(-50%) rotate(-3deg);background:rgba(201,180,160,.6)}
.editando .el{outline:1px dashed rgba(255,255,255,.75);outline-offset:2px;cursor:move} .editando .el.sel{outline:2px solid #e9c58b}
.el .h{display:none;position:absolute;right:-13px;bottom:-13px;width:26px;height:26px;border-radius:50%;background:#e9c58b;border:2px solid #fff;cursor:nwse-resize;z-index:5;touch-action:none}
.editando .el.sel .h{display:block}
#edbar{position:fixed;left:0;right:0;bottom:max(6px,env(safe-area-inset-bottom));display:none;justify-content:center;gap:6px;flex-wrap:wrap;z-index:30;padding:0 6px}
#edbar button{background:rgba(30,26,24,.92);color:#fff;border:1px solid rgba(255,255,255,.25);border-radius:10px;padding:8px 11px;font:500 13px var(--sans);cursor:pointer}
#edbar button.ok{background:#e9c58b;color:#1c1917;border:0} #edbar button:disabled{opacity:.35}
#edfondo{position:fixed;left:50%;transform:translateX(-50%);bottom:calc(max(6px,env(safe-area-inset-bottom)) + 96px);display:none;gap:10px;align-items:center;flex-wrap:wrap;justify-content:center;z-index:31;background:rgba(30,26,24,.96);border:1px solid rgba(255,255,255,.25);border-radius:12px;padding:10px 12px;color:#fff;font:500 13px var(--sans);max-width:94vw}
#edfondo input[type=color]{width:44px;height:34px;padding:0;border:1px solid rgba(255,255,255,.4);border-radius:8px;background:none}
#edfondo input[type=range]{width:130px} #edfondo button{background:rgba(255,255,255,.14);color:#fff;border:0;border-radius:8px;padding:7px 10px;font:inherit;cursor:pointer}
#edfondo .mu{display:flex;gap:5px} #edfondo .mu i{width:26px;height:26px;border-radius:50%;border:1px solid rgba(255,255,255,.5);cursor:pointer;display:block}
#edtip{position:fixed;top:10px;left:0;right:0;text-align:center;color:#e8d9d2;font-size:12.5px;z-index:30;display:none;pointer-events:none;padding:0 90px}
#acomodar{width:auto!important;padding:0 14px;font-size:13px!important}
.figus .art{font-size:calc(var(--w)*.026);font-weight:500;letter-spacing:.3em;text-transform:uppercase;color:var(--suave)}
.figus h2{font-family:var(--serif);font-weight:500;font-size:calc(var(--w)*.058);line-height:1.08;letter-spacing:.005em;margin:calc(var(--w)*.008) 0 0;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.figus h2.largo{font-size:calc(var(--w)*.046)} .figus .sub{font-size:calc(var(--w)*.024);letter-spacing:.08em;color:var(--suave);margin-top:calc(var(--w)*.008);line-height:1.4;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.pila{position:relative;flex:1;min-height:0;margin:calc(var(--w)*.035) 0}
.fig{background:#fff center/cover no-repeat}
/* figuritas: pegadas con cinta, inclinadas */
.e-figuritas .pila{margin:calc(var(--w)*.025) calc(var(--w)*-.04) calc(var(--w)*.03)}
.e-figuritas .fig{position:absolute;height:76%;aspect-ratio:4/5;border:calc(var(--w)*.014) solid #fff;box-shadow:0 calc(var(--w)*.01) calc(var(--w)*.035) rgba(40,30,25,.24)}
.e-figuritas .fig::before{content:"";position:absolute;left:50%;top:calc(var(--w)*-.03);width:34%;height:calc(var(--w)*.04);transform:translateX(-50%) rotate(-3deg);background:rgba(201,180,160,.6)}
.e-figuritas .fig.a{left:0;top:0;transform:rotate(-4deg)} .e-figuritas .fig.b{right:0;top:7%;transform:rotate(3.5deg);z-index:2} .e-figuritas .fig.c{left:24%;bottom:0;transform:rotate(-1.5deg);z-index:3}
.e-figuritas .fig.sola{left:50%;top:2%;height:94%;transform:translateX(-50%) rotate(-2deg)} .e-figuritas .fig.d1{left:1%;top:0;height:84%} .e-figuritas .fig.d2{right:1%;bottom:0;height:84%;top:auto}
/* editorial: una grande y dos chicas, todo a escuadra */
.e-editorial .pila{display:grid;grid-template-columns:1.62fr 1fr;grid-template-rows:1fr 1fr;gap:calc(var(--w)*.022)}
.e-editorial .fig:first-child{grid-row:1/3} .e-editorial .pila.n1{grid-template-columns:1fr} .e-editorial .pila.n2 .fig{grid-row:1/3} .e-editorial .pila.n2{grid-template-columns:1fr 1fr}
/* trío: tres en fila con aire */
.e-trio .pila{display:flex;align-items:center;justify-content:center;gap:calc(var(--w)*.022)}
.e-trio .fig{flex:1;aspect-ratio:4/5;max-height:100%} .e-trio .pila.n1 .fig{flex:0 0 62%}
/* plena: la foto a página entera, los datos sobre una banda de papel abajo */
.e-plena{padding:0;background:#1d1716 center/cover no-repeat;justify-content:flex-end}
.e-plena .banda{background:var(--papel);margin:calc(var(--w)*.05);padding:calc(var(--w)*.045) calc(var(--w)*.05)}
.e-plena h2{font-size:calc(var(--w)*.052)} .e-plena .datos{border:0;padding-top:calc(var(--w)*.02)}
.datos{border-top:1px solid var(--raya);padding-top:calc(var(--w)*.03);display:flex;justify-content:space-between;align-items:flex-end;gap:calc(var(--w)*.03)}
.datos .lbl{font-size:calc(var(--w)*.022);font-weight:500;letter-spacing:.3em;text-transform:uppercase;color:var(--suave)}
.chips{display:flex;flex-wrap:wrap;gap:calc(var(--w)*.012);margin-top:calc(var(--w)*.014)}
.chip{border:1px solid currentColor;padding:calc(var(--w)*.008) calc(var(--w)*.022);font-size:calc(var(--w)*.026);font-weight:500;letter-spacing:.12em;text-transform:uppercase}
.linea{font-size:calc(var(--w)*.026);letter-spacing:.06em;margin-top:calc(var(--w)*.018);color:var(--suave)}
.precio{font-family:var(--serif);font-weight:500;font-size:calc(var(--w)*.074);line-height:1;white-space:nowrap}
.grupal{background:#1d1716 center/cover no-repeat} .grupal .pie{position:absolute;left:0;right:0;bottom:0;padding:calc(var(--w)*.05);color:#fff;font-size:calc(var(--w)*.024);font-weight:500;letter-spacing:.3em;text-transform:uppercase;background:linear-gradient(transparent,rgba(0,0,0,.45))}
.fin{align-items:center;justify-content:center;text-align:center;padding:calc(var(--w)*.1);background:var(--papel)}
.fin h2{font-family:var(--sans);font-weight:300;text-transform:uppercase;letter-spacing:.32em;margin:0 -.32em calc(var(--w)*.05) 0;font-size:calc(var(--w)*.07)} .fin p{font-size:calc(var(--w)*.03);letter-spacing:.14em;margin:0;line-height:1.7}
.num{position:absolute;bottom:calc(var(--w)*.02);font-size:calc(var(--w)*.02);letter-spacing:.2em;color:var(--suave)} .num.i{left:calc(var(--w)*.04)} .num.d{right:calc(var(--w)*.04)}
.lomo::after{content:"";position:absolute;top:0;bottom:0;width:7%;pointer-events:none}
.lomo.izq::after{right:0;background:linear-gradient(90deg,transparent,rgba(0,0,0,.16))} .lomo.der::after{left:0;background:linear-gradient(270deg,transparent,rgba(0,0,0,.16))}
#ui{position:fixed;left:0;right:0;bottom:max(8px,env(safe-area-inset-bottom));display:flex;justify-content:center;gap:14px;align-items:center;color:#e8d9d2;font-size:13px;z-index:20}
#ui button{background:rgba(255,255,255,.12);color:#fff;border:0;border-radius:99px;width:38px;height:38px;font-size:18px;cursor:pointer} #ui button:disabled{opacity:.25}
#volver{position:fixed;top:10px;left:12px;z-index:20;color:#e8d9d2;font-size:13px;text-decoration:none;background:rgba(0,0,0,.35);padding:5px 10px;border-radius:99px}
#msg{color:#e8d9d2;text-align:center;padding:30px}
</style></head><body>
<div id="mesa"><div id="libro"><div id="msg">Abriendo el catálogo…</div></div></div>
<div id="ui" style="display:none"><button id="ant" aria-label="Página anterior">‹</button><span id="pos"></span><button id="sig" aria-label="Página siguiente">›</button><button id="acomodar" style="display:none">✏️ Acomodar esta página</button></div>
<a id="volver" href="%%VOLVER%%" style="display:none">◀ Editar</a>
<div id="edtip">Arrastrá para mover · el punto dorado cambia el tamaño · tocá algo y usá los botones</div>
<div id="edfondo"><label>Color <input type="color" id="fCol"></label>
  <span class="mu" id="fMu"></span><button id="fAuto">Como las fotos</button>
  <button id="fSubir">📷 Subir mi fondo</button><input type="file" id="fFile" accept="image/*" style="display:none">
  <label>Grano <input type="range" id="fGr" min="0" max="100" step="1"></label></div>
<div id="edbar"><button data-ed="menos">A −</button><button data-ed="mas">A +</button><button data-ed="gi">⟲</button><button data-ed="gd">⟳</button><button data-ed="frente">Al frente</button><button data-ed="fondo">🎨 Fondo</button><button data-ed="reset">Deshacer acomodo</button><button data-ed="ok" class="ok">✓ Listo</button></div>
<script>
const DATA = "%%DATA%%", IMG = "%%IMG%%", VOLVER = "%%VOLVER%%";
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const bg = iid => iid ? `style="background-image:url('${IMG}${iid}.jpg')"` : "";
let C = null, P = [], VISTAS = [], V = 0, DOBLE = false, GIRANDO = false, ED = null, SEL = null;
// EL GRANO del papel se dibuja acá, punto por punto, para que tenga el MISMO tamaño en cualquier pantalla (con un
// filtro del navegador salía fino en un celular y grueso como granito en una tablet).
(() => { try{ const N = 256, c = document.createElement("canvas"); c.width = c.height = N; const x = c.getContext("2d"), d = x.createImageData(N, N);
    for(let i = 0; i < N * N; i++){ const v = Math.max(0, Math.min(255, 128 + (Math.random() + Math.random() + Math.random() - 1.5) * 165)); d.data[i * 4] = d.data[i * 4 + 1] = d.data[i * 4 + 2] = v; d.data[i * 4 + 3] = 255; }
    x.putImageData(d, 0, 0); const dpr = window.devicePixelRatio || 1, punto = Math.max(1, dpr / 1.6);
    document.documentElement.style.setProperty("--grano", `url(${c.toDataURL()})`); document.documentElement.style.setProperty("--gs", (N * punto / dpr) + "px");
    if(dpr < 1.5) document.documentElement.style.setProperty("--go", ".26");   // en pantallas de baja resolución el punto es grande: más suave
  }catch(e){} })();
function paginas(){
  const pt = C.portada, hayTx = !!(pt.titulo || pt.leyenda);
  const out = [`<div class="pg portada ${hayTx ? "contx" : ""} ${pt.tono === "oscuro" ? "oscuro" : ""}" ${bg(pt.img)}>${hayTx ? `<div class="tx">${pt.titulo ? `<h1>${esc(pt.titulo)}</h1>` : ""}${pt.leyenda ? `<p><span>${esc(pt.leyenda)}</span></p>` : ""}</div>` : ""}</div>`];
  C.productos.forEach(p => {
    const fs = (p.figus || []).filter(Boolean), est = ["figuritas", "editorial", "trio", "plena"].includes(p.estilo) ? p.estilo : "figuritas";
    const cls = fs.length === 1 ? ["sola"] : fs.length === 2 ? ["a d1", "b d2"] : ["a", "b", "c"];
    const talles = String(p.talles || "").split(/[,·\/|;]+|\s{2,}/).map(x => x.trim()).filter(Boolean);
    // El nombre largo se parte en la primera coma: adelante el nombre, y el resto como una línea chica de detalle.
    const nom = String(p.nombre || "").trim(), ic = nom.indexOf(","), tit = ic > 3 ? nom.slice(0, ic).trim() : nom, sub = ic > 3 ? nom.slice(ic + 1).trim() : "";
    const cabIn = `<div class="art">${p.articulo ? "Art. " + esc(p.articulo) : ""}</div><h2 class="${tit.length > 30 ? "largo" : ""}">${esc(tit)}</h2>${sub ? `<div class="sub">${esc(sub.charAt(0).toUpperCase() + sub.slice(1))}</div>` : ""}`;
    const cab = `<div class="cab">${cabIn}</div>`, k = out.length >> 1;
    const datos = `<div class="datos"><div>${talles.length ? `<div class="lbl">Talles</div><div class="chips">${talles.map(t => `<span class="chip">${esc(t)}</span>`).join("")}</div>` : ""}
        ${p.colores ? `<div class="linea">${esc(p.colores)}</div>` : ""}</div>${p.precio ? `<div class="precio">${esc(p.precio)}</div>` : ""}</div>`;
    if(est === "plena") out.push(`<div class="pg figus e-plena" ${bg(fs[0])}><div class="banda">${cab}${datos}</div></div>`);
    else if(p.lay){
      // ACOMODADA A MANO: cada cosa en su lugar, su tamaño y su giro
      const L = p.lay, pos = e => `left:${e.x * 100}%;top:${e.y * 100}%;width:${e.w * 100}%;z-index:${e.z || 1};`;
      const tx = (kk, cls, inner, def) => { const e = L[kk] || (L[kk] = def); return `<div class="el ${cls}" data-el="${kk}" style="${pos(e)}--w:calc(var(--pw)*${e.s || 1})">${inner}<i class="h"></i></div>`; };
      const fb = fondoDe(p);
      out.push(`<div class="pg figus libre ${fb.osc ? "osc" : ""}" data-k="${k}" style="${fb.c ? `--pp:${fb.c};` : ""}${fb.g != null ? `--go:${fb.g};` : ""}${fb.img ? `background-image:url('${IMG}${fb.img}.jpg');background-size:cover;background-position:center;` : ""}">`
        + tx("cab", "cab", cabIn, {x: .07, y: .05, w: .86, s: 1, z: 1})
        + fs.map((f, i) => { const e = L["f" + i] || (L["f" + i] = {x: .08 + .22 * i, y: .28, w: .4, r: 0, z: 2 + i});
            return `<div class="el fig ${est === "figuritas" ? "marco" : ""}" data-el="f${i}" style="${pos(e)}transform:rotate(${e.r || 0}deg);background-image:url('${IMG}${f}.jpg')"><i class="h"></i></div>`; }).join("")
        + tx("dat", "datos", datos.replace(/^<div class="datos">/, "").replace(/<\/div>$/, ""), {x: .07, y: .82, w: .86, s: 1, z: 1}) + `</div>`);
    }
    else out.push(`<div class="pg figus e-${est} ${p._osc ? "osc" : ""}" data-k="${k}" ${p._bg ? `style="--pp:${p._bg}"` : ""}>${cab}<div class="pila n${fs.length}">${fs.map((f, i) => `<div class="fig ${est === "figuritas" ? cls[i] : ""}" ${bg(f)}></div>`).join("")}</div>${datos}</div>`);
    out.push(`<div class="pg grupal" ${bg(p.grupal)}></div>`);   // la grupal va limpia: el artículo ya está en la página de al lado
  });
  out.push(`<div class="pg fin"><h2>${esc(C.portada.titulo || C.nombre)}</h2><p>${esc(C.contacto || "")}</p></div>`);
  return out;
}
// EL PAPEL DE CADA PÁGINA = EL FONDO DE SUS FOTOS. Se mide en los bordes de la primera foto del producto (ahí está
// el fondo, no ella) y se toma la mediana, así la sombra o un brazo que toque el borde no lo corren.
function colorDeFondo(iid){ return new Promise(ok => { const im = new Image(); im.onerror = () => ok(null);
  im.onload = () => { try{ const W = 40, H = 50, c = document.createElement("canvas"); c.width = W; c.height = H; const x = c.getContext("2d", {willReadFrequently: true});
      x.drawImage(im, 0, 0, W, H); const d = x.getImageData(0, 0, W, H).data, m = [[], [], []];
      for(let y = 0; y < H * .65; y++) for(const X of [0, 1, 2, W - 3, W - 2, W - 1]){ const k = (y * W + X) * 4; m[0].push(d[k]); m[1].push(d[k + 1]); m[2].push(d[k + 2]); }
      for(let X = 3; X < W - 3; X++) for(const y of [0, 1]){ const k = (y * W + X) * 4; m[0].push(d[k]); m[1].push(d[k + 1]); m[2].push(d[k + 2]); }
      ok(m.map(a => a.sort((p, q) => p - q)[a.length >> 1])); }catch(e){ ok(null); } };
  im.src = IMG + iid + ".jpg"; }); }
const hexRGB = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
async function papeles(){
  const f = C.fondo || "auto"; if(f === "papel") return;
  for(const p of C.productos){ const rgb = /^#/.test(f) ? hexRGB(f) : await colorDeFondo((p.figus || []).filter(Boolean)[0] || p.grupal || "");
    if(!rgb) continue; p._bg = `rgb(${rgb.join(",")})`; p._osc = (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) < 140; }
  P = paginas(); pintar();
}
function medir(){
  const vw = innerWidth, vh = innerHeight; DOBLE = vw / vh > 1.05;
  const h = Math.min(vh * (DOBLE ? .9 : .92), (DOBLE ? vw * .48 : vw) * 5 / 4), w = h * 4 / 5;   // todas las hojas en 4:5, como las fotos
  document.documentElement.style.setProperty("--w", w + "px"); document.documentElement.style.setProperty("--pw", w + "px"); document.documentElement.style.setProperty("--h", h + "px");
  // las vistas: de a una página, o el libro abierto (portada sola a la derecha, y el final solo a la izquierda)
  const actual = VISTAS[V] ? (VISTAS[V][1] != null ? VISTAS[V][1] : VISTAS[V][0]) : 0;
  VISTAS = [];
  if(DOBLE){ VISTAS.push([null, 0]); for(let i = 1; i < P.length - 1; i += 2) VISTAS.push([i, i + 1 < P.length - 1 ? i + 1 : null]); VISTAS.push([P.length - 1, null]); }
  else P.forEach((_, i) => VISTAS.push([i]));
  V = Math.max(0, VISTAS.findIndex(v => v.includes(actual))); pintar();
}
const cara = (i, lado) => i == null ? `<div class="cara vacia"></div>` : `<div class="cara ${DOBLE ? "lomo " + lado : ""}">${P[i]}<span class="num ${lado === "izq" ? "i" : "d"}">${i ? i : ""}</span><i class="borde"></i></div>`;
function pintar(){
  if(ED) P = paginas();
  const v = VISTAS[V], L = document.getElementById("libro");
  L.innerHTML = DOBLE ? `<div id="cI">${cara(v[0], "izq")}</div><div id="cD">${cara(v[1], "der")}</div>` : `<div id="cD">${cara(v[0], "der")}</div>`;
  document.getElementById("pos").textContent = `${V + 1} / ${VISTAS.length}`;
  document.getElementById("ant").disabled = V === 0; document.getElementById("sig").disabled = V === VISTAS.length - 1;
  if(ED) armarEdicion();
}
// El fondo de una página acomodada: su color propio si se lo eligieron (si no, el de las fotos) y su grano.
const oscuro = rgb => (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) < 140;
function fondoDe(p){ const b = (p.lay && p.lay.bg) || {}; if(b.img) return {img: b.img, c: "", osc: !!b.osc, g: b.g != null ? b.g : null}; if(b.c) return {c: b.c, osc: oscuro(hexRGB(b.c)), g: b.g != null ? b.g : null};
  return {c: p._bg || "", osc: !!p._osc, g: b.g != null ? b.g : null}; }
function pintarFondo(){ if(!ED) return; const p = C.productos[ED.k], pg = document.querySelector(`#libro .pg[data-k="${ED.k}"]`), f = fondoDe(p); if(!pg) return;
  if(f.c) pg.style.setProperty("--pp", f.c); else pg.style.removeProperty("--pp"); pg.classList.toggle("osc", f.osc);
  if(f.g != null) pg.style.setProperty("--go", f.g); else pg.style.removeProperty("--go");
  pg.style.backgroundImage = f.img ? `url('${IMG}${f.img}.jpg')` : ""; pg.style.backgroundSize = f.img ? "cover" : ""; pg.style.backgroundPosition = f.img ? "center" : ""; }
// Una imagen propia de fondo: se achica acá, se mide si es clara u oscura (para el color de las letras) y se sube.
function prepararFondo(file){ return new Promise((ok, mal) => { const im = new Image(), u = URL.createObjectURL(file);
  im.onload = () => { const k = Math.min(1, 1800 / Math.max(im.width, im.height)), c = document.createElement("canvas"); c.width = Math.round(im.width * k); c.height = Math.round(im.height * k);
    c.getContext("2d").drawImage(im, 0, 0, c.width, c.height); URL.revokeObjectURL(u);
    const m = document.createElement("canvas"); m.width = m.height = 24; const x = m.getContext("2d"); x.drawImage(c, 0, 0, 24, 24); const d = x.getImageData(0, 0, 24, 24).data; let t = [0, 0, 0];
    for(let q = 0; q < d.length; q += 4){ t[0] += d[q]; t[1] += d[q + 1]; t[2] += d[q + 2]; }
    ok({img: c.toDataURL("image/jpeg", 0.9), osc: oscuro(t.map(n => n / 576))}); };
  im.onerror = () => mal(new Error("No pude leer esa imagen.")); im.src = u; }); }
function abrirFondo(on){ const w = document.getElementById("edfondo"); w.style.display = on ? "flex" : "none"; if(!on || !ED) return;
  const p = C.productos[ED.k], b = (p.lay.bg = p.lay.bg || {}), f = fondoDe(p), aHex = c => { const m = /^rgb\((\d+),\s*(\d+),\s*(\d+)\)/.exec(c || ""); return m ? "#" + [1, 2, 3].map(i => (+m[i]).toString(16).padStart(2, "0")).join("") : (/^#/.test(c || "") ? c : "#f6f1e9"); };
  const col = document.getElementById("fCol"), gr = document.getElementById("fGr"); col.value = aHex(f.c); gr.value = Math.round((b.g != null ? b.g : .42) * 100);
  col.oninput = () => { b.c = col.value; delete b.img; delete b.osc; pintarFondo(); }; gr.oninput = () => { b.g = gr.value / 100; pintarFondo(); };
  const sub = document.getElementById("fSubir"), fil = document.getElementById("fFile"); sub.onclick = () => fil.click();
  fil.onchange = async () => { const f0 = fil.files[0]; fil.value = ""; if(!f0) return; sub.disabled = true; sub.textContent = "Subiendo…";
    try{ const pr = await prepararFondo(f0), r = await fetch(DATA + "/img", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({img: pr.img})}), j = await r.json();
      if(!r.ok) throw new Error(j.detail || "No se pudo subir."); b.img = j.iid; b.osc = pr.osc; delete b.c; pintarFondo(); }
    catch(err){ alert(err.message); } sub.disabled = false; sub.textContent = "📷 Subir mi fondo"; };
  document.getElementById("fAuto").onclick = () => { delete b.c; delete b.img; delete b.osc; pintarFondo(); col.value = aHex(fondoDe(p).c); };
  const mu = document.getElementById("fMu"); mu.innerHTML = ["#f6f1e9", "#e3cfc6", "#c9b8a6", "#9a8f86", "#5b524c", "#1c1917"].map(c => `<i data-c="${c}" style="background:${c}" title="${c}"></i>`).join("");
  mu.onclick = ev => { const c = ev.target.dataset.c; if(!c) return; b.c = c; delete b.img; delete b.osc; col.value = c; pintarFondo(); }; }
// ── ✏️ ACOMODAR A MANO: mover, agrandar, girar y poner adelante el título, los datos y cada foto ──
const GIRO = {a: -4, b: 3.5, c: -1.5, sola: -2};
function prodDeVista(){ for(const i of VISTAS[V]) if(i != null && i >= 1 && i < P.length - 1 && i % 2 === 1) return (i - 1) / 2; return null; }
function medirLay(k){
  // el punto de partida es lo que se ve: se mide dónde quedó cada cosa en la página armada sola
  const pg = document.querySelector(`#libro .pg[data-k="${k}"]`); if(!pg) return null;
  const W = pg.offsetWidth, H = pg.offsetHeight, L = {}, r4 = n => Math.round(n * 1e4) / 1e4;
  const caja = (el, extra) => { let x = el.offsetLeft, y = el.offsetTop, o = el.offsetParent; while(o && o !== pg){ x += o.offsetLeft; y += o.offsetTop; o = o.offsetParent; }
    return Object.assign({x: r4(x / W), y: r4(y / H), w: r4(el.offsetWidth / W)}, extra); };
  const cab = pg.querySelector(".cab"), dat = pg.querySelector(".datos");
  if(cab) L.cab = caja(cab, {s: 1, z: 1}); if(dat) L.dat = caja(dat, {s: 1, z: 1});
  pg.querySelectorAll(".fig").forEach((f, i) => { const c = [...f.classList].find(x => GIRO[x] != null), e = caja(f, {r: c ? GIRO[c] : 0, z: 2 + i});
    if(f.classList.contains("sola")) e.x = r4(e.x - e.w / 2); L["f" + i] = e; });
  return L;
}
function armarEdicion(){
  const pg = document.querySelector(`#libro .pg[data-k="${ED.k}"]`); if(!pg) return;
  pg.classList.add("editando"); const L = C.productos[ED.k].lay;
  const poner = (el, e) => { el.style.left = e.x * 100 + "%"; el.style.top = e.y * 100 + "%"; el.style.width = e.w * 100 + "%"; el.style.zIndex = e.z || 1;
    if(el.classList.contains("fig")) el.style.transform = `rotate(${e.r || 0}deg)`; else el.style.setProperty("--w", `calc(var(--pw)*${e.s || 1})`); };
  ED.poner = () => pg.querySelectorAll(".el").forEach(el => { poner(el, L[el.dataset.el]); el.classList.toggle("sel", el.dataset.el === SEL); });
  pg.querySelectorAll(".el").forEach(el => {
    let ini = null;
    el.onpointerdown = ev => { ev.stopPropagation(); ev.preventDefault(); SEL = el.dataset.el; const e = L[SEL];
      ini = {px: ev.clientX, py: ev.clientY, x: e.x, y: e.y, w: e.w, tam: ev.target.classList.contains("h")}; el.setPointerCapture(ev.pointerId); ED.poner(); barra(); };
    el.onpointermove = ev => { if(!ini) return; const e = L[SEL], dx = (ev.clientX - ini.px) / pg.offsetWidth, dy = (ev.clientY - ini.py) / pg.offsetHeight;
      if(ini.tam) e.w = Math.max(.1, Math.min(1.4, ini.w + dx)); else { e.x = Math.max(-.4, Math.min(1.1, ini.x + dx)); e.y = Math.max(-.4, Math.min(1.1, ini.y + dy)); }
      poner(el, e); };
    el.onpointerup = el.onpointercancel = ev => { ini = null; ev.stopPropagation(); };
  });
  ED.poner(); barra();
}
function barra(){
  const esFoto = SEL && SEL[0] === "f"; document.querySelectorAll("#edbar [data-ed]").forEach(b => { const a = b.dataset.ed;
    if(a === "fondo") return; b.disabled = (["menos", "mas", "frente"].includes(a) && !SEL) || (["gi", "gd"].includes(a) && !esFoto);
    if(a === "menos") b.textContent = esFoto ? "Foto −" : "Letra −"; if(a === "mas") b.textContent = esFoto ? "Foto +" : "Letra +"; });
}
function modoEdicion(on){
  document.getElementById("edbar").style.display = on ? "flex" : "none"; document.getElementById("edtip").style.display = on ? "block" : "none";
  document.getElementById("ui").style.display = on ? "none" : "flex";
}
async function guardarLay(k, lay){ const r = await fetch(DATA + "/lay", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({i: k, lay})});
  if(!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "No se pudo guardar el acomodo."); }
document.getElementById("acomodar").onclick = () => {
  const k = prodDeVista(); if(k == null) return alert("Andá primero a la página de un producto (la de las fotos chicas y los talles).");
  const p = C.productos[k]; if(p.estilo === "plena") return alert("La foto a página entera no se acomoda: elegí otro estilo para este producto en ◀ Editar.");
  if(!p.lay){ const L = medirLay(k); if(!L) return; p.lay = L; }
  ED = {k}; SEL = null; modoEdicion(true); pintar();
};
document.getElementById("edbar").onclick = async ev => {
  const a = (ev.target.closest("[data-ed]") || {dataset: {}}).dataset.ed; if(!a || !ED) return; const p = C.productos[ED.k], L = p.lay, e = SEL && L[SEL], foto = SEL && SEL[0] === "f";
  if(a === "fondo") return abrirFondo(document.getElementById("edfondo").style.display !== "flex");
  if(a === "ok" || a === "reset"){ abrirFondo(false);
    if(p.lay && p.lay.bg && !Object.keys(p.lay.bg).length) delete p.lay.bg;
    if(a === "reset"){ if(!confirm("¿Volver esta página al armado automático?")) return; delete p.lay; }
    const k = ED.k; ED = null; SEL = null; modoEdicion(false); P = paginas(); pintar();
    try{ await guardarLay(k, p.lay || null); }catch(err){ alert(err.message); } return; }
  if(!e) return;
  if(a === "mas" || a === "menos"){ const d = a === "mas" ? 1 : -1; if(foto) e.w = Math.max(.1, Math.min(1.4, e.w + .03 * d)); else e.s = Math.max(.4, Math.min(3, (e.s || 1) + .07 * d)); }
  if(a === "gi") e.r = (e.r || 0) - 1.5; if(a === "gd") e.r = (e.r || 0) + 1.5;
  if(a === "frente") e.z = Math.min(99, Math.max(...Object.keys(L).filter(q => q !== "bg").map(q => L[q].z || 1)) + 1);
  ED.poner();
};
function pasar(d){
  if(ED || GIRANDO || !VISTAS[V + d]) return; GIRANDO = true;
  const L = document.getElementById("libro"), a = VISTAS[V], b = VISTAS[V + d], h = document.createElement("div"); h.className = "hoja";
  const fin = () => { V += d; GIRANDO = false; pintar(); };
  if(!DOBLE){
    // una página: la de arriba gira sobre el borde izquierdo y descubre la que sigue (al volver, al revés)
    h.style.left = "0"; h.style.transformOrigin = "left center";
    if(d > 0){ h.innerHTML = cara(a[0], "der"); document.getElementById("cD").innerHTML = cara(b[0], "der"); L.appendChild(h); requestAnimationFrame(() => requestAnimationFrame(() => h.style.transform = "rotateY(-115deg)")); }
    else { h.innerHTML = cara(b[0], "der"); h.style.transform = "rotateY(-115deg)"; L.appendChild(h); requestAnimationFrame(() => requestAnimationFrame(() => h.style.transform = "rotateY(0deg)")); }
  } else if(d > 0){
    // libro abierto, hacia adelante: la hoja derecha da vuelta sobre el lomo y cae a la izquierda
    h.style.left = "var(--w)"; h.style.transformOrigin = "left center";
    h.innerHTML = cara(a[1], "der") + cara(b[0], "izq").replace('class="cara', 'class="cara dorso');
    document.getElementById("cD").innerHTML = cara(b[1], "der"); L.appendChild(h);
    requestAnimationFrame(() => requestAnimationFrame(() => h.style.transform = "rotateY(-180deg)"));
  } else {
    h.style.left = "0"; h.style.transformOrigin = "right center";
    h.innerHTML = cara(a[0], "izq") + cara(b[1], "der").replace('class="cara', 'class="cara dorso');
    document.getElementById("cI").innerHTML = cara(b[0], "izq"); L.appendChild(h);
    requestAnimationFrame(() => requestAnimationFrame(() => h.style.transform = "rotateY(180deg)"));
  }
  let hecho = false; const una = () => { if(hecho) return; hecho = true; fin(); };
  h.addEventListener("transitionend", una); setTimeout(una, 900);
}
document.getElementById("sig").onclick = () => pasar(1); document.getElementById("ant").onclick = () => pasar(-1);
addEventListener("keydown", e => { if(e.key === "ArrowRight" || e.key === " ") pasar(1); if(e.key === "ArrowLeft") pasar(-1); });
let x0 = null; const mesa = document.getElementById("mesa");
mesa.addEventListener("pointerdown", e => { x0 = e.clientX; });
mesa.addEventListener("pointerup", e => { if(ED){ x0 = null; return; } if(x0 == null) return; const dx = e.clientX - x0; x0 = null;
  if(Math.abs(dx) > 40) pasar(dx < 0 ? 1 : -1); else pasar(e.clientX > innerWidth / 2 ? 1 : -1); });
addEventListener("resize", () => { if(C) medir(); });
(async () => { try{ const r = await fetch(DATA); const j = await r.json(); if(!r.ok) throw new Error(j.detail || "No se pudo abrir.");
    C = j.catalogo; C.portada = C.portada || {}; C.productos = C.productos || []; document.title = C.portada.titulo || C.nombre || "Catálogo";
    P = paginas(); document.getElementById("ui").style.display = "flex"; if(VOLVER){ document.getElementById("volver").style.display = ""; document.getElementById("acomodar").style.display = ""; } medir(); papeles();
    // las fotos de las páginas que siguen, de a poco, para que al pasar ya estén
    [C.portada.img].concat(...C.productos.map(p => [...(p.figus || []), p.grupal])).filter(Boolean).forEach((f, i) => setTimeout(() => { new Image().src = IMG + f + ".jpg"; }, 150 * i));
  }catch(e){ document.getElementById("msg").textContent = e.message; } })();
</script></body></html>"""
