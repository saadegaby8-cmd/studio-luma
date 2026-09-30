"""
FILMADO DE CERO (prueba) — un video de ella filmado por el motor desde cero, NO una foto
que cobra vida, con SU voz.

Hasta ahora casi todo en la app partía de una FOTO quieta y la animaba (image-to-video):
el video arranca en una pose de catálogo, la cámara está clavada y se nota que "la foto
se mueve". Acá la cara, el cuerpo y la prenda van sólo como REFERENCIA (reference-to-video)
y el motor filma de cero, como si alguien se filmara con el celular.

v1.2 (después de la primera prueba real con Kling: no respetó la prenda, "habla raro" y
va tan rápido que se nota la IA):
  1. La VOZ es la de Reels (Gemini, rioplatense, con su tono y energía), no la que inventa
     el motor. Primero sale la voz y se mide: el video dura lo que dura lo que dice (antes
     callaba 2 s y metía todo el texto en 3,5 s).
  2. CLAUDE DIRIGE la toma: una sola acción tranquila, marcada con lo que dice; y si el
     texto está vacío, lo escribe él (lo que escribís vos va tal cual).
  3. Kling filma SIN voz, con ella como @Element1 y LA PRENDA como @Element2 (antes la
     prenda iba como imagen suelta y Kling inventó un corpiño liso).
  4. Lip-sync (Sync Lipsync v2 Pro en fal): la boca del video se ajusta a su voz.
  5. Claude mira el resultado contra la foto de la prenda y dice si la respetó (sólo
     informa: no vuelve a gastar solo).
"""

from __future__ import annotations

import asyncio
import base64
import math
import os
import subprocess
import time
import uuid as _uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

import claude_director as _claude
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
    COSTO_TTS,
    PJ_DIR,
    VOCES,
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
    _tts_mp3,
)
from reels import (
    ENERGIAS_VOZ,
    TONOS,
    _cuerpo_en,
    _instruccion_voz,
    _ref_cuerpo,
    _tratar_voz,
    _voz_reel,
)
from videos_luma import _duracion_video, _ffmpeg_bin, _spawn

ROUTE_PREFIX = os.environ.get("FILMADO_PREFIX", "/filmado").rstrip("/")
API = ROUTE_PREFIX + "/api"
VERSION = "1.2.0"   # subí este número cada vez que cambiamos el archivo

SEG = 5
DURACIONES = (5, 8, 10)
SEG_MIN, SEG_MAX = 3, 15            # lo que acepta Kling por clip
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
LIPSYNC_MODEL = os.getenv("FILMADO_LIPSYNC_MODEL", "fal-ai/sync-lipsync/v2/pro")
PRECIO_LIPSYNC_SEG = float(os.getenv("FILMADO_PRECIO_LIPSYNC", "0.083"))
ENERGIA_FILMADO = "natural"         # con sus pausas: la primera prueba "iba tan rápido que parecía IA"
ACCION_DEFAULT = ("tranquila, frente a la cámara, le muestra el conjunto de cerca y toca el encaje "
                  "para que se vea la tela")
DICE_DEFAULT = ("Chicas, me llegó el conjunto que les dije. Miren este encaje, es divino y re "
                "cómodo. Escríbanme por DM que les paso los talles.")
DIR = PJ_DIR / "filmado"
DIR.mkdir(parents=True, exist_ok=True)

# Lo que hace que se vea FILMADO y no una foto animada.
_FILMADO = (
    "This is REAL phone footage, not an animated photo: the video starts in the MIDDLE of her "
    "movement (never from a still, posed frame), the phone camera is handheld with small natural "
    "shakes and tiny reframing, focus and exposure adjust slightly as she moves, real indoor "
    "light with soft shadows, subtle grain. She moves like a real person: weight shifts, "
    "breathing, blinking, hair moving, small hand gestures, CALM and unhurried, at real-time "
    "speed (never slow motion, never sped up). Real skin with pores and natural shine, no "
    "smoothing, no plastic or waxy look, no doll face. Her body keeps its real proportions and "
    "weight in every frame (do not enlarge or reshape her). No text, no logos, no watermark, no "
    "other people."
)
_NEGATIVO = ("animated photo, static camera, frozen pose, mannequin, plastic skin, waxy, doll, "
             "CGI, 3D render, slow motion, fast motion, walking into frame, morphing face, "
             "exaggerated body, extra fingers, deformed hands, text, watermark")
_HABLA_MUDA = (" She is TALKING to the camera the whole time (her voice is added later): her mouth "
               "opens and moves naturally as if speaking, her face stays well visible and mostly "
               "towards the lens, with the small head movements and expressions of someone talking.")


def _k_lista() -> str:
    return _pfx() + "filmado:lista"


def _mp4(jid: str) -> Path:
    return DIR / f"{jid}.mp4"


def _refs_texto(motor: str, n_ref_ella: int, n_prendas: int) -> Tuple[str, str]:
    """Cómo se nombra a ella y a la prenda en el pedido, según el motor."""
    if MOTORES[motor]["tipo"] == "kling":
        return "@Element1", "@Element2"
    ella = "the woman of @Image1" + (f" (also @Image2{' and @Image3' if n_ref_ella > 2 else ''})"
                                      if n_ref_ella > 1 else "")
    p0 = n_ref_ella + 1
    return ella, f"@Image{p0}" + (f" and @Image{p0 + 1}" if n_prendas > 1 else "")


async def prompt_filmado(doc: Dict[str, Any], accion: str, motor: str, n_ref_ella: int,
                         n_prendas: int, dice: str = "", puesta: bool = False,
                         lugar: str = "dormitorio", toma: str = "") -> str:
    """El pedido al motor. `toma` es la dirección que escribió Claude (en inglés); si no hay,
    se arma con la acción escrita. La voz NO va: el motor filma mudo y el lip-sync pone su voz."""
    ella, prenda = _refs_texto(motor, n_ref_ella, n_prendas)
    cuerpo = await _cuerpo_en(doc)
    if puesta:
        ropa = (f"wearing EXACTLY the lingerie set {prenda} (same design, cut, colour, lace pattern, "
                "straps and trims — not a generic or plain version)")
    else:
        # Como el video de referencia (UGC): ella vestida de entrecasa MOSTRANDO el producto.
        # Además es lo que menos rebota en los filtros.
        ropa = (f"wearing a casual fitted black t-shirt and jeans, and holding the lingerie set {prenda} "
                "in her hands to show it (EXACTLY that design, cut, colour, lace pattern, straps and "
                "trims — not a generic or plain version)")
    if not toma:
        toma = (await _al_ingles({"a": accion})).get("a") or accion
    return (
        f"Vertical 9:16 UGC Instagram reel filmed by herself on a phone: {ella}, the same exact woman "
        f"(same face, hair and body), {ropa}. {toma}"
        + (f" Her body: {cuerpo}." if cuerpo else "")
        + f" PLACE: {LUGARES.get(lugar, LUGARES['dormitorio'])[1]}."
        + (_HABLA_MUDA if dice else "") + " " + _FILMADO
    )


# ── Claude dirige ────────────────────────────────────────────────────────────

_SYSTEM_DIRECTOR = (
    "You are the director of short UGC Instagram videos for LUMA Íntima, an Argentine lingerie "
    "brand. The video is ONE continuous shot, filmed by an AI video model from reference images, "
    "of the brand's AI model talking to her phone camera. Her voice is recorded separately and "
    "lip-synced later, so the model must film her TALKING with her face visible.\n"
    "Write the SHOT DIRECTION for the video model, in English, max 90 words: what she does, "
    "timed to what she says, in {seg} seconds. Rules: she is ALREADY in frame at the start, "
    "close to the lens (medium close-up, phone held at arm's length or on a stand), facing the "
    "camera; ONE calm action and at most two small gestures in total (for example: she lifts the "
    "garment into frame at chest height, touches the lace with her fingertips, smiles); no "
    "walking in, no turning around, no looking down for long, no fast moves; the garment stays "
    "clearly visible and in focus for most of the shot. Never sexual.\n"
    "{texto_regla}"
    "Answer in JSON: {{\"toma\": \"...\"{texto_json}}}"
)


async def dirigir(dice: str, seg: int, puesta: bool, lugar: str, accion: str,
                  prendas: List[str], escribir: bool, palabras_max: int) -> Tuple[Dict[str, Any], float]:
    """Claude arma la toma (y el texto, si no lo escribió la persona). Devuelve ({toma, texto},
    costo). Levanta ClaudeNoDisponible si no puede."""
    regla = (f"Also write WHAT SHE SAYS, in Spanish from Argentina (Rioplatense, voseo, casual, like a "
             f"real influencer talking to her followers), at most {palabras_max} words: she presents the "
             "set, says one concrete thing about it (the lace, how comfortable it is, the colour) and "
             "invites to write by DM for sizes. No hashtags, no emojis.\n" if escribir else
             f"What she says (do not change it): \"{dice}\"\n")
    system = _SYSTEM_DIRECTOR.format(seg=seg, texto_regla=regla,
                                     texto_json=", \\\"texto\\\": \\\"...\\\"" if escribir else "")
    parts: List[Dict[str, Any]] = [{"type": "text", "text":
        f"Place: {LUGARES.get(lugar, LUGARES['dormitorio'])[1]}. The garment is "
        + ("WORN by her." if puesta else "held in her hands (she wears a casual t-shirt).")
        + (f" The owner's idea for the action: {accion}." if accion else "")
        + " These are the real product photos of the garment:"}]
    for b in prendas[:2]:
        parts.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b}})
    data, costo = await _claude.pedir_json(system, parts, max_tokens=4000, esfuerzo="medium")
    toma = str(data.get("toma") or "").strip()
    if len(toma) < 30:
        raise _claude.ClaudeNoDisponible("Claude no devolvió una toma usable.")
    out = {"toma": toma[:900]}
    if escribir:
        texto = str(data.get("texto") or "").strip()
        if len(texto) < 10:
            raise _claude.ClaudeNoDisponible("Claude no devolvió el texto.")
        out["texto"] = " ".join(texto.split()[:palabras_max * 2])     # sólo si se pasó de largo en serio
    return out, costo


# ── Voz, lip-sync y revisión ─────────────────────────────────────────────────

async def _voz(doc: Dict[str, Any], texto: str, voz: str, tono: str, energia: str, jid: str) -> Tuple[bytes, float]:
    """La voz de Reels (Gemini, rioplatense) con su tratamiento; devuelve (mp3, segundos)."""
    falso = {"voz": voz, "tono": tono, "voz_energia": energia}
    await _cobrar(COSTO_TTS)
    mp3 = await _tts_mp3(texto, _voz_reel(doc, falso), doc, instruccion=_instruccion_voz(doc, falso))
    await budget_record("filmado_voz", "mp3", COSTO_TTS, 1, note="filmado de cero: voz")
    af = ENERGIAS_VOZ.get(energia, ENERGIAS_VOZ[ENERGIA_FILMADO])["af"]
    if af:
        try:
            mp3 = await asyncio.to_thread(_tratar_voz, mp3, af, DIR, f"{jid}_voz")
        except Exception as e:
            print(f"[filmado] no pude tratar la voz: {e}")
    p = DIR / f"{jid}_voz.mp3"
    p.write_bytes(mp3)
    return mp3, round(_duracion_video(p) or len(texto.split()) / 2.3, 2)


def _juntar_audio(video: Path, mp3: Path, salida: Path) -> None:
    """Si el lip-sync falla: el video de Kling con su voz encima (sin mover la boca)."""
    res = subprocess.run([_ffmpeg_bin(), "-y", "-i", str(video), "-i", str(mp3), "-map", "0:v", "-map", "1:a",
                          "-c:v", "copy", "-c:a", "aac", "-shortest", str(salida)], capture_output=True, timeout=180)
    if res.returncode != 0 or not salida.exists():
        raise RuntimeError("ffmpeg: " + res.stderr.decode(errors="ignore")[-300:])


def _cuadro(video: Path, t: float) -> Optional[str]:
    out = video.with_name(video.stem + f"_c{int(t * 10)}.jpg")
    res = subprocess.run([_ffmpeg_bin(), "-y", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                          "-vf", "scale=720:-2", str(out)], capture_output=True, timeout=60)
    if res.returncode != 0 or not out.exists():
        return None
    try:
        return base64.b64encode(out.read_bytes()).decode()
    finally:
        out.unlink()


async def _procesar(jid: str, pid: str, accion: str, motor: str, prendas: List[str],
                    sub: Optional[str], dice: str = "", puesta: bool = False,
                    lugar: str = "dormitorio", seg: int = SEG, voz: str = "",
                    tono: str = "cercana", energia: str = ENERGIA_FILMADO, hablar: bool = True) -> None:
    set_current_sub(sub)
    try:
        doc = await _doc(pid)
        refs = await _refs_identidad(doc)
        if not refs:
            raise RuntimeError("Este personaje todavía no tiene retrato aprobado.")
        retrato = refs[0][1]
        cara = await recorte_cara_avatar({"id": "pj:" + str(doc.get("id", "")), "ref_b64": retrato})
        cuerpo = _ref_cuerpo(refs)
        ella = ([cara or retrato] + ([retrato] if cara else []) + ([cuerpo] if cuerpo else []))[:3]
        m = MOTORES[motor]
        costo_total = 0.0
        # 1) Claude dirige (y escribe el texto si quedó vacío y hay que hablar).
        escribir = hablar and not dice
        palabras_seg = ENERGIAS_VOZ.get(energia, ENERGIAS_VOZ[ENERGIA_FILMADO])["palabras_seg"]
        palabras_max = max(8, min(int((seg - 1) * palabras_seg * 0.8), int((SEG_MAX - 1) * palabras_seg / 2)))
        toma, director = "", ""
        if _claude.disponible():
            await _job_set(jid, {"estado": "generando", "paso": "Claude está dirigiendo la toma…"})
            try:
                dirigido, c = await dirigir(dice, seg, puesta, lugar, accion, prendas, escribir, palabras_max)
                costo_total += c
                await budget_record("filmado_claude", _claude.MODELO, c, 1, note="filmado de cero: dirección")
                toma, director = dirigido["toma"], _claude.DIRECTORES["claude"]
                if escribir:
                    dice = dirigido["texto"]
            except _claude.ClaudeNoDisponible as e:
                print(f"[filmado] Claude no pudo dirigir: {e}")
        if escribir and not dice:
            dice = DICE_DEFAULT
        # 2) La voz primero: el video dura lo que dura lo que dice.
        mp3, dur_voz = b"", 0.0
        if hablar and dice:
            await _job_set(jid, {"estado": "generando", "paso": "Grabando su voz…"})
            mp3, dur_voz = await _voz(doc, dice, voz, tono, energia, jid)
            costo_total += COSTO_TTS
            seg = max(SEG_MIN, min(SEG_MAX, int(math.ceil(dur_voz + 0.8))))
        # 3) El motor filma (mudo si después va el lip-sync).
        key = await _fal_key()
        headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
        await _job_set(jid, {"estado": "generando", "paso": "Subiendo sus fotos y la prenda a fal…"})
        crudo = DIR / f"{jid}_crudo.mp4"
        async with httpx.AsyncClient(timeout=300) as cli:
            async def subir(b64: str, nombre: str) -> str:
                return await _fal_subir(cli, key, base64.b64decode(b64), "image/jpeg", nombre)
            u_ella = [await subir(b, f"{jid}-ella{i}.jpg") for i, b in enumerate(ella)]
            u_prendas = [await subir(b, f"{jid}-prenda{i}.jpg") for i, b in enumerate(prendas)]
            prompt = await prompt_filmado(doc, accion, motor, len(u_ella), len(u_prendas),
                                          dice if mp3 else "", puesta, lugar, toma)
            if m["tipo"] == "kling":
                # La PRENDA como elemento propio (@Element2): como imagen suelta, Kling la tomaba
                # de inspiración e inventaba un corpiño liso.
                payload: Dict[str, Any] = {
                    "prompt": prompt,
                    "elements": [{"frontal_image_url": u_ella[0], "reference_image_urls": u_ella[1:]},
                                 {"frontal_image_url": u_prendas[0], "reference_image_urls": u_prendas[1:]}],
                    "duration": str(seg), "aspect_ratio": "9:16", "generate_audio": False,
                    "negative_prompt": _NEGATIVO, "cfg_scale": 0.5}
                opc: Tuple[str, ...] = ("negative_prompt", "cfg_scale")
            else:
                payload = {"prompt": prompt, "image_urls": u_ella + u_prendas, "resolution": "720p",
                           "duration": str(seg), "aspect_ratio": "9:16", "generate_audio": False,
                           "enable_safety_checker": False}
                opc = ("enable_safety_checker", "resolution")
            await _job_set(jid, {"paso": f"{m['label']} está filmando ({seg} s)…", "prompt": prompt[:1500]})
            await _fal_enviar(cli, headers, m["modelo"], payload, jid, opc)
            job = await kv.get(_k_job(jid)) or {}
            await _fal_esperar_y_bajar(cli, headers, job["fal_status_url"], job["fal_result_url"],
                                       crudo, jid, inicio=job.get("fal_inicio"))
            c = round(seg * m["precio_seg"], 3)
            costo_total += c
            await budget_record("filmado_prueba", motor, c, 1, note=f"{doc.get('nombre', '')}: filmado de cero")
            # 4) Lip-sync: su voz en la boca del video.
            lipsync = ""
            if mp3:
                await _job_set(jid, {"paso": "Ajustando la boca a su voz (lip-sync)…"})
                try:
                    u_vid = await _fal_subir(cli, key, crudo.read_bytes(), "video/mp4", f"{jid}.mp4")
                    u_voz = await _fal_subir(cli, key, mp3, "audio/mpeg", f"{jid}.mp3")
                    await _fal_enviar(cli, headers, LIPSYNC_MODEL,
                                      {"video_url": u_vid, "audio_url": u_voz, "sync_mode": "cut_off"},
                                      jid, ("sync_mode",))
                    job = await kv.get(_k_job(jid)) or {}
                    await _fal_esperar_y_bajar(cli, headers, job["fal_status_url"], job["fal_result_url"],
                                               _mp4(jid), jid, inicio=job.get("fal_inicio"))
                    c = round(dur_voz * PRECIO_LIPSYNC_SEG, 3)
                    costo_total += c
                    await budget_record("filmado_lipsync", LIPSYNC_MODEL, c, 1, note="filmado de cero: lip-sync")
                    lipsync = "ok"
                except Exception as e:
                    print(f"[filmado] lip-sync falló: {e}")
                    lipsync = f"falló ({str(getattr(e, 'detail', '') or e)[:120]}): va su voz encima, sin mover la boca"
                    await asyncio.to_thread(_juntar_audio, crudo, DIR / f"{jid}_voz.mp3", _mp4(jid))
            else:
                crudo.replace(_mp4(jid))
        # 5) Claude revisa la prenda en el video (sólo informa).
        revision = None
        if _claude.disponible():
            await _job_set(jid, {"paso": "Claude está revisando la prenda en el video…"})
            dur = _duracion_video(_mp4(jid)) or seg
            cuadro = await asyncio.to_thread(_cuadro, _mp4(jid), dur * 0.6)
            if cuadro:
                try:
                    pedido = ("La prenda del producto tiene que verse EXACTA (diseño, color, encaje, "
                              "breteles) " + ("puesta." if puesta else "en sus manos, mostrándola.")
                              + " Es la misma modelo de la cara de referencia. Video de celular realista.")
                    revision, c = await _claude.revisar_foto(cuadro, pedido, cara, prendas[:2])
                    costo_total += c
                    await budget_record("filmado_claude", _claude.MODELO, c, 1, note="filmado de cero: revisión")
                except _claude.ClaudeNoDisponible as e:
                    print(f"[filmado] Claude no pudo revisar: {e}")
        for x in (crudo, DIR / f"{jid}_voz.mp3"):
            try:
                x.unlink()
            except OSError:
                pass
        link = await _guardar_en_drive(f"{_slug(doc.get('nombre', ''))}-filmado-{jid}.mp4",
                                       _mp4(jid).read_bytes(), "video/mp4")
        item = {"id": jid, "pid": pid, "motor": motor, "accion": accion[:120], "dice": dice[:300],
                "puesta": puesta, "lugar": lugar, "seg": seg, "voz_seg": dur_voz, "lipsync": lipsync,
                "director": director, "costo": round(costo_total, 2),
                "revision": {k: revision.get(k) for k in ("puntaje", "fallas")} if revision else None,
                "ts": time.strftime("%Y-%m-%d %H:%M")}
        lista = (await kv.get(_k_lista())) or []
        lista.insert(0, item)
        await kv.set(_k_lista(), lista[:20])
        await _job_set(jid, {"estado": "listo", "paso": "", "drive": link, "resultado": item})
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
            "accion": ACCION_DEFAULT, "dice": DICE_DEFAULT, "precio_lipsync_seg": PRECIO_LIPSYNC_SEG,
            "lugares": {k: v[0] for k, v in LUGARES.items()}, "voces": VOCES, "tonos": list(TONOS),
            "energias": {k: {"nombre": v["nombre"], "palabras_seg": v["palabras_seg"]} for k, v in ENERGIAS_VOZ.items()},
            "energia_default": ENERGIA_FILMADO, "claude": _claude.disponible(),
            "fal_key": bool(await _fal_key())}


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
    accion = _texto(payload.get("accion"), 400)
    dice = _texto(payload.get("dice"), 400)
    hablar = payload.get("hablar") is not False
    puesta = bool(payload.get("puesta"))
    lugar = payload.get("lugar") if payload.get("lugar") in LUGARES else "dormitorio"
    voces_ok = {v for lst in VOCES.values() for v, _ in lst}
    voz = payload.get("voz") if payload.get("voz") in voces_ok else ""
    tono = payload.get("tono") if payload.get("tono") in TONOS else "cercana"
    energia = payload.get("energia") if payload.get("energia") in ENERGIAS_VOZ else ENERGIA_FILMADO
    try:
        seg = int(payload.get("seg") or 8)
    except (TypeError, ValueError):
        seg = 8
    seg = seg if seg in DURACIONES else 8
    if hablar:
        # La duración la pone lo que dice: se estima para cobrar. Si lo escribe Claude, apunta
        # a un clip de ~9 s (corto se ve más natural).
        n = len(dice.split()) if dice else int(8 * ENERGIAS_VOZ[energia]["palabras_seg"] * 0.8)
        seg = max(SEG_MIN, min(SEG_MAX, int(math.ceil(n / ENERGIAS_VOZ[energia]["palabras_seg"] + 0.8))))
        if not dice and not _claude.disponible():
            seg = max(SEG_MIN, min(SEG_MAX, int(math.ceil(len(DICE_DEFAULT.split()) / ENERGIAS_VOZ[energia]["palabras_seg"] + 0.8))))
        if dice and n / ENERGIAS_VOZ[energia]["palabras_seg"] > SEG_MAX - 1:
            raise HTTPException(400, f"El texto es largo para un solo clip ({n} palabras): con esa energía "
                                     f"entran unas {int((SEG_MAX - 1) * ENERGIAS_VOZ[energia]['palabras_seg'])}.")
    m = MOTORES[motor]
    costo = round(seg * m["precio_seg"] + (seg * PRECIO_LIPSYNC_SEG + COSTO_TTS if hablar else 0), 2)
    await _cobrar(costo)
    jid = _uuid.uuid4().hex[:10]
    await _job_nuevo(jid, doc["id"], "filmado", 360, {"costo": costo, "titulo": "Filmado de cero (prueba)"})
    _spawn(_procesar(jid, doc["id"], accion, motor, prendas, CURRENT_SUB.get(), dice, puesta, lugar, seg,
                     voz, tono, energia, hablar))
    return {"job": jid, "costo": costo, "seg": seg}


@router.get(API + "/job/{jid}")
async def api_job(jid: str) -> Dict[str, Any]:
    j = await kv.get(_k_job(jid))
    if not isinstance(j, dict):
        raise HTTPException(404, "Ese trabajo no existe.")
    j = await _revisar_job(j)
    return {k: j.get(k) for k in ("id", "estado", "paso", "error", "costo", "drive", "resultado")}


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
  <p class="hint">Un video de ella <b>filmado por el motor desde cero</b>, no una foto que cobra vida. Primero se graba <b>su voz de Reels</b> (y el video dura lo que dura lo que dice), <b>Claude dirige la toma</b> mirando la prenda, el motor filma con su cara y la prenda como referencias, y al final el <b>lip-sync</b> le pone la boca en sincro con su voz. Claude revisa la prenda en el video y te dice cómo salió (no vuelve a gastar solo).</p>
  <p class="hint" id="aviso_claude" style="display:none;color:var(--bad)">Claude no está disponible (falta ANTHROPIC_API_KEY): la toma sale de lo que escribas en "Qué hace" y hay que escribir qué dice.</p>
  <div class="row"><div><label>Modelo (personaje)</label><select id="pid"></select></div>
  <div><label>Motor</label><select id="motor"></select></div></div>
  <label>La prenda (1 o 2 fotos del producto)</label><input type="file" id="prendas" accept="image/*" multiple><div class="thumbs" id="thumbs"></div>
  <div class="row"><div><label>Dónde</label><select id="lugar"></select></div>
  <div><label>La prenda</label><select id="puesta"><option value="no">La muestra en la mano (vestida de entrecasa)</option><option value="si">La tiene puesta</option></select></div></div>
  <label>Qué hace (idea para Claude; él arma la toma calma y a tiempo con lo que dice)</label><textarea id="accion" rows="2"></textarea>
  <div class="row"><div><label>¿Habla?</label><select id="hablar"><option value="si">Sí, con su voz de Reels</option><option value="no">No, sin voz</option></select></div>
  <div id="caja_seg" style="display:none"><label>Duración</label><select id="seg"></select></div></div>
  <div id="caja_voz"><label>Qué dice (vacío = lo escribe Claude; si lo escribís vos va tal cual)</label><textarea id="dice" rows="2"></textarea>
  <p class="hint" id="largo"></p>
  <div class="row"><div><label>Voz</label><select id="voz"></select></div><div><label>Tono</label><select id="tono"></select></div></div>
  <label>Energía</label><select id="energia"></select></div>
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
const habla = () => $("#hablar").value === "si";
function segVoz(){ const e = CFG.energias[$("#energia").value] || {palabras_seg: 2.3}; const n = ($("#dice").value.trim() || CFG.dice).split(/\s+/).filter(Boolean).length; return {n, voz: n / e.palabras_seg, ps: e.palabras_seg}; }
function costo(){ const m = CFG.motores[$("#motor").value]; if(!m) return;
  $("#caja_seg").style.display = habla() ? "none" : ""; $("#caja_voz").style.display = habla() ? "" : "none"; $("#largo").textContent = ""; $("#largo").style.color = "";
  if(!habla()){ const s = +$("#seg").value || CFG.seg; $("#costo").textContent = `Cuesta ~US$${(m.precio_seg * s).toFixed(2)} (un clip de ${s} s, sin voz).`; return; }
  const v = segVoz(), s = Math.max(3, Math.min(15, Math.ceil(v.voz + 0.8)));
  if($("#dice").value.trim()){ $("#largo").textContent = `${v.n} palabras ≈ ${v.voz.toFixed(1)} s de voz → un clip de ${s} s.`;
    if(v.voz > 14){ $("#largo").textContent = `Es largo para un solo clip: ${v.n} palabras ≈ ${v.voz.toFixed(1)} s. Con esta energía entran unas ${Math.floor(14 * v.ps)} palabras.`; $("#largo").style.color = "var(--bad)"; }
    else if(v.voz > 10){ $("#largo").textContent += " Se puede, pero con menos texto se ve más natural."; } }
  const c = m.precio_seg * s + CFG.precio_lipsync_seg * s + 0.02 + (CFG.claude ? 0.1 : 0);
  $("#costo").textContent = `Cuesta ~US$${c.toFixed(2)}: filmar ${s} s (US$${(m.precio_seg * s).toFixed(2)}) + lip-sync (US$${(CFG.precio_lipsync_seg * s).toFixed(2)}) + voz` + (CFG.claude ? " + Claude (dirige y revisa)." : "."); }
function revision(v){ if(!v || !v.revision) return v && v.lipsync && v.lipsync !== "ok" ? `<div style="color:var(--bad)">Lip-sync ${esc(v.lipsync)}</div>` : "";
  const r = v.revision, f = (r.fallas || []).map(x => `<li>${esc(x)}</li>`).join("");
  return `<div><b>Claude revisó la prenda: ${esc(r.puntaje)}/10</b>${f ? `<ul style="margin:4px 0 0 18px;padding:0">${f}</ul>` : " · no vio fallas"}</div>` + (v.lipsync && v.lipsync !== "ok" ? `<div style="color:var(--bad)">Lip-sync ${esc(v.lipsync)}</div>` : ""); }
async function lista(){ const l = (await api("/lista")).videos; $("#lista").innerHTML = l.length ? l.map(v => `<div><video src="${API}/mp4/${v.id}" controls playsinline preload="metadata" style="max-width:200px"></video><br>${esc((CFG.motores[v.motor] || {}).label || v.motor)} · ${esc(v.ts)}${v.costo != null ? " · US$" + esc(v.costo) : ""}${v.dice ? `<br>“${esc(v.dice)}”` : ""}${revision(v)}</div>`).join("") : '<span class="hint">Todavía no hiciste ninguna.</span>'; }
$("#prendas").onchange = async e => { PRENDAS = await Promise.all(Array.from(e.target.files).slice(0, 2).map(leer)); $("#thumbs").innerHTML = PRENDAS.map(s => `<img src="${s}">`).join(""); };
["#motor", "#seg", "#hablar", "#energia"].forEach(k => $(k).onchange = costo); $("#dice").oninput = costo;
$("#generar").onclick = async () => { const b = $("#generar"); b.disabled = true; $("#salida").innerHTML = "";
  try{ const r = await api("/generar", {method: "POST", body: JSON.stringify({pid: $("#pid").value, motor: $("#motor").value, accion: $("#accion").value, dice: $("#dice").value, puesta: $("#puesta").value === "si", lugar: $("#lugar").value, seg: +$("#seg").value, hablar: habla(), voz: $("#voz").value, tono: $("#tono").value, energia: $("#energia").value, prendas: PRENDAS})});
    for(;;){ const j = await api("/job/" + r.job);
      if(j.estado === "listo"){ $("#estado").textContent = ""; const v = j.resultado || {}; $("#salida").innerHTML = `<video src="${API}/mp4/${r.job}" controls playsinline autoplay></video>` + (v.dice ? `<p class="hint">Dice: “${esc(v.dice)}”</p>` : "") + revision(v) + (v.costo != null ? `<p class="hint">Costó ~US$${esc(v.costo)}</p>` : ""); lista(); break; }
      if(j.estado === "error") throw new Error(j.error || "Falló");
      $("#estado").innerHTML = `<span class="spin"></span>${esc(j.paso || "En cola…")}`; await new Promise(res => setTimeout(res, 5000)); } }
  catch(e){ $("#estado").textContent = "Falló: " + e.message; } b.disabled = false; };
(async () => {
  CFG = await api("/config");
  $("#motor").innerHTML = Object.entries(CFG.motores).map(([k, v]) => `<option value="${k}" ${k === CFG.motor_default ? "selected" : ""}>${esc(v.label)} · ~US$${v.costo}</option>`).join("");
  $("#lugar").innerHTML = Object.entries(CFG.lugares).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  $("#seg").innerHTML = CFG.duraciones.map(s => `<option value="${s}" ${s === 8 ? "selected" : ""}>${s} s</option>`).join("");
  $("#voz").innerHTML = '<option value="">La del personaje</option>' + CFG.voces.mujer.map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  $("#tono").innerHTML = CFG.tonos.map(t => `<option value="${t}" ${t === "cercana" ? "selected" : ""}>${esc(t)}</option>`).join("");
  $("#energia").innerHTML = Object.entries(CFG.energias).map(([k, v]) => `<option value="${k}" ${k === CFG.energia_default ? "selected" : ""}>${esc(v.nombre)}</option>`).join("");
  $("#aviso_claude").style.display = CFG.claude ? "none" : "";
  $("#accion").value = CFG.accion; if(!CFG.claude) $("#dice").value = CFG.dice; costo();
  try{ const pj = await (await fetch("/personajes/api/lista")).json(); $("#pid").innerHTML = (pj.personajes || []).map(p => `<option value="${esc(p.id)}">${esc(p.nombre)}</option>`).join(""); }catch(e){}
  if(new URLSearchParams(location.search).get("embed")){ const avisar = () => parent.postMessage({cambiosAlto: document.documentElement.scrollHeight, de: "filmado"}, "*"); new ResizeObserver(avisar).observe(document.body); }
  lista();
})();
</script></body></html>
"""
