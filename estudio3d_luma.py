# -*- coding: utf-8 -*-
"""
estudio3d_luma.py — El Estudio 3D del Storyboard
================================================

Como los "film studios" con maniquíes: armás la escena en 3D (en el navegador, con three.js),
con uno o más maniquíes grises articulados, sus poses y la cámara (altura, ángulo, lente), sobre
tu lugar de fondo. "Usar esta pose" saca una foto de la escena en 9:16 y la guarda en el cuadro
del storyboard como GUÍA DE POSE Y CÁMARA: Gemini dibuja el cuadro con ella en esa pose, en ese
lugar del encuadre y desde ese ángulo (con su cara, la prenda y tu lugar de siempre).

Todo corre en el navegador: no tiene costo de API. La página la sirve storyboard_luma
(/storyboard/3d?sid=...&cid=...).
"""

PAGINA_3D = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Estudio 3D · Storyboard</title>
<link href="https://fonts.googleapis.com/css2?family=Bodoni+Moda:wght@500;600&family=Jost:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root{--ink:#ecebf1;--ink-soft:#96919f;--line:#2c2a34;--ivory:#131218;--card:#1b1a21;--card-2:#232128;--rose:#c9a86b;--rose-deep:#d8b878;--ok:#5fae86;--bad:#e0736f}
  *{box-sizing:border-box} html,body{margin:0;height:100%;background:var(--ivory);color:var(--ink);font-family:Jost,system-ui,sans-serif;font-size:14px}
  .app{display:grid;grid-template-columns:300px 1fr 280px;height:100vh}
  .panel{background:var(--card);border-right:1px solid var(--line);padding:12px;overflow:auto}
  .panel.der{border-right:none;border-left:1px solid var(--line)}
  h2{font-family:'Bodoni Moda',serif;font-weight:600;font-size:19px;margin:0 0 4px} h3{font-size:13px;margin:14px 0 6px;font-weight:500;color:var(--rose-deep);text-transform:uppercase;letter-spacing:.06em}
  .hint{color:var(--ink-soft);font-size:12px;line-height:1.45}
  button{background:var(--card-2);color:var(--ink);border:1px solid var(--line);border-radius:999px;padding:6px 12px;font:inherit;font-size:12.5px;cursor:pointer;margin:0 4px 6px 0}
  button.on{background:var(--rose);color:#17140d;border-color:var(--rose)} button.go{background:linear-gradient(150deg,var(--rose-deep),var(--rose));color:#17140d;border:none;font-weight:500;font-size:14px;padding:9px 16px}
  label{display:block;font-size:12px;color:var(--ink-soft);margin:6px 0 2px} input[type=range]{width:100%} select{width:100%;background:var(--card-2);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:6px}
  .escena{position:relative;display:flex;align-items:center;justify-content:center;background:#0d0c11;overflow:hidden}
  #lienzo{display:block;box-shadow:0 0 0 1px var(--line),0 10px 40px #0008;border-radius:4px}
  .tag{position:absolute;top:10px;left:50%;transform:translateX(-50%);background:#000a;padding:4px 10px;border-radius:999px;font-size:12px;color:var(--ink-soft)}
  .estado{margin-top:8px;font-size:12.5px} .mal{color:var(--bad)} .bien{color:var(--ok)}
  @media(max-width:900px){.app{grid-template-columns:1fr;height:auto}.escena{height:70vh}}
</style>
<script type="importmap">{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}</script>
</head><body>
<div class="app">
  <div class="panel">
    <h2>🧍 Estudio 3D</h2>
    <div class="hint" id="info">Armá la pose y la cámara. Tocá una parte del maniquí para moverla (aparece un aro: arrastralo).</div>
    <h3>Poses</h3><div id="poses"></div>
    <h3>Maniquíes</h3>
    <div><button id="agregar">＋ Otro maniquí</button><button id="quitar">🗑 Quitar el elegido</button></div>
    <div><button id="modoMover">↔ Moverla</button><button id="modoGirar">⟳ Girarla</button><button id="modoParte" class="on">✋ Mover partes</button></div>
    <label>Girar todo el cuerpo</label><input type="range" id="giro" min="-180" max="180" step="5" value="0">
    <div class="hint" id="parteInfo">Parte elegida: ninguna</div>
    <div><button id="resetParte">↺ Enderezar la parte</button><button id="resetTodo">↺ Pose inicial</button></div>
  </div>
  <div class="escena" id="escena"><canvas id="lienzo"></canvas><div class="tag">Así se ve el cuadro (9:16)</div></div>
  <div class="panel der">
    <h3>Cámara</h3>
    <div><button data-cam="ojos">A los ojos</button><button data-cam="bajo">Desde abajo</button><button data-cam="alto">Desde arriba</button><button data-cam="cenital">Cenital</button><button data-cam="espalda">Desde atrás</button></div>
    <div><button data-dist="detalle">Detalle</button><button data-dist="primer">Primer plano</button><button data-dist="medio">Medio</button><button data-dist="americano">Americano</button><button data-dist="entero">Entero</button></div>
    <label>Lente: <span id="lenteV">35</span> mm</label><input type="range" id="lente" min="16" max="85" step="1" value="35">
    <div class="hint">Arrastrá en la escena para girar la cámara; rueda o dos dedos para acercar.</div>
    <h3>Fondo</h3>
    <label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="fondo" checked style="width:auto"> Mi lugar de fondo</label>
    <h3>Usar</h3>
    <button class="go" id="usar">✓ Usar esta pose en el cuadro</button>
    <div class="estado" id="estado"></div>
    <div class="hint" style="margin-top:8px">Se guarda una foto de esta escena como <b>guía de pose y cámara</b> del cuadro. Después volvé al storyboard y tocá ↻ en ese cuadro: se dibuja con ella en esta pose, con su cara, la prenda y tu lugar.</div>
  </div>
</div>
<script type="module">
import * as THREE from "three";
import {OrbitControls} from "three/addons/controls/OrbitControls.js";
import {TransformControls} from "three/addons/controls/TransformControls.js";

const API = "%%API%%";
const q = new URLSearchParams(location.search), SID = q.get("sid"), CID = q.get("cid");
const $ = s => document.querySelector(s);
const lienzo = $("#lienzo");
const renderer = new THREE.WebGLRenderer({canvas: lienzo, antialias: true, preserveDrawingBuffer: true});
renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
const scene = new THREE.Scene(); scene.background = new THREE.Color(0x2a2830);
const camara = new THREE.PerspectiveCamera(50, 9 / 16, 0.05, 100);
scene.add(new THREE.HemisphereLight(0xffffff, 0x444450, 1.1));
const sol = new THREE.DirectionalLight(0xffffff, 1.6); sol.position.set(2, 4, 3); scene.add(sol);
const piso = new THREE.Mesh(new THREE.PlaneGeometry(12, 12), new THREE.MeshStandardMaterial({color: 0x8f8a84, roughness: 1}));
piso.rotation.x = -Math.PI / 2; scene.add(piso);
const grilla = new THREE.GridHelper(12, 24, 0x6f6a64, 0x7d7872); grilla.position.y = 0.002; scene.add(grilla);
let telon = null;

// ── EL MANIQUÍ: articulaciones (Object3D) con piezas grises; mira hacia +Z, su izquierda es +X ──
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

// ── POSES (ángulos en radianes por articulación; lo que no figura queda derecho) ──
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

// ── EDICIÓN ──
const orbita = new OrbitControls(camara, lienzo); orbita.enableDamping = true; orbita.target.set(0, 1.0, 0);
const gizmo = new TransformControls(camara, lienzo); gizmo.setSpace("local"); gizmo.setSize(0.8); scene.add(gizmo);
gizmo.addEventListener("dragging-changed", e => { orbita.enabled = !e.value; });
gizmo.addEventListener("objectChange", () => { if(ACTUAL && gizmo.object === ACTUAL.raiz) ACTUAL.raiz.userData.base = [ACTUAL.raiz.position.x, ACTUAL.raiz.position.z, ACTUAL.raiz.rotation.y]; });
let ACTUAL = null, PARTE = null, MODO = "parte";
function pintarElegido(){
  for(const M of PERSONAS) M.raiz.traverse(o => { if(o.isMesh) o.material = (M === ACTUAL ? (o.parent === PARTE ? ELEGIDO : GRIS) : GRIS); });
  $("#parteInfo").textContent = "Parte elegida: " + (PARTE ? NOMBRES[PARTE.name] || PARTE.name : (MODO === "parte" ? "ninguna (tocá el maniquí)" : "todo el cuerpo"));
  $("#giro").value = ACTUAL ? Math.round(THREE.MathUtils.radToDeg(ACTUAL.raiz.rotation.y + (ACTUAL.raiz.rotation.y > Math.PI ? -2 * Math.PI : 0))) : 0;
}
function modo(m){
  MODO = m; ["modoMover", "modoGirar", "modoParte"].forEach(id => $("#" + id).classList.toggle("on", id === {mover: "modoMover", girar: "modoGirar", parte: "modoParte"}[m]));
  gizmo.detach();
  if(!ACTUAL) return;
  if(m === "mover"){ PARTE = null; gizmo.setMode("translate"); gizmo.showY = false; gizmo.attach(ACTUAL.raiz); }
  else if(m === "girar"){ PARTE = null; gizmo.setMode("rotate"); gizmo.showX = gizmo.showZ = false; gizmo.showY = true; gizmo.attach(ACTUAL.raiz); }
  else { gizmo.setMode("rotate"); gizmo.showX = gizmo.showY = gizmo.showZ = true; if(PARTE) gizmo.attach(PARTE); }
  if(m !== "girar"){ gizmo.showX = gizmo.showZ = true; gizmo.showY = m !== "mover"; }
  pintarElegido();
}
$("#modoMover").onclick = () => modo("mover"); $("#modoGirar").onclick = () => modo("girar"); $("#modoParte").onclick = () => modo("parte");
const ray = new THREE.Raycaster(), ptr = new THREE.Vector2(); let abajo = null;
lienzo.addEventListener("pointerdown", e => { abajo = [e.clientX, e.clientY]; });
lienzo.addEventListener("pointerup", e => {
  if(!abajo || Math.hypot(e.clientX - abajo[0], e.clientY - abajo[1]) > 5 || gizmo.dragging) return;
  const r = lienzo.getBoundingClientRect(); ptr.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  ray.setFromCamera(ptr, camara);
  const metas = []; PERSONAS.forEach(M => M.raiz.traverse(o => { if(o.isMesh) metas.push(o); }));
  const hit = ray.intersectObjects(metas, false)[0];
  if(!hit) return;
  ACTUAL = PERSONAS.find(M => { let ok = false; M.raiz.traverse(o => { if(o === hit.object) ok = true; }); return ok; });
  PARTE = MODO === "parte" ? hit.object.userData.j : null;
  modo(MODO);
});
$("#giro").oninput = () => { if(!ACTUAL) return; ACTUAL.raiz.rotation.y = THREE.MathUtils.degToRad(+$("#giro").value); ACTUAL.raiz.userData.base = [ACTUAL.raiz.position.x, ACTUAL.raiz.position.z, ACTUAL.raiz.rotation.y]; };
$("#resetParte").onclick = () => { if(PARTE) PARTE.rotation.set(0, 0, 0); };
$("#resetTodo").onclick = () => { if(ACTUAL) pose(ACTUAL, "de_pie"); };
$("#poses").innerHTML = Object.entries(POSES).map(([k, [n]]) => `<button data-pose="${k}">${n}</button>`).join("");
document.querySelectorAll("[data-pose]").forEach(b => b.onclick = () => { const M = ACTUAL || PERSONAS[0]; if(M){ pose(M, b.dataset.pose); ACTUAL = M; modo(MODO); } });
$("#agregar").onclick = () => { const M = maniqui(); M.raiz.position.x = 0.7 * (PERSONAS.length - 1); M.raiz.userData.base = [M.raiz.position.x, 0, 0]; ACTUAL = M; PARTE = null; modo("mover"); };
$("#quitar").onclick = () => { if(!ACTUAL || PERSONAS.length < 2) return; gizmo.detach(); scene.remove(ACTUAL.raiz); PERSONAS.splice(PERSONAS.indexOf(ACTUAL), 1); ACTUAL = PERSONAS[0]; PARTE = null; modo("parte"); };

// ── CÁMARA ──
const ALTURA_OJOS = 1.58;
function lente(mm){ camara.fov = 2 * THREE.MathUtils.radToDeg(Math.atan(36 / (2 * mm))); camara.updateProjectionMatrix(); $("#lenteV").textContent = mm; }
$("#lente").oninput = () => lente(+$("#lente").value);
const DIST = {detalle: [0.55, 1.15], primer: [0.8, 1.55], medio: [1.4, 1.25], americano: [2.1, 1.05], entero: [3.0, 0.9]};
let ANG = "ojos", PLANO = "americano";
function ponerCamara(){
  const [d, ty] = DIST[PLANO] || DIST.americano;
  const centro = ACTUAL ? ACTUAL.raiz.position : new THREE.Vector3();
  const frente = ANG === "espalda" ? -1 : 1, yaw = ACTUAL ? ((ACTUAL.raiz.userData.base || [0, 0, 0])[2]) : 0;
  let alt = {ojos: ALTURA_OJOS, bajo: 0.55, alto: 2.35, cenital: 3.2, espalda: ALTURA_OJOS}[ANG] ?? ALTURA_OJOS;
  if(PLANO === "detalle" || PLANO === "primer") alt = {bajo: ty - 0.35, alto: ty + 0.45, cenital: ty + 1.0}[ANG] ?? (ANG === "ojos" || ANG === "espalda" ? ALTURA_OJOS - (PLANO === "detalle" ? 0.35 : 0) : alt);
  const dd = ANG === "cenital" ? 0.25 : d;
  camara.position.set(centro.x + Math.sin(yaw) * dd * frente, alt, centro.z + Math.cos(yaw) * dd * frente);
  orbita.target.set(centro.x, ty, centro.z); orbita.update();
}
document.querySelectorAll("[data-cam]").forEach(b => b.onclick = () => { ANG = b.dataset.cam; ponerCamara(); });
document.querySelectorAll("[data-dist]").forEach(b => b.onclick = () => { PLANO = b.dataset.dist; ponerCamara(); });

// ── FONDO: tu lugar ──
function telonCon(src){
  new THREE.TextureLoader().load(src, tex => {
    tex.colorSpace = THREE.SRGBColorSpace;
    const asp = tex.image.width / tex.image.height, alto = 3.2;
    telon = new THREE.Mesh(new THREE.PlaneGeometry(alto * asp, alto), new THREE.MeshBasicMaterial({map: tex}));
    telon.position.set(0, alto / 2 - 0.05, -2.6); scene.add(telon); telon.visible = $("#fondo").checked;
  });
}
$("#fondo").onchange = () => { if(telon) telon.visible = $("#fondo").checked; };

// ── TAMAÑO: siempre 9:16 ──
function medir(){
  const caja = $("#escena").getBoundingClientRect();
  let h = caja.height - 24, w = h * 9 / 16; if(w > caja.width - 24){ w = caja.width - 24; h = w * 16 / 9; }
  renderer.setSize(Math.floor(w), Math.floor(h), true); camara.aspect = 9 / 16; camara.updateProjectionMatrix();
}
window.addEventListener("resize", medir);
(function loop(){ requestAnimationFrame(loop); orbita.update(); renderer.render(scene, camara); })();

// ── GUARDAR / CARGAR la escena ──
function escena(){
  return {personas: PERSONAS.map(M => ({pose: M.pose || "", pos: [M.raiz.position.x, M.raiz.position.y, M.raiz.position.z],
            rot: [M.raiz.rotation.x, M.raiz.rotation.y, M.raiz.rotation.z],
            j: Object.fromEntries(Object.entries(M.j).map(([k, o]) => [k, [o.rotation.x, o.rotation.y, o.rotation.z].map(v => +v.toFixed(3))]))})),
          cam: {pos: camara.position.toArray(), target: orbita.target.toArray(), lente: +$("#lente").value}, fondo: $("#fondo").checked};
}
function cargar(e){
  for(const p of e.personas || []){
    const M = maniqui(); M.raiz.position.set(...p.pos); M.raiz.rotation.set(...p.rot); M.raiz.userData.base = [p.pos[0], p.pos[2], p.rot[1]]; M.pose = p.pose;
    for(const [k, v] of Object.entries(p.j || {})) if(M.j[k]) M.j[k].rotation.set(...v);
  }
  if(e.cam){ camara.position.fromArray(e.cam.pos); orbita.target.fromArray(e.cam.target); $("#lente").value = e.cam.lente || 35; lente(+$("#lente").value); }
  if(e.fondo === false) $("#fondo").checked = false;
}
function describir(){
  // En palabras, para el pedido: la altura y la distancia de la cámara respecto de ella.
  const M = PERSONAS[0]; if(!M) return "";
  const c = camara.position, p = M.raiz.position, d = Math.hypot(c.x - p.x, c.z - p.z);
  const alt = c.y > 2.6 ? "from high above, looking down" : c.y > 1.85 ? "from above her eye level, looking down" : c.y < 0.9 ? "from a low angle, looking up at her" : "at her eye level";
  const dist = d < 0.7 ? "very close (detail)" : d < 1.1 ? "close-up distance" : d < 1.8 ? "medium shot distance" : d < 2.6 ? "knees-up distance" : "full-body distance";
  const yawCam = Math.atan2(c.x - p.x, c.z - p.z), rel = Math.abs(((yawCam - M.raiz.rotation.y) * 180 / Math.PI + 540) % 360 - 180);
  const lado = rel < 30 ? "from the front" : rel < 70 ? "from a three-quarter angle" : rel < 115 ? "from her side (profile)" : rel < 155 ? "from a three-quarter back angle" : "from behind";
  return `camera ${alt}, ${lado}, ${dist}, ${$("#lente").value}mm lens` + (PERSONAS.length > 1 ? `, ${PERSONAS.length} people in the scene` : "") + (M.pose ? `, pose: ${POSES[M.pose] ? POSES[M.pose][0] : M.pose}` : "");
}
$("#usar").onclick = async () => {
  const est = $("#estado"); est.textContent = "Guardando…";
  gizmo.visible = false; grilla.visible = false; const sel = ACTUAL; const parte = PARTE; ACTUAL = null; PARTE = null; pintarElegido();
  const w = 720, h = 1280, previo = renderer.getSize(new THREE.Vector2());
  renderer.setPixelRatio(1); renderer.setSize(w, h, false); renderer.render(scene, camara);
  const imagen = lienzo.toDataURL("image/jpeg", 0.9);
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1)); renderer.setSize(previo.x, previo.y, true);
  gizmo.visible = true; grilla.visible = true; ACTUAL = sel; PARTE = parte; modo(MODO);
  try{
    const r = await fetch(`${API}/${SID}/cuadro/${CID}/escena`, {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({escena: escena(), imagen, descripcion: describir()})});
    const d = await r.json(); if(!r.ok) throw new Error(d.detail || "No se pudo guardar.");
    est.innerHTML = '<span class="bien">✓ Guardada. Volvé al storyboard y tocá ↻ en este cuadro para dibujarlo con esta pose.</span>';
    try{ if(window.opener) window.opener.postMessage({de: "estudio3d", cid: CID}, "*"); }catch(e){}
  }catch(e){ est.innerHTML = `<span class="mal">${e.message}</span>`; }
};

// ── ARRANQUE ──
(async () => {
  medir(); lente(35);
  let d = {};
  try{ const r = await fetch(`${API}/${SID}/cuadro/${CID}/escena`); d = await r.json(); if(!r.ok) throw new Error(d.detail || "No encuentro el cuadro."); }
  catch(e){ $("#info").innerHTML = `<span class="mal">${e.message}</span>`; }
  if(d.lugar) telonCon(d.lugar);
  if(d.que_se_ve) $("#info").innerHTML = `<b>Cuadro ${d.n || ""}:</b> ${d.que_se_ve.replace(/[<>&]/g, "")}<br>Tocá una parte del maniquí para moverla (aparece un aro: arrastralo).`;
  if(d.escena && (d.escena.personas || []).length) cargar(d.escena);
  else {
    const M = maniqui(); ACTUAL = M;
    if(d.angulo === "espalda") pose(M, "espalda_mira");
    PLANO = {detalle: "detalle", primer: "primer", medio: "medio", americano: "americano", entero: "entero", espejo: "entero"}[d.plano] || "americano";
    ANG = {ojos: "ojos", bajo: "bajo", alto: "alto", cenital: "cenital"}[d.angulo] || "ojos";
    ponerCamara();
  }
  ACTUAL = ACTUAL || PERSONAS[0]; modo("parte");
})();
</script></body></html>
"""
