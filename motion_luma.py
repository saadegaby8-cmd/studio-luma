# -*- coding: utf-8 -*-
"""
motion_luma.py — El "motion designer" de Studio Luma
====================================================

Lo que hace que un video se vea de agencia y no armado a mano, en UN solo lugar
para que lo usen igual Comerciales, Reels, Filmado y Videos:

- BEATS: escucha la música y encuentra los golpes (sin librerías: ffmpeg decodifica
  y la cuenta se hace acá). Los cortes caen en el beat.
- UNIR CON TRANSICIONES: cada corte con la suya (corte seco, fundido, whip pan,
  zoom, deslizar, flash, glitch, negro). Se arma por PEDAZOS —cuerpo de cada toma
  y el pedacito de transición— y se pegan al final: ffmpeg nunca tiene abiertos
  más de dos videos a la vez, así no se come la memoria del servidor.
- RAMPA DE VELOCIDAD: la toma arranca rápida y frena en cámara lenta.
- TEXTOS ANIMADOS (ASS, lo dibuja libass): fundido, sube enmascarado, máquina de
  escribir, palabra por palabra, rebote, tracking de cine, deslizar, sticker, y
  el texto "kinetic" (una palabra por beat).
- LOGO: en una esquina todo el video, y la PLACA FINAL animada (el logo se arma,
  entra el texto).
- ESTILOS: paquetes que deciden todo junto (Película, Energía, Editorial, UGC).

Las tipografías están en fuentes/ (licencia OFL, ver fuentes/LICENCIAS.txt) con
nombres propios ("LumaImpacto"…), así libass las encuentra sin depender de las
fuentes que tenga instaladas el servidor.
"""

import array
import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

VERSION = "1.0.0"

FUENTES_DIR = Path(__file__).resolve().parent / "fuentes"

# clave → (familia dentro del TTF, lo que se lee en la pantalla)
FUENTES: Dict[str, Tuple[str, str]] = {
    "impacto": ("LumaImpacto", "Impacto · bloque condensado (deporte, energía)"),
    "condensada": ("LumaCondensada", "Condensada · alta y fina (moderna, limpia)"),
    "elegante": ("LumaElegante", "Elegante · serif de revista"),
    "elegante_italica": ("LumaEleganteItalica", "Elegante itálica · serif suave"),
    "moderna": ("LumaModerna", "Moderna · sans negrita (campaña)"),
    "limpia": ("LumaLimpia", "Limpia · sans liviana (subtítulos, UGC)"),
}


def fuente_path(clave: str) -> Optional[Path]:
    fam = FUENTES.get(clave, FUENTES["moderna"])[0]
    p = FUENTES_DIR / f"{fam}.ttf"
    return p if p.exists() else None


# ─────────────────────────────────────────────────────────────────────────────
# ffmpeg
# ─────────────────────────────────────────────────────────────────────────────

def ffmpeg_bin() -> Optional[str]:
    sistema = shutil.which("ffmpeg")
    if sistema:
        return sistema
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def ff(args: List[str], timeout: int = 600, cwd: Optional[Path] = None) -> Tuple[bool, str]:
    b = ffmpeg_bin()
    if not b:
        return False, "no hay ffmpeg"
    try:
        r = subprocess.run([b, "-y", "-hide_banner", "-loglevel", "error"] + args,
                           capture_output=True, timeout=timeout, cwd=str(cwd) if cwd else None)
        if r.returncode == 0:
            return True, ""
        err = (r.stderr or b"").decode(errors="replace")[-500:]
        print(f"[motion] ffmpeg falló: {err}")
        return False, err
    except Exception as e:
        return False, str(e)[:300]


def duracion(p: Path) -> float:
    b = ffmpeg_bin()
    if not b or not Path(p).exists():
        return 0.0
    try:
        r = subprocess.run([b, "-i", str(p)], capture_output=True, text=True, timeout=30)
        m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", r.stderr)
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        pass
    return 0.0


def dims(formato: str) -> Tuple[int, int]:
    return (1080, 1920) if formato == "9:16" else (1920, 1080)


_ENC = ["-c:v", "libx264", "-preset", "fast", "-crf", "19", "-pix_fmt", "yuv420p", "-r", "24", "-an"]


# ─────────────────────────────────────────────────────────────────────────────
# BEATS
# ─────────────────────────────────────────────────────────────────────────────

_SR = 8000      # muestras por segundo para escuchar (alcanza para el bombo y el redoblante)
_HOP = 200      # 25 ms por cuadro de análisis → 40 cuadros por segundo
_FPS_A = _SR / _HOP


def _envolvente(musica: Path, max_seg: float) -> List[float]:
    """La "fuerza de ataque" de la música cuadro a cuadro: cuánto SUBE la energía (los
    golpes), con el grave pesando más (el bombo marca el pulso)."""
    b = ffmpeg_bin()
    if not b:
        return []
    r = subprocess.run([b, "-hide_banner", "-loglevel", "error", "-i", str(musica), "-t", f"{max_seg:.2f}",
                        "-ac", "1", "-ar", str(_SR), "-f", "s16le", "-"], capture_output=True, timeout=120)
    if r.returncode != 0 or not r.stdout:
        return []
    x = array.array("h")
    x.frombytes(r.stdout[: len(r.stdout) // 2 * 2])
    a = 1.0 - math.exp(-2 * math.pi * 150.0 / _SR)    # pasabajos de ~150 Hz: el bombo
    lp = 0.0
    e_full: List[float] = []
    e_low: List[float] = []
    sf = sl = 0.0
    n = 0
    for v in x:
        lp += a * (v - lp)
        sf += v * v
        sl += lp * lp
        n += 1
        if n == _HOP:
            e_full.append(sf)
            e_low.append(sl)
            sf = sl = 0.0
            n = 0
    if not e_full:
        return []
    onset = [0.0]
    for i in range(1, len(e_full)):
        df = math.log(e_full[i] + 1e3) - math.log(e_full[i - 1] + 1e3)
        dl = math.log(e_low[i] + 1e3) - math.log(e_low[i - 1] + 1e3)
        onset.append(max(df, 0.0) * 0.5 + max(dl, 0.0))
    return onset


def beats(musica: Path, max_seg: float = 180.0) -> Dict[str, Any]:
    """{"bpm", "beats": [s…], "fuertes": [s…]} — los beats de la música y, de esos, los
    "uno" de cada compás (los golpes fuertes). Vacío si no se pudo escuchar."""
    o = _envolvente(Path(musica), max_seg)
    if len(o) < _FPS_A * 4:
        return {"bpm": 0, "beats": [], "fuertes": []}
    media = sum(o) / len(o)
    oc = [v - media for v in o]
    # El tempo: la periodicidad más fuerte entre 70 y 180 BPM, con preferencia suave
    # por ~120 (si duda entre 60 y 120, casi siempre es 120).
    mejor, mejor_l = -1e18, 0
    lmin, lmax = int(_FPS_A * 60 / 180), int(_FPS_A * 60 / 70) + 1
    ac: Dict[int, float] = {}
    for lag in range(lmin, lmax + 1):
        s = 0.0
        for t in range(lag, len(oc)):
            s += oc[t] * oc[t - lag]
        bpm = 60.0 * _FPS_A / lag
        peso = math.exp(-0.5 * (math.log2(bpm / 120.0) / 0.9) ** 2)
        ac[lag] = s
        if s * peso > mejor:
            mejor, mejor_l = s * peso, lag
    # Afinar el período con una parábola sobre los vecinos.
    per = float(mejor_l)
    if mejor_l - 1 in ac and mejor_l + 1 in ac:
        y0, y1, y2 = ac[mejor_l - 1], ac[mejor_l], ac[mejor_l + 1]
        den = y0 - 2 * y1 + y2
        if den < 0:
            per = mejor_l + 0.5 * (y0 - y2) / den
    # La fase: dónde caen los golpes con ese período.
    pasos = 24
    mejor_f, fase = -1.0, 0.0
    for k in range(pasos):
        f = per * k / pasos
        s, t = 0.0, f
        while t < len(o):
            s += o[int(t)]
            t += per
        if s > mejor_f:
            mejor_f, fase = s, f
    marcas: List[float] = []
    t = fase
    while t < len(o):
        i = int(round(t))
        lo, hi = max(0, i - 2), min(len(o) - 1, i + 2)
        j = max(range(lo, hi + 1), key=lambda q: o[q])    # al golpe real más cercano
        marcas.append(j / _FPS_A)
        # El próximo se busca desde el golpe REAL (no desde la cuenta): así un tempo
        # apenas mal medido no se va corriendo a lo largo de la canción.
        t = (j if o[j] > 0 else t) + per
    # Los "uno" del compás: de las 4 fases posibles, la que junta más energía.
    mejor_c, c0 = -1.0, 0
    for c in range(4):
        s = sum(o[int(round(m * _FPS_A))] for m in marcas[c::4] if int(round(m * _FPS_A)) < len(o))
        if s > mejor_c:
            mejor_c, c0 = s, c
    return {"bpm": round(60.0 * _FPS_A / per, 1), "beats": [round(m, 3) for m in marcas],
            "fuertes": [round(m, 3) for m in marcas[c0::4]]}


# ─────────────────────────────────────────────────────────────────────────────
# TRANSICIONES y UNIR
# ─────────────────────────────────────────────────────────────────────────────

# clave → (transición de xfade, segundos, filtro extra sobre el pedacito, lo que se lee)
TRANSICIONES: Dict[str, Tuple[str, float, str, str]] = {
    "corte": ("", 0.0, "", "Corte seco"),
    "fundido": ("fade", 0.5, "", "Fundido"),
    "fundido_largo": ("fade", 1.0, "", "Fundido largo"),
    "negro": ("fadeblack", 0.6, "", "Pasa por negro"),
    "whip": ("smoothleft", 0.28, "gblur=sigma=30:sigmaV=0.01", "Whip pan (barrido)"),
    "whip_arriba": ("smoothup", 0.28, "gblur=sigma=0.01:sigmaV=30", "Whip hacia arriba"),
    "zoom": ("zoomin", 0.35, "", "Zoom de entrada"),
    "desliza": ("slideleft", 0.35, "", "Desliza"),
    "flash": ("fadewhite", 0.22, "", "Flash de luz"),
    "glitch": ("pixelize", 0.25, "rgbashift=rh=-14:bh=14", "Glitch"),
    "cortina": ("circleopen", 0.45, "", "Círculo que se abre"),
}


def _fit(w: int, h: int) -> str:
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps=24,format=yuv420p"


def planear_cortes(disponible: Sequence[float], trans: Sequence[str], marcas: Sequence[float] = (),
                   inicio: float = 0.0, minimo: float = 0.7, max_recorte: float = 0.45
                   ) -> List[Tuple[float, float]]:
    """Dónde empieza y cuánto dura (en el clip) cada toma, para que los cortes caigan en
    las `marcas` (los beats, en el tiempo del video final; `inicio` es dónde arranca
    este cuerpo en ese tiempo). Una toma sólo se ACORTA (nunca se inventa video que
    no hay) y nunca pierde más de `max_recorte` de lo que dura. Devuelve, por toma,
    (segundo del clip donde arranca lo visible, duración en el clip)."""
    n = len(disponible)
    d = [TRANSICIONES.get(trans[k] if k < len(trans) else "corte", TRANSICIONES["corte"])[1]
         for k in range(max(n - 1, 0))]
    out: List[Tuple[float, float]] = []
    S = inicio          # dónde arranca la toma k en el video
    for k in range(n):
        dk_prev = d[k - 1] if k > 0 else 0.0
        if k == n - 1:
            out.append((0.0, disponible[k]))
            break
        dk = min(d[k], disponible[k] / 3.0, disponible[k + 1] / 3.0)
        natural = S + disponible[k] - dk / 2.0                  # el corte si no se toca
        lo = max(S + minimo, S + disponible[k] * (1 - max_recorte)) - dk / 2.0
        cand = [m for m in marcas if lo <= m <= natural + 0.03]
        T = max(cand) if cand else natural
        largo = T + dk / 2.0 - S
        out.append((0.0, max(largo, 0.2)))
        S = T - dk / 2.0
        _ = dk_prev
    return out


def unir(clips: Sequence[Path], salida: Path, formato: str, trans: Sequence[str] = (),
         marcas: Sequence[float] = (), inicio: float = 0.0) -> bool:
    """Une las tomas, cada corte con su transición (`trans[k]` va entre la toma k y la
    k+1; si falta, corte seco), y con los cortes en los beats si vienen `marcas`.
    Se arma por pedazos (cuerpo, transición, cuerpo…) que se pegan al final."""
    clips = [Path(c) for c in clips]
    if not clips:
        return False
    w, h = dims(formato)
    disp = [max(duracion(c), 0.2) for c in clips]
    plan = planear_cortes(disp, trans, marcas, inicio)
    tmp = salida.parent / (salida.stem + "_pz")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    pedazos: List[Path] = []
    n = len(clips)
    dur_t = []
    for k in range(n - 1):
        clave = trans[k] if k < len(trans) and trans[k] in TRANSICIONES else "corte"
        dk = TRANSICIONES[clave][1]
        dk = min(dk, plan[k][1] / 3.0, plan[k + 1][1] / 3.0) if dk > 0 else 0.0
        dur_t.append((clave, dk))
    for k, c in enumerate(clips):
        ini, largo = plan[k]
        cab = dur_t[k - 1][1] if k > 0 else 0.0           # lo que se come la transición de antes
        cola = dur_t[k][1] if k < n - 1 else 0.0           # y la de después
        a, b = ini + cab, ini + largo - cola
        if b - a > 0.04:
            p = tmp / f"c{k:02d}.mp4"
            ok, _ = ff(["-ss", f"{a:.3f}", "-i", str(c), "-t", f"{b - a:.3f}", "-vf", _fit(w, h)] + _ENC + [str(p)], 600)
            if not ok:
                return False
            pedazos.append(p)
        if k < n - 1 and dur_t[k][1] > 0:
            clave, dk = dur_t[k]
            xf, _, extra, _ = TRANSICIONES[clave]
            nxt = clips[k + 1]
            ini2 = plan[k + 1][0]
            p = tmp / f"t{k:02d}.mp4"
            fil = (f"[0:v]{_fit(w, h)}[a];[1:v]{_fit(w, h)}[b];"
                   f"[a][b]xfade=transition={xf}:duration={dk:.3f}:offset=0"
                   + (f",{extra}" if extra else "") + ",format=yuv420p[v]")
            ok, _ = ff(["-ss", f"{ini + largo - dk:.3f}", "-t", f"{dk:.3f}", "-i", str(c),
                        "-ss", f"{ini2:.3f}", "-t", f"{dk:.3f}", "-i", str(nxt),
                        "-filter_complex", fil, "-map", "[v]"] + _ENC + [str(p)], 300)
            if not ok:
                return False
            pedazos.append(p)
    lista = tmp / "lista.txt"
    lista.write_text("".join(f"file '{p.name}'\n" for p in pedazos), encoding="utf-8")
    ok, _ = ff(["-f", "concat", "-safe", "0", "-i", lista.name, "-c", "copy",
                str(salida.resolve())], 600, cwd=tmp)
    esperado = sum(duracion(p) for p in pedazos)
    if not ok or duracion(salida) < esperado - 0.6:
        ok, _ = ff(["-f", "concat", "-safe", "0", "-i", lista.name] + _ENC + ["-movflags", "+faststart",
                    str(salida.resolve())], 900, cwd=tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    return ok and salida.exists() and duracion(salida) >= esperado - 0.6


def tiempos_de_corte(clips_dur: Sequence[float], trans: Sequence[str], marcas: Sequence[float] = (),
                     inicio: float = 0.0) -> List[float]:
    """Dónde quedan los cortes en el video unido (para ubicar textos en la toma justa)."""
    plan = planear_cortes(clips_dur, trans, marcas, inicio)
    out, S = [], inicio
    for k in range(len(plan) - 1):
        dk = TRANSICIONES.get(trans[k] if k < len(trans) else "corte", TRANSICIONES["corte"])[1]
        dk = min(dk, plan[k][1] / 3.0, plan[k + 1][1] / 3.0) if dk > 0 else 0.0
        T = S + plan[k][1] - dk / 2.0
        out.append(round(T, 3))
        S = T - dk / 2.0
    return out


# ─────────────────────────────────────────────────────────────────────────────
# RAMPA DE VELOCIDAD
# ─────────────────────────────────────────────────────────────────────────────

def rampa(src: Path, dst: Path, formato: str, seg: float, punto: float = 0.45,
          rapido: float = 2.0, lento: float = 0.45) -> bool:
    """Arranca rápida (×`rapido`) y en `punto` (fracción del clip) frena a cámara lenta
    (×`lento`): el golpe de las campañas de deporte. Sale de `seg` segundos; los cuadros
    que faltan en la parte lenta se mezclan para que no salte."""
    D = duracion(src)
    if D <= 0.3:
        return False
    w, h = dims(formato)
    p = D * punto
    fil = (f"[0:v]split=2[x][y];"
           f"[x]trim=0:{p:.3f},setpts=(PTS-STARTPTS)/{rapido:.3f}[a];"
           f"[y]trim={p:.3f}:{D:.3f},setpts=(PTS-STARTPTS)/{lento:.3f},framerate=fps=24[b];"
           f"[a][b]concat=n=2:v=1:a=0,{_fit(w, h)}[v]")
    ok, _ = ff(["-i", str(src), "-filter_complex", fil, "-map", "[v]", "-t", f"{seg:.3f}"] + _ENC + [str(dst)], 600)
    return ok and dst.exists() and duracion(dst) > 0.3


# ─────────────────────────────────────────────────────────────────────────────
# TEXTOS ANIMADOS (ASS)
# ─────────────────────────────────────────────────────────────────────────────

ANIMACIONES: Dict[str, str] = {
    "fundido": "Fundido (aparece suave)",
    "sube": "Sube enmascarado (sale desde abajo, como recortado)",
    "escribe": "Máquina de escribir (letra por letra)",
    "palabras": "Palabra por palabra",
    "rebote": "Rebote (entra con escala y rebota)",
    "tracking": "Tracking de cine (las letras se juntan despacio)",
    "desliza": "Desliza (entra de costado)",
    "sticker": "Sticker (cartel que salta)",
}


def _t(s: float) -> str:
    s = max(s, 0.0)
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    return f"{h}:{m:02d}:{s % 60:05.2f}"


def _color(hex_: str, alpha: int = 0) -> str:
    """'#RRGGBB' → '&HAABBGGRR' (ASS va al revés)."""
    v = (hex_ or "#FFFFFF").lstrip("#")
    if len(v) != 6:
        v = "FFFFFF"
    return f"&H{alpha:02X}{v[4:6]}{v[2:4]}{v[0:2]}".upper()


def _esc(txt: str) -> str:
    return (str(txt or "").replace("\\", "/").replace("{", "(").replace("}", ")")
            .replace("\n", "\\N"))


def _pos(pos: Any, w: int, h: int) -> Tuple[int, int]:
    if isinstance(pos, (list, tuple)) and len(pos) == 2:
        return int(float(pos[0]) * w), int(float(pos[1]) * h)
    return {"arriba": (w // 2, int(h * 0.20)), "abajo": (w // 2, int(h * 0.80)),
            "centro_bajo": (w // 2, int(h * 0.62)), "abajo_izq": (int(w * 0.30), int(h * 0.82))
            }.get(str(pos), (w // 2, h // 2))


def _ancho_texto(txt: str, clave: str, fs: int, esp: float) -> float:
    """Cuánto mide el texto en píxeles (con el espaciado entre letras)."""
    try:
        from PIL import ImageFont
        fp = fuente_path(clave)
        f = ImageFont.truetype(str(fp), fs) if fp else ImageFont.load_default()
        return f.getlength(txt) + esp * max(len(txt) - 1, 0)
    except Exception:
        return fs * 0.55 * len(txt) + esp * max(len(txt) - 1, 0)


def _evento(item: Dict[str, Any], w: int, h: int) -> List[str]:
    """Las líneas de evento de UN texto según su animación."""
    txt = _esc(item.get("texto", "")).strip()
    if not txt:
        return []
    ini, fin = float(item.get("ini", 0)), float(item.get("fin", 2))
    if fin - ini < 0.3:
        fin = ini + 0.3
    dur_ms = int((fin - ini) * 1000)
    anim = item.get("anim", "fundido")
    fam = FUENTES.get(item.get("fuente", "moderna"), FUENTES["moderna"])[0]
    fs = int(h * float(item.get("tam", 0.06)))
    col = _color(item.get("color", "#FFFFFF"))
    sombra = item.get("sombra", True)
    x, y = _pos(item.get("pos", "centro"), w, h)
    esp = float(item.get("espaciado", 0))
    # Que entre a lo ancho, contando lo que se abre el tracking al principio.
    extra_tr = fs * 0.18 if anim == "tracking" else 0.0
    plano = txt.replace("\\N", " ")
    ancho = _ancho_texto(plano, item.get("fuente", "moderna"), fs, esp + extra_tr)
    maximo = w * 0.88
    if ancho > maximo:
        k = maximo / ancho
        fs = max(int(fs * k), 12)
        esp *= k
        extra_tr *= k
    base = (f"\\an5\\fn{fam}\\fs{fs}\\c{col}\\3c&H000000&\\bord{max(1, fs // 28) if sombra else 0}"
            f"\\shad{max(1, fs // 22) if sombra else 0}\\4a&H90&\\fsp{esp:.0f}")
    sal = min(450, dur_ms // 3)
    tags = ""
    texto = txt
    if anim == "fundido":
        tags = f"\\pos({x},{y})\\fad({min(500, dur_ms // 3)},{sal})"
    elif anim == "sube":
        alto = int(fs * 0.75)
        tags = (f"\\move({x},{y + int(fs * 1.1)},{x},{y},0,520)\\clip(0,{y - alto},{w},{y + alto})"
                f"\\fad(0,{sal})")
    elif anim == "desliza":
        tags = f"\\move({x - int(w * 0.35)},{y},{x},{y},0,420)\\fad(200,{sal})"
    elif anim == "rebote":
        tags = (f"\\pos({x},{y})\\fscx30\\fscy30\\t(0,170,\\fscx118\\fscy118)"
                f"\\t(170,320,\\fscx100\\fscy100)\\fad(100,{sal})")
    elif anim == "tracking":
        tags = (f"\\pos({x},{y})\\fsp{esp + extra_tr:.0f}\\t(0,{min(1800, dur_ms)},0.6,\\fsp{esp:.0f})"
                f"\\fad({min(700, dur_ms // 3)},{sal})")
    elif anim == "sticker":
        tags = (f"\\pos({x},{y})\\frz-4\\bord{max(6, fs // 4)}\\3c{_color(item.get('fondo', '#E8C9B8'))}"
                f"\\shad0\\fscx20\\fscy20\\t(0,150,\\fscx112\\fscy112)\\t(150,260,\\fscx100\\fscy100)\\fad(0,{sal})")
    elif anim in ("escribe", "palabras"):
        # Karaoke \ko: lo que todavía no "sonó" va en el color secundario, que es
        # transparente; así aparece de a una letra (o palabra) sin moverse el resto.
        partes = list(txt) if anim == "escribe" else re.split(r"(\s+)", txt)
        visibles = [p for p in partes if p.strip()]
        total_cs = max(int(min(dur_ms * 0.55, (45 if anim == "escribe" else 260) * len(visibles)) / 10), 1)
        paso = max(total_cs // max(len(visibles), 1), 1)
        trozos = []
        for p in partes:
            trozos.append(f"{{\\ko{paso if p.strip() else 0}}}{p}")
        texto = "".join(trozos)
        tags = f"\\pos({x},{y})\\2a&HFF&\\fad(0,{sal})"
    else:
        tags = f"\\pos({x},{y})\\fad(300,{sal})"
    return [f"Dialogue: 0,{_t(ini)},{_t(fin)},L,,0,0,0,,{{{base}{tags}}}{texto}"]


def ass_documento(w: int, h: int, items: Sequence[Dict[str, Any]]) -> str:
    cab = ("[Script Info]\nScriptType: v4.00+\nWrapStyle: 2\nScaledBorderAndShadow: yes\n"
           f"PlayResX: {w}\nPlayResY: {h}\n\n[V4+ Styles]\n"
           "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
           "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
           "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
           "Style: L,LumaModerna,60,&H00FFFFFF,&HFF000000,&H00000000,&H90000000,0,0,0,0,100,100,0,0,1,2,2,5,"
           f"{int(w * 0.06)},{int(w * 0.06)},0,1\n\n[Events]\n"
           "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    ev: List[str] = []
    for it in items:
        ev += _evento(it, w, h)
    return cab + "\n".join(ev) + "\n"


def quemar_textos(src: Path, dst: Path, items: Sequence[Dict[str, Any]], formato: str) -> bool:
    """Dibuja los textos animados sobre el video. Si no hay textos, no hace nada (False)."""
    items = [i for i in items if str(i.get("texto") or "").strip()]
    if not items:
        return False
    w, h = dims(formato)
    ass = dst.parent / (dst.stem + "_textos.ass")
    ass.write_text(ass_documento(w, h, items), encoding="utf-8")
    ok, _ = ff(["-i", str(Path(src).resolve()), "-vf", f"ass={ass.name}:fontsdir={FUENTES_DIR}",
                "-c:v", "libx264", "-preset", "fast", "-crf", "19", "-pix_fmt", "yuv420p", "-an",
                str(Path(dst).resolve())], 900, cwd=ass.parent)
    return ok and dst.exists() and duracion(dst) >= duracion(src) - 0.3


def titulo_items(titulo: str, arriba: str, sub: str, ini: float, fin: float, anim: str,
                 fuente: str, color: str = "#FFFFFF", pos: Any = "centro") -> List[Dict[str, Any]]:
    """Un título de campaña de tres renglones (chico arriba, grande, chico abajo), los
    tres con la misma animación y un poquito escalonados."""
    x, y = (0.5, 0.5) if pos == "centro" else (0.5, 0.78 if pos == "abajo" else 0.22)
    chica = "limpia" if fuente in ("moderna", "impacto", "condensada") else "elegante_italica"
    out = []
    if arriba:
        out.append({"texto": arriba.upper(), "ini": ini, "fin": fin, "anim": "tracking" if anim == "tracking" else "fundido",
                    "fuente": chica, "tam": 0.022, "espaciado": 10, "color": color, "pos": (x, y - 0.065)})
    out.append({"texto": titulo, "ini": ini + 0.15, "fin": fin, "anim": anim, "fuente": fuente,
                "tam": 0.085 if fuente in ("impacto", "condensada") else 0.07, "color": color, "pos": (x, y),
                "espaciado": 4 if fuente in ("elegante", "elegante_italica") else 0})
    if sub:
        out.append({"texto": sub.upper(), "ini": ini + 0.45, "fin": fin, "anim": "fundido", "fuente": chica,
                    "tam": 0.02, "espaciado": 12, "color": color, "pos": (x, y + 0.07)})
    return out


def kinetic_items(palabras: Sequence[str], marcas: Sequence[float], desde: float, hasta: float,
                  fuente: str = "impacto", cada: int = 2, color: str = "#FFFFFF") -> List[Dict[str, Any]]:
    """Texto kinetic: una palabra (o frase corta) por golpe, grande y con rebote, desde
    `desde` hasta `hasta`. Sin beats, cada 0,9 s."""
    palabras = [p.strip() for p in palabras if p and p.strip()]
    if not palabras:
        return []
    ms = [m for m in marcas if desde <= m <= hasta][::max(cada, 1)]
    if len(ms) < len(palabras):
        paso = 0.9
        ms = [desde + i * paso for i in range(len(palabras) + 1)]
    out = []
    for i, p in enumerate(palabras):
        ini = ms[i]
        fin = ms[i + 1] if i + 1 < len(ms) else ini + 0.9
        out.append({"texto": p.upper(), "ini": ini, "fin": min(fin, hasta + 0.5), "anim": "rebote",
                    "fuente": fuente, "tam": 0.075, "color": color, "pos": "centro_bajo"})
    return out


# ─────────────────────────────────────────────────────────────────────────────
# LOGO y PLACA FINAL ANIMADA
# ─────────────────────────────────────────────────────────────────────────────

ESQUINAS = {"abajo_der": "Abajo a la derecha", "abajo_izq": "Abajo a la izquierda",
            "arriba_der": "Arriba a la derecha", "arriba_izq": "Arriba a la izquierda"}


def con_logo(src: Path, dst: Path, logo: Path, formato: str, esquina: str = "abajo_der",
             ancho: float = 0.16, opacidad: float = 0.85) -> bool:
    """Tu logo chico en una esquina durante todo el video (aparece con fundido)."""
    if not Path(logo).exists():
        return False
    w, h = dims(formato)
    lw = int(w * ancho) // 2 * 2
    m = int(w * 0.05)
    x = m if esquina.endswith("izq") else f"W-w-{m}"
    y = int(h * 0.04) if esquina.startswith("arriba") else f"H-h-{int(h * 0.04)}"
    fil = (f"[1:v]scale={lw}:-1,format=rgba,colorchannelmixer=aa={opacidad:.2f},"
           f"fade=t=in:st=0.3:d=0.8:alpha=1[lg];[0:v][lg]overlay={x}:{y}:shortest=0:eof_action=repeat,format=yuv420p[v]")
    ok, _ = ff(["-i", str(src), "-loop", "1", "-framerate", "24", "-i", str(logo), "-filter_complex", fil,
                "-map", "[v]", "-t", f"{duracion(src):.3f}", "-c:v", "libx264", "-preset", "fast", "-crf", "19",
                "-pix_fmt", "yuv420p", "-an", str(dst)], 900)
    return ok and dst.exists() and duracion(dst) >= duracion(src) - 0.3


def placa_animada(dst: Path, formato: str, texto: str, sub: str = "", logo: Optional[Path] = None,
                  fuente: str = "elegante", fondo: str = "#000000", color: str = "#FFFFFF",
                  seg: float = 3.0, arriba: str = "") -> bool:
    """La placa final: el logo entra creciendo y con fundido, después el texto con
    tracking y la línea chica. Sin logo, el texto es el protagonista."""
    w, h = dims(formato)
    items: List[Dict[str, Any]] = []
    hay_logo = bool(logo and Path(logo).exists())
    y_txt = 0.60 if hay_logo else 0.48
    if texto:
        items.append({"texto": texto, "ini": 0.5 if hay_logo else 0.2, "fin": seg, "anim": "tracking",
                      "fuente": fuente, "tam": 0.055 if hay_logo else 0.075, "color": color,
                      "pos": (0.5, y_txt), "sombra": False, "espaciado": 6})
    if arriba:
        items.append({"texto": arriba.upper(), "ini": 0.3, "fin": seg, "anim": "fundido", "fuente": "limpia",
                      "tam": 0.019, "color": color, "pos": (0.5, y_txt - 0.06), "sombra": False, "espaciado": 10})
    if sub:
        items.append({"texto": sub, "ini": 1.0, "fin": seg, "anim": "fundido", "fuente": "limpia",
                      "tam": 0.021, "color": color, "pos": (0.5, y_txt + 0.06), "sombra": False, "espaciado": 4})
    ass = dst.parent / (dst.stem + "_placa.ass")
    ass.write_text(ass_documento(w, h, items), encoding="utf-8")
    fondo_c = (fondo or "#000000").lstrip("#")
    args = ["-f", "lavfi", "-i", f"color=c=0x{fondo_c}:s={w}x{h}:r=24:d={seg:.2f}"]
    if hay_logo:
        lw = int(w * 0.42) // 2 * 2
        # El logo crece de 88 % a 100 % en el primer segundo (escala por cuadro).
        fil = (f"[1:v]format=rgba,scale=w='{lw}*(0.88+0.12*min(t/1.0,1))':h=-1:eval=frame,"
               f"fade=t=in:st=0:d=0.7:alpha=1[lg];"
               f"[0:v][lg]overlay=(W-w)/2:(H*0.42)-h/2:eval=frame:shortest=1,"
               f"ass={ass.name}:fontsdir={FUENTES_DIR},fade=t=out:st={seg - 0.4:.2f}:d=0.4,format=yuv420p[v]")
        args += ["-loop", "1", "-framerate", "24", "-t", f"{seg:.2f}", "-i", str(Path(logo).resolve()),
                 "-filter_complex", fil, "-map", "[v]"]
    else:
        args += ["-vf", f"ass={ass.name}:fontsdir={FUENTES_DIR},fade=t=out:st={seg - 0.4:.2f}:d=0.4,format=yuv420p"]
    ok, _ = ff(args + ["-t", f"{seg:.2f}"] + _ENC + [str(Path(dst).resolve())], 300, cwd=ass.parent)
    return ok and dst.exists()


# ─────────────────────────────────────────────────────────────────────────────
# ESTILOS: todo junto con un toque
# ─────────────────────────────────────────────────────────────────────────────

ESTILOS: Dict[str, Dict[str, Any]] = {
    "ninguno": {"nombre": "Sin motion (como hasta ahora)"},
    "pelicula": {"nombre": "Película · cortes en el beat, títulos de cine, pasa por negro entre actos",
                 "transiciones": ["corte"], "entre_actos": "negro", "beat": True, "cada_beats": 2,
                 "anim_titulo": "tracking", "fuente": "elegante", "kinetic": False, "flash_fuertes": False},
    "energia": {"nombre": "Energía · cortes al beat, whips, zooms, flashes y texto kinetic",
                "transiciones": ["corte", "whip", "corte", "zoom", "flash", "corte", "glitch", "whip_arriba"],
                "entre_actos": "flash", "beat": True, "cada_beats": 1, "anim_titulo": "rebote",
                "fuente": "impacto", "kinetic": True, "flash_fuertes": True},
    "editorial": {"nombre": "Editorial · lento, fundidos, serif itálica que sube",
                  "transiciones": ["fundido"], "entre_actos": "fundido_largo", "beat": True, "cada_beats": 4,
                  "anim_titulo": "sube", "fuente": "elegante_italica", "kinetic": False, "flash_fuertes": False},
    "ugc": {"nombre": "UGC / Instagram · cortes y deslizar, textos palabra por palabra, stickers",
            "transiciones": ["corte", "desliza", "corte", "zoom"], "entre_actos": "desliza", "beat": True,
            "cada_beats": 2, "anim_titulo": "palabras", "fuente": "moderna", "kinetic": True, "flash_fuertes": False},
}


def transiciones_de(estilo: str, n_cortes: int, actos: Sequence[int] = ()) -> List[str]:
    """La transición de cada corte según el estilo: rota su lista y, donde cambia el
    acto (comienzo → acción → fin), pone la de "entre actos"."""
    e = ESTILOS.get(estilo) or {}
    lista = e.get("transiciones") or ["corte"]
    out = []
    for k in range(n_cortes):
        t = lista[k % len(lista)]
        if actos and k + 1 < len(actos) and actos[k] and actos[k + 1] and actos[k] != actos[k + 1]:
            t = e.get("entre_actos", t)
        out.append(t)
    return out


def marcas_de(info_beats: Dict[str, Any], cada: int = 1, fuertes: bool = False) -> List[float]:
    """De los beats, los que sirven para cortar: todos, uno cada `cada`, o los fuertes."""
    if fuertes:
        return list(info_beats.get("fuertes") or [])
    b = list(info_beats.get("beats") or [])
    return b[::max(int(cada), 1)] if b else []


# ─────────────────────────────────────────────────────────────────────────────
# DESTELLOS en los golpes fuertes (estilo Energía)
# ─────────────────────────────────────────────────────────────────────────────

def destellos(src: Path, dst: Path, tiempos: Sequence[float], fuerza: float = 0.32, dur: float = 0.08) -> bool:
    """Un fogonazo de luz cortito en cada tiempo (los "uno" del compás): el video late
    con la música. Como mucho 16, para no cansar."""
    ts = [t for t in tiempos if t > 0.2][:16]
    if not ts:
        return False
    en = "+".join(f"between(t,{t:.3f},{t + dur:.3f})" for t in ts)
    ok, _ = ff(["-i", str(src), "-vf", f"eq=brightness={fuerza:.2f}:contrast=1.08:enable='{en}',format=yuv420p",
                "-c:v", "libx264", "-preset", "fast", "-crf", "19", "-an", str(dst)], 900)
    return ok and dst.exists() and duracion(dst) >= duracion(src) - 0.3


# ─────────────────────────────────────────────────────────────────────────────
# EL LOGO DE LA CUENTA (uno por cuenta, lo comparten todas las secciones)
# ─────────────────────────────────────────────────────────────────────────────

LOGO_DIR = (Path("/data/motion_luma") if Path("/data").exists() else Path("/tmp/motion_luma"))


def logo_path(prefijo: str) -> Path:
    import hashlib
    LOGO_DIR.mkdir(parents=True, exist_ok=True)
    return LOGO_DIR / f"logo_{hashlib.sha1(prefijo.encode()).hexdigest()[:12]}.png"


def guardar_logo(prefijo: str, data: bytes) -> Dict[str, Any]:
    """Guarda el logo como PNG transparente. Si viene sin transparencia (un JPG) y el
    borde es parejo (fondo blanco o negro), ese fondo se vuelve transparente. Se recortan
    los márgenes vacíos y se limita a 1200 px."""
    import io
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    im.load()
    im = im.convert("RGBA")
    quitado = ""
    alfa_min = im.getchannel("A").getextrema()[0]
    if alfa_min == 255:          # sin transparencia: ¿el fondo es parejo?
        w, h = im.size
        borde = [im.getpixel((x, y))[:3] for x in range(0, w, max(w // 20, 1)) for y in (0, h - 1)] + \
                [im.getpixel((x, y))[:3] for y in range(0, h, max(h // 20, 1)) for x in (0, w - 1)]
        prom = tuple(sum(c[i] for c in borde) / len(borde) for i in range(3))
        parejo = all(max(abs(c[i] - prom[i]) for i in range(3)) < 24 for c in borde)
        if parejo:
            px = im.load()
            for y in range(h):
                for x in range(w):
                    r, g, b, a = px[x, y]
                    dist = max(abs(r - prom[0]), abs(g - prom[1]), abs(b - prom[2]))
                    if dist < 40:
                        px[x, y] = (r, g, b, 0 if dist < 22 else int(255 * (dist - 22) / 18))
            quitado = "blanco" if sum(prom) > 600 else ("negro" if sum(prom) < 120 else "parejo")
    caja = im.getchannel("A").getbbox()
    if caja:
        im = im.crop(caja)
    im.thumbnail((1200, 1200))
    p = logo_path(prefijo)
    im.save(p, "PNG")
    return {"ok": True, "ancho": im.size[0], "alto": im.size[1], "fondo_quitado": quitado}
