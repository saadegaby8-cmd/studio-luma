# -*- coding: utf-8 -*-
"""
estudio3d_luma.py — El Estudio 3D del Storyboard
================================================

Como los "film studios" con maniquíes, pero para TODO el reel: una sola escena (tu lugar de
fondo, la luz) con un momento por cuadro del storyboard. Cada momento tiene su cámara (ángulo,
plano, lente y movimiento) y la pose de la modelo al empezar y, si se mueve, al terminar.

- El director (Claude) arma la escena solo a partir del storyboard (/escena3d/director).
- El ANIMATIC muestra el reel en movimiento: cada momento en sus segundos, de la pose de inicio a
  la de final, con su movimiento de cámara; se puede bajar como video (webm).
- "Usar en todos los cuadros" guarda la foto de inicio de cada momento como GUÍA DE POSE Y CÁMARA
  de su cuadro: Gemini dibuja el cuadro con ella en esa pose y desde ese ángulo.

Todo corre en el navegador (three.js): no tiene costo de API, salvo la llamada del director.
"""

# Las poses armadas, para el director (las mismas claves que la página).
POSES_3D = {
    "de_pie": "standing relaxed, arms down",
    "mano_cadera": "standing, one hand on her hip",
    "presenta": "presenting something with her open palm up beside her shoulder (to show the price)",
    "senala": "pointing to the side with her arm stretched",
    "espalda_mira": "back to the camera, looking back over her shoulder",
    "bretel": "adjusting a bra strap with her fingers",
    "pelo": "one hand running through her hair",
    "selfie": "mirror selfie, phone held at chest height",
    "camina": "walking, mid-step",
    "sentada": "sitting (on the bed or a chair)",
    "rodillas": "kneeling (on the bed)",
    "acostada": "lying on her side",
}

# La luz (las mismas claves que la página) y cómo se le dice al dibujo y al video.
LUCES_3D = {
    "ventana": "soft natural daylight from a window at her side, gentle shadows",
    "atardecer": "warm golden-hour sunlight from behind and to the side, glowing rim light",
    "aro": "soft frontal ring light, even and flattering, catchlights in her eyes",
    "estudio": "soft even studio light, a large softbox key and a gentle fill",
    "velador": "night: warm low light from a bedside lamp, dark corners, intimate",
}

MANIQUI_JS = r"""// ── EL MANIQUÍ ──
const GRIS = new THREE.MeshStandardMaterial({color: 0xc9c6c1, roughness: 0.5});
const ELEGIDO = new THREE.MeshStandardMaterial({color: 0xd8b878, roughness: 0.5, emissive: 0x3a2a08});
function pieza(geo, j, pos, escala){ const m = new THREE.Mesh(geo, GRIS); if(pos) m.position.set(...pos); if(escala) m.scale.set(...escala); m.userData.j = j; j.add(m); return m; }
function capsula(len, r, j, arriba){ return pieza(new THREE.CapsuleGeometry(r, Math.max(0.001, len - 2 * r), 6, 14), j, [0, arriba ? len / 2 : -len / 2, 0]); }
function esfera(r, j, pos, esc){ return pieza(new THREE.SphereGeometry(r, 24, 16), j, pos, esc); }
function articulacion(nombre, padre, pos, M){ const j = new THREE.Object3D(); j.name = nombre; j.position.set(...pos); padre.add(j); M.j[nombre] = j; return j; }
const PERSONAS = [];
function maniqui(){
  const M = {raiz: new THREE.Group(), j: {}};
  const pelvis = articulacion("pelvis", M.raiz, [0, 0.93, 0], M); esfera(0.15, pelvis, [0, 0, 0], [1.0, 0.68, 0.72]);
  const cintura = articulacion("cintura", pelvis, [0, 0.07, 0], M); capsula(0.2, 0.112, cintura, true);
  const pecho = articulacion("pecho", cintura, [0, 0.19, 0], M); esfera(0.15, pecho, [0, 0.09, 0], [1.18, 0.88, 0.74]);
  esfera(0.055, pecho, [0.06, 0.07, 0.085]); esfera(0.055, pecho, [-0.06, 0.07, 0.085]);
  const cuello = articulacion("cuello", pecho, [0, 0.21, 0], M); capsula(0.07, 0.038, cuello, true);
  const cabeza = articulacion("cabeza", cuello, [0, 0.07, 0], M); esfera(0.092, cabeza, [0, 0.095, 0.005], [0.84, 1.04, 0.95]);
  pieza(new THREE.ConeGeometry(0.02, 0.05, 10), cabeza, [0, 0.085, 0.092]).rotation.x = Math.PI / 2;   // la nariz: para dónde mira
  for(const [lado, x] of [["izq", 1], ["der", -1]]){
    const hombro = articulacion("hombro_" + lado, pecho, [0.165 * x, 0.165, 0], M); esfera(0.045, hombro, [0, 0, 0]);
    capsula(0.28, 0.04, hombro);
    const codo = articulacion("codo_" + lado, hombro, [0, -0.28, 0], M); capsula(0.25, 0.034, codo);
    const muneca = articulacion("muneca_" + lado, codo, [0, -0.25, 0], M); esfera(0.045, muneca, [0, -0.06, 0.005], [0.75, 1.4, 0.38]);
    const cadera = articulacion("cadera_" + lado, pelvis, [0.085 * x, -0.04, 0], M); capsula(0.43, 0.068, cadera);
    const rodilla = articulacion("rodilla_" + lado, cadera, [0, -0.43, 0], M); capsula(0.41, 0.05, rodilla);
    const tobillo = articulacion("tobillo_" + lado, rodilla, [0, -0.41, 0], M); pieza(new THREE.BoxGeometry(0.075, 0.05, 0.2), tobillo, [0, -0.04, 0.055]);
  }
  pose(M, "de_pie"); scene.add(M.raiz); PERSONAS.push(M); return M;
}

const NOMBRES = {pelvis: "la cadera (todo el torso)", cintura: "la cintura", pecho: "el pecho", cuello: "el cuello", cabeza: "la cabeza",
  hombro_izq: "el brazo izquierdo", codo_izq: "el codo izquierdo", muneca_izq: "la mano izquierda", hombro_der: "el brazo derecho",
  codo_der: "el codo derecho", muneca_der: "la mano derecha", cadera_izq: "la pierna izquierda", rodilla_izq: "la rodilla izquierda",
  tobillo_izq: "el pie izquierdo", cadera_der: "la pierna derecha", rodilla_der: "la rodilla derecha", tobillo_der: "el pie derecho"};


// ── POSES ──
const POSES = {
  de_pie: ["De pie", {hombro_izq: [0, 0, 0.1], hombro_der: [0, 0, -0.1], codo_izq: [-0.1, 0, 0], codo_der: [-0.1, 0, 0]}],
  mano_cadera: ["Mano en la cadera", {hombro_izq: [0.25, 0, 0.55], codo_izq: [0, 0, -1.7], hombro_der: [0, 0, -0.12], cadera_der: [-0.08, 0, 0], rodilla_der: [0.15, 0, 0], pelvis: [0, 0, 0.05], cabeza: [0, 0, -0.08]}],
  presenta: ["Presenta el precio (palma arriba)", {hombro_der: [-0.25, 0, -0.6], codo_der: [-1.45, 0, 0], muneca_der: [0, 1.5, -0.2], hombro_izq: [0, 0, 0.1], cabeza: [0, -0.3, 0.05], pelvis: [0, 0, 0.04]}],
  senala: ["Señala al costado", {hombro_der: [-0.2, 0, -1.35], codo_der: [-0.25, 0, 0], hombro_izq: [0, 0, 0.1], cabeza: [0, -0.35, 0]}],
  espalda_mira: ["De espalda, mira por sobre el hombro", {raiz: [0, Math.PI, 0], pecho: [0, 0.35, 0], cuello: [0, 0.4, 0], cabeza: [0, 0.55, 0.05], hombro_izq: [0, 0, 0.12], hombro_der: [0, 0, -0.12]}],
  bretel: ["Se acomoda el bretel", {hombro_der: [-1.2, 0, -0.25], codo_der: [-1.9, 0, 0.35], hombro_izq: [0, 0, 0.1], cabeza: [0.1, 0.25, 0]}],
  pelo: ["Mano en el pelo", {hombro_izq: [-2.5, 0, 0.45], codo_izq: [-1.6, 0, 0], hombro_der: [0, 0, -0.12], cabeza: [0, 0, 0.12], pelvis: [0, 0, -0.06]}],
  selfie: ["Selfie en el espejo", {hombro_der: [-1.3, 0, -0.1], codo_der: [-1.25, 0, 0], muneca_der: [0, 0, 0], hombro_izq: [0, 0, 0.12], pelvis: [0, 0, 0.05], rodilla_izq: [0.25, 0, 0], cadera_izq: [-0.15, 0, 0]}],
  camina: ["Camina", {cadera_izq: [-0.38, 0, 0], rodilla_izq: [0.15, 0, 0], cadera_der: [0.25, 0, 0], rodilla_der: [0.45, 0, 0], hombro_izq: [0.3, 0, 0.08], hombro_der: [-0.3, 0, -0.08], codo_der: [-0.3, 0, 0]}],
  sentada: ["Sentada", {raiz_y: -0.45, cadera_izq: [-1.5, 0, 0.05], rodilla_izq: [1.5, 0, 0], cadera_der: [-1.5, 0, -0.05], rodilla_der: [1.5, 0, 0], hombro_izq: [0.1, 0, 0.3], codo_izq: [-0.5, 0, 0], hombro_der: [0.1, 0, -0.3], codo_der: [-0.5, 0, 0]}],
  rodillas: ["De rodillas", {raiz_y: -0.42, cadera_izq: [0.05, 0, 0.05], rodilla_izq: [1.55, 0, 0], cadera_der: [0.05, 0, -0.05], rodilla_der: [1.55, 0, 0], tobillo_izq: [0.6, 0, 0], tobillo_der: [0.6, 0, 0], hombro_izq: [0, 0, 0.15], hombro_der: [0, 0, -0.15]}],
  acostada: ["Acostada de costado", {raiz: [0, 0, Math.PI / 2], raiz_y: 0.22, raiz_x: -0.9, cadera_izq: [-0.5, 0, 0], rodilla_izq: [0.9, 0, 0], hombro_der: [-1.4, 0, 0], codo_der: [-1.8, 0, 0], cabeza: [0, 0, -0.35]}],
};
function pose(M, clave){
  const [, p] = POSES[clave] || POSES.de_pie;
  for(const j of Object.values(M.j)) j.rotation.set(0, 0, 0);
  const base = M.raiz.userData.base || [M.raiz.position.x, M.raiz.position.z, M.raiz.rotation.y];
  M.raiz.userData.base = base;
  M.raiz.rotation.set(0, base[2], 0); M.raiz.position.set(base[0], 0, base[1]);
  for(const [k, v] of Object.entries(p)){
    if(k === "raiz") M.raiz.rotation.set(v[0], base[2] + v[1], v[2]);
    else if(k === "raiz_y") M.raiz.position.y = v;
    else if(k === "raiz_x") M.raiz.position.x = base[0] + v;
    else if(M.j[k]) M.j[k].rotation.set(...v);
  }
  M.pose = clave;
}
function estado(M){
  return {pose: M.pose || "", base: (M.raiz.userData.base || [0, 0, 0]).slice(), pos: M.raiz.position.toArray(), rot: [M.raiz.rotation.x, M.raiz.rotation.y, M.raiz.rotation.z],
          j: Object.fromEntries(Object.entries(M.j).map(([k, o]) => [k, [o.rotation.x, o.rotation.y, o.rotation.z].map(v => +v.toFixed(3))]))};
}
function aplicar(M, st){
  if(!st) return;
  M.raiz.position.fromArray(st.pos); M.raiz.rotation.set(...st.rot); M.raiz.userData.base = (st.base || [st.pos[0], st.pos[2], st.rot[1]]).slice(); M.pose = st.pose;
  for(const [k, o] of Object.entries(M.j)) o.rotation.set(...((st.j || {})[k] || [0, 0, 0]));
}

"""

PAGINA_3D = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Estudio 3D · Storyboard</title>
<link href="https://fonts.googleapis.com/css2?family=Bodoni+Moda:wght@500;600&family=Jost:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root{--ink:#ecebf1;--ink-soft:#96919f;--line:#2c2a34;--ivory:#131218;--card:#1b1a21;--card-2:#232128;--rose:#c9a86b;--rose-deep:#d8b878;--ok:#5fae86;--bad:#e0736f}
  *{box-sizing:border-box} html,body{margin:0;height:100%;background:var(--ivory);color:var(--ink);font-family:Jost,system-ui,sans-serif;font-size:14px}
  .app{display:grid;grid-template-columns:290px 1fr 290px;grid-template-rows:1fr auto;height:100vh}
  .panel{background:var(--card);border-right:1px solid var(--line);padding:12px;overflow:auto}
  .panel.der{border-right:none;border-left:1px solid var(--line)}
  h2{font-family:'Bodoni Moda',serif;font-weight:600;font-size:19px;margin:0 0 4px} h3{font-size:12px;margin:14px 0 6px;font-weight:500;color:var(--rose-deep);text-transform:uppercase;letter-spacing:.06em}
  .hint{color:var(--ink-soft);font-size:12px;line-height:1.45}
  button{background:var(--card-2);color:var(--ink);border:1px solid var(--line);border-radius:999px;padding:6px 12px;font:inherit;font-size:12.5px;cursor:pointer;margin:0 4px 6px 0}
  button:disabled{opacity:.5;cursor:default}
  button.on{background:var(--rose);color:#17140d;border-color:var(--rose)} button.go{background:linear-gradient(150deg,var(--rose-deep),var(--rose));color:#17140d;border:none;font-weight:500;font-size:13.5px;padding:8px 14px}
  label{display:block;font-size:12px;color:var(--ink-soft);margin:6px 0 2px} input[type=range]{width:100%} select{width:100%;background:var(--card-2);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:6px}
  .escena{position:relative;display:flex;align-items:center;justify-content:center;background:#0d0c11;overflow:hidden}
  #lienzo{display:block;box-shadow:0 0 0 1px var(--line),0 10px 40px #0008;border-radius:4px}
  .tag{position:absolute;top:10px;left:50%;transform:translateX(-50%);background:#000a;padding:4px 10px;border-radius:999px;font-size:12px;color:var(--ink-soft)}
  #cartelAnim{position:absolute;bottom:18px;left:50%;transform:translateX(-50%);background:#000b;padding:6px 12px;border-radius:10px;font-size:13px;max-width:80%;text-align:center}
  #reloj{position:absolute;top:10px;right:14px;font-variant-numeric:tabular-nums;color:var(--rose-deep);font-size:13px}
  .linea{grid-column:1 / span 3;display:flex;gap:6px;padding:8px 10px;background:var(--card);border-top:1px solid var(--line);overflow-x:auto}
  .mom{min-width:120px;background:var(--card-2);border:1px solid var(--line);border-radius:10px;padding:6px 8px;font-size:12px;cursor:pointer}
  .mom div{color:var(--ink-soft);font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .mom.on{border-color:var(--rose);box-shadow:0 0 0 1px var(--rose)}
  .estado{margin-top:8px;font-size:12.5px} .mal{color:var(--bad)} .bien{color:var(--ok)}
  .spin{display:inline-block;width:12px;height:12px;border:2px solid var(--ink-soft);border-top-color:var(--rose);border-radius:50%;animation:g 1s linear infinite;vertical-align:-2px} @keyframes g{to{transform:rotate(360deg)}}
  @media(max-width:900px){.app{grid-template-columns:1fr;height:auto}.escena{height:70vh}.linea{grid-column:1}}
</style>
<script type="importmap">{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}</script>
</head><body>
<div class="app">
  <div class="panel">
    <h2>🧍 Estudio 3D</h2>
    <div class="hint" id="info"></div>
    <h3>La pose de este momento</h3>
    <div><button id="editIni" class="on">▶ Inicio</button><button id="editFin">⏹ Final</button><button id="sinFin" title="Que no cambie de pose">✕ Sin final</button></div>
    <div class="hint">Inicio = cómo arranca el momento (de acá sale el cuadro). Final = cómo termina (si se mueve); el animatic va de una a otra.</div>
    <div id="poses" style="margin-top:6px"></div>
    <h3>El maniquí</h3>
    <div><button id="modoParte" class="on">✋ Mover partes</button><button id="modoMover">↔ Moverla</button><button id="modoGirar">⟳ Girarla</button></div>
    <div class="hint" id="parteInfo">Parte elegida: ninguna</div>
    <div><button id="resetParte">↺ Enderezar la parte</button><button id="espejo">⇋ Espejo</button></div>
    <div><button id="agregar">＋ Otro maniquí</button><button id="quitar">🗑 Quitar el elegido</button></div>
    <h3>La luz</h3><div id="luces"></div>
    <label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="fondo" checked style="width:auto"> Mi lugar de fondo</label>
  </div>
  <div class="escena" id="escena"><canvas id="lienzo"></canvas><div class="tag">Así se ve el cuadro (9:16)</div><div id="reloj"></div><div id="cartelAnim" style="display:none"></div></div>
  <div class="panel der">
    <button class="go" id="director" style="width:100%">🎬 El director arma la escena</button>
    <div class="hint">Lee el storyboard y ubica a la modelo, sus poses de inicio y final y la cámara de cada momento.</div>
    <h3>Animatic</h3>
    <div><button id="play">▶ Ver animatic</button><button id="grabar">⏺ Bajar en video</button><button id="parar">■</button></div>
    <h3>La cámara de este momento</h3>
    <div><button data-cam="ojos">A los ojos</button><button data-cam="bajo">Desde abajo</button><button data-cam="alto">Desde arriba</button><button data-cam="cenital">Cenital</button><button data-cam="tres_cuartos">3/4</button><button data-cam="perfil">Perfil</button><button data-cam="espalda">Desde atrás</button><button data-cam="holandes">Inclinada</button></div>
    <div><button data-dist="detalle">Detalle</button><button data-dist="primer">Primer plano</button><button data-dist="medio">Medio</button><button data-dist="americano">Americano</button><button data-dist="entero">Entero</button></div>
    <label>Lente: <span id="lenteV">35</span> mm</label><input type="range" id="lente" min="16" max="85" step="1" value="35">
    <label>Movimiento de cámara</label><select id="mov"></select>
    <div class="hint">Arrastrá en la escena para girar la cámara; rueda o dos dedos para acercar.</div>
    <h3>Usar</h3>
    <button class="go" id="usarTodos" style="width:100%">✓ Usar en todos los cuadros</button>
    <button id="usar" style="margin-top:6px">✓ Sólo este momento</button>
    <div class="estado" id="estado"></div>
  </div>
  <div class="linea" id="linea"></div>
</div>
<script type="module">
import * as THREE from "three";
import {OrbitControls} from "three/addons/controls/OrbitControls.js";
import {TransformControls} from "three/addons/controls/TransformControls.js";

const API = "%%API%%";
const MOVS = %%MOVS%%;
const q = new URLSearchParams(location.search), SID = q.get("sid"), CID0 = q.get("cid");
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const lienzo = $("#lienzo");
const renderer = new THREE.WebGLRenderer({canvas: lienzo, antialias: true, preserveDrawingBuffer: true});
renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
renderer.toneMapping = THREE.ACESFilmicToneMapping;
const scene = new THREE.Scene(); scene.background = new THREE.Color(0x2a2830);
const camara = new THREE.PerspectiveCamera(50, 9 / 16, 0.05, 100); scene.add(camara);
const piso = new THREE.Mesh(new THREE.PlaneGeometry(14, 14), new THREE.MeshStandardMaterial({color: 0x8f8a84, roughness: 1}));
piso.rotation.x = -Math.PI / 2; scene.add(piso);
const grilla = new THREE.GridHelper(14, 28, 0x6f6a64, 0x7d7872); grilla.position.y = 0.002; scene.add(grilla);
let telon = null;

// ── LA LUZ: presets (los mismos nombres que usa el pedido de cada cuadro) ──
const LUCES = {
  ventana: ["Ventana de costado", () => [["hemi", 0xfff6ea, 0x3a3640, 0.55], ["dir", 0xfff1df, 2.4, [-3.2, 2.6, 0.6]]]],
  atardecer: ["Atardecer dorado", () => [["hemi", 0xffe2c0, 0x3a3040, 0.85], ["dir", 0xffa64d, 2.4, [-2.5, 1.1, -2.2]], ["dir", 0xffd6a0, 0.6, [1.5, 1.8, 3]]]],
  aro: ["Luz de aro (de frente)", () => [["hemi", 0xffffff, 0x50505a, 0.7], ["cam", 0xffffff, 1.8]]],
  estudio: ["Estudio suave", () => [["hemi", 0xffffff, 0x55555f, 0.9], ["dir", 0xffffff, 1.4, [2, 3.5, 3]], ["dir", 0xdfe8ff, 0.6, [-3, 2, 2]]]],
  velador: ["De noche, con velador", () => [["hemi", 0x8a90b0, 0x101018, 0.18], ["punto", 0xffb46a, 6, [1.0, 0.75, 0.8]]]],
};
let LUZ = "ventana", LUCES_OBJ = [];
function luz(clave){
  LUZ = LUCES[clave] ? clave : "ventana";
  LUCES_OBJ.forEach(o => o.parent && o.parent.remove(o)); LUCES_OBJ = [];
  for(const d of LUCES[LUZ][1]()){
    let o;
    if(d[0] === "hemi") o = new THREE.HemisphereLight(d[1], d[2], d[3]);
    if(d[0] === "dir"){ o = new THREE.DirectionalLight(d[1], d[2]); o.position.set(...d[3]); }
    if(d[0] === "punto"){ o = new THREE.PointLight(d[1], d[2], 6, 1.6); o.position.set(...d[3]); }
    if(d[0] === "cam"){ o = new THREE.DirectionalLight(d[1], d[2]); o.position.set(0, 0, 1); camara.add(o); LUCES_OBJ.push(o); continue; }
    scene.add(o); LUCES_OBJ.push(o);
  }
  document.querySelectorAll("[data-luz]").forEach(b => b.classList.toggle("on", b.dataset.luz === LUZ));
}

%%COMUN%%
// ── EDICIÓN ──
const orbita = new OrbitControls(camara, lienzo); orbita.enableDamping = true; orbita.target.set(0, 1.0, 0);
const gizmo = new TransformControls(camara, lienzo); gizmo.setSpace("local"); gizmo.setSize(0.8); scene.add(gizmo);
gizmo.addEventListener("dragging-changed", e => { orbita.enabled = !e.value; if(!e.value) guardarMomento(); });
gizmo.addEventListener("objectChange", () => { if(ACTUAL && gizmo.object === ACTUAL.raiz) ACTUAL.raiz.userData.base = [ACTUAL.raiz.position.x, ACTUAL.raiz.position.z, ACTUAL.raiz.rotation.y]; });
orbita.addEventListener("end", () => guardarMomento());
let ACTUAL = null, PARTE = null, MODO = "parte";
function pintarElegido(){
  for(const M of PERSONAS) M.raiz.traverse(o => { if(o.isMesh) o.material = (M === ACTUAL && o.parent === PARTE ? ELEGIDO : GRIS); });
  $("#parteInfo").textContent = "Parte elegida: " + (PARTE ? NOMBRES[PARTE.name] || PARTE.name : (MODO === "parte" ? "ninguna (tocá el maniquí)" : "todo el cuerpo"));
}
function modo(m){
  MODO = m; ["modoMover", "modoGirar", "modoParte"].forEach(id => $("#" + id).classList.toggle("on", id === {mover: "modoMover", girar: "modoGirar", parte: "modoParte"}[m]));
  gizmo.detach();
  if(ACTUAL && !ANIMANDO){
    if(m === "mover"){ PARTE = null; gizmo.setMode("translate"); gizmo.showX = gizmo.showZ = true; gizmo.showY = false; gizmo.attach(ACTUAL.raiz); }
    else if(m === "girar"){ PARTE = null; gizmo.setMode("rotate"); gizmo.showX = gizmo.showZ = false; gizmo.showY = true; gizmo.attach(ACTUAL.raiz); }
    else { gizmo.setMode("rotate"); gizmo.showX = gizmo.showY = gizmo.showZ = true; if(PARTE) gizmo.attach(PARTE); }
  }
  pintarElegido();
}
$("#modoMover").onclick = () => modo("mover"); $("#modoGirar").onclick = () => modo("girar"); $("#modoParte").onclick = () => modo("parte");
const ray = new THREE.Raycaster(), ptr = new THREE.Vector2(); let abajo = null;
lienzo.addEventListener("pointerdown", e => { abajo = [e.clientX, e.clientY]; });
lienzo.addEventListener("pointerup", e => {
  if(ANIMANDO || !abajo || Math.hypot(e.clientX - abajo[0], e.clientY - abajo[1]) > 5 || gizmo.dragging) return;
  const r = lienzo.getBoundingClientRect(); ptr.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  ray.setFromCamera(ptr, camara);
  const metas = []; PERSONAS.forEach(M => { if(M.raiz.visible) M.raiz.traverse(o => { if(o.isMesh) metas.push(o); }); });
  const hit = ray.intersectObjects(metas, false)[0];
  if(!hit) return;
  ACTUAL = PERSONAS.find(M => { let ok = false; M.raiz.traverse(o => { if(o === hit.object) ok = true; }); return ok; });
  PARTE = MODO === "parte" ? hit.object.userData.j : null;
  modo(MODO);
});
$("#resetParte").onclick = () => { if(PARTE){ PARTE.rotation.set(0, 0, 0); guardarMomento(); } };
$("#espejo").onclick = () => {
  // La misma pose del otro lado: se cambian izquierda y derecha y se invierten los giros laterales.
  const M = ACTUAL || PERSONAS[0]; if(!M) return;
  const st = estado(M), nj = {};
  for(const [k, v] of Object.entries(st.j)){
    const o = k.endsWith("_izq") ? k.replace("_izq", "_der") : k.endsWith("_der") ? k.replace("_der", "_izq") : k;
    nj[o] = [v[0], -v[1], -v[2]];
  }
  st.j = nj; aplicar(M, st); guardarMomento();
};
$("#poses").innerHTML = Object.entries(POSES).map(([k, [n]]) => `<button data-pose="${k}">${n}</button>`).join("");
document.querySelectorAll("[data-pose]").forEach(b => b.onclick = () => { const M = PERSONAS[0]; pose(M, b.dataset.pose); ACTUAL = M; modo(MODO); guardarMomento(); });
$("#agregar").onclick = () => { const M = maniqui(); M.raiz.position.x = 0.7 * (PERSONAS.length - 1); M.raiz.userData.base = [M.raiz.position.x, 0, 0]; ACTUAL = M; PARTE = null; modo("mover"); };
$("#quitar").onclick = () => { if(!ACTUAL || PERSONAS.length < 2 || ACTUAL === PERSONAS[0]) return; gizmo.detach(); scene.remove(ACTUAL.raiz); PERSONAS.splice(PERSONAS.indexOf(ACTUAL), 1); ACTUAL = PERSONAS[0]; PARTE = null; modo("parte"); };

// ── LA CÁMARA ──
const ALTURA_OJOS = 1.58;
function lente(mm){ camara.fov = 2 * THREE.MathUtils.radToDeg(Math.atan(36 / (2 * mm))); camara.updateProjectionMatrix(); $("#lenteV").textContent = mm; $("#lente").value = mm; }
$("#lente").oninput = () => { lente(+$("#lente").value); guardarMomento(); };
const DIST = {detalle: [0.8, 1.15], primer: [1.0, 1.55], medio: [1.4, 1.25], americano: [2.1, 1.05], entero: [3.0, 0.9], espejo: [2.6, 0.95]};
function ponerCamara(plano, ang){
  const M = PERSONAS[0]; const [d, ty0] = DIST[plano] || DIST.americano;
  const centro = M ? new THREE.Vector3(M.raiz.position.x, 0, M.raiz.position.z) : new THREE.Vector3();
  const ty = M && M.raiz.position.y < -0.2 ? ty0 + M.raiz.position.y * 0.8 : ty0;
  const base = M ? M.raiz.rotation.y : 0;      // hacia dónde mira su cuerpo de verdad (con la pose)
  const giro = {perfil: Math.PI / 2, tres_cuartos: Math.PI / 4, hombro: Math.PI * 0.85, espalda: Math.PI}[ang] || 0;
  const yaw = base + giro;
  let alt = {bajo: 0.55, alto: 2.35, cenital: 3.2, suelo: 0.15}[ang] ?? ALTURA_OJOS;
  if(plano === "detalle" || plano === "primer") alt = {bajo: ty - 0.35, alto: ty + 0.45, cenital: ty + 1.0, suelo: 0.2}[ang] ?? (ALTURA_OJOS - (plano === "detalle" ? 0.35 : 0));
  const dd = ang === "cenital" ? 0.25 : d;
  camara.position.set(centro.x + Math.sin(yaw) * dd, alt, centro.z + Math.cos(yaw) * dd);
  camara.up.set(0, 1, 0);
  orbita.target.set(centro.x, ty, centro.z); orbita.update();
  if(ang === "holandes"){ camara.rotateZ(0.12); }
}
document.querySelectorAll("[data-cam]").forEach(b => b.onclick = () => { const m = momento(); if(m){ m.angulo = b.dataset.cam; } ponerCamara(m ? m.plano : "americano", b.dataset.cam); guardarMomento(); });
document.querySelectorAll("[data-dist]").forEach(b => b.onclick = () => { const m = momento(); if(m){ m.plano = b.dataset.dist; } ponerCamara(b.dataset.dist, m ? m.angulo : "ojos"); guardarMomento(); });
$("#mov").onchange = () => { const e = escMomento(); if(e){ e.mov = $("#mov").value; guardarEscena(); } };

// ── FONDO ──
function telonCon(src){
  new THREE.TextureLoader().load(src, tex => {
    tex.colorSpace = THREE.SRGBColorSpace;
    const asp = tex.image.width / tex.image.height, alto = 3.2;
    telon = new THREE.Mesh(new THREE.PlaneGeometry(alto * asp, alto), new THREE.MeshBasicMaterial({map: tex}));
    telon.position.set(0, alto / 2 - 0.05, -2.6); scene.add(telon); telon.visible = $("#fondo").checked;
  });
}
$("#fondo").onchange = () => { if(telon) telon.visible = $("#fondo").checked; ESC.fondo = $("#fondo").checked; guardarEscena(); };

// ── TAMAÑO: siempre 9:16 ──
function medir(){
  const caja = $("#escena").getBoundingClientRect();
  let h = caja.height - 24, w = h * 9 / 16; if(w > caja.width - 24){ w = caja.width - 24; h = w * 16 / 9; }
  renderer.setSize(Math.floor(w), Math.floor(h), true); camara.aspect = 9 / 16; camara.updateProjectionMatrix();
}
window.addEventListener("resize", medir);

// ── LA ESCENA DEL REEL: un momento por cuadro, con su cámara y su pose de inicio (y de final) ──
let SB = null, CUR = null, EDIT = "ini", ESC = {luz: "ventana", fondo: true, momentos: {}, extras: []};
const momento = () => SB && SB.cuadros.find(c => c.id === CUR);
const escMomento = () => CUR ? (ESC.momentos[CUR] = ESC.momentos[CUR] || {}) : null;
let tGuardar = null;
function guardarEscena(){ clearTimeout(tGuardar); tGuardar = setTimeout(async () => {
  try{ await fetch(`${API}/${SID}/escena3d`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({escena3d: ESC})}); }catch(e){}
}, 600); }
function guardarMomento(){
  if(ANIMANDO || !CUR) return; const e = escMomento();
  e.cam = {pos: camara.position.toArray(), target: orbita.target.toArray(), lente: +$("#lente").value};
  if(PERSONAS[0]) e[EDIT] = estado(PERSONAS[0]);
  ESC.extras = PERSONAS.slice(1).map(estado);
  pintarLinea(); guardarEscena();
}
function cargarMomento(cid, cual){
  if(!SB) return; CUR = cid; EDIT = cual || "ini";
  const c = momento(), e = escMomento(), M = PERSONAS[0];
  const producto = c && c.tipo === "producto";
  PERSONAS.forEach(P => P.raiz.visible = !producto);
  if(e[EDIT]) aplicar(M, e[EDIT]);
  else if(EDIT === "fin" && e.ini) aplicar(M, e.ini);
  else { M.raiz.userData.base = [0, 0, 0]; pose(M, c && c.angulo === "espalda" ? "espalda_mira" : "de_pie"); }
  if(e.cam){ camara.position.fromArray(e.cam.pos); orbita.target.fromArray(e.cam.target); lente(e.cam.lente || 35); camara.up.set(0, 1, 0); orbita.update(); }
  else { lente(c && (c.plano === "detalle" || c.plano === "primer") ? 50 : 35); ponerCamara(c ? c.plano : "americano", c ? c.angulo : "ojos"); }
  $("#mov").value = e.mov || (c && c.movimiento) || "mano";
  ["#editIni", "#editFin"].forEach(s => $(s).classList.toggle("on", (s === "#editIni") === (EDIT === "ini")));
  $("#info").innerHTML = c ? `<b>Momento ${SB.cuadros.indexOf(c) + 1} · ${c.seg} s:</b> ${esc(c.que_se_ve)}${c.linea ? `<br><span class="hint">${esc(c.linea).replace(/\n/g, "<br>")}</span>` : ""}` : "";
  ACTUAL = M; PARTE = null; modo(MODO); pintarLinea();
}
$("#editIni").onclick = () => { guardarMomento(); cargarMomento(CUR, "ini"); };
$("#editFin").onclick = () => { guardarMomento(); cargarMomento(CUR, "fin"); };
$("#sinFin").onclick = () => { const e = escMomento(); if(e){ delete e.fin; guardarEscena(); cargarMomento(CUR, "ini"); } };
function pintarLinea(){
  if(!SB) return;
  $("#linea").innerHTML = SB.cuadros.map((c, i) => { const e = ESC.momentos[c.id] || {};
    return `<div class="mom ${c.id === CUR ? "on" : ""}" data-m="${c.id}" style="flex:${c.seg}"><b>${i + 1}</b> · ${c.seg} s${e.fin ? " · ↦" : ""}${c.guia_url ? " · 🧍" : ""}<div>${esc((c.que_se_ve || "").slice(0, 60))}</div></div>`; }).join("");
  document.querySelectorAll("[data-m]").forEach(d => d.onclick = () => { if(ANIMANDO) return; guardarMomento(); cargarMomento(d.dataset.m, "ini"); });
}

// ── EL DIRECTOR ARMA LA ESCENA ──
$("#director").onclick = async () => {
  const est = $("#estado"); est.innerHTML = '<span class="spin"></span> El director está armando la escena…'; $("#director").disabled = true;
  try{
    const r = await fetch(`${API}/${SID}/escena3d/director`, {method: "POST"}); const d = await r.json();
    if(!r.ok) throw new Error(d.detail || "No pudo.");
    const M = PERSONAS[0];
    for(const c of SB.cuadros){
      const b = (d.momentos || {})[c.id]; if(!b) continue;
      const e = ESC.momentos[c.id] = ESC.momentos[c.id] || {};
      M.raiz.userData.base = [b.x || 0, b.z || 0, THREE.MathUtils.degToRad(b.giro || 0)];
      pose(M, POSES[b.ini] ? b.ini : "de_pie"); e.ini = estado(M);
      if(b.fin && POSES[b.fin] && b.fin !== b.ini){ pose(M, b.fin); e.fin = estado(M); } else delete e.fin;
      aplicar(M, e.ini); lente(c.plano === "detalle" || c.plano === "primer" ? 50 : 35); ponerCamara(c.plano, c.angulo);
      e.cam = {pos: camara.position.toArray(), target: orbita.target.toArray(), lente: +$("#lente").value};
      e.mov = c.movimiento || "mano";
    }
    if(d.luz && LUCES[d.luz]){ luz(d.luz); ESC.luz = LUZ; }
    guardarEscena(); cargarMomento(SB.cuadros[0].id, "ini");
    est.innerHTML = `<span class="bien">✓ Escena armada${d.nota ? ": " + esc(d.nota) : ""}. Mirá el animatic (▶) y retocá lo que quieras.</span>`;
  }catch(e){ est.innerHTML = `<span class="mal">${esc(e.message)}</span>`; }
  $("#director").disabled = false;
};

// ── EL ANIMATIC: cada momento en sus segundos, de la pose de inicio a la de final, con su cámara ──
let ANIMANDO = false, GRABADOR = null;
const sacudir = t => new THREE.Vector3(Math.sin(t * 7.1) * 0.006 + Math.sin(t * 13.3) * 0.003, Math.sin(t * 5.3) * 0.006, 0);
function interpolar(M, a, b, k){
  const qa = new THREE.Quaternion(), qb = new THREE.Quaternion(), ea = new THREE.Euler(), eb = new THREE.Euler();
  for(const [n, o] of Object.entries(M.j)){
    qa.setFromEuler(ea.set(...((a.j || {})[n] || [0, 0, 0]))); qb.setFromEuler(eb.set(...((b.j || {})[n] || [0, 0, 0])));
    o.quaternion.copy(qa).slerp(qb, k);
  }
  M.raiz.position.fromArray(a.pos).lerp(new THREE.Vector3(...b.pos), k);
  qa.setFromEuler(ea.set(...a.rot)); qb.setFromEuler(eb.set(...b.rot)); M.raiz.quaternion.copy(qa).slerp(qb, k);
}
function camaraEn(e, mov, k, t){
  const p = new THREE.Vector3(...e.cam.pos), tg = new THREE.Vector3(...e.cam.target);
  const s = k * k * (3 - 2 * k);
  if(mov === "acerca") p.lerp(tg, 0.22 * s);
  if(mov === "aleja") p.sub(tg).multiplyScalar(1 + 0.28 * s).add(tg);
  if(mov === "orbita"){ const v = p.clone().sub(tg); v.applyAxisAngle(new THREE.Vector3(0, 1, 0), 0.45 * s); p.copy(tg).add(v); }
  if(mov === "paneo"){ const lado = new THREE.Vector3().subVectors(tg, p).cross(new THREE.Vector3(0, 1, 0)).normalize(); tg.addScaledVector(lado, 0.35 * (s - 0.5)); }
  if(mov === "sigue" && PERSONAS[0]){ const pv = new THREE.Vector3(); PERSONAS[0].j.pecho.getWorldPosition(pv); tg.lerp(new THREE.Vector3(pv.x, tg.y, pv.z), 0.8); }
  if(mov === "mano" || mov === "sigue") p.add(sacudir(t));
  camara.position.copy(p); camara.up.set(0, 1, 0); camara.lookAt(tg); camara.fov = 2 * THREE.MathUtils.radToDeg(Math.atan(36 / (2 * (e.cam.lente || 35)))); camara.updateProjectionMatrix();
}
async function animatic(grabar){
  if(ANIMANDO || !SB) return; guardarMomento();
  const sin = SB.cuadros.filter(c => !(ESC.momentos[c.id] || {}).cam);
  for(const c of sin){ cargarMomento(c.id, "ini"); guardarMomento(); }
  ANIMANDO = true; gizmo.detach(); grilla.visible = false; orbita.enabled = false; ACTUAL = null; PARTE = null; pintarElegido();
  const M = PERSONAS[0]; let partes = [];
  if(grabar && lienzo.captureStream && window.MediaRecorder){
    const tipo = MediaRecorder.isTypeSupported("video/webm;codecs=vp9") ? "video/webm;codecs=vp9" : "video/webm";
    GRABADOR = new MediaRecorder(lienzo.captureStream(30), {mimeType: tipo, videoBitsPerSecond: 6e6});
    GRABADOR.ondataavailable = ev => { if(ev.data.size) partes.push(ev.data); }; GRABADOR.start();
  }
  const cartel = $("#cartelAnim"); cartel.style.display = "";
  for(const [i, c] of SB.cuadros.entries()){
    if(!ANIMANDO) break;
    const e = ESC.momentos[c.id] || {}; const a = e.ini || estado(M), b = e.fin || a;
    PERSONAS.forEach(P => P.raiz.visible = c.tipo !== "producto");
    const dur = c.seg * 1000, t0 = performance.now();
    cartel.innerHTML = `<b>${i + 1}</b> · ${esc(c.que_se_ve || "")}`;
    await new Promise(fin => { const paso = now => {
      if(!ANIMANDO) return fin();
      const t = Math.min(1, (now - t0) / dur), k = t < 0.15 ? 0 : t > 0.85 ? 1 : (t - 0.15) / 0.7;
      interpolar(M, a, b, k * k * (3 - 2 * k)); camaraEn(e, e.mov || c.movimiento || "mano", t, (now - t0) / 1000);
      const seg = Math.min(c.seg, Math.floor(t * c.seg)); $("#reloj").textContent = `${i + 1} · ${seg}/${c.seg} s`;
      if(t < 1) requestAnimationFrame(paso); else fin(); };
      requestAnimationFrame(paso); });
  }
  if(GRABADOR){ await new Promise(r => { GRABADOR.onstop = r; GRABADOR.stop(); }); GRABADOR = null;
    if(partes.length){ const url = URL.createObjectURL(new Blob(partes, {type: "video/webm"})); const a = document.createElement("a"); a.href = url; a.download = "animatic.webm"; a.click(); } }
  cartel.style.display = "none"; $("#reloj").textContent = "";
  ANIMANDO = false; grilla.visible = true; orbita.enabled = true; cargarMomento(CUR || SB.cuadros[0].id, "ini");
}
$("#play").onclick = () => animatic(false);
$("#grabar").onclick = () => animatic(true);
$("#parar").onclick = () => { ANIMANDO = false; };

// ── USAR: la foto de inicio de cada momento como guía de su cuadro ──
async function foto(){
  gizmo.visible = false; grilla.visible = false; const sel = [ACTUAL, PARTE]; ACTUAL = null; PARTE = null; pintarElegido();
  const previo = renderer.getSize(new THREE.Vector2());
  renderer.setPixelRatio(1); renderer.setSize(720, 1280, false); renderer.render(scene, camara);
  const img = lienzo.toDataURL("image/jpeg", 0.9);
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1)); renderer.setSize(previo.x, previo.y, true);
  gizmo.visible = true; grilla.visible = true; [ACTUAL, PARTE] = sel; pintarElegido();
  return img;
}
function describir(c, e){
  const M = PERSONAS[0]; if(!M) return "";
  const cpos = camara.position, p = M.raiz.position, d = Math.hypot(cpos.x - p.x, cpos.z - p.z);
  const alt = cpos.y > 2.6 ? "from high above, looking down" : cpos.y > 1.85 ? "from above her eye level, looking down" : cpos.y < 0.9 ? "from a low angle, looking up at her" : "at her eye level";
  const dist = d < 0.7 ? "very close (detail)" : d < 1.1 ? "close-up distance" : d < 1.8 ? "medium shot distance" : d < 2.6 ? "knees-up distance" : "full-body distance";
  const yawCam = Math.atan2(cpos.x - p.x, cpos.z - p.z), rel = Math.abs(((yawCam - M.raiz.rotation.y) * 180 / Math.PI + 540) % 360 - 180);
  const lado = rel < 30 ? "from the front" : rel < 70 ? "from a three-quarter angle" : rel < 115 ? "from her side (profile)" : rel < 155 ? "from a three-quarter back angle" : "from behind";
  const nombre = k => POSES[k] ? POSES[k][0] : "";
  return `camera ${alt}, ${lado}, ${dist}, ${$("#lente").value}mm lens` + (PERSONAS.length > 1 ? `, ${PERSONAS.length} people in the scene` : "")
    + (e.ini && nombre(e.ini.pose) ? `, starting pose: ${nombre(e.ini.pose)}` : "") + (e.fin && nombre(e.fin.pose) ? `, ending pose: ${nombre(e.fin.pose)}` : "");
}
async function usar(soloEste){
  const est = $("#estado"); guardarMomento();
  const lista = soloEste ? [momento()] : SB.cuadros;
  let ok = 0;
  for(const c of lista){
    if(!c || c.tipo === "producto") continue;
    cargarMomento(c.id, "ini"); const e = escMomento();
    est.innerHTML = `<span class="spin"></span> Guardando la guía del momento ${SB.cuadros.indexOf(c) + 1}…`;
    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    const imagen = await foto();
    const r = await fetch(`${API}/${SID}/cuadro/${c.id}/escena`, {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({escena: {momento: e}, imagen, descripcion: describir(c, e)})});
    if(r.ok) ok++;
  }
  const d = await (await fetch(`${API}/${SID}`)).json(); SB = d.storyboard; pintarLinea();
  est.innerHTML = `<span class="bien">✓ ${ok} guía${ok === 1 ? "" : "s"} guardada${ok === 1 ? "" : "s"}. Volvé al storyboard y tocá "🎨 Dibujar los que faltan": se dibujan con estas poses y cámaras.</span>`;
  try{ if(window.opener) window.opener.postMessage({de: "estudio3d"}, "*"); }catch(e){}
}
$("#usar").onclick = () => usar(true);
$("#usarTodos").onclick = () => usar(false);
document.querySelectorAll("[data-luz]").forEach(b => b.onclick = () => { luz(b.dataset.luz); ESC.luz = LUZ; guardarEscena(); });

// ── ARRANQUE ──
(function loop(){ requestAnimationFrame(loop); if(!ANIMANDO) orbita.update(); renderer.render(scene, camara); })();
(async () => {
  medir(); lente(35); luz("ventana"); maniqui();
  $("#luces").innerHTML = Object.entries(LUCES).map(([k, [n]]) => `<button data-luz="${k}">${n}</button>`).join("");
  document.querySelectorAll("[data-luz]").forEach(b => b.onclick = () => { luz(b.dataset.luz); ESC.luz = LUZ; guardarEscena(); });
  $("#mov").innerHTML = Object.entries(MOVS).map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
  try{
    const r = await fetch(`${API}/${SID}/escena3d`); const d = await r.json(); if(!r.ok) throw new Error(d.detail || "No encuentro el storyboard.");
    SB = d.storyboard; if(d.escena3d) ESC = Object.assign(ESC, d.escena3d);
    if(d.lugar) telonCon(d.lugar);
    $("#fondo").checked = ESC.fondo !== false; luz(ESC.luz || d.luz || "ventana");
    for(const st of ESC.extras || []){ const X = maniqui(); aplicar(X, st); }
    if(!SB.cuadros.length) throw new Error("Este storyboard todavía no tiene cuadros.");
    cargarMomento(CID0 && SB.cuadros.some(c => c.id === CID0) ? CID0 : SB.cuadros[0].id, "ini");
    if(!Object.keys(ESC.momentos || {}).length) $("#estado").innerHTML = 'Tocá <b>🎬 El director arma la escena</b> para que ubique a la modelo y las cámaras de todo el reel.';
  }catch(e){ $("#info").innerHTML = `<span class="mal">${esc(e.message)}</span>`; }
})();

</script></body></html>
"""
PAGINA_3D = PAGINA_3D.replace("%%COMUN%%", MANIQUI_JS)
