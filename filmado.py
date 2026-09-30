"""
FILMADO DE CERO (prueba) — un video de ella filmado por el motor desde cero, NO una foto
que cobra vida.

Hasta ahora casi todo en la app partía de una FOTO quieta y la animaba (image-to-video):
el video arranca en una pose de catálogo, la cámara está clavada y se nota que "la foto
se mueve". Acá la cara, el cuerpo y la prenda van sólo como REFERENCIA (reference-to-video)
y el motor inventa el movimiento, la cámara y la luz, como si alguien se filmara con el
celular. Es una prueba: un clip de 5 s, para ver si se ve como una persona real antes de
pasar Reels a este sistema.

Motores (fal): Kling 3.0 Omni (lo que usa Comerciales, @Element1 = ella) y Seedance 2.0
reference-to-video (lo que usa Personajes "Movete vos", @Image1… = las referencias).
"""

from __future__ import annotations

import base64
import os
import time
import uuid as _uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from imagenes_ia import (
    CURRENT_SUB,
    _al_ingles,
    _compress_ref,
    _pfx,
    _strip_data_url,
    budget_record,
    kv,
    recorte_cara_avatar,
    set_current_sub,
)
from personajes import (
    PJ_DIR,
    _bind,
    _cobrar,
    _doc,
    _fal_enviar,
    _fal_esperar_y_bajar,
    _fal_key,
    _fal_subir,
    _guardar_en_drive,
    _job_nuevo,
    _job_set,
    _k_job,
    _refs_identidad,
    _revisar_job,
    _slug,
    _texto,
)
from reels import _cuerpo_en, _ref_cuerpo
from videos_luma import _spawn

ROUTE_PREFIX = os.environ.get("FILMADO_PREFIX", "/filmado").rstrip("/")
API = ROUTE_PREFIX + "/api"
VERSION = "1.1.0"   # subí este número cada vez que cambiamos el archivo

SEG = 5
DURACIONES = (5, 8, 10)
LUGARES = {
    "dormitorio": ("Su dormitorio (como el video de referencia)",
                   "her own bedroom at home: an unmade bed with rumpled sheets, a bedside lamp switched "
                   "on with warm light, a window with soft daylight and curtains, a dresser with makeup, "
                   "perfume and a standing mirror; an ordinary lived-in room, not a set"),
    "probador": ("El probador de un local",
                 "the fitting room of a small lingerie shop: a curtain, a full-length mirror, a hook with "
                 "hangers, warm shop light"),
}
MOTORES = {
    "kling_pro": {"label": "Kling 3.0 Omni · Pro", "tipo": "kling",
                  "modelo": os.getenv("FAL_KLING_PRO_MODEL", "fal-ai/kling-video/o3/pro/reference-to-video"),
                  "precio_seg": float(os.getenv("COMERCIALES_PRECIO_PRO", "0.112"))},
    "seedance2": {"label": "Seedance 2.0", "tipo": "seedance",
                  "modelo": os.getenv("FAL_SEEDANCE_REF_MODEL", "bytedance/seedance-2.0/reference-to-video"),
                  "precio_seg": float(os.getenv("PERSONAJES_PRECIO_SEEDANCE_REF", "0.30"))},
}
MOTOR_DEFAULT = "kling_pro"
ACCION_DEFAULT = ("se filma con el celular en la mano, se acerca a la cámara, le muestra el conjunto de "
                  "cerca, lo toca para que se vea la tela y se ríe")
DICE_DEFAULT = ("Chicas, me llegó el conjunto que les dije, miren este encaje, es divino y re cómodo. "
                "Escríbanme por DM que les paso los talles.")
DIR = PJ_DIR / "filmado"
DIR.mkdir(parents=True, exist_ok=True)

# Lo que hace que se vea FILMADO y no una foto animada.
_FILMADO = (
    "This is REAL phone footage, not an animated photo: the video starts in the MIDDLE of her "
    "movement (never from a still, posed frame), the phone camera is handheld with small natural "
    "shakes and tiny reframing, focus and exposure adjust slightly as she moves, real indoor "
    "light with soft shadows, a little motion blur on fast movements, subtle grain. She moves "
    "like a real person: weight shifts, breathing, blinking, hair moving, small hand gestures, "
    "unhurried, at real-time speed (never slow motion). Real skin with pores and natural "
    "shine, no smoothing, no plastic or waxy look, no doll face. Her body keeps its real "
    "proportions and weight in every frame. The garment stays exactly the same: same design, "
    "colour, lace and straps. No text, no logos, no watermark, no other people."
)
_NEGATIVO = ("animated photo, static camera, frozen pose, mannequin, plastic skin, waxy, doll, "
             "CGI, 3D render, slow motion, morphing face, extra fingers, deformed hands, text, watermark")


def _k_lista() -> str:
    return _pfx() + "filmado:lista"


def _mp4(jid: str) -> Path:
    return DIR / f"{jid}.mp4"


async def prompt_filmado(doc: Dict[str, Any], accion: str, motor: str, n_ref_ella: int,
                         n_prendas: int, dice: str = "", puesta: bool = False,
                         lugar: str = "dormitorio") -> str:
    accion_en = (await _al_ingles({"a": accion})).get("a") or accion
    cuerpo = await _cuerpo_en(doc)
    if MOTORES[motor]["tipo"] == "kling":
        ella, prenda = "@Element1", ("@Image1" if n_prendas == 1 else "@Image1 and @Image2")
    else:
        ella = "the woman of @Image1" + (f" (also @Image2{' and @Image3' if n_ref_ella > 2 else ''})"
                                          if n_ref_ella > 1 else "")
        p0 = n_ref_ella + 1
        prenda = f"@Image{p0}" + (f" and @Image{p0 + 1}" if n_prendas > 1 else "")
    if puesta:
        ropa = (f"wearing EXACTLY the lingerie set of {prenda} (same design, colour, lace, straps and "
                "trims)")
    else:
        # Como el video de referencia (UGC): ella vestida de entrecasa MOSTRANDO el producto.
        # Además es lo que menos rebota en los filtros.
        ropa = (f"wearing a casual fitted black t-shirt and jeans, and holding in her hands the lingerie "
                f"set of {prenda} (EXACTLY that design, colour, lace, straps and trims) to show it")
    voz = ""
    if dice:
        voz = (" She TALKS to the camera the whole time, in Argentine Spanish with a natural Rioplatense "
               "accent, casual and warm like a real influencer talking to her followers, and says: "
               f"\"{dice}\" Her lips, face and hands move in sync with what she says; natural pauses, "
               "a little laugh.")
    return (
        f"Vertical 9:16 UGC Instagram reel filmed by herself on a phone: {ella}, the same exact woman "
        f"(same face, hair and body), {ropa}, {accion_en}."
        + (f" Her body: {cuerpo}." if cuerpo else "")
        + f" PLACE: {LUGARES.get(lugar, LUGARES['dormitorio'])[1]}." + voz + " " + _FILMADO
    )


async def _procesar(jid: str, pid: str, accion: str, motor: str, prendas: List[str],
                    sub: Optional[str], dice: str = "", puesta: bool = False,
                    lugar: str = "dormitorio", seg: int = SEG) -> None:
    set_current_sub(sub)
    try:
        doc = await _doc(pid)
        refs = await _refs_identidad(doc)
        if not refs:
            raise RuntimeError("Este personaje todavía no tiene retrato aprobado.")
        retrato = refs[0][1]
        cara = await recorte_cara_avatar({"id": "pj:" + str(doc.get("id", "")), "ref_b64": retrato})
        cuerpo = _ref_cuerpo(refs)
        ella = [cara or retrato] + ([retrato] if cara else []) + ([cuerpo] if cuerpo else [])
        ella = ella[:3]
        key = await _fal_key()
        m = MOTORES[motor]
        headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
        await _job_set(jid, {"estado": "generando", "paso": "Subiendo sus fotos y la prenda a fal…"})
        async with httpx.AsyncClient(timeout=300) as cli:
            async def subir(b64: str, nombre: str) -> str:
                return await _fal_subir(cli, key, base64.b64decode(b64), "image/jpeg", nombre)
            u_ella = [await subir(b, f"{jid}-ella{i}.jpg") for i, b in enumerate(ella)]
            u_prendas = [await subir(b, f"{jid}-prenda{i}.jpg") for i, b in enumerate(prendas)]
            prompt = await prompt_filmado(doc, accion, motor, len(u_ella), len(u_prendas), dice, puesta, lugar)
            if m["tipo"] == "kling":
                payload: Dict[str, Any] = {
                    "prompt": prompt,
                    "elements": [{"frontal_image_url": u_ella[0], "reference_image_urls": u_ella[1:]}],
                    "image_urls": u_prendas, "duration": str(seg), "aspect_ratio": "9:16",
                    "generate_audio": bool(dice), "negative_prompt": _NEGATIVO, "cfg_scale": 0.5}
                opc = ("negative_prompt", "cfg_scale") + (() if dice else ("generate_audio",))
            else:
                payload = {"prompt": prompt, "image_urls": u_ella + u_prendas, "resolution": "720p",
                           "duration": str(seg), "aspect_ratio": "9:16", "generate_audio": bool(dice),
                           "enable_safety_checker": False}
                # Con voz, generate_audio NUNCA se saca: sin eso sale muda.
                opc = ("enable_safety_checker", "resolution") + (() if dice else ("generate_audio",))
            await _job_set(jid, {"paso": f"{m['label']} está filmando el video…", "prompt": prompt[:1500]})
            await _fal_enviar(cli, headers, m["modelo"], payload, jid, opc)
            job = await kv.get(_k_job(jid)) or {}
            await _fal_esperar_y_bajar(cli, headers, job["fal_status_url"], job["fal_result_url"],
                                       _mp4(jid), jid, inicio=job.get("fal_inicio"))
        costo = round(seg * m["precio_seg"], 3)
        await budget_record("filmado_prueba", motor, costo, 1, note=f"{doc.get('nombre', '')}: filmado de cero")
        link = await _guardar_en_drive(f"{_slug(doc.get('nombre', ''))}-filmado-{jid}.mp4",
                                       _mp4(jid).read_bytes(), "video/mp4")
        lista = (await kv.get(_k_lista())) or []
        lista.insert(0, {"id": jid, "pid": pid, "motor": motor, "accion": accion[:120],
                         "dice": dice[:120], "puesta": puesta, "lugar": lugar, "seg": seg,
                         "ts": time.strftime("%Y-%m-%d %H:%M")})
        await kv.set(_k_lista(), lista[:20])
        await _job_set(jid, {"estado": "listo", "paso": "", "drive": link})
    except Exception as e:
        await _job_set(jid, {"estado": "error", "error": str(getattr(e, "detail", "") or e)[:600]})


router = APIRouter(dependencies=[Depends(_bind)])


@router.get(ROUTE_PREFIX, response_class=HTMLResponse)
async def ui() -> HTMLResponse:
    return HTMLResponse(PAGINA.replace("%%API%%", API).replace("%%VERSION%%", VERSION))


@router.get(API + "/config")
async def api_config() -> Dict[str, Any]:
    return {"motores": {k: {"label": v["label"], "costo": round(SEG * v["precio_seg"], 2),
                            "precio_seg": v["precio_seg"]} for k, v in MOTORES.items()},
            "motor_default": MOTOR_DEFAULT, "seg": SEG, "duraciones": list(DURACIONES),
            "accion": ACCION_DEFAULT, "dice": DICE_DEFAULT,
            "lugares": {k: v[0] for k, v in LUGARES.items()}, "fal_key": bool(await _fal_key())}


@router.post(API + "/generar")
async def api_generar(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    doc = await _doc(str(payload.get("pid") or ""))
    if not (doc.get("hoja") or {}).get("retrato"):
        raise HTTPException(400, "Ese personaje todavía no tiene retrato aprobado.")
    if not await _fal_key():
        raise HTTPException(400, "Falta la API key de fal (FAL_KEY en Railway).")
    motor = payload.get("motor") if payload.get("motor") in MOTORES else MOTOR_DEFAULT
    prendas = []
    for a in (payload.get("prendas") or [])[:2]:
        try:
            prendas.append(_compress_ref(base64.b64decode(_strip_data_url(str(a))), max_dim=1536, q=92))
        except Exception:
            raise HTTPException(400, "No pude leer una foto de la prenda.")
    if not prendas:
        raise HTTPException(400, "Subí al menos una foto de la prenda.")
    accion = _texto(payload.get("accion"), 400) or ACCION_DEFAULT
    dice = _texto(payload.get("dice"), 400)
    puesta = bool(payload.get("puesta"))
    lugar = payload.get("lugar") if payload.get("lugar") in LUGARES else "dormitorio"
    try:
        seg = int(payload.get("seg") or SEG)
    except (TypeError, ValueError):
        seg = SEG
    seg = seg if seg in DURACIONES else SEG
    costo = round(seg * MOTORES[motor]["precio_seg"], 2)
    await _cobrar(costo)
    jid = _uuid.uuid4().hex[:10]
    await _job_nuevo(jid, doc["id"], "filmado", 240, {"costo": costo, "titulo": "Filmado de cero (prueba)"})
    _spawn(_procesar(jid, doc["id"], accion, motor, prendas, CURRENT_SUB.get(), dice, puesta, lugar, seg))
    return {"job": jid, "costo": costo}


@router.get(API + "/job/{jid}")
async def api_job(jid: str) -> Dict[str, Any]:
    j = await kv.get(_k_job(jid))
    if not isinstance(j, dict):
        raise HTTPException(404, "Ese trabajo no existe.")
    j = await _revisar_job(j)
    return {k: j.get(k) for k in ("id", "estado", "paso", "error", "costo", "drive")}


@router.get(API + "/lista")
async def api_lista() -> Dict[str, Any]:
    return {"videos": [x for x in ((await kv.get(_k_lista())) or []) if _mp4(x["id"]).exists()]}


@router.get(API + "/mp4/{jid}")
async def api_mp4(jid: str):
    if not any(x["id"] == jid for x in (await kv.get(_k_lista())) or []) or not _mp4(jid).exists():
        raise HTTPException(404, "Ese video no existe.")
    return FileResponse(str(_mp4(jid)), media_type="video/mp4", filename=f"filmado-{jid}.mp4")


PAGINA = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Filmado de cero · Studio Luma</title>
<link href="https://fonts.googleapis.com/css2?family=Bodoni+Moda:wght@500;600&family=Jost:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root{--ink:#ecebf1;--ink-soft:#96919f;--line:#2c2a34;--ivory:#131218;--card:#1b1a21;--card-2:#232128;--rose:#c9a86b;--rose-deep:#d8b878;--ok:#5fae86;--bad:#e0736f}
  *{box-sizing:border-box} body{margin:0;background:var(--ivory);color:var(--ink);font-family:Jost,system-ui,sans-serif;font-size:16px;line-height:1.55}
  main{max-width:1080px;margin:0 auto;padding:16px} .card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:18px;margin-bottom:16px}
  h2{font-family:'Bodoni Moda',serif;font-weight:600;font-size:22px;margin:0 0 6px} .hint{color:var(--ink-soft);font-size:13px;margin:4px 0 8px}
  label{display:block;font-size:13px;color:var(--ink-soft);margin:10px 0 4px}
  input,select,textarea{width:100%;background:var(--card-2);color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:10px 12px;font:inherit;font-size:15px}
  button{background:var(--card-2);color:var(--ink);border:1px solid var(--line);border-radius:999px;padding:9px 16px;font:inherit;font-size:14px;cursor:pointer}
  button.go{background:linear-gradient(150deg,var(--rose-deep),var(--rose));color:#17140d;border:none;font-weight:500} button:disabled{opacity:.5}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:10px} @media(max-width:640px){.row{grid-template-columns:1fr}}
  video{width:100%;max-width:360px;border-radius:14px;background:#000} .thumbs img{width:64px;height:64px;object-fit:cover;border-radius:8px;margin:6px 6px 0 0}
  .vids{display:flex;gap:12px;flex-wrap:wrap} .vids div{font-size:12.5px;color:var(--ink-soft)}
  .spin{display:inline-block;width:12px;height:12px;border:2px solid var(--ink-soft);border-top-color:var(--rose);border-radius:50%;animation:g 1s linear infinite;vertical-align:-1px;margin-right:6px}@keyframes g{to{transform:rotate(360deg)}}
</style></head><body><main>
<div class="card">
  <h2>Filmado de cero (prueba)</h2>
  <p class="hint">Un video de ella <b>filmado por el motor desde cero</b>, no una foto que cobra vida: su cara, su cuerpo y la prenda van sólo como referencia, y el motor inventa el movimiento, una cámara de celular en mano y, si escribís qué dice, <b>su voz en el mismo video</b> (como los videos UGC de Instagram). Un clip para ver si se ve como una persona real.</p>
  <div class="row"><div><label>Modelo (personaje)</label><select id="pid"></select></div>
  <div><label>Motor</label><select id="motor"></select></div></div>
  <label>La prenda (1 o 2 fotos del producto)</label><input type="file" id="prendas" accept="image/*" multiple><div class="thumbs" id="thumbs"></div>
  <div class="row"><div><label>Dónde</label><select id="lugar"></select></div>
  <div><label>La prenda</label><select id="puesta"><option value="no">La muestra en la mano (vestida de entrecasa)</option><option value="si">La tiene puesta</option></select></div></div>
  <label>Qué hace (en castellano)</label><textarea id="accion" rows="2"></textarea>
  <label>Qué dice (en castellano; la voz sale en el mismo video. Vacío = sin voz)</label><textarea id="dice" rows="2"></textarea>
  <div class="row"><div><label>Duración</label><select id="seg"></select></div><div></div></div>
  <p class="hint" id="costo"></p>
  <button class="go" id="generar">🎥 Filmar la prueba</button>
  <p class="hint" id="estado"></p><div id="salida"></div>
</div>
<div class="card"><h2>Pruebas anteriores</h2><div class="vids" id="lista"></div></div>
</main>
<script>
const API = "%%API%%"; let CFG = {}, PRENDAS = [];
const $ = s => document.querySelector(s);
const esc = t => String(t == null ? "" : t).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
async function api(p, o){ const r = await fetch(API + p, Object.assign({headers: {"Content-Type": "application/json"}}, o || {})); const d = await r.json().catch(() => ({})); if(!r.ok) throw new Error(d.detail || ("HTTP " + r.status)); return d; }
const leer = f => new Promise((ok, mal) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = mal; r.readAsDataURL(f); });
function costo(){ const m = CFG.motores[$("#motor").value]; const s = +$("#seg").value || CFG.seg;
  $("#costo").textContent = m ? `Cuesta ~US$${(m.precio_seg * s).toFixed(2)} (un clip de ${s} s)` + ($("#dice").value.trim() ? ". Con voz puede salir algo más, según fal." : ".") : ""; }
async function lista(){ const l = (await api("/lista")).videos; $("#lista").innerHTML = l.length ? l.map(v => `<div><video src="${API}/mp4/${v.id}" controls playsinline preload="metadata" style="max-width:200px"></video><br>${esc((CFG.motores[v.motor] || {}).label || v.motor)} · ${esc(v.ts)}</div>`).join("") : '<span class="hint">Todavía no hiciste ninguna.</span>'; }
$("#prendas").onchange = async e => { PRENDAS = await Promise.all(Array.from(e.target.files).slice(0, 2).map(leer)); $("#thumbs").innerHTML = PRENDAS.map(s => `<img src="${s}">`).join(""); };
$("#motor").onchange = costo; $("#seg").onchange = costo; $("#dice").oninput = costo;
$("#generar").onclick = async () => { const b = $("#generar"); b.disabled = true; $("#salida").innerHTML = "";
  try{ const r = await api("/generar", {method: "POST", body: JSON.stringify({pid: $("#pid").value, motor: $("#motor").value, accion: $("#accion").value, dice: $("#dice").value, puesta: $("#puesta").value === "si", lugar: $("#lugar").value, seg: +$("#seg").value, prendas: PRENDAS})});
    for(;;){ const j = await api("/job/" + r.job);
      if(j.estado === "listo"){ $("#estado").textContent = ""; $("#salida").innerHTML = `<video src="${API}/mp4/${r.job}" controls playsinline autoplay></video>`; lista(); break; }
      if(j.estado === "error") throw new Error(j.error || "Falló");
      $("#estado").innerHTML = `<span class="spin"></span>${esc(j.paso || "En cola…")}`; await new Promise(res => setTimeout(res, 5000)); } }
  catch(e){ $("#estado").textContent = "Falló: " + e.message; } b.disabled = false; };
(async () => {
  CFG = await api("/config");
  $("#motor").innerHTML = Object.entries(CFG.motores).map(([k, v]) => `<option value="${k}" ${k === CFG.motor_default ? "selected" : ""}>${esc(v.label)} · ~US$${v.costo}</option>`).join("");
  $("#lugar").innerHTML = Object.entries(CFG.lugares).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  $("#seg").innerHTML = CFG.duraciones.map(s => `<option value="${s}" ${s === 8 ? "selected" : ""}>${s} s</option>`).join("");
  $("#accion").value = CFG.accion; $("#dice").value = CFG.dice; costo();
  try{ const pj = await (await fetch("/personajes/api/lista")).json(); $("#pid").innerHTML = (pj.personajes || []).map(p => `<option value="${esc(p.id)}">${esc(p.nombre)}</option>`).join(""); }catch(e){}
  if(new URLSearchParams(location.search).get("embed")){ const avisar = () => parent.postMessage({cambiosAlto: document.documentElement.scrollHeight, de: "filmado"}, "*"); new ResizeObserver(avisar).observe(document.body); }
  lista();
})();
</script></body></html>
"""
