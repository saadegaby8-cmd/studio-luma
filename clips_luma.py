# -*- coding: utf-8 -*-
"""
clips_luma.py — 🎞 VIDEO POR FOTOGRAMAS (en /clips)
===================================================

Un video largo armado con imágenes en los momentos que ella elige: el PRINCIPIO (empieza así), una o
varias en el MEDIO (en el segundo 12 tiene que pasar esto) y el FINAL (termina así), y entre una y otra
lo que pasa en ese tramo ("camina hacia la orilla y se da vuelta"). Se filma tramo por tramo con el
motor común (video_motor.py: fal, Higgsfield o Kling directo, con el modelo elegido) y se une.
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import time
import uuid as _uuid
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

import higgsfield_luma as hf
import video_motor as V
from imagenes_ia import _pfx, budget_record, kv
from personajes import PJ_DIR, _bind, _fal_key
from videos_luma import _spawn

VERSION = "1.0.0"
PREF = os.environ.get("CLIPS_PREFIX", "/clips").rstrip("/")
API = PREF + "/api"
DIR = PJ_DIR / "clips"
DIR.mkdir(parents=True, exist_ok=True)
PARALELO = 3
_CORRIENDO: set = set()     # los trabajos que corren en ESTE proceso (tras un reinicio, queda vacío)
MAX_CLAVES = 11


async def _hf_clave() -> None:
    await hf.cargar_de_ajustes()


router = APIRouter(dependencies=[Depends(_bind), Depends(_hf_clave)])


def _k(jid: str) -> str:
    return _pfx() + "clipjob:" + jid


def _dir(jid: str) -> Path:
    d = DIR / jid
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _disponibles() -> Dict[str, bool]:
    return {"fal": bool(await _fal_key()), "higgsfield": hf.disponible(), "kling": bool(V.kling_auth())}


def _jpg(data_url: str, aspect: str) -> str:
    """La imagen recortada al formato del video (al centro) y en JPEG."""
    from PIL import Image, ImageOps
    raw = base64.b64decode(data_url.split(",", 1)[1] if data_url.startswith("data:") else data_url)
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    a, b = {"9:16": (9, 16), "16:9": (16, 9), "1:1": (1, 1), "4:5": (4, 5)}.get(aspect, (9, 16))
    w, h = im.size
    if w / h > a / b:
        nw = int(h * a / b)
        im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:
        nh = int(w * b / a)
        im = im.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    im.thumbnail((1536, 1536))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode()


@router.get(API + "/catalogo")
async def api_catalogo() -> Dict[str, Any]:
    return V.catalogo(await _disponibles())


@router.post(API + "/crear")
async def api_crear(p: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    modelo, prov = str(p.get("modelo") or ""), str(p.get("proveedor") or "")
    cal = "pro" if p.get("calidad") == "pro" else "std"
    aspect = str(p.get("formato") or "9:16")
    audio = bool(p.get("audio"))
    m = V.MODELOS.get(modelo)
    if not m or m.get("tipo") != "i2v" or not V.rutas(modelo, prov, cal):
        raise HTTPException(400, "Ese modelo no se puede pedir por ese proveedor.")
    if not (await _disponibles()).get(prov):
        raise HTTPException(400, f"Falta la clave de {V.PROVEEDORES.get(prov, prov)}.")
    claves = sorted([c for c in (p.get("claves") or []) if c.get("img")], key=lambda c: float(c.get("t") or 0))
    if len(claves) < 2:
        raise HTTPException(400, "Poné al menos dos imágenes: la del principio y la del final.")
    if len(claves) > MAX_CLAVES:
        raise HTTPException(400, f"Como mucho {MAX_CLAVES} imágenes.")
    tramos_txt = list(p.get("tramos") or [])
    lo, hi = m["seg"]
    tramos = []
    for k in range(len(claves) - 1):
        seg = float(claves[k + 1]["t"]) - float(claves[k]["t"])
        if seg < lo - 0.01 or seg > hi + 0.01:
            raise HTTPException(400, f"El tramo {k + 1} dura {seg:g} s y {m['nombre']} filma de {lo} a {hi} s por tramo: "
                                     "mové las imágenes o agregá una en el medio.")
        tramos.append({"seg": seg, "texto": str(tramos_txt[k] if k < len(tramos_txt) else "").strip()[:1200]})
    jid = _uuid.uuid4().hex[:12]
    d = _dir(jid)
    for k, c in enumerate(claves):
        (d / f"clave{k}.b64").write_text(await asyncio.to_thread(_jpg, str(c["img"]), aspect))
    costo = V.costo_video(modelo, prov, cal, audio, [t["seg"] for t in tramos])
    job = {"id": jid, "estado": "trabajando", "paso": "Arrancando…", "modelo": modelo, "proveedor": prov,
           "calidad": cal, "formato": aspect, "audio": audio, "general": str(p.get("general") or "").strip()[:1500],
           "tramos": tramos, "hechos": 0, "costo_estimado": costo, "costo": 0.0, "error": "", "inicio": time.time()}
    await kv.set(_k(jid), job, ttl=7 * 86400)
    _spawn(_trabajar(jid, _pfx()))
    return {"id": jid, "costo_estimado": costo}


def _prompt(general: str, texto: str, k: int, n: int) -> str:
    base = "A real, continuous fashion video shot. " + (general + ". " if general else "")
    return (base + (texto.rstrip(". ") + ". " if texto else "She moves naturally from the first frame to the last frame. ")
            + "The shot STARTS exactly on the given first frame and ENDS exactly on the given last frame: same "
              "person, same garment, same place and light; the movement in between is how she gets from one to the other. "
              "Natural, physically real motion, no morphing, no cuts.")


async def _set(jid: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    job = await kv.get(_k(jid)) or {"id": jid}
    job.update(patch)
    await kv.set(_k(jid), job, ttl=7 * 86400)
    return job


async def _trabajar(jid: str, pfx: str) -> None:
    _CORRIENDO.add(jid)
    job = await kv.get(_k(jid)) or {}
    d = _dir(jid)
    try:
        await hf.cargar_de_ajustes()
        key_fal = await _fal_key()
        tramos = job["tramos"]
        n = len(tramos)
        # Los modelos de video entienden mejor en inglés: lo que ella escribió se traduce (si falla, va tal cual)
        try:
            from imagenes_ia import _al_ingles
            tr = await _al_ingles({**{f"t{k}": t["texto"] for k, t in enumerate(tramos) if t["texto"]},
                                   **({"g": job["general"]} if job.get("general") else {})})
            for k, t in enumerate(tramos):
                t["texto"] = tr.get(f"t{k}") or t["texto"]
            job["general"] = tr.get("g") or job.get("general", "")
        except Exception:
            pass
        sem = asyncio.Semaphore(PARALELO)
        hechos = {"n": 0, "costo": float(job.get("costo") or 0.0)}

        async def uno(k: int) -> Path:
            async with sem:
                destino = d / f"tramo{k}.mp4"
                if destino.exists() and destino.stat().st_size > 1000:      # ya filmado antes de un reinicio
                    hechos["n"] += 1
                    return destino
                ini = (d / f"clave{k}.b64").read_text()
                fin = (d / f"clave{k + 1}.b64").read_text()
                prompt = _prompt(job.get("general", ""), tramos[k]["texto"], k, n)
                seg = V.seg_valido(job["modelo"], tramos[k]["seg"])
                r = await V.con_recuperacion(
                    V.clave_pendiente("clips", jid, k, job["modelo"], job["proveedor"], job["calidad"], prompt, seg), destino,
                    round(seg * V.precio_seg(job["modelo"], job["proveedor"], job["calidad"], job["audio"]), 3),
                    lambda: V.tramo(job["proveedor"], job["modelo"], job["calidad"], ini, fin, prompt, tramos[k]["seg"],
                                    job["audio"], job["formato"], destino, key_fal=key_fal), key_fal=key_fal)
                hechos["n"] += 1
                hechos["costo"] += r["costo"]
                await budget_record("clips_video", job["modelo"], r["costo"], 1,
                                    note=f"video por fotogramas: tramo {k + 1} de {n} ({V.PROVEEDORES[job['proveedor']]})")
                await _set(jid, {"hechos": hechos["n"], "costo": round(hechos["costo"], 3),
                                 "paso": f"Filmados {hechos['n']} de {n} tramos…"})
                return destino
        await _set(jid, {"paso": f"Filmando {n} tramo{'s' if n > 1 else ''} con {V.MODELOS[job['modelo']]['nombre']} "
                                 f"por {V.PROVEEDORES[job['proveedor']]}…"})
        clips = await asyncio.gather(*[uno(k) for k in range(n)])
        await _set(jid, {"paso": "Uniendo los tramos…"})
        salida = d / "video.mp4"
        await asyncio.to_thread(V.unir, list(clips), [t["seg"] for t in tramos], salida, job["formato"])
        await _set(jid, {"estado": "listo", "paso": "Listo ✓", "video": f"{API}/video/{jid}"})
    except Exception as e:
        await _set(jid, {"estado": "error", "error": str(e)[:600], "paso": ""})
    finally:
        _CORRIENDO.discard(jid)


@router.get(API + "/job/{jid}")
async def api_job(jid: str) -> Dict[str, Any]:
    job = await kv.get(_k(jid))
    if not job:
        raise HTTPException(404, "No encontré ese video.")
    if job.get("estado") == "trabajando" and jid not in _CORRIENDO:
        # Un reinicio del servidor (un deploy) cortó el trabajo: se retoma. Los tramos ya filmados no se vuelven
        # a pagar y los que ya estaban pedidos se recuperan del proveedor.
        job = await _set(jid, {"paso": "Retomando después de un reinicio del servidor…"})
        _spawn(_trabajar(jid, _pfx()))
    return job


@router.get(API + "/video/{jid}")
async def api_video(jid: str) -> FileResponse:
    if not await kv.get(_k(jid)):
        raise HTTPException(404, "No encontré ese video.")
    p = _dir(jid) / "video.mp4"
    if not p.exists():
        raise HTTPException(404, "Todavía no está.")
    return FileResponse(p, media_type="video/mp4", filename=f"video_{jid}.mp4")


@router.get(API + "/motor.js")
async def api_motor_js() -> HTMLResponse:
    """El selector de proveedor y modelo, para usar en cualquier pantalla de video."""
    return HTMLResponse(SELECTOR_JS.replace("%%API%%", API), media_type="text/javascript")


# El SELECTOR (proveedor · modelo · calidad · sonido) con el costo: se arma en un <div> de cualquier pantalla.
SELECTOR_JS = r"""
window.MotorVideo = (function(){
  let CAT = null;
  async function catalogo(){ if(!CAT){ const r = await fetch("%%API%%/catalogo"); CAT = await r.json(); } return CAT; }
  function precio(m, prov, cal, audio){ const p = (m.proveedores||[]).find(x=>x.id===prov); if(!p) return 0;
    return (audio ? p.precio_audio : p.precio)[cal] || 0; }
  async function armar(div, cambio, opc){
    const cat0 = await catalogo(); const tipo = (opc && opc.tipo) || "i2v";
    const cat = {...cat0, modelos: cat0.modelos.filter(m => (m.tipo || "i2v") === tipo)};
    div._cat = cat;
    let guard = {}; try{ guard = JSON.parse(localStorage.getItem("luma_motor_video")||"{}"); }catch(e){}
    div.innerHTML = '<div class="mv-fila" style="display:flex;flex-wrap:wrap;gap:6px">'
      + '<select class="mv-modelo"></select><select class="mv-prov"></select>'
      + '<select class="mv-cal"><option value="std">720p</option><option value="pro">1080p</option></select>'
      + '<label class="mv-aud" style="display:flex;align-items:center;gap:4px"><input type="checkbox" class="mv-audio"> con sonido</label></div>';
    const $ = s => div.querySelector(s);
    $(".mv-modelo").innerHTML = cat.modelos.map(m=>'<option value="'+m.id+'">'+m.nombre+'</option>').join("");
    $(".mv-modelo").value = cat.modelos.some(m=>m.id===guard.modelo) ? guard.modelo
                          : (cat.modelos.some(m=>m.id===cat.defecto.modelo) ? cat.defecto.modelo : (cat.modelos[0]||{}).id);
    const provs = () => { const m = cat.modelos.find(x=>x.id===$(".mv-modelo").value);
      $(".mv-prov").innerHTML = m.proveedores.map(p=>'<option value="'+p.id+'">'+p.nombre+(p.conectado?"":" (sin clave)")+'</option>').join("");
      const pref = guard.proveedor && m.proveedores.some(p=>p.id===guard.proveedor) ? guard.proveedor
                 : (m.proveedores.find(p=>p.conectado)||m.proveedores[0]||{}).id;
      $(".mv-prov").value = pref || "";
      $(".mv-aud").style.display = m.audio ? "" : "none"; };
    provs();
    $(".mv-cal").value = guard.calidad || "std";
    $(".mv-audio").checked = !!guard.audio;
    const avisar = () => { const v = valor(div);
      try{ localStorage.setItem("luma_motor_video", JSON.stringify({...guard, ...v})); }catch(e){}
      cambio && cambio(v); };
    $(".mv-modelo").onchange = () => { provs(); avisar(); };
    [".mv-prov", ".mv-cal", ".mv-audio"].forEach(s => $(s).onchange = avisar);
    avisar();
  }
  function valor(div){ const $ = s => div.querySelector(s); const cat = div._cat || CAT;
    const m = cat.modelos.find(x=>x.id===$(".mv-modelo").value) || {};
    const v = {modelo:$(".mv-modelo").value, proveedor:$(".mv-prov").value, calidad:$(".mv-cal").value,
               audio:m.audio && $(".mv-audio").checked};
    v.precio_seg = precio(m, v.proveedor, v.calidad, v.audio); v.seg = m.seg; v.nombre = m.nombre; v.fin = m.fin;
    v.conectado = ((m.proveedores||[]).find(p=>p.id===v.proveedor)||{}).conectado;
    return v; }
  return {armar, valor, catalogo};
})();
"""


PAGINA = r"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Video por fotogramas · Studio Luma</title>
<style>
:root{--bg:#141414;--card:#1e1e1e;--line:#333;--txt:#eee;--mut:#9a9a9a;--acc:#d8b878;--ok:#6fcf8f;--bad:#ef6b6b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.45 system-ui,sans-serif}
.wrap{max-width:980px;margin:0 auto;padding:16px}a{color:var(--acc)}
h1{font-size:22px;margin:6px 0 4px}.hint{color:var(--mut);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin:12px 0}
select,input,textarea,button{font:inherit;color:var(--txt);background:#262626;border:1px solid var(--line);border-radius:8px;padding:7px 9px}
textarea{width:100%;min-height:56px}button{cursor:pointer}button.go{background:var(--acc);color:#111;border:0;font-weight:700;padding:10px 16px}
.clave{display:flex;gap:10px;align-items:center;border:1px solid var(--line);border-radius:10px;padding:8px;background:#1a1a1a}
.clave img{height:110px;border-radius:6px}.tramo{margin:6px 0 6px 40px;border-left:3px dashed var(--line);padding:4px 0 4px 12px}
.costo{font-weight:700;color:var(--acc)}.err{color:var(--bad)}
</style></head><body><div class="wrap">
<a href="/">← Studio Luma</a>
<h1>🎞 Video por fotogramas</h1>
<p class="hint">Poné la imagen del <b>principio</b>, las del <b>medio</b> que quieras (en el segundo en que tienen que pasar) y la del <b>final</b>. Entre una y otra escribí qué pasa en ese tramo: cómo llega de una a la otra. Se filma tramo por tramo, cada uno arranca en una imagen y termina en la siguiente, y se une en un solo video.</p>
<div class="card"><b>Motor</b><div id="motor" style="margin-top:6px"></div>
  <div style="display:flex;gap:8px;align-items:center;margin-top:8px"><label>Formato</label>
  <select id="formato"><option value="9:16">9:16 (reel)</option><option value="4:5">4:5</option><option value="1:1">1:1</option><option value="16:9">16:9</option></select></div>
  <div class="hint" id="limites" style="margin-top:6px"></div></div>
<div class="card"><b>Todo el video</b> <span class="hint">(opcional: el lugar, la prenda, el estilo, la cámara)</span>
  <textarea id="general" placeholder="Playa al atardecer, ella con la bikini turquesa, cámara en mano, look de película"></textarea></div>
<div class="card"><b>Línea de tiempo</b>
  <div id="linea" style="margin-top:8px"></div>
  <label class="go" style="display:inline-block;margin-top:10px;background:#262626;color:var(--txt);border:1px dashed var(--line);border-radius:8px;padding:8px 12px;cursor:pointer">➕ Agregar imagen<input type="file" id="subir" accept="image/*" multiple style="display:none"></label>
  <div class="hint" style="margin-top:6px">Las imágenes se ordenan por su segundo. La primera va en 0.</div></div>
<div class="card"><div>Duración: <b id="dur">0 s</b> · Costo estimado: <span class="costo" id="costo">—</span></div>
  <div class="err" id="aviso"></div>
  <button class="go" id="crear" style="margin-top:10px">🎬 Crear video</button>
  <div id="estado" style="margin-top:10px"></div></div>
</div>
<script src="%%API%%/motor.js"></script>
<script>
const API="%%API%%";let CLAVES=[],TRAMOS=[],MV=null;
const $=s=>document.querySelector(s);
const esc=t=>String(t||"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function leer(f){return new Promise(ok=>{const r=new FileReader();r.onload=()=>ok(r.result);r.readAsDataURL(f);});}
function pintar(){
  const box=$("#linea");let h="";
  CLAVES.forEach((c,i)=>{
    const rol=i===0?"PRINCIPIO":(i===CLAVES.length-1?"FINAL":"MEDIO");
    h+='<div class="clave"><img src="'+c.img+'"><div style="flex:1"><b>'+rol+'</b><div style="margin-top:6px">en el segundo <input type="number" min="0" step="0.5" value="'+c.t+'" data-i="'+i+'" class="seg" style="width:80px"'+(i===0?' disabled':'')+'></div></div><button data-i="'+i+'" class="del">🗑</button></div>';
    if(i<CLAVES.length-1){const lg=(CLAVES[i+1].t-c.t);
      h+='<div class="tramo"><div class="hint">Tramo '+(i+1)+' · '+lg.toFixed(1)+' s — ¿qué pasa? ¿cómo llega a la imagen siguiente?</div><textarea data-i="'+i+'" class="tr" placeholder="camina hacia la orilla, se da vuelta y mira a cámara">'+esc(TRAMOS[i]||"")+'</textarea></div>';}
  });
  box.innerHTML=h||'<p class="hint">Todavía no hay imágenes.</p>';
  box.querySelectorAll(".seg").forEach(x=>x.onchange=()=>{CLAVES[+x.dataset.i].t=Math.max(0,+x.value||0);CLAVES.sort((a,b)=>a.t-b.t);pintar();});
  box.querySelectorAll(".tr").forEach(x=>x.oninput=()=>{TRAMOS[+x.dataset.i]=x.value;});
  box.querySelectorAll(".del").forEach(x=>x.onclick=()=>{CLAVES.splice(+x.dataset.i,1);TRAMOS.splice(+x.dataset.i,1);if(CLAVES[0])CLAVES[0].t=0;pintar();});
  calcular();
}
function calcular(){
  if(!MV)return;const v=MotorVideo.valor($("#motor"));
  const dur=CLAVES.length?CLAVES[CLAVES.length-1].t:0;$("#dur").textContent=dur.toFixed(1)+" s";
  $("#limites").textContent=v.nombre+": cada tramo de "+v.seg[0]+" a "+v.seg[1]+" s · US$ "+v.precio_seg.toFixed(3)+" por segundo"+(v.fin?"":" · OJO: este modelo no recibe la imagen final, el tramo puede no terminar igual")+(v.conectado?"":" · ⚠ falta la clave de este proveedor");
  let costo=0,aviso="";
  for(let i=0;i<CLAVES.length-1;i++){const lg=CLAVES[i+1].t-CLAVES[i].t;
    if(lg<v.seg[0]-0.01||lg>v.seg[1]+0.01)aviso="El tramo "+(i+1)+" dura "+lg.toFixed(1)+" s: con este modelo cada tramo va de "+v.seg[0]+" a "+v.seg[1]+" s.";
    costo+=Math.max(v.seg[0],Math.min(v.seg[1],Math.round(lg)))*v.precio_seg;}
  $("#costo").textContent=CLAVES.length>1?"US$ "+costo.toFixed(2):"—";$("#aviso").textContent=aviso;
}
$("#subir").onchange=async e=>{
  for(const f of e.target.files){const img=await leer(f);
    const t=CLAVES.length?CLAVES[CLAVES.length-1].t+5:0;CLAVES.push({img,t});}
  e.target.value="";pintar();
};
$("#crear").onclick=async()=>{
  const v=MotorVideo.valor($("#motor"));
  if(CLAVES.length<2)return alert("Poné al menos la imagen del principio y la del final.");
  if($("#aviso").textContent)return alert($("#aviso").textContent);
  if(!confirm("Crear el video: "+$("#dur").textContent+" con "+v.nombre+" — "+$("#costo").textContent+". ¿Dale?"))return;
  const b=$("#crear");b.disabled=true;$("#estado").textContent="Mandando…";
  try{
    const r=await fetch(API+"/crear",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({...v,formato:$("#formato").value,general:$("#general").value,claves:CLAVES,tramos:TRAMOS.slice(0,CLAVES.length-1)})});
    const d=await r.json();if(!r.ok)throw new Error(d.detail||r.status);
    seguir(d.id);
  }catch(e){$("#estado").innerHTML='<span class="err">'+esc(e.message)+'</span>';b.disabled=false;}
};
async function seguir(id){
  try{localStorage.setItem("luma_clip_ultimo",id);}catch(e){}
  while(true){
    let j;try{j=await (await fetch(API+"/job/"+id)).json();}catch(e){await new Promise(s=>setTimeout(s,4000));continue;}
    if(j.estado==="listo"){$("#estado").innerHTML='<video src="'+j.video+'" controls style="width:100%;max-width:420px;border-radius:10px"></video><div><a href="'+j.video+'" download>⬇ Bajar video</a> · costó US$ '+(j.costo||0).toFixed(2)+'</div>';$("#crear").disabled=false;return;}
    if(j.estado==="error"){$("#estado").innerHTML='<span class="err">❌ '+esc(j.error)+'</span>'+(j.costo?'<div class="hint">Se gastaron US$ '+j.costo.toFixed(2)+' en los tramos que sí salieron.</div>':'');$("#crear").disabled=false;return;}
    $("#estado").textContent=j.paso||"Trabajando…";
    await new Promise(s=>setTimeout(s,5000));
  }
}
MotorVideo.armar($("#motor"),()=>{MV=true;calcular();});
$("#formato").onchange=calcular;
pintar();
(function(){let u=null;try{u=localStorage.getItem("luma_clip_ultimo");}catch(e){}if(u)fetch(API+"/job/"+u).then(r=>r.ok?r.json():null).then(j=>{if(j&&j.estado==="trabajando"){$("#crear").disabled=true;seguir(u);}});})();
</script></body></html>"""


@router.get(PREF, response_class=HTMLResponse)
async def ui() -> HTMLResponse:
    return HTMLResponse(PAGINA.replace("%%API%%", API), headers={"Cache-Control": "no-store"})
