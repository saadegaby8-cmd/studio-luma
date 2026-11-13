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
VERSION = "1.0.0"
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


def _publico(c: Dict[str, Any]) -> Dict[str, Any]:
    """Lo que ve quien abre el catálogo: nada de la cuenta, sólo el contenido."""
    return {"nombre": c.get("nombre", ""), "portada": c.get("portada") or {},
            "productos": [{k: p.get(k) for k in ("articulo", "nombre", "talles", "colores", "precio", "figus", "grupal")}
                          for p in c.get("productos") or []],
            "contacto": c.get("contacto", "")}


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
        if isinstance(payload.get("portada"), dict):
            p = payload["portada"]
            c["portada"] = {"img": _iid(p.get("img"), validas), "titulo": _t(p.get("titulo"), 60),
                            "leyenda": _t(p.get("leyenda"), 160)}
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
                              "grupal": _iid(p.get("grupal"), validas)})
            c["productos"] = prods
        usadas = {c["portada"].get("img")} | {x for p in c["productos"] for x in p["figus"] + [p["grupal"]]}
        for iid in validas - usadas:
            await kv.delete(_k_img(cid, iid))
        c["imgs"] = sorted(validas & usadas)
        c["actualizado"] = time.strftime("%Y-%m-%d %H:%M")
        await kv.set(_k(cid), c)
    return {"catalogo": c}


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
input,textarea{width:100%;background:#141318;color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:8px;font:inherit}
label{display:block;font-size:12.5px;color:var(--ink-soft);margin:8px 0 3px}
.fila{display:flex;gap:8px;align-items:center;flex-wrap:wrap} .g2{display:grid;grid-template-columns:1fr 1fr;gap:10px} .g3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
.slot{position:relative;background:#141318 center/cover no-repeat;border:1.5px dashed var(--line);border-radius:10px;aspect-ratio:4/5;display:flex;align-items:center;justify-content:center;text-align:center;color:var(--ink-soft);font-size:12px;cursor:pointer;overflow:hidden}
.slot.con{border-style:solid} .slot .x{position:absolute;top:4px;right:4px;background:rgba(0,0,0,.65);color:#fff;border:0;border-radius:50%;width:24px;height:24px;padding:0;line-height:1}
.slot input{display:none} .fotos{display:grid;grid-template-columns:repeat(3,1fr) 1.6fr;gap:8px;margin-top:8px}
.portada{display:grid;grid-template-columns:180px 1fr;gap:14px} .portada .slot{aspect-ratio:9/16}
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
        <div><label>Contacto al final (WhatsApp, Instagram…)</label><input data-c="contacto" value="${esc(C.contacto || "")}" placeholder="ej: @luma.intima · 11 5555-5555"></div></div>
      <div class="fila" style="margin-top:10px"><a class="btn go" href="${PREF}/ver?id=${C.id}" target="_blank">👁 Ver el catálogo</a>
        ${C.token ? `<button id="copiar">🔗 Copiar el link para clientas</button><button id="despub">Despublicar</button>` : `<button id="pub">🌐 Publicar (link para clientas)</button>`}
        <span class="hint" id="est"></span><span style="flex:1"></span><button id="otros">Mis catálogos</button></div>
      ${C.token ? `<div class="hint" style="margin-top:6px">Publicado: <a href="${link}" target="_blank">${esc(link)}</a> · lo abre cualquiera que tenga el link, sin iniciar sesión.</div>` : `<div class="hint" style="margin-top:6px">Sin publicar: sólo lo ves vos.</div>`}</div>
    <div class="card"><h2>La portada</h2><div class="portada">${slot("portada", C.portada.img, "Tocá para elegir la foto de portada")}
      <div><label>Título</label><input data-p="titulo" value="${esc(C.portada.titulo)}" placeholder="ej: Colección Verano 2027">
        <label>Leyenda</label><textarea data-p="leyenda" rows="3" placeholder="ej: Básicos sin costuras · venta por mayor y menor">${esc(C.portada.leyenda)}</textarea>
        <p class="hint">La foto va a página entera (vertical), con el título y la leyenda encima.</p></div></div></div>
    ${C.productos.map((p, i) => `<div class="card"><div class="fila" style="justify-content:space-between"><h2>Producto ${i + 1}</h2>
        <span><button class="sm" data-sube="${i}" ${i ? "" : "disabled"}>↑</button> <button class="sm" data-baja="${i}" ${i < C.productos.length - 1 ? "" : "disabled"}>↓</button> <button class="sm" data-borra="${i}">🗑</button></span></div>
      <div class="g3"><div><label>Artículo</label><input data-k="articulo" data-i="${i}" value="${esc(p.articulo)}" placeholder="ej: M326"></div>
        <div><label>Nombre</label><input data-k="nombre" data-i="${i}" value="${esc(p.nombre)}" placeholder="ej: Top sin costuras"></div>
        <div><label>Talles que vienen</label><input data-k="talles" data-i="${i}" value="${esc(p.talles)}" placeholder="ej: S · M · L · XL"></div>
        <div><label>Colores (opcional)</label><input data-k="colores" data-i="${i}" value="${esc(p.colores)}" placeholder="ej: negro, blanco, beige"></div>
        <div><label>Precio (opcional)</label><input data-k="precio" data-i="${i}" value="${esc(p.precio)}" placeholder="ej: $19.800"></div></div>
      <label>Página izquierda: 3 figuritas · Página derecha: la foto grupal</label>
      <div class="fotos">${[0, 1, 2].map(k => slot(`figu:${i}:${k}`, p.figus[k], `Figurita ${k + 1}`)).join("")}${slot(`grupal:${i}`, p.grupal, "Foto grupal", "grupal")}</div></div>`).join("")}
    <p><button class="go" id="mas">＋ Agregar producto</button></p>`;
  $$("[data-c]").forEach(x => x.oninput = () => { C[x.dataset.c] = x.value; guardar(); });
  $$("[data-p]").forEach(x => x.oninput = () => { C.portada[x.dataset.p] = x.value; guardar(); });
  $$("[data-k]").forEach(x => x.oninput = () => { C.productos[+x.dataset.i][x.dataset.k] = x.value; guardar(); });
  $$("[data-file]").forEach(x => x.onchange = async () => { const f = x.files[0]; if(!f) return; const lab = x.closest(".slot"); lab.textContent = "Subiendo…";
    try{ const r = await post(`/${C.id}/img`, {img: await achicar(f)}); poner(x.dataset.file, r.iid); guardar(true); setTimeout(pintar, 400); }catch(e){ alert(e.message); pintar(); } });
  $$("[data-quitar]").forEach(b => b.onclick = e => { e.preventDefault(); poner(b.dataset.quitar, ""); guardar(true); pintar(); });
  $$("[data-borra]").forEach(b => b.onclick = () => { if(!confirm("¿Borrar este producto del catálogo?")) return; C.productos.splice(+b.dataset.borra, 1); guardar(true); pintar(); });
  $$("[data-sube]").forEach(b => b.onclick = () => { const i = +b.dataset.sube; [C.productos[i - 1], C.productos[i]] = [C.productos[i], C.productos[i - 1]]; guardar(true); pintar(); });
  $$("[data-baja]").forEach(b => b.onclick = () => { const i = +b.dataset.baja; [C.productos[i + 1], C.productos[i]] = [C.productos[i], C.productos[i + 1]]; guardar(true); pintar(); });
  $("#mas").onclick = () => { C.productos.push({id: "", articulo: "", nombre: "", talles: "", colores: "", precio: "", figus: ["", "", ""], grupal: ""}); guardar(true); pintar(); window.scrollTo(0, document.body.scrollHeight); };
  if($("#pub")) $("#pub").onclick = async () => { if(!confirm("Publicar crea un link que cualquiera con el link puede abrir sin iniciar sesión. ¿Publicar?")) return; C = (await post(`/${C.id}/publicar`, {on: true})).catalogo; pintar(); };
  if($("#despub")) $("#despub").onclick = async () => { if(!confirm("El link deja de funcionar. ¿Despublicar?")) return; C = (await post(`/${C.id}/publicar`, {on: false})).catalogo; pintar(); };
  if($("#copiar")) $("#copiar").onclick = async () => { try{ await navigator.clipboard.writeText(link); $("#est").innerHTML = '<span class="bien">✓ link copiado</span>'; }catch(e){ prompt("Copiá el link:", link); } };
  $("#otros").onclick = () => { history.replaceState(null, "", PREF); lista(); };
}
async function lista(){
  const d = await api("/lista");
  $("#app").innerHTML = `<h1>📖 Catálogo</h1><p class="hint">El catálogo digital para el celular, que se hojea como un libro: portada, y por cada producto tres figuritas con el artículo y los talles a la izquierda y la foto grupal a la derecha.</p>
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
<link href="https://fonts.googleapis.com/css2?family=Jost:wght@400;500;600&family=Playfair+Display:ital,wght@0,500;0,600;1,500&family=Caveat:wght@500;600&display=swap" rel="stylesheet">
<style>
:root{--papel:#F3E6DF;--papel2:#EAD8CF;--tinta:#3b2f2c;--rosa:#C9A59A;--fondo:#2a2423}
*{box-sizing:border-box} html,body{margin:0;height:100%;background:var(--fondo);overflow:hidden;font-family:Jost,system-ui,sans-serif;color:var(--tinta);-webkit-user-select:none;user-select:none;touch-action:pan-y}
#mesa{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;perspective:2200px}
#libro{position:relative;display:flex;filter:drop-shadow(0 18px 40px rgba(0,0,0,.45))}
.cara{position:relative;width:var(--w);height:var(--h);overflow:hidden;background:var(--papel)}
.vacia{background:transparent}
.hoja{position:absolute;top:0;width:var(--w);height:var(--h);transform-style:preserve-3d;transition:transform .75s cubic-bezier(.3,.1,.2,1);z-index:5}
.hoja > .cara{position:absolute;inset:0;backface-visibility:hidden;-webkit-backface-visibility:hidden}
.hoja > .dorso{transform:rotateY(180deg)}
.pg{position:absolute;inset:0;display:flex;flex-direction:column}
.portada{background:#1d1716 center/cover no-repeat;color:#fff;justify-content:flex-end}
.portada::after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.05) 35%,rgba(0,0,0,.62))}
.portada .tx{position:relative;z-index:1;padding:calc(var(--w)*.08);padding-bottom:calc(var(--w)*.14)}
.portada h1{font-family:"Playfair Display",serif;font-weight:600;font-size:calc(var(--w)*.105);line-height:1.05;margin:0 0 calc(var(--w)*.03)}
.portada p{font-size:calc(var(--w)*.042);margin:0;opacity:.95;line-height:1.35}
.figus{padding:calc(var(--w)*.07) calc(var(--w)*.06);background:radial-gradient(120% 80% at 20% 0%,#F8EEE8,var(--papel) 60%,var(--papel2))}
.figus .art{font-size:calc(var(--w)*.034);letter-spacing:.18em;text-transform:uppercase;color:#8d6f66}
.figus h2{font-family:"Playfair Display",serif;font-weight:600;font-size:calc(var(--w)*.074);line-height:1.1;margin:calc(var(--w)*.01) 0 0}
.pila{position:relative;flex:1;margin:calc(var(--w)*.03) 0}
.fig{position:absolute;width:52%;aspect-ratio:4/5;background:#fff center/cover no-repeat;border:calc(var(--w)*.016) solid #fff;border-radius:calc(var(--w)*.012);box-shadow:0 calc(var(--w)*.012) calc(var(--w)*.04) rgba(60,40,35,.28)}
.fig::before{content:"";position:absolute;left:50%;top:calc(var(--w)*-.034);width:34%;height:calc(var(--w)*.045);transform:translateX(-50%) rotate(-3deg);background:rgba(201,165,154,.6);border-radius:2px}
.fig.a{left:0;top:0;transform:rotate(-5deg)} .fig.b{right:0;top:20%;transform:rotate(4deg);z-index:2} .fig.c{left:9%;bottom:0;transform:rotate(-2deg);z-index:3}
.fig.sola{left:14%;top:6%;width:72%;transform:rotate(-2deg)} .fig.d1{left:2%;top:2%;width:58%} .fig.d2{right:2%;bottom:2%;width:58%}
.datos{border-top:1px solid rgba(59,47,44,.18);padding-top:calc(var(--w)*.03)}
.datos .lbl{font-size:calc(var(--w)*.03);letter-spacing:.14em;text-transform:uppercase;color:#8d6f66}
.chips{display:flex;flex-wrap:wrap;gap:calc(var(--w)*.016);margin-top:calc(var(--w)*.014)}
.chip{border:1px solid var(--tinta);border-radius:99px;padding:calc(var(--w)*.008) calc(var(--w)*.03);font-size:calc(var(--w)*.038);font-weight:500}
.linea{font-size:calc(var(--w)*.036);margin-top:calc(var(--w)*.016)} .precio{font-family:"Caveat",cursive;font-size:calc(var(--w)*.085);color:#a2574b;line-height:1}
.grupal{background:#1d1716 center/cover no-repeat} .grupal .pie{position:absolute;left:0;right:0;bottom:0;padding:calc(var(--w)*.05);color:#fff;font-size:calc(var(--w)*.034);letter-spacing:.14em;text-transform:uppercase;background:linear-gradient(transparent,rgba(0,0,0,.5))}
.fin{align-items:center;justify-content:center;text-align:center;padding:calc(var(--w)*.1);background:radial-gradient(circle at 50% 30%,#F8EEE8,var(--papel2))}
.fin h2{font-family:"Playfair Display",serif;font-style:italic;font-size:calc(var(--w)*.09);margin:0 0 calc(var(--w)*.04)} .fin p{font-size:calc(var(--w)*.045);margin:0}
.num{position:absolute;bottom:calc(var(--w)*.02);font-size:calc(var(--w)*.028);color:#8d6f66} .num.i{left:calc(var(--w)*.04)} .num.d{right:calc(var(--w)*.04)}
.lomo::after{content:"";position:absolute;top:0;bottom:0;width:7%;pointer-events:none}
.lomo.izq::after{right:0;background:linear-gradient(90deg,transparent,rgba(0,0,0,.16))} .lomo.der::after{left:0;background:linear-gradient(270deg,transparent,rgba(0,0,0,.16))}
#ui{position:fixed;left:0;right:0;bottom:max(8px,env(safe-area-inset-bottom));display:flex;justify-content:center;gap:14px;align-items:center;color:#e8d9d2;font-size:13px;z-index:20}
#ui button{background:rgba(255,255,255,.12);color:#fff;border:0;border-radius:99px;width:38px;height:38px;font-size:18px;cursor:pointer} #ui button:disabled{opacity:.25}
#volver{position:fixed;top:10px;left:12px;z-index:20;color:#e8d9d2;font-size:13px;text-decoration:none;background:rgba(0,0,0,.35);padding:5px 10px;border-radius:99px}
#msg{color:#e8d9d2;text-align:center;padding:30px}
</style></head><body>
<div id="mesa"><div id="libro"><div id="msg">Abriendo el catálogo…</div></div></div>
<div id="ui" style="display:none"><button id="ant" aria-label="Página anterior">‹</button><span id="pos"></span><button id="sig" aria-label="Página siguiente">›</button></div>
<a id="volver" href="%%VOLVER%%" style="display:none">◀ Editar</a>
<script>
const DATA = "%%DATA%%", IMG = "%%IMG%%", VOLVER = "%%VOLVER%%";
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const bg = iid => iid ? `style="background-image:url('${IMG}${iid}.jpg')"` : "";
let C = null, P = [], VISTAS = [], V = 0, DOBLE = false, GIRANDO = false;
function paginas(){
  const out = [`<div class="pg portada" ${bg(C.portada.img)}><div class="tx"><h1>${esc(C.portada.titulo || C.nombre)}</h1><p>${esc(C.portada.leyenda || "")}</p></div></div>`];
  C.productos.forEach(p => {
    const fs = (p.figus || []).filter(Boolean), cls = fs.length === 1 ? ["sola"] : fs.length === 2 ? ["a d1", "b d2"] : ["a", "b", "c"];
    const talles = String(p.talles || "").split(/[,·\/|;]+|\s{2,}/).map(x => x.trim()).filter(Boolean);
    out.push(`<div class="pg figus"><div class="art">${p.articulo ? "Art. " + esc(p.articulo) : ""}</div><h2>${esc(p.nombre || "")}</h2>
      <div class="pila">${fs.map((f, i) => `<div class="fig ${cls[i]}" ${bg(f)}></div>`).join("")}</div>
      <div class="datos">${talles.length ? `<div class="lbl">Talles</div><div class="chips">${talles.map(t => `<span class="chip">${esc(t)}</span>`).join("")}</div>` : ""}
        ${p.colores ? `<div class="linea"><b>Colores:</b> ${esc(p.colores)}</div>` : ""}${p.precio ? `<div class="precio">${esc(p.precio)}</div>` : ""}</div></div>`);
    out.push(`<div class="pg grupal" ${bg(p.grupal)}>${p.articulo || p.nombre ? `<div class="pie">${esc([p.articulo ? "Art. " + p.articulo : "", p.nombre].filter(Boolean).join(" · "))}</div>` : ""}</div>`);
  });
  out.push(`<div class="pg fin"><h2>${esc(C.portada.titulo || C.nombre)}</h2><p>${esc(C.contacto || "")}</p></div>`);
  return out;
}
function medir(){
  const vw = innerWidth, vh = innerHeight; DOBLE = vw / vh > 1.05;
  const h = Math.min(vh * (DOBLE ? .9 : 1), (DOBLE ? vw * .48 : vw) * 16 / 9), w = h * 9 / 16;
  document.documentElement.style.setProperty("--w", w + "px"); document.documentElement.style.setProperty("--h", h + "px");
  // las vistas: de a una página, o el libro abierto (portada sola a la derecha, y el final solo a la izquierda)
  const actual = VISTAS[V] ? (VISTAS[V][1] != null ? VISTAS[V][1] : VISTAS[V][0]) : 0;
  VISTAS = [];
  if(DOBLE){ VISTAS.push([null, 0]); for(let i = 1; i < P.length - 1; i += 2) VISTAS.push([i, i + 1 < P.length - 1 ? i + 1 : null]); VISTAS.push([P.length - 1, null]); }
  else P.forEach((_, i) => VISTAS.push([i]));
  V = Math.max(0, VISTAS.findIndex(v => v.includes(actual))); pintar();
}
const cara = (i, lado) => i == null ? `<div class="cara vacia"></div>` : `<div class="cara ${DOBLE ? "lomo " + lado : ""}">${P[i]}<span class="num ${lado === "izq" ? "i" : "d"}">${i ? i : ""}</span></div>`;
function pintar(){
  const v = VISTAS[V], L = document.getElementById("libro");
  L.innerHTML = DOBLE ? `<div id="cI">${cara(v[0], "izq")}</div><div id="cD">${cara(v[1], "der")}</div>` : `<div id="cD">${cara(v[0], "der")}</div>`;
  document.getElementById("pos").textContent = `${V + 1} / ${VISTAS.length}`;
  document.getElementById("ant").disabled = V === 0; document.getElementById("sig").disabled = V === VISTAS.length - 1;
}
function pasar(d){
  if(GIRANDO || !VISTAS[V + d]) return; GIRANDO = true;
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
mesa.addEventListener("pointerup", e => { if(x0 == null) return; const dx = e.clientX - x0; x0 = null;
  if(Math.abs(dx) > 40) pasar(dx < 0 ? 1 : -1); else pasar(e.clientX > innerWidth / 2 ? 1 : -1); });
addEventListener("resize", () => { if(C) medir(); });
(async () => { try{ const r = await fetch(DATA); const j = await r.json(); if(!r.ok) throw new Error(j.detail || "No se pudo abrir.");
    C = j.catalogo; C.portada = C.portada || {}; C.productos = C.productos || []; document.title = C.portada.titulo || C.nombre || "Catálogo";
    P = paginas(); document.getElementById("ui").style.display = "flex"; if(VOLVER) document.getElementById("volver").style.display = ""; medir();
    // las fotos de las páginas que siguen, de a poco, para que al pasar ya estén
    [C.portada.img].concat(...C.productos.map(p => [...(p.figus || []), p.grupal])).filter(Boolean).forEach((f, i) => setTimeout(() => { new Image().src = IMG + f + ".jpg"; }, 150 * i));
  }catch(e){ document.getElementById("msg").textContent = e.message; } })();
</script></body></html>"""
