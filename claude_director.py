"""
Claude como DIRECTOR (Comerciales) y GUIONISTA / DIRECTORA DE ARTE (Reels).

Claude no genera imágenes ni video: lee el material (fotos, cuadros de video, el
producto) y escribe. En Studio Luma escribe lo mismo que escribía Gemini —la historia y
las tomas de un comercial, el guion de un reel, las preguntas de la directora de arte—
y los motores de siempre (Kling, Seedance, Seedream, OmniHuman…) filman.

La clave va en Railway como ANTHROPIC_API_KEY (también se acepta CLAUDE_API_KEY). Si
no está, o si Claude falla, quien llama cae a Gemini y lo dice.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

MODELO = os.getenv("CLAUDE_DIRECTOR_MODEL", "claude-opus-5-5")
# Profundidad del razonamiento de Opus 5.5 (su default es "medium"). Un comercial o un
# guion se piensan una vez y guían plata de video: "high" vale lo que cuesta.
ESFUERZO = os.getenv("CLAUDE_DIRECTOR_EFFORT", "high")
# Precio de Claude Opus 5.5 (US$ por millón de tokens): entrada y salida.
PRECIO_IN = float(os.getenv("CLAUDE_PRECIO_IN", "4.0"))
PRECIO_OUT = float(os.getenv("CLAUDE_PRECIO_OUT", "20.0"))
DIRECTORES = {
    "claude": "Claude Opus 5.5",
    "gemini": "Gemini",
}
DIRECTOR_DEFAULT = "claude"


class ClaudeNoDisponible(Exception):
    """Claude no pudo dirigir (sin clave, error de la API, se negó, no devolvió JSON).
    Quien llama cae a Gemini y muestra el motivo."""


def clave() -> str:
    return (os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY") or "").strip()


def disponible() -> bool:
    return bool(clave())


def partes_a_claude(parts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Las partes con formato Gemini ({"text"} / {"inlineData"}) al formato de Claude."""
    out: List[Dict[str, Any]] = []
    for p in parts:
        if p.get("text"):
            out.append({"type": "text", "text": str(p["text"])})
            continue
        inline = p.get("inlineData") or p.get("inline_data")
        if inline and inline.get("data"):
            out.append({"type": "image",
                        "source": {"type": "base64",
                                   "media_type": inline.get("mimeType") or inline.get("mime_type")
                                   or "image/jpeg",
                                   "data": inline["data"]}})
    return out


def _leer_json(raw: str) -> Dict[str, Any]:
    raw = re.sub(r"^```(json)?|```$", "", (raw or "").strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, flags=re.S)
        if not m:
            raise ClaudeNoDisponible("Claude no devolvió un JSON legible.")
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            raise ClaudeNoDisponible("Claude no devolvió un JSON legible.")
    if not isinstance(data, dict):
        raise ClaudeNoDisponible("Claude devolvió algo que no es un objeto JSON.")
    return data


async def _crear(**kwargs: Any) -> Any:
    """La llamada a la API (aparte, para poder simularla en las pruebas)."""
    import anthropic
    cliente = anthropic.AsyncAnthropic(api_key=clave(), timeout=300.0, max_retries=2)
    return await cliente.beta.messages.create(**kwargs)


async def pedir_json(system: str, parts: List[Dict[str, Any]],
                     max_tokens: int = 16000, esfuerzo: str = "") -> Tuple[Dict[str, Any], float]:
    """Le pide a Claude una respuesta en JSON. `parts` puede traer texto e imágenes (en
    formato Gemini o Claude). Devuelve (datos, costo en US$). Levanta ClaudeNoDisponible
    si no hay clave, si la API falla, si se niega o si no devolvió JSON."""
    if not clave():
        raise ClaudeNoDisponible("Falta ANTHROPIC_API_KEY en Railway.")
    contenido = partes_a_claude(parts) if any("type" not in p for p in parts) else parts
    try:
        import anthropic
    except ImportError:
        raise ClaudeNoDisponible("Falta instalar el paquete 'anthropic' (requirements.txt).")
    try:
        r = await _crear(
            model=MODELO,
            max_tokens=max_tokens,
            system=(system + "\n\nRespondé SOLO con el JSON pedido: sin texto antes ni después, "
                    "sin markdown."),
            messages=[{"role": "user", "content": contenido}],
            output_config={"effort": esfuerzo or ESFUERZO},
            # Si el filtro de Claude se niega (fotos de lencería, por ejemplo), la API
            # reintenta sola con el modelo de respaldo que corresponde a ese motivo.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError:
        raise ClaudeNoDisponible("La ANTHROPIC_API_KEY cargada no es válida.")
    except anthropic.PermissionDeniedError as e:
        raise ClaudeNoDisponible(f"La clave de Anthropic no tiene permiso: {str(e)[:160]}")
    except anthropic.RateLimitError:
        raise ClaudeNoDisponible("Anthropic está limitando los pedidos (probá en un rato).")
    except anthropic.APIStatusError as e:
        raise ClaudeNoDisponible(f"Anthropic devolvió error {e.status_code}: {str(e)[:200]}")
    except anthropic.APIConnectionError:
        raise ClaudeNoDisponible("No pude conectar con Anthropic.")
    if getattr(r, "stop_reason", "") == "refusal":
        raise ClaudeNoDisponible("Claude no quiso dirigir este material.")
    texto = "".join(getattr(b, "text", "") for b in (r.content or [])
                    if getattr(b, "type", "") == "text")
    if getattr(r, "stop_reason", "") == "max_tokens" and not texto.strip().endswith("}"):
        raise ClaudeNoDisponible("La respuesta de Claude quedó cortada.")
    u = getattr(r, "usage", None)
    costo = 0.0
    if u is not None:
        entrada = (int(getattr(u, "input_tokens", 0) or 0)
                   + int(getattr(u, "cache_creation_input_tokens", 0) or 0)
                   + int(getattr(u, "cache_read_input_tokens", 0) or 0))
        costo = (entrada * PRECIO_IN + int(getattr(u, "output_tokens", 0) or 0) * PRECIO_OUT) / 1e6
    return _leer_json(texto), round(costo, 4)


def elegido(v: Optional[str]) -> str:
    v = str(v or "").strip().lower()
    return v if v in DIRECTORES else DIRECTOR_DEFAULT


# ─────────────────────────────────────────────────────────────────────────────
# CLAUDE Y SEEDREAM: escribe el pedido y revisa la foto
# ─────────────────────────────────────────────────────────────────────────────
# Seedream obedece mejor un pedido corto, en inglés y con la toma PRIMERO. Los prompts de
# la app son largos (identidad, prenda, realismo, contexto) y la toma quedaba enterrada:
# Seedream agarraba lo que quería. Claude lo reordena sin perder las reglas, y después
# mira la foto y dice si cumplió; si no, la app la pide de nuevo con la corrección.

_SYSTEM_REESCRIBIR = (
    "You write prompts for Seedream (ByteDance), a multi-reference image EDITING model. You get "
    "the app's long prompt (with its rules and the roles of each reference image) and the "
    "OWNER'S REQUEST, written by the owner of a lingerie and clothing brand. Rewrite it as ONE "
    "concise English prompt of at most 230 words, in this order:\n"
    "1) THE SHOT the owner asked for, first and literal: pose, action, where the hands are, "
    "framing, camera angle and distance, place and light. Concrete visual words. If anything "
    "in the long prompt contradicts the owner's request, THE OWNER WINS.\n"
    "2) Which reference image is what, keeping EXACTLY the same image numbers as the long prompt.\n"
    "3) Her BODY, if the long prompt describes it (height, build, bust, waist, hips, glutes): "
    "keep it with the SAME meaning and the same sizes, in neutral catalogue words, and say she "
    "must not be slimmed or idealised. Never drop it and never make it smaller.\n"
    "4) Identity: the same exact person as the face reference, not a lookalike.\n"
    "5) Garment fidelity: copy the garment of the product photo(s) exactly (design, colour, "
    "fabric, straps, trims).\n"
    "6) Realism and look, in one short sentence.\n"
    "Keep every hard rule of the long prompt (one person, no text, no logos, no copying the pose "
    "of a reference). Underwear is always an e-commerce catalogue photo: never add sexual words "
    "(the body description above is not sexual: keep it). Do not invent things the owner did not "
    "ask for. Answer in JSON: "
    '{"prompt": "..."}'
)

_SYSTEM_REVISAR = (
    "You are the strict quality checker of a photo studio for a lingerie and clothing brand. "
    "The FIRST image is the photo an AI model just generated. The next images are the "
    "references: first the face of the model, then the real product photo(s). Check the "
    "generated photo against the OWNER'S REQUEST, in this order of importance:\n"
    "1) The shot: pose, action, hands, framing, camera angle, place and light that she asked for.\n"
    "2) The garment: same design, colour, fabric and details as the product photos.\n"
    "3) The body: if the request describes her body (height, build, bust, hips, glutes), the "
    "photo shows THAT body, not a slimmer standard model.\n"
    "4) The face: recognisably the same person as the face reference.\n"
    "5) Anatomy: hands, fingers, arms and legs correct; realistic skin, not plastic.\n"
    "Give a score from 0 to 10 (8 or more = it complies). Answer in JSON: "
    '{"puntaje": 0-10, "fallas": ["short sentences IN SPANISH (Rioplatense) for the owner, '
    'only what is wrong"], "correccion": "an English instruction for the image model that '
    'fixes ONLY what failed, concrete and visual; empty if nothing failed"}'
)


async def reescribir_prompt(prompt: str, pedido: str) -> Tuple[str, float]:
    """El pedido para Seedream, corto, en inglés y con la toma de la dueña primero.
    Levanta ClaudeNoDisponible si no puede (quien llama sigue con el prompt de siempre)."""
    data, costo = await pedir_json(
        _SYSTEM_REESCRIBIR,
        [{"type": "text", "text": f"OWNER'S REQUEST (in Spanish):\n{pedido}\n\n"
                                  f"APP'S LONG PROMPT:\n{prompt}"}],
        max_tokens=6000, esfuerzo="medium")
    nuevo = str(data.get("prompt") or "").strip()
    if len(nuevo) < 40:
        raise ClaudeNoDisponible("Claude no devolvió un pedido usable.")
    return nuevo, costo


async def revisar_foto(foto_b64: str, pedido: str, cara_b64: str = "",
                       prendas_b64: Optional[List[str]] = None) -> Tuple[Dict[str, Any], float]:
    """Claude mira la foto y dice si cumple el pedido. Devuelve ({puntaje, fallas,
    correccion, cumple}, costo). Levanta ClaudeNoDisponible si no puede."""
    parts: List[Dict[str, Any]] = [
        {"type": "text", "text": f"OWNER'S REQUEST (in Spanish):\n{pedido}\n\nGENERATED PHOTO:"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": foto_b64}},
    ]
    if cara_b64:
        parts += [{"type": "text", "text": "FACE REFERENCE:"},
                  {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                               "data": cara_b64}}]
    for k, b in enumerate((prendas_b64 or [])[:2]):
        parts += [{"type": "text", "text": f"PRODUCT PHOTO {k + 1}:"},
                  {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                               "data": b}}]
    data, costo = await pedir_json(_SYSTEM_REVISAR, parts, max_tokens=6000, esfuerzo="medium")
    try:
        puntaje = max(0, min(10, int(round(float(data.get("puntaje"))))))
    except (TypeError, ValueError):
        raise ClaudeNoDisponible("Claude no dio un puntaje.")
    fallas = [str(x).strip()[:200] for x in (data.get("fallas") or []) if str(x).strip()][:6]
    corr = str(data.get("correccion") or "").strip()[:800]
    return {"puntaje": puntaje, "fallas": fallas, "correccion": corr,
            "cumple": puntaje >= 8}, costo


def seedream_con_claude(settings: Dict[str, Any]) -> bool:
    """¿Claude escribe y revisa los pedidos de Seedream? (Ajustes → "Claude con Seedream")."""
    return disponible() and str(settings.get("claude_seedream", "si")).lower() not in (
        "no", "0", "off", "false")
