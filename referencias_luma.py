# -*- coding: utf-8 -*-
"""
referencias_luma.py — El KIT DE REFERENCIAS de Studio Luma: "Mis lugares"
=========================================================================

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
    _pfx,
    budget_check,
    budget_record,
    gemini_generate,
    get_settings,
    kv,
    session_sub_from_request,
    set_current_sub,
)

VERSION = "1.0.0"
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


@router.get(ROUTE_PREFIX, response_class=HTMLResponse)
async def ui() -> HTMLResponse:
    return HTMLResponse(PAGINA.replace("%%API%%", API).replace("%%VERSION%%", VERSION))


PAGINA = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mis lugares · Studio Luma</title>
<link href="https://fonts.googleapis.com/css2?family=Bodoni+Moda:wght@500;600&family=Jost:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root{--ink:#ecebf1;--ink-soft:#96919f;--line:#2c2a34;--ivory:#131218;--card:#1b1a21;--card-2:#232128;--rose:#c9a86b;--rose-deep:#d8b878;--ok:#5fae86;--bad:#e0736f}
  *{box-sizing:border-box} body{margin:0;background:var(--ivory);color:var(--ink);font-family:Jost,system-ui,sans-serif;font-size:16px;line-height:1.55}
  main{max-width:1080px;margin:0 auto;padding:16px} .card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:18px;margin-bottom:16px}
  h2{font-family:'Bodoni Moda',serif;font-weight:600;font-size:22px;margin:0 0 6px} h3{font-size:16px;margin:0 0 4px;font-weight:500}
  .hint{color:var(--ink-soft);font-size:13px;margin:4px 0 8px} label{display:block;font-size:13px;color:var(--ink-soft);margin:10px 0 4px}
  input,textarea{width:100%;background:var(--card-2);color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:10px 12px;font:inherit;font-size:15px}
  button{background:var(--card-2);color:var(--ink);border:1px solid var(--line);border-radius:999px;padding:9px 16px;font:inherit;font-size:14px;cursor:pointer}
  button.go{background:linear-gradient(150deg,var(--rose-deep),var(--rose));color:#17140d;border:none;font-weight:500} button:disabled{opacity:.5}
  .vistas{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:10px} @media(max-width:640px){.vistas{grid-template-columns:repeat(2,1fr)}}
  .v{border:1px dashed var(--line);border-radius:12px;padding:8px;text-align:center;font-size:12px;color:var(--ink-soft);background:var(--card-2)}
  .v img{width:100%;aspect-ratio:9/16;object-fit:cover;border-radius:8px;display:block;margin-bottom:6px}
  .v .vacio{width:100%;aspect-ratio:9/16;border-radius:8px;display:flex;align-items:center;justify-content:center;background:var(--card);margin-bottom:6px;font-size:26px}
  .fila{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px} a{color:var(--rose-deep)}
  .mal{color:var(--bad)}
</style></head><body><main>
<div class="card">
  <h2>Mis lugares <small style="font-family:Jost;font-size:12px;color:var(--ink-soft)">v%%VERSION%%</small></h2>
  <p class="hint">Los lugares de tus reels, siempre iguales: tu local, tu depósito, el probador, tu casa. Cada lugar tiene hasta 4 vistas
  del <b>mismo lugar, vacío (sin gente)</b>: plano abierto, plano medio, un rincón y el espejo. Los reels los usan como referencia,
  así todas las tomas pasan en el mismo lugar, con la misma luz.</p>
  <p class="hint">👉 Lo más realista: <b>fotos reales</b> con el celular, de pie, a la altura de los ojos, con buena luz y sin gente.
  Si no tenés, describí el lugar y la IA lo genera (unos US$0,04 por vista).</p>
  <p class="hint"><a href="/filmado">← Volver a Filmado de cero</a> · <a href="/reels">Reels</a></p>
</div>
<div class="card">
  <h3>Nuevo lugar</h3>
  <label>Nombre</label><input id="nombre" maxlength="60" placeholder="Mi local, El probador, Depósito…">
  <label>Cómo es (opcional si subís fotos; obligatorio para generarlo)</label>
  <textarea id="desc" rows="3" placeholder="Ej.: un local de lencería chico y cálido, paredes blanco tiza, exhibidores de madera clara con corpiños colgados, un probador con cortina de lino beige y un espejo grande, luz cálida"></textarea>
  <div class="fila"><button class="go" id="crear">＋ Crear lugar</button><span class="hint" id="est"></span></div>
</div>
<div id="lista"></div>
<script>
const API = "%%API%%"; let VISTAS = {}, COSTO = 0.04;
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function api(p, o = {}) { const r = await fetch(API + p, o); let d = {}; try { d = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error(d.detail || ("HTTP " + r.status)); return d; }
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
      try { const d = await api(`/lugares/${x.id}/generar`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({vistas: Object.keys(VISTAS).filter(k => k !== "espejo")})});
        est.textContent = d.hechas.length ? `Listo: ${d.hechas.length} vista(s).` : "No faltaba ninguna."; cargar(); }
      catch (e) { est.innerHTML = `<span class="mal">${esc(e.message)}</span>`; } ev.target.disabled = false; };
    c.querySelector("[data-borrar]").onclick = async () => { if (!confirm(`¿Borrar "${x.nombre}"?`)) return; await api(`/lugares/${x.id}`, {method: "DELETE"}); cargar(); };
    $("#lista").appendChild(c);
  });
}
async function cargar() { try { const d = await api("/lugares"); VISTAS = d.vistas; COSTO = d.costo_vista; pintar(d.lugares); } catch (e) { $("#lista").innerHTML = `<div class="card mal">${esc(e.message)}</div>`; } }
$("#crear").onclick = async () => {
  try { await api("/lugares", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({nombre: $("#nombre").value, desc: $("#desc").value})});
    $("#nombre").value = ""; $("#desc").value = ""; $("#est").textContent = "Creado ✓ — ahora subí sus fotos o generalas abajo."; cargar(); }
  catch (e) { $("#est").innerHTML = `<span class="mal">${esc(e.message)}</span>`; } };
cargar();
</script></main></body></html>"""
