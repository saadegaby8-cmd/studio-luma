"""
================================================================================
LUMEN · CORTO — una película con historia, armada y filmada adentro de Lumen
================================================================================
Le contás la historia ("3 amigas en Costa Rica se levantan temprano para ir a surfear…") y:

1. EL DIRECTOR (Claude) la parte en ESCENAS (un lugar y un momento cada una: la cabaña, la
   pick-up, la playa) y cada escena en TOMAS de 5 a 7 segundos, con continuidad: misma gente,
   vestuario por escena y una luz que avanza con la historia.
2. CADA ESCENA ES UN PROYECTO DE LUMEN: el director deja armado el set en 3D (formas, modelos,
   las amigas como maniquíes, autos que se mueven) y la cámara de cada toma, con un corte entre
   toma y toma. Se abre en el editor de siempre y se corrige a mano o hablándole al mundo (🪄).
3. FILMAR UNA TOMA: el editor graba el recorrido 3D de esa toma (la previs) y lo manda acá. Kling
   (4.0 con referencias, por Higgsfield) lo convierte en video real con las caras de las amigas que
   aparecen, su vestuario de esa escena y el lugar: copia la cámara y los movimientos del 3D.
4. MONTAR: une todas las tomas en orden (y la música, si la subís).

Lo pagado no se pierde: cada pedido aceptado queda anotado en la toma y, si un reinicio del
servidor borra el archivo, se vuelve a bajar del proveedor sin volver a pagarlo.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Body, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

import video_motor as VM
from imagenes_ia import _pfx, kv, session_sub_from_request, set_current_sub

router = APIRouter()
PREF = "/lumen"
API = PREF + "/api/corto"
SEG_MIN, SEG_MAX = 3, 10
MAX_ESCENAS, MAX_TOMAS = 8, 30
MOTOR_DEFECTO = {"modelo": os.getenv("LUMEN_CORTO_MODELO", "kling40_ref"),
                 "proveedor": os.getenv("LUMEN_CORTO_PROVEEDOR", "higgsfield"), "calidad": "std"}


def _k(cid: str) -> str:
    return _pfx() + f"lumen:corto:{cid}"


def _k_idx() -> str:
    return _pfx() + "lumen:cortos"


def _dir(cid: str) -> Path:
    from personajes import PJ_DIR
    d = PJ_DIR.parent / "lumen" / (_pfx().replace(":", "-").strip("-") or "x") / "cortos" / cid
    d.mkdir(parents=True, exist_ok=True)
    return d


def _clip(cid: str, tid: str) -> Path:
    return _dir(cid) / f"{tid}.mp4"


def _final(cid: str) -> Path:
    return _dir(cid) / "pelicula.mp4"


async def _corto(cid: str) -> Dict[str, Any]:
    c = await kv.get(_k(cid))
    if not isinstance(c, dict):
        raise HTTPException(404, "No encuentro ese corto.")
    return c


_LOCKS: Dict[str, asyncio.Lock] = {}


async def _guardar(c: Dict[str, Any]) -> None:
    c["actualizado"] = time.strftime("%Y-%m-%d %H:%M:%S")
    await kv.set(_k(c["id"]), c)


def _tomas(c: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [t for e in c.get("escenas") or [] for t in e.get("tomas") or []]


def _buscar_toma(c: Dict[str, Any], tid: str):
    for e in c.get("escenas") or []:
        for t in e.get("tomas") or []:
            if t["id"] == tid:
                return e, t
    raise HTTPException(404, "No encuentro esa toma.")


# ─────────────────────────────────────────────────────────────────────────────
# EL DIRECTOR: la historia → escenas, tomas y el set 3D de cada escena
# ─────────────────────────────────────────────────────────────────────────────

_SYS_DIRECTOR = """You are the director, DP and production designer of a short film made with AI video from 3D previs.
The owner (Argentina) tells you the story. Characters (index: name — how they look):
{personajes}
Write the SHORT FILM as SCENES (one place and moment each) and SHOTS of {seg_min}-{seg_max} seconds (prefer 5-7),
with real film continuity: the same people, the same wardrobe inside a scene, a light that moves forward with the
story (night → blue hour → sunrise…), each shot starting where the previous ended. Fast, varied coverage: wide,
medium, close, details, POV, drone, tracking — like a real edit. Cameras at human height unless it is a drone; lenses
24-85 mm; never a wide-angle close to a face.
For EACH SCENE you also BUILD THE 3D SET in a simple previs editor (meters, y up, ground y=0, people 1.7 m, a car
4.5 m) with these operations:
- {{"op":"agregar","id":"s1","forma":"<one of: {formas}>","x":0,"z":0,"y":optional,"giro":deg,"escala":1,"color":"#rrggbb optional","nombre":"optional"}}
- {{"op":"modelo","id":"s2","buscar":"english word of a real 3D model (car, pickup truck, bed, table, toaster, surfboard…)","respaldo":"<forma>","x":0,"z":0,"giro":deg,"escala":1}}
- {{"op":"persona","id":"p0","quien":<character index>,"pose":"<one of: {poses}>","x":0,"z":0,"giro":deg}}
- {{"op":"cielo","cielo":"<one of: {cielos}>"}}
Every character that appears in the scene gets ONE persona op (id "p"+index). Interiors: walls (forma "pared"),
floor is already there. Vehicles: if a person rides in it, put the vehicle as a modelo and the people where they sit.
Then each SHOT has its camera inside the scene's own timeline (seconds counted from the start of THAT shot):
"camara": [{{"t":0,"cam":[x,y,z],"mira":[x,y,z],"lente":35}}, {{"t":<shot seconds>, ...}}] (1 to 3 keys; it moves
between them), and optional "animar": [{{"id":"<id of a set item or persona>","puntos":[{{"t":0,"x":..,"z":..}},
{{"t":<seconds>,"x":..,"z":..}}]}}] for things that move in that shot (the truck driving, a girl walking).
Answer ONLY JSON:
{{"titulo":"…","escenas":[{{"nombre":"Spanish short","lugar_es":"Spanish","lugar_en":"English, the real place in detail
(materials, plants, light)","luz_en":"English: time of day and light of this scene","vestuario_en":{{"<index>":"English:
what she wears in this scene"}},"set":[ops…],"tomas":[{{"seg":6,"que_es":"Spanish, what happens (one line)",
"accion_en":"English, max 60 words: the action of this shot second by second, who does what, never describe faces",
"quienes":[indexes of who appears],"camara":[…],"animar":[…]}}]}}]}}
Total: {dur_min}-{dur_max} seconds, max {max_tomas} shots."""


def _limpiar_plan(d: Dict[str, Any], n_pj: int) -> Dict[str, Any]:
    escenas = []
    for e in (d.get("escenas") or [])[:MAX_ESCENAS]:
        if not isinstance(e, dict):
            continue
        tomas = []
        for t in (e.get("tomas") or []):
            if not isinstance(t, dict) or len(tomas) + sum(len(x["tomas"]) for x in escenas) >= MAX_TOMAS:
                continue
            try:
                seg = int(round(float(t.get("seg") or 6)))
            except (TypeError, ValueError):
                seg = 6
            seg = max(SEG_MIN, min(SEG_MAX, seg))
            quienes = sorted({int(q) for q in t.get("quienes") or [] if str(q).lstrip("-").isdigit() and 0 <= int(q) < n_pj})
            tomas.append({"id": secrets.token_hex(4), "seg": seg, "que_es": str(t.get("que_es") or "")[:200],
                          "accion_en": str(t.get("accion_en") or "")[:600], "quienes": quienes,
                          "camara": [k for k in (t.get("camara") or []) if isinstance(k, dict)][:4],
                          "animar": [a for a in (t.get("animar") or []) if isinstance(a, dict)][:12],
                          "estado": "", "error": "", "pedido": None})
        if not tomas:
            continue
        vest = e.get("vestuario_en") if isinstance(e.get("vestuario_en"), dict) else {}
        escenas.append({"k": len(escenas), "nombre": str(e.get("nombre") or f"Escena {len(escenas) + 1}")[:60],
                        "lugar_es": str(e.get("lugar_es") or "")[:200], "lugar_en": str(e.get("lugar_en") or "")[:500],
                        "luz_en": str(e.get("luz_en") or "")[:300],
                        "vestuario_en": {str(k): str(v)[:300] for k, v in vest.items() if str(k).isdigit()},
                        "set": [o for o in (e.get("set") or []) if isinstance(o, dict)][:80], "tomas": tomas, "pid": ""})
    if not escenas:
        raise HTTPException(502, "El director no devolvió escenas: probá contar la historia con un poco más de detalle.")
    return {"titulo": str(d.get("titulo") or "")[:80], "escenas": escenas}


async def _ficha_pj(pid: str) -> Dict[str, Any]:
    from personajes import _doc
    doc = await _doc(pid)
    ap = doc.get("apariencia") or {}
    rasgos = ", ".join(str(ap.get(k)) for k in ("pelo", "ojos", "piel", "contextura", "rasgos") if ap.get(k))
    return {"id": pid, "nombre": doc.get("nombre") or "Ella", "rasgos": rasgos or str(doc.get("desc_cara") or "")[:200]}


@router.post(API + "/nuevo")
async def api_nuevo(request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """{nombre, historia, personajes:[pid], formato} → el director arma las escenas y las tomas, y cada escena queda
    como un proyecto de Lumen con su set y sus cámaras."""
    import claude_director as _claude
    import lumen as L
    from imagenes_ia import budget_check, budget_record
    from estudio3d_luma import POSES_3D
    set_current_sub(session_sub_from_request(request))
    historia = str(payload.get("historia") or "").strip()[:3000]
    if len(historia) < 15:
        raise HTTPException(400, "Contá la historia (quiénes, dónde, qué pasa y cómo termina).")
    pjs = [str(p) for p in (payload.get("personajes") or []) if re.fullmatch(r"[0-9a-zA-Z_-]{1,40}", str(p))][:6]
    if not pjs:
        raise HTTPException(400, "Elegí quiénes actúan (de 👥 Personajes).")
    if not _claude.disponible():
        raise HTTPException(400, "Para armar el corto hace falta la clave de Anthropic (ANTHROPIC_API_KEY en Railway).")
    ok, motivo, _, _ = await budget_check(0.3)
    if not ok:
        raise HTTPException(402, motivo)
    fichas = [await _ficha_pj(p) for p in pjs]
    fmt = payload.get("formato") if payload.get("formato") in L.FORMATOS else "9:16"
    try:
        dur_obj = max(20, min(180, int(payload.get("duracion") or 60)))
    except (TypeError, ValueError):
        dur_obj = 60
    sistema = _SYS_DIRECTOR.format(
        personajes="\n".join(f"{k}: {f['nombre']} — {f['rasgos'] or 'see her photos'}" for k, f in enumerate(fichas)),
        seg_min=SEG_MIN, seg_max=SEG_MAX, formas=", ".join(sorted(L._nombres_formas())), poses=", ".join(sorted(POSES_3D)),
        cielos=", ".join(L.MUNDO["cielos"]), dur_min=int(dur_obj * 0.8), dur_max=int(dur_obj * 1.2), max_tomas=MAX_TOMAS)
    partes = [{"text": f"FORMAT: {fmt}. TARGET LENGTH: about {dur_obj} s.\nTHE STORY (Spanish):\n{historia}"}]
    try:
        data, costo = await _claude.pedir_json(sistema, partes, max_tokens=24000,
                                               esfuerzo=os.getenv("LUMEN_CORTO_ESFUERZO", "medium"))
    except _claude.ClaudeNoDisponible as e:
        raise HTTPException(502, f"El director no pudo: {e}")
    await budget_record("lumen_corto", _claude.MODELO, costo, 1, note="Lumen: el director arma el corto")
    plan = _limpiar_plan(data, len(fichas))
    cid = secrets.token_hex(5)
    idx_p = (await kv.get(L._k_idx())) or []
    nombre = str(payload.get("nombre") or plan["titulo"] or "Mi corto").strip()[:80]
    for e in plan["escenas"]:
        pid = secrets.token_hex(5)
        dur = sum(t["seg"] for t in e["tomas"])
        p = {"id": pid, "nombre": f"{nombre} · {e['k'] + 1}. {e['nombre']}", "formato": fmt, "dur": dur, "escena3d": None,
             "tomas": [], "creado": time.strftime("%Y-%m-%d %H:%M"), "actualizado": time.strftime("%Y-%m-%d %H:%M:%S"),
             "corto": {"cid": cid, "k": e["k"]},
             "plan_corto": {"set": e["set"], "personajes": pjs, "nombres": [f["nombre"] for f in fichas],
                            "tomas": [{"id": t["id"], "seg": t["seg"], "camara": t["camara"], "animar": t["animar"]}
                                      for t in e["tomas"]]}}
        await kv.set(L._k_proy(pid), p)
        idx_p = [pid] + idx_p
        e["pid"] = pid
    await kv.set(L._k_idx(), idx_p)
    c = {"id": cid, "nombre": nombre, "historia": historia, "formato": fmt, "personajes": pjs, "fichas": fichas,
         "motor": dict(MOTOR_DEFECTO), "escenas": plan["escenas"], "creado": time.strftime("%Y-%m-%d %H:%M")}
    await _guardar(c)
    await kv.set(_k_idx(), [cid] + [x for x in ((await kv.get(_k_idx())) or []) if x != cid][:40])
    return {"corto": _vista(c), "costo": round(costo, 3)}


def _vista(c: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in c.items() if k not in ("historia",)}
    out["historia"] = c.get("historia", "")
    t0 = 0
    for e in out["escenas"]:
        e = e
        ini = 0
        for t in e["tomas"]:
            t["t0"], t["t1"] = ini, ini + t["seg"]
            ini += t["seg"]
            t["video_url"] = f"{API}/{c['id']}/toma/{t['id']}.mp4?v={t.get('ts', 0)}" if (t.get("estado") == "listo") else ""
        e["editor_url"] = f"{PREF}/editor?id={e['pid']}&corto={c['id']}"
        t0 += ini
    out["dur"] = t0
    out["listas"] = sum(1 for t in _tomas(c) if t.get("estado") == "listo")
    out["total"] = len(_tomas(c))
    out["final_url"] = f"{API}/{c['id']}/pelicula.mp4?v={c.get('final_ts', 0)}" if c.get("final_ts") else ""
    out["costo_seg"] = VM.precio_seg(c["motor"]["modelo"], c["motor"]["proveedor"], c["motor"]["calidad"], False)
    return out


@router.get(API + "/lista")
async def api_lista(request: Request) -> Dict[str, Any]:
    set_current_sub(session_sub_from_request(request))
    out = []
    for cid in (await kv.get(_k_idx())) or []:
        c = await kv.get(_k(cid))
        if isinstance(c, dict):
            out.append({"id": cid, "nombre": c.get("nombre"), "escenas": len(c.get("escenas") or []),
                        "tomas": len(_tomas(c)), "listas": sum(1 for t in _tomas(c) if t.get("estado") == "listo"),
                        "creado": c.get("creado", "")})
    return {"cortos": out}


@router.get(API + "/{cid}")
async def api_get(cid: str, request: Request) -> Dict[str, Any]:
    set_current_sub(session_sub_from_request(request))
    return {"corto": _vista(await _corto(cid))}


@router.post(API + "/{cid}/toma/{tid}")
async def api_toma_editar(cid: str, tid: str, request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Corregir lo que pasa en una toma (el texto que recibe Kling) o quiénes aparecen."""
    set_current_sub(session_sub_from_request(request))
    async with _LOCKS.setdefault(cid, asyncio.Lock()):
        c = await _corto(cid)
        _, t = _buscar_toma(c, tid)
        if "accion_en" in payload:
            t["accion_en"] = str(payload["accion_en"] or "")[:600]
        if "que_es" in payload:
            t["que_es"] = str(payload["que_es"] or "")[:200]
        if isinstance(payload.get("quienes"), list):
            t["quienes"] = sorted({int(q) for q in payload["quienes"] if str(q).isdigit() and int(q) < len(c["personajes"])})
        await _guardar(c)
    return {"corto": _vista(c)}


@router.post(API + "/{cid}/motor")
async def api_motor(cid: str, request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    set_current_sub(session_sub_from_request(request))
    m = payload.get("modelo")
    if m not in VM.MODELOS or VM.MODELOS[m].get("tipo") != "ref" or not VM.rutas(m, str(payload.get("proveedor")), "std"):
        raise HTTPException(400, "Elegí un modelo con referencias y un proveedor que lo tenga.")
    async with _LOCKS.setdefault(cid, asyncio.Lock()):
        c = await _corto(cid)
        c["motor"] = {"modelo": m, "proveedor": payload["proveedor"], "calidad": "pro" if payload.get("calidad") == "pro" else "std"}
        await _guardar(c)
    return {"corto": _vista(c)}


# ─────────────────────────────────────────────────────────────────────────────
# FILMAR UNA TOMA: la previs 3D (lo que grabó el editor) + sus caras + su vestuario + el lugar → Kling
# ─────────────────────────────────────────────────────────────────────────────

async def _refs_pj(cli: httpx.AsyncClient, key: str, pid: str, nombre_archivo: str) -> List[str]:
    """Cara de frente, perfil y retrato/cuerpo de una actriz, subidas a fal (URLs que Kling puede leer)."""
    from imagenes_ia import _compress_ref
    from personajes import _doc, _fal_subir, _refs_identidad, caras_hd
    doc = await _doc(pid)
    hd = await caras_hd(doc)
    refs = await _refs_identidad(doc)
    fotos = [x for x in (hd.get("cara_hd"), hd.get("perfil_hd")) if x]
    fotos += [b for et, b in refs if et.startswith("retrato") or et.startswith("cuerpo entero de frente")]
    fotos = list(dict.fromkeys(fotos))[:3]
    return [await _fal_subir(cli, key, base64.b64decode(_compress_ref(base64.b64decode(b), max_dim=1400, q=90)),
                             "image/jpeg", f"{nombre_archivo}-{i}.jpg") for i, b in enumerate(fotos)]


def _prompt_toma(c: Dict[str, Any], e: Dict[str, Any], t: Dict[str, Any], hay_video: bool) -> str:
    ref: Dict[str, str] = {}
    for n, k in enumerate(t["quienes"], 1):
        f = c["fichas"][k]
        ref[f"@Element{n}"] = (f"{f['nombre']}: the same exact woman in every photo of @Element{n} — keep her face, "
                               f"hair and body identical. In this scene she wears: {e['vestuario_en'].get(str(k)) or 'her outfit of this scene'}.")
    if hay_video:
        ref["@Video1"] = ("the 3D previs of THIS shot (grey blocking): copy EXACTLY its camera movement, framing, lens, "
                          "where each person is and how they move; replace the grey mannequins with the real women and "
                          "the grey shapes with the real place and objects")
    pedido = {
        "video": f"Photoreal live-action shot of a short film, {t['seg']} seconds, vertical {c['formato']}, one continuous shot, "
                 "real time (never slow motion), no cuts.",
        "references": ref,
        "place": e["lugar_en"], "light": e["luz_en"],
        "action": t["accion_en"],
        "who": f"ONLY {len(t['quienes'])} person(s) in frame: " + ", ".join(c["fichas"][k]["nombre"] for k in t["quienes"])
               if t["quienes"] else "no people in frame",
        "realism": "it must look filmed, not generated: real skin with pores, natural asymmetry, real hair, candid "
                   "expressions, directional real light with shadows, cinema camera, natural lens, film grain",
        "forbidden": "text, logos, watermarks, extra people, slow motion, CGI look, plastic skin",
    }
    return json.dumps(pedido, ensure_ascii=False)


async def _filmar(sub: Optional[str], cid: str, tid: str, previs: Optional[Path]) -> None:
    from imagenes_ia import budget_record
    from personajes import _fal_key, _fal_subir
    set_current_sub(sub)
    c = await _corto(cid)
    e, t = _buscar_toma(c, tid)
    m = c["motor"]
    destino = _clip(cid, tid)
    crudo = _dir(cid) / f"{tid}_crudo.mp4"
    try:
        key = await _fal_key()
        if not key:
            raise RuntimeError("Falta la clave de fal (para subir las referencias).")
        async with httpx.AsyncClient(timeout=300) as cli:
            els = []
            for n, k in enumerate(t["quienes"]):
                urls = await _refs_pj(cli, key, c["personajes"][k], f"corto-{cid}-{tid}-{k}")
                if urls:
                    els.append({"frontal_image_url": urls[0], "reference_image_urls": urls[1:] or urls[:1]})
            payload: Dict[str, Any] = {"prompt": _prompt_toma(c, e, t, previs is not None), "elements": els,
                                       "duration": str(t["seg"]), "aspect_ratio": c["formato"],
                                       "generate_audio": False}
            if previs is not None:
                payload["video_urls"] = [await _fal_subir(cli, key, previs.read_bytes(), "video/mp4", f"corto-{cid}-{tid}-previs.mp4")]
            if not els and not payload.get("video_urls"):
                raise RuntimeError("Esta toma no tiene a nadie ni previs: Kling necesita al menos una referencia.")
        seg = VM.seg_valido(m["modelo"], t["seg"])
        costo_est = round(seg * VM.precio_seg(m["modelo"], m["proveedor"], m["calidad"], False), 3)
        clave = VM.clave_pendiente("corto", cid, tid, payload["prompt"], m["modelo"], m["proveedor"], m["calidad"])
        pedido: Dict[str, Any] = {}
        async def pedir() -> Dict[str, Any]:
            prev = VM.AL_ACEPTAR.get()
            async def anotar(info: Dict[str, Any]) -> None:
                pedido.update(info)
                if prev:
                    await prev(info)
            tok = VM.AL_ACEPTAR.set(anotar)
            try:
                return await VM.referencias(m["proveedor"], m["modelo"], m["calidad"], payload, crudo, key_fal=key)
            finally:
                VM.AL_ACEPTAR.reset(tok)
        r = await VM.con_recuperacion(clave, crudo, costo_est, pedir, key_fal=key)
        await budget_record("lumen_corto_toma", m["modelo"], float(r.get("costo") or costo_est), 1,
                            note=f"Lumen corto: toma de {seg} s ({VM.PROVEEDORES.get(m['proveedor'], '')} {r.get('id', '')})")
        crudo.replace(destino)
        cambios = {"estado": "listo", "error": "", "ts": int(time.time()), "pedido": pedido or t.get("pedido"),
                   "costo": float(r.get("costo") or costo_est)}
    except Exception as ex:
        crudo.unlink(missing_ok=True)
        cambios = {"estado": "error", "error": str(getattr(ex, "detail", "") or ex)[:300]}
    finally:
        if previs:
            previs.unlink(missing_ok=True)
    async with _LOCKS.setdefault(cid, asyncio.Lock()):
        c = await _corto(cid)
        _, t = _buscar_toma(c, tid)
        t.update(cambios)
        await _guardar(c)


@router.post(API + "/{cid}/toma/{tid}/filmar")
async def api_filmar(cid: str, tid: str, request: Request, video: Optional[UploadFile] = File(None)) -> Dict[str, Any]:
    """El editor manda la previs 3D de esa toma (webm) y se filma en segundo plano."""
    import lumen as L
    from imagenes_ia import budget_check
    sub = session_sub_from_request(request)
    set_current_sub(sub)
    c = await _corto(cid)
    _, t = _buscar_toma(c, tid)
    if t.get("estado") == "filmando":
        raise HTTPException(409, "Esa toma ya se está filmando.")
    m = c["motor"]
    ok, motivo, _, _ = await budget_check(round(t["seg"] * VM.precio_seg(m["modelo"], m["proveedor"], m["calidad"], False), 2))
    if not ok:
        raise HTTPException(402, motivo)
    previs = None
    if video is not None:
        datos = await video.read()
        if len(datos) > 150_000_000:
            raise HTTPException(413, "La previs pesa demasiado.")
        web, previs = _dir(cid) / f"{tid}_previs.webm", _dir(cid) / f"{tid}_previs.mp4"
        web.write_bytes(datos)
        try:
            await L._a_mp4(web, previs)
        finally:
            web.unlink(missing_ok=True)
    async with _LOCKS.setdefault(cid, asyncio.Lock()):
        c = await _corto(cid)
        _, t = _buscar_toma(c, tid)
        t.update({"estado": "filmando", "error": "", "desde": int(time.time())})
        await _guardar(c)
    asyncio.create_task(_filmar(sub, cid, tid, previs))
    return {"corto": _vista(c)}


async def _asegurar_clip(c: Dict[str, Any], t: Dict[str, Any]) -> bool:
    """El video de la toma en disco: si un reinicio lo borró, se vuelve a bajar del proveedor (ya está pagado)."""
    p = _clip(c["id"], t["id"])
    if p.exists():
        return True
    info = t.get("pedido")
    if not isinstance(info, dict) or not info.get("id"):
        return False
    from personajes import _fal_key
    return await VM.recuperar(info, p, key_fal=await _fal_key())


@router.get(API + "/{cid}/toma/{tid}.mp4")
async def api_toma_video(cid: str, tid: str, request: Request):
    set_current_sub(session_sub_from_request(request))
    c = await _corto(cid)
    _, t = _buscar_toma(c, tid)
    if not await _asegurar_clip(c, t):
        raise HTTPException(404, "Esa toma todavía no está filmada.")
    return FileResponse(_clip(cid, tid), media_type="video/mp4")


# ─────────────────────────────────────────────────────────────────────────────
# MONTAR: las tomas en orden, mismo tamaño y ritmo, con la música si la hay
# ─────────────────────────────────────────────────────────────────────────────

def _ff(args: List[str], timeout: int = 900) -> None:
    from filmado import _ffmpeg_bin
    r = subprocess.run([_ffmpeg_bin()] + args, capture_output=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg: " + r.stderr.decode(errors="ignore")[-300:])


@router.post(API + "/{cid}/montar")
async def api_montar(cid: str, request: Request, musica: Optional[UploadFile] = File(None)) -> Dict[str, Any]:
    set_current_sub(session_sub_from_request(request))
    c = await _corto(cid)
    tomas = _tomas(c)
    faltan = [str(i + 1) for i, t in enumerate(tomas) if t.get("estado") != "listo"]
    if faltan:
        raise HTTPException(400, f"Faltan filmar tomas: {', '.join(faltan)}.")
    for t in tomas:
        if not await _asegurar_clip(c, t):
            raise HTTPException(400, f"No encuentro el video de la toma «{t['que_es'][:40]}»: filmala de nuevo.")
    w, h = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "4:5": (1080, 1350)}.get(c["formato"], (1080, 1920))
    d = _dir(cid)
    entradas: List[str] = []
    for t in tomas:
        entradas += ["-i", str(_clip(cid, t["id"]))]
    filtros = "".join(f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps=24,setsar=1,format=yuv420p[v{i}];"
                      for i in range(len(tomas)))
    filtros += "".join(f"[v{i}]" for i in range(len(tomas))) + f"concat=n={len(tomas)}:v=1:a=0[vo]"
    sin_musica = d / "pelicula_sin_musica.mp4"
    await asyncio.to_thread(_ff, ["-y"] + entradas + ["-filter_complex", filtros, "-map", "[vo]", "-c:v", "libx264",
                                                     "-crf", "17", "-preset", "medium", "-movflags", "+faststart", str(sin_musica)])
    if musica is not None:
        mp = d / "musica_subida"
        mp.write_bytes(await musica.read())
        from videos_luma import _duracion_video
        total = _duracion_video(sin_musica)
        await asyncio.to_thread(_ff, ["-y", "-i", str(sin_musica), "-i", str(mp), "-filter_complex",
                                      f"[1:a]atrim=0:{total:.3f},afade=t=in:d=0.4,afade=t=out:st={max(0, total - 1.5):.3f}:d=1.5[a]",
                                      "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                                      "-shortest", "-movflags", "+faststart", str(_final(cid))])
        mp.unlink(missing_ok=True)
        sin_musica.unlink(missing_ok=True)
    else:
        sin_musica.replace(_final(cid))
    async with _LOCKS.setdefault(cid, asyncio.Lock()):
        c = await _corto(cid)
        c["final_ts"] = int(time.time())
        await _guardar(c)
    return {"corto": _vista(c)}


@router.get(API + "/{cid}/pelicula.mp4")
async def api_pelicula(cid: str, request: Request):
    set_current_sub(session_sub_from_request(request))
    c = await _corto(cid)
    if not _final(cid).exists():
        raise HTTPException(404, "La película todavía no está montada (o el servidor se reinició): tocá Montar de nuevo.")
    return FileResponse(_final(cid), media_type="video/mp4", filename=f"{re.sub(r'[^0-9A-Za-z]+', '-', c['nombre'])}.mp4")


@router.delete(API + "/{cid}")
async def api_borrar(cid: str, request: Request) -> Dict[str, Any]:
    set_current_sub(session_sub_from_request(request))
    await kv.delete(_k(cid))
    await kv.set(_k_idx(), [x for x in ((await kv.get(_k_idx())) or []) if x != cid])
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# LA PANTALLA DEL CORTO (/lumen/corto?id=…): escenas, tomas, filmar y montar
# ─────────────────────────────────────────────────────────────────────────────

@router.get(PREF + "/corto", response_class=HTMLResponse)
async def pagina() -> HTMLResponse:
    import lumen as L
    return HTMLResponse(PAGINA.replace("%%API%%", API).replace("%%PREF%%", PREF).replace("%%VERSION%%", L.VERSION))


PAGINA = r"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Lumen · Corto</title><link href="https://fonts.googleapis.com/css2?family=Jost:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{--bg:#141318;--card:#1c1b22;--card-2:#26242d;--line:#3a3842;--ink:#eee;--ink-soft:#aaa;--rose-deep:#e9c58b}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);font-family:Jost,system-ui,sans-serif;font-size:14px}
main{max-width:980px;margin:0 auto;padding:16px} h1{font-size:22px;margin:4px 0} h2{font-size:17px;margin:0 0 4px}
a{color:var(--rose-deep)} .hint{color:var(--ink-soft);font-size:12.5px} .mal{color:#f08080} .bien{color:#9fd49f}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin:12px 0}
button,.btn{background:var(--card-2);color:var(--ink);border:1px solid var(--line);border-radius:9px;padding:6px 12px;font:inherit;cursor:pointer;text-decoration:none;display:inline-block}
button.go,.btn.go{background:var(--rose-deep);color:#1c1b22;border:0;font-weight:600} button:disabled{opacity:.5;cursor:default}
.tomas{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px;margin-top:10px}
.t{background:var(--card-2);border:1px solid var(--line);border-radius:10px;padding:8px} .t video{width:100%;border-radius:8px;margin-top:6px;max-height:300px;background:#000}
textarea{width:100%;background:#141318;color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:6px;font:inherit;font-size:12.5px}
.fila{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:8px}
</style></head><body><main>
<div class="fila" style="margin:0"><a href="%%PREF%%">◀ Lumen</a><span class="hint">v%%VERSION%%</span></div>
<div id="app"><p class="hint">Cargando…</p></div>
</main><script>
const API = "%%API%%", PREF = "%%PREF%%", ID = new URLSearchParams(location.search).get("id");
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let C = null, CAT = null;
async function api(u, op){ const r = await fetch(API + u, op); const j = await r.json().catch(() => ({})); if(!r.ok) throw new Error(j.detail || r.status); return j; }
function pintar(){
  const n = C.fichas.map(f => f.nombre);
  $("#app").innerHTML = `<h1>🎬 ${esc(C.nombre)}</h1>
    <div class="hint">${C.escenas.length} escenas · ${C.total} tomas · ${C.dur} s · ${esc(n.join(", "))} · filmadas ${C.listas}/${C.total} · ~US$${(C.costo_seg * C.dur).toFixed(2)} todo</div>
    <div class="card"><b>Cómo se hace</b><div class="hint" style="margin-top:4px">1) Abrí cada escena en Lumen: el set y las cámaras ya están armados. Corregí lo que quieras a mano o con 🪄 Pedile al mundo.
      2) En el editor, abajo, cada toma tiene <b>🎬 Filmar</b>: graba su previs 3D y Kling la hace real con sus caras, su ropa y el lugar.
      3) Cuando estén todas, <b>✂️ Montar</b> (con tu música si querés).</div>
      <div class="fila">Motor: <span id="motor" style="display:flex;gap:6px;flex-wrap:wrap"></span></div></div>
    ${C.escenas.map(e => `<div class="card"><div class="fila" style="margin:0;justify-content:space-between"><h2>${e.k + 1}. ${esc(e.nombre)}</h2>
        <a class="btn go" href="${e.editor_url}" target="_blank">🧊 Abrir escena en Lumen</a></div>
      <div class="hint">${esc(e.lugar_es)}</div>
      <div class="tomas">${e.tomas.map((t, i) => `<div class="t" data-t="${t.id}"><b>Toma ${i + 1}</b> <span class="hint">${t.seg} s · ${esc(t.quienes.map(q => n[q]).join(", ") || "sin gente")}</span>
        <div>${esc(t.que_es)}</div>
        <details><summary class="hint" style="cursor:pointer">Lo que recibe Kling</summary><textarea rows="4" data-acc="${t.id}">${esc(t.accion_en)}</textarea></details>
        <div class="hint">${t.estado === "listo" ? '<span class="bien">✓ filmada</span>' : t.estado === "filmando" ? "⏳ filmando…" : t.estado === "error" ? `<span class="mal">${esc(t.error)}</span>` : "sin filmar: filmala desde el editor"}</div>
        ${t.video_url ? `<video src="${t.video_url}" controls playsinline preload="none"></video>` : ""}</div>`).join("")}</div></div>`).join("")}
    <div class="card"><h2>✂️ La película</h2>
      <div class="fila"><label class="btn"><input type="file" accept="audio/*" id="musica" style="display:none">🎵 Música (opcional)</label><span class="hint" id="musNom"></span>
        <button class="go" id="montar" ${C.listas < C.total ? "disabled" : ""}>✂️ Montar ${C.listas < C.total ? `(faltan ${C.total - C.listas})` : ""}</button><span class="hint" id="est"></span></div>
      ${C.final_url ? `<video src="${C.final_url}" controls playsinline style="width:100%;max-height:70vh;margin-top:10px;border-radius:10px;background:#000"></video><div class="fila"><a href="${C.final_url}" download>⬇️ Bajar la película</a></div>` : ""}</div>`;
  $$("[data-acc]").forEach(x => x.onchange = async () => { try{ C = (await api(`/${ID}/toma/${x.dataset.acc}`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({accion_en: x.value})})).corto; }catch(e){ alert(e.message); } });
  $("#musica").onchange = () => { $("#musNom").textContent = ($("#musica").files[0] || {}).name || ""; };
  $("#montar").onclick = async () => { $("#montar").disabled = true; $("#est").textContent = "Montando…";
    try{ const fd = new FormData(); if($("#musica").files[0]) fd.append("musica", $("#musica").files[0]);
      C = (await api(`/${ID}/montar`, {method: "POST", body: fd})).corto; pintar(); }catch(e){ $("#est").innerHTML = `<span class="mal">${esc(e.message)}</span>`; $("#montar").disabled = false; } };
  // El motor del corto: sólo los modelos con referencias; se muestra lo que tiene guardado y cambia sólo si lo tocás.
  (async () => { if(!CAT){ try{ CAT = await (await fetch("/clips/api/catalogo")).json(); }catch(e){ return; } }
    const ms = CAT.modelos.filter(m => m.tipo === "ref"), m0 = ms.find(m => m.id === C.motor.modelo) || ms[0]; if(!m0) return;
    $("#motor").innerHTML = `<select id="mMod">${ms.map(m => `<option value="${m.id}" ${m.id === m0.id ? "selected" : ""}>${esc(m.nombre)}</option>`).join("")}</select>
      <select id="mProv">${m0.proveedores.map(p => `<option value="${p.id}" ${p.id === C.motor.proveedor ? "selected" : ""}>${esc(p.nombre)}${p.conectado ? "" : " (sin clave)"} · US$${p.precio[C.motor.calidad || "std"].toFixed(3)}/s</option>`).join("")}</select>
      <select id="mCal"><option value="std" ${C.motor.calidad !== "pro" ? "selected" : ""}>720p</option><option value="pro" ${C.motor.calidad === "pro" ? "selected" : ""}>1080p</option></select>`;
    const guardar = async () => { const m = ms.find(x => x.id === $("#mMod").value); const prov = m.proveedores.some(p => p.id === $("#mProv").value) ? $("#mProv").value : (m.proveedores[0] || {}).id;
      try{ C = (await api(`/${ID}/motor`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({modelo: m.id, proveedor: prov, calidad: $("#mCal").value})})).corto; pintar(); }catch(e){ alert(e.message); } };
    $("#mMod").onchange = $("#mProv").onchange = $("#mCal").onchange = guardar; })();
}
async function cargar(){ try{ C = (await api("/" + ID)).corto; pintar(); }catch(e){ $("#app").innerHTML = `<p class="mal">${esc(e.message)}</p>`; } }
cargar();
setInterval(async () => { if(!C || !C.escenas.some(e => e.tomas.some(t => t.estado === "filmando"))) return; try{ C = (await api("/" + ID)).corto; pintar(); }catch(e){} }, 8000);
window.addEventListener("focus", () => cargar());
</script></body></html>"""
