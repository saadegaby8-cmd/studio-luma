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
                     max_tokens: int = 16000) -> Tuple[Dict[str, Any], float]:
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
            output_config={"effort": ESFUERZO},
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
