"""Copiar una toma CALCANDO la foto, gratis y en el navegador.

La foto de referencia pasa por el detector de poses de MediaPipe (corre en el navegador de
la usuaria, sin costo y sin filtros), que además de los puntos del cuerpo da la MÁSCARA exacta
de cada persona. Con eso se arma un CALCO: dentro de cada máscara, la misma foto pasada a gris
y suavizada (se borran detalles finos: rasgos, maquillaje, tatuajes, texturas de la ropa; quedan
las luces y sombras, el volumen, las manos y los dedos), la cara más difuminada, el contorno
marcado y el fondo gris liso. Es la pose, la cámara y el encuadre EXACTOS de la foto, sin copiar
a la persona. Esa imagen es la guía que reciben Seedream y Nano Banana.

Además está el DIBUJO: toda la foto (modelos, muebles y fondo) pasada a líneas negras sobre blanco, como un
libro para colorear, con las caras como un óvalo vacío (las encuentra el detector de caras de MediaPipe).
Es la opción por defecto y la que mejor anda en fotos grupales encimadas.

Si no carga (sin internet a los CDN), se usa el maniquí con IA."""

POSE3D_JS = r"""
const THREE_URL = "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js";
const MP_URL = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs";
const MP_WASM = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm";
const MP_MODEL = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task";
const FACE_MODEL = "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite";
let detector = null, THREE = null, visionMod = null, visionFs = null, detCaras = null;
async function cargarThree(){ if(!THREE) THREE = await import(THREE_URL); return THREE; }

async function cargarDetector(){
  if(detector) return detector;
  const v = visionMod = await import(MP_URL);
  const fs = visionFs = await v.FilesetResolver.forVisionTasks(MP_WASM);
  const opts = d => ({baseOptions:{modelAssetPath:MP_MODEL, delegate:d}, runningMode:"IMAGE",
                      numPoses:4, minPoseDetectionConfidence:0.3, minPosePresenceConfidence:0.3,
                      outputSegmentationMasks:true});
  try { detector = await v.PoseLandmarker.createFromOptions(fs, opts("GPU")); }
  catch(e){ detector = await v.PoseLandmarker.createFromOptions(fs, opts("CPU")); }
  return detector;
}

// Las caras con el detector de caras de MediaPipe (más firme que los puntos del cuerpo cuando hay
// varias personas encimadas). Si no carga, quedan las caras sacadas de los puntos del cuerpo.
async function buscarCaras(img, W, H){
  try{
    if(!detCaras) detCaras = await visionMod.FaceDetector.createFromOptions(visionFs,
      {baseOptions:{modelAssetPath:FACE_MODEL}, runningMode:"IMAGE", minDetectionConfidence:0.5});
    // El detector está hecho para caras grandes en el cuadro: se recorre la foto por partes ampliadas
    // (así encuentra también las caras chicas de una foto grupal) y se juntan los repetidos.
    const NW = img.naturalWidth, NH = img.naturalHeight, k = W / NW, todas = [];
    const mirar = (x0, y0, lado) => {
      const c = lienzo(512, 512); c.getContext("2d").drawImage(img, x0, y0, lado, lado, 0, 0, 512, 512);
      for(const d of detCaras.detect(c).detections){ const bb = d.boundingBox, e = lado/512;
        todas.push({x0:x0 + bb.originX*e, y0:y0 + bb.originY*e, w:bb.width*e, h:bb.height*e, p:d.categories[0].score}); }
    };
    const lado0 = Math.min(NW, NH);
    for(const f of [1, 0.6, 0.4]){ const lado = lado0*f, paso = lado/2;
      for(let y = 0; y + lado <= NH + 1; y += paso) for(let x = 0; x + lado <= NW + 1; x += paso) mirar(x, y, lado);
      if((NH - lado) % paso) for(let x = 0; x + lado <= NW + 1; x += paso) mirar(x, NH - lado, lado);
      if((NW - lado) % paso) for(let y = 0; y + lado <= NH + 1; y += paso) mirar(NW - lado, y, lado); }
    todas.sort((p, q) => q.p - p.p);
    const cruce = (p, q) => { const ix = Math.max(0, Math.min(p.x0+p.w, q.x0+q.w) - Math.max(p.x0, q.x0)),
                                    iy = Math.max(0, Math.min(p.y0+p.h, q.y0+q.h) - Math.max(p.y0, q.y0));
                              return ix*iy / Math.min(p.w*p.h, q.w*q.h); };
    const quedan = [];
    // con 0,8 quedan las caras de verdad (en las pruebas, ≥0,86); manos, escotes y almohadones dan menos
    for(const c of todas.filter(c => c.p >= 0.8)) if(!quedan.some(q => cruce(c, q) > 0.3)) quedan.push(c);
    return quedan.map(b => ({x:(b.x0 + b.w/2)*k, y:(b.y0 + b.h*0.45)*k, r:Math.max(b.w, b.h)*0.62*k, p:b.p}));
  }catch(e){ console.warn("caras", e); return null; }
}

// El DIBUJO: toda la foto (modelos, muebles, fondo) pasada a líneas negras finas sobre blanco, como un
// libro para colorear (bordes de Canny sobre la foto suavizada). Las caras quedan como un óvalo vacío.
function dibujar(img, caras, W, H){
  const c0 = lienzo(W, H), g0 = c0.getContext("2d");
  g0.filter = "grayscale(1) blur(" + Math.max(1.6, W/320).toFixed(2) + "px)"; g0.drawImage(img, 0, 0, W, H);
  const px = g0.getImageData(0, 0, W, H).data, L = new Float32Array(W*H);
  for(let i = 0; i < W*H; i++) L[i] = px[i*4];
  // gradiente (Sobel)
  const mag = new Float32Array(W*H), dir = new Uint8Array(W*H);
  for(let y = 1; y < H-1; y++) for(let x = 1; x < W-1; x++){
    const i = y*W+x;
    const gx = -L[i-W-1] - 2*L[i-1] - L[i+W-1] + L[i-W+1] + 2*L[i+1] + L[i+W+1];
    const gy = -L[i-W-1] - 2*L[i-W] - L[i-W+1] + L[i+W-1] + 2*L[i+W] + L[i+W+1];
    mag[i] = Math.hypot(gx, gy);
    const ang = (Math.atan2(gy, gx) * 180 / Math.PI + 180) % 180;
    dir[i] = ang < 22.5 || ang >= 157.5 ? 0 : ang < 67.5 ? 1 : ang < 112.5 ? 2 : 3;
  }
  // nos quedamos sólo con la cresta del borde (líneas de 1 píxel)
  const nms = new Float32Array(W*H);
  const off = [[1,0],[1,1],[0,1],[-1,1]];
  for(let y = 1; y < H-1; y++) for(let x = 1; x < W-1; x++){
    const i = y*W+x, [dx, dy] = off[dir[i]], m = mag[i];
    if(m >= mag[i + dy*W + dx] && m >= mag[i - dy*W - dx]) nms[i] = m;
  }
  // umbrales según la propia foto: los bordes más marcados siempre, los suaves si siguen a uno marcado
  const muestra = []; for(let i = 0; i < W*H; i += 7) if(nms[i] > 0) muestra.push(nms[i]);
  muestra.sort((p, q) => p - q);
  const alto = muestra[Math.floor(muestra.length*0.86)] || 60, bajo = alto * 0.5;
  const borde = new Uint8Array(W*H), pila = [];
  for(let i = 0; i < W*H; i++) if(nms[i] >= alto){ borde[i] = 1; pila.push(i); }
  while(pila.length){ const i = pila.pop();
    for(const d of [-W-1, -W, -W+1, -1, 1, W-1, W, W+1]){ const j = i + d;
      if(j > 0 && j < W*H && !borde[j] && nms[j] >= bajo){ borde[j] = 1; pila.push(j); } } }
  const out = lienzo(W, H), co = out.getContext("2d"), od = co.createImageData(W, H), o = od.data;
  const grosor = W >= 900 ? 1 : 0;          // en fotos grandes, la línea un poco más gruesa para que se lea
  for(let y = 0; y < H; y++) for(let x = 0; x < W; x++){
    const i = y*W+x; let negro = borde[i];
    if(!negro && grosor && x > 0 && y > 0) negro = borde[i-1] || borde[i-W];
    o[i*4] = o[i*4+1] = o[i*4+2] = negro ? 30 : 255; o[i*4+3] = 255;
  }
  co.putImageData(od, 0, 0);
  co.lineWidth = Math.max(1.5, W/500); co.strokeStyle = "#1e1e1e"; co.fillStyle = "#fff";
  for(const c of caras){ co.beginPath(); co.ellipse(c.x, c.y, c.r*0.72, c.r*0.92, 0, 0, Math.PI*2); co.fill(); co.stroke(); }
  return out;
}

function lienzo(W, H){ const c = document.createElement("canvas"); c.width = W; c.height = H; return c; }

// El CALCO: la foto en gris "yeso" dentro de las máscaras de las personas.
// Un color por persona, de izquierda a derecha: A rosa, B celeste, C verde, D amarillo.
const COLORES = [[236,150,172],[132,176,232],[136,210,150],[232,206,120]];
const COLORES_HEX = [0xec96ac, 0x84b0e8, 0x88d296, 0xe8ce78];
function calcar(img, masks, caras, W, H){
  // 1) La foto suavizada (se van texturas, rasgos y tatuajes; quedan luces y sombras)
  const suave = lienzo(W, H), cs = suave.getContext("2d");
  cs.filter = "grayscale(1) blur(" + Math.max(1.5, W/300).toFixed(1) + "px) contrast(1.15)";
  cs.drawImage(img, 0, 0, W, H);
  // 2) Las caras, mucho más difuminadas (nada de rasgos)
  const muy = lienzo(W, H), cm = muy.getContext("2d");
  cm.filter = "grayscale(1) blur(" + Math.max(4, W/70).toFixed(1) + "px)";
  cm.drawImage(img, 0, 0, W, H);
  cs.filter = "none";
  for(const c of caras){
    cs.save(); cs.beginPath(); cs.ellipse(c.x, c.y, c.r*0.8, c.r, 0, 0, Math.PI*2); cs.clip();
    cs.drawImage(muy, 0, 0); cs.restore();
  }
  const ds = cs.getImageData(0, 0, W, H).data;
  // 3) La máscara unida de todas las personas (a la resolución del calco)
  //    y de quién es cada píxel (la persona con la máscara más fuerte), para pintar a cada una de su color
  const m = new Float32Array(W*H), quien = new Uint8Array(W*H);
  masks.forEach((mk, k) => {
    const mw = mk.width, mh = mk.height, a = mk.data;
    for(let y = 0; y < H; y++){ const sy = Math.min(mh-1, Math.floor(y*mh/H));
      for(let x = 0; x < W; x++){ const v = a[sy*mw + Math.min(mw-1, Math.floor(x*mw/W))];
        if(v > m[y*W+x]){ m[y*W+x] = v; quien[y*W+x] = k; } } }
  });
  // 4) Componer: yeso gris claro con sus sombras dentro, fondo gris liso, contorno marcado
  const out = lienzo(W, H), co = out.getContext("2d");
  const od = co.createImageData(W, H), o = od.data;
  let lmin = 255, lmax = 0;
  for(let i = 0; i < W*H; i++) if(m[i] > .5){ const l = ds[i*4]; if(l < lmin) lmin = l; if(l > lmax) lmax = l; }
  const rango = Math.max(30, lmax - lmin);
  for(let i = 0; i < W*H; i++){
    const a = Math.max(0, Math.min(1, (m[i] - .35) / .3));        // borde suave
    const yeso = 95 + 140 * (ds[i*4] - lmin) / rango;              // 95..235: volumen con sombras
    const fondo = 128;
    const col = COLORES[quien[i] % COLORES.length];
    for(let c = 0; c < 3; c++) o[i*4+c] = Math.round(fondo*(1-a) + Math.min(255, yeso*col[c]/200)*a);
    o[i*4+3] = 255;
  }
  // contorno: donde la máscara cruza 0.5
  for(let y = 1; y < H-1; y++) for(let x = 1; x < W-1; x++){
    const i = y*W+x, adentro = m[i] > .5, q = quien[i];
    const borde = j => m[j] <= .5 || quien[j] !== q;      // contorno también entre una persona y otra
    if(adentro && (borde(i-1) || borde(i+1) || borde(i-W) || borde(i+W))){
      o[i*4] = o[i*4+1] = o[i*4+2] = 45; }
  }
  co.putImageData(od, 0, 0);
  return out;
}


// ── MANIQUÍ 3D de mujer, liso, con sombras ──
function puntos(lm, W, H){
  const P = lm.map(p => ({x:p.x*W, y:-p.y*H, z:-(p.z||0)*W*.6, v:(p.visibility==null?1:p.visibility)}));
  const mid = (a,b) => ({x:(a.x+b.x)/2, y:(a.y+b.y)/2, z:(a.z+b.z)/2, v:Math.min(a.v,b.v)});
  P.m15 = mid(P[17], P[19]); P.m16 = mid(P[18], P[20]);
  P.hombros = mid(P[11], P[12]); P.cadera = mid(P[23], P[24]); P.orejas = mid(P[7], P[8]);
  return P;
}
const dist = (a,b) => Math.hypot(a.x-b.x, a.y-b.y, a.z-b.z);
const lerp = (a,b,t) => ({x:a.x+(b.x-a.x)*t, y:a.y+(b.y-a.y)*t, z:a.z+(b.z-a.z)*t});
function malla(T, g, mat){ const m = new T.Mesh(g, mat); m.castShadow = true; m.receiveShadow = true; return m; }
// Segmento que se afina: de radio ra (en a) a rb (en b), con las puntas redondeadas.
function segmento(T, mat, a, b, ra, rb){
  const L = dist(a,b); if(!(L > 1)) return [];
  const m = malla(T, new T.CylinderGeometry(rb, ra, L, 22, 1, true), mat);
  m.position.set((a.x+b.x)/2, (a.y+b.y)/2, (a.z+b.z)/2);
  m.quaternion.setFromUnitVectors(new T.Vector3(0,1,0), new T.Vector3(b.x-a.x, b.y-a.y, b.z-a.z).normalize());
  return [m, bola(T, mat, a, ra), bola(T, mat, b, rb)];
}
function bola(T, mat, p, r){ const m = malla(T, new T.SphereGeometry(r, 24, 16), mat); m.position.set(p.x,p.y,p.z); return m; }
function ovalo(T, mat, c, dir, rx, ry, rz){
  const m = malla(T, new T.SphereGeometry(1, 32, 24), mat);
  m.position.set(c.x, c.y, c.z); m.quaternion.setFromUnitVectors(new T.Vector3(0,1,0), dir); m.scale.set(rx, ry, rz); return m;
}
function armarManiqui(T, P, mat){
  const g = new T.Group(), add = o => { if(!o) return; (Array.isArray(o) ? o : [o]).forEach(x => x && g.add(x)); };
  const s = Math.max(20, Math.hypot(P[11].x-P[12].x, P[11].y-P[12].y));
  const ok = (...js) => js.every(j => P[j] && P[j].v >= .3);   // lo que no se ve (fuera de cuadro) no se inventa
  // brazos (se afinan hacia la muñeca)
  if(ok(11,13)) add(segmento(T, mat, P[11], P[13], .12*s, .09*s));
  if(ok(13,15)) add(segmento(T, mat, P[13], P[15], .09*s, .065*s));
  if(ok(12,14)) add(segmento(T, mat, P[12], P[14], .12*s, .09*s));
  if(ok(14,16)) add(segmento(T, mat, P[14], P[16], .09*s, .065*s));
  if(ok(15)) add(segmento(T, mat, P[15], P.m15, .065*s, .05*s));
  if(ok(16)) add(segmento(T, mat, P[16], P.m16, .065*s, .05*s));
  // piernas (muslo ancho → rodilla → tobillo fino)
  if(ok(23,25)) add(segmento(T, mat, P[23], P[25], .2*s, .12*s));
  if(ok(25,27)) add(segmento(T, mat, P[25], P[27], .11*s, .065*s));
  if(ok(24,26)) add(segmento(T, mat, P[24], P[26], .2*s, .12*s));
  if(ok(26,28)) add(segmento(T, mat, P[26], P[28], .11*s, .065*s));
  if(ok(27,31)) add(segmento(T, mat, P[27], P[31], .065*s, .045*s));
  if(ok(28,32)) add(segmento(T, mat, P[28], P[32], .065*s, .045*s));
  // torso: pecho, cintura y cadera, alineados con la columna
  const largo = dist(P.hombros, P.cadera);
  if(largo > 1){
    const col = new T.Vector3(P.hombros.x-P.cadera.x, P.hombros.y-P.cadera.y, P.hombros.z-P.cadera.z).normalize();
    const anchoH = Math.max(.6*s, Math.hypot(P[11].x-P[12].x, P[11].y-P[12].y));
    const anchoC = Math.max(.55*s, Math.hypot(P[23].x-P[24].x, P[23].y-P[24].y));
    add(ovalo(T, mat, lerp(P.hombros, P.cadera, .22), col, anchoH*.47, largo*.36, .27*s));   // pecho
    add(ovalo(T, mat, lerp(P.hombros, P.cadera, .55), col, anchoH*.36, largo*.34, .22*s));    // cintura
    add(ovalo(T, mat, lerp(P.hombros, P.cadera, .9), col, anchoC*.8, largo*.3, .28*s));   // cadera
    // busto suave, hacia donde mira el pecho (hacia la cámara si está de frente)
    for(const lado of [11, 12]){
      const b = lerp(lerp(P.hombros, P[lado], .5), P.cadera, .3);
      add(bola(T, mat, {x:b.x, y:b.y, z:b.z + .17*s}, .15*s));
    }
    add(segmento(T, mat, P[11], P[12], .11*s, .11*s));                                      // hombros
  }
  // cuello y cabeza (óvalo, con una nariz chiquita que marca hacia dónde mira)
  add(segmento(T, mat, P.hombros, lerp(P.hombros, P.orejas, .6), .11*s, .1*s));
  const rc = Math.max(dist(P[7],P[8])*.68, .3*s);
  add(ovalo(T, mat, P.orejas, new T.Vector3(0,1,0), rc*.82, rc*1.05, rc*.9));
  const hacia = new T.Vector3(P[0].x-P.orejas.x, P[0].y-P.orejas.y, P[0].z-P.orejas.z);
  if(hacia.length() > 1){ hacia.setLength(rc*.85); add(bola(T, mat, {x:P.orejas.x+hacia.x, y:P.orejas.y+hacia.y, z:P.orejas.z+hacia.z}, rc*.16)); }
  return g;
}
function renderManiqui(T, personas, W, H){
  const renderer = new T.WebGLRenderer({antialias:true, preserveDrawingBuffer:true});
  renderer.setSize(W, H); renderer.setPixelRatio(1);
  renderer.shadowMap.enabled = true; renderer.shadowMap.type = T.PCFSoftShadowMap;
  const scene = new T.Scene(); scene.background = new T.Color(0x8e8e8e);
  const cam = new T.OrthographicCamera(0, W, 0, -H, 0.1, 40000); cam.position.set(0, 0, 10000);
  scene.add(new T.HemisphereLight(0xffffff, 0x555555, .9));
  const sol = new T.DirectionalLight(0xffffff, 2.2);
  sol.position.set(W*.15, H*.2, 6000); sol.target.position.set(W*.55, -H*.6, -2000);
  sol.castShadow = true; sol.shadow.mapSize.set(2048, 2048);
  const sc = sol.shadow.camera; sc.left = -W*1.5; sc.right = W*1.5; sc.top = H*1.5; sc.bottom = -H*1.5; sc.near = 1; sc.far = 40000;
  sol.shadow.bias = -0.0005; scene.add(sol); scene.add(sol.target);
  let zmin = 1e9;
  personas.forEach((lm, k) => { const P = puntos(lm, W, H);
    const mat = new T.MeshStandardMaterial({color:COLORES_HEX[k % COLORES_HEX.length], roughness:.6, metalness:0});
    const g = armarManiqui(T, P, mat); scene.add(g);
    for(const p of lm) zmin = Math.min(zmin, -(p.z||0)*W*.6); });
  // pared del fondo: recibe las sombras (dan la profundidad: qué va adelante y qué atrás)
  const pared = new T.Mesh(new T.PlaneGeometry(W*4, H*4), new T.MeshStandardMaterial({color:0x9a9a9a, roughness:1}));
  pared.position.set(W/2, -H/2, zmin - W*.35); pared.receiveShadow = true; scene.add(pared);
  renderer.render(scene, cam);
  return renderer.domElement;
}

function abrirModal(){
  let ov = document.getElementById("pose3d-ov");
  if(ov) ov.remove();
  ov = document.createElement("div"); ov.id = "pose3d-ov";
  ov.style.cssText = "position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.82);display:flex;align-items:center;justify-content:center;padding:12px";
  ov.innerHTML = '<div style="background:var(--card,#1e1b1b);color:inherit;border-radius:14px;padding:14px;max-width:980px;width:100%;max-height:96vh;overflow:auto">'
    + '<div style="font-weight:600;margin-bottom:6px">Copiar la toma de la foto (gratis): dibujo, calco o maniquí 3D</div>'
    + '<p class="hint" id="p3-est" style="margin:0 0 8px">Cargando el detector…</p>'
    + '<div style="display:flex;gap:6px;margin:0 0 8px"><button class="ghost" id="p3-m-dibujo">🖍️ Dibujo</button><button class="ghost" id="p3-m-calco">✏️ Calco</button>'
    + '<button class="ghost" id="p3-m-3d">🧍 Maniquí 3D</button></div>'
    + '<div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-start">'
    + '<img id="p3-foto" style="max-width:48%;max-height:62vh;border-radius:10px">'
    + '<div id="p3-lienzo" style="max-width:48%"></div></div>'
    + '<p class="hint" style="margin:8px 0">El dibujo pasa toda la foto a líneas, como un libro para colorear (modelos, muebles y fondo; las caras quedan vacías). El calco copia la pose, la cámara, el encuadre y las sombras de la foto, sin la cara, el maquillaje, los tatuajes ni la ropa. El maniquí 3D es una figura de mujer lisa, con sombras, armada con los puntos del cuerpo. Cada persona va de un color: de izquierda a derecha, modelo A rosa, B celeste, C verde.</p>'
    + '<div style="display:flex;gap:8px;flex-wrap:wrap">'
    + '<button class="go" id="p3-usar" disabled>✅ Usar esta guía (gratis)</button>'
    + '<button class="ghost" id="p3-ia">✨ Maniquí con IA (~US$0,07)</button>'
    + '<button class="ghost" id="p3-no">Cancelar</button></div></div>';
  document.body.appendChild(ov);
  return ov;
}

// Devuelve una promesa: {modo:"3d", guia, personas} | {modo:"ia"} | null (cancelado)
async function copiarToma(file){
  const ov = abrirModal();
  const $ = id => ov.querySelector("#"+id);
  const est = t => { $("p3-est").textContent = t; };
  $("p3-foto").src = URL.createObjectURL(file);
  return new Promise(async resolve => {
    $("p3-no").onclick = () => { ov.remove(); resolve(null); };
    $("p3-ia").onclick = () => { ov.remove(); resolve({modo:"ia"}); };
    try{
      const img = $("p3-foto"); await img.decode();
      const det = await cargarDetector();
      est("Calcando…");
      const res = det.detect(img);
      const personas = (res && res.landmarks) || [];
      const crudas = (res && res.segmentationMasks) || [];
      const sinGente = !personas.length || !crudas.length;   // el dibujo no necesita encontrar a nadie
      const esc = Math.min(1, 1024 / Math.max(img.naturalWidth, img.naturalHeight));
      const W = Math.round(img.naturalWidth*esc), H = Math.round(img.naturalHeight*esc);
      let masks = crudas.map(mk => { const d = {width:mk.width, height:mk.height, data:mk.getAsFloat32Array()};
                                       try{ mk.close && mk.close(); }catch(e){} return d; });
      // Las caras: entre las orejas, del tamaño de la cabeza
      const caras = personas.map(lm => {
        const a = lm[7], b = lm[8], n = lm[0];
        const r = Math.max(Math.hypot((a.x-b.x)*W, (a.y-b.y)*H) * 0.85, W*0.03);
        return {x:(a.x+b.x+n.x)/3*W, y:(a.y+b.y+n.y)/3*H, r};
      });
      // ¿El detector entendió los cuerpos? En fotos con varias personas muy encimadas a veces junta
      // todo en un esqueleto encogido (hombros del ancho de la cabeza): eso no sirve de guía.
      const malas = sinGente ? 0 : personas.filter(lm => {
        const d = (a,b) => Math.hypot((lm[a].x-lm[b].x)*W, (lm[a].y-lm[b].y)*H);
        const cab = Math.max(d(7,8), 1), torso = Math.hypot(((lm[11].x+lm[12].x)-(lm[23].x+lm[24].x))*W/2, ((lm[11].y+lm[12].y)-(lm[23].y+lm[24].y))*H/2);
        return d(11,12) < 1.4*cab || torso < 1.3*cab;
      }).length;
      const aviso = malas ? " ⚠️ El detector no entendió bien " + (malas === personas.length ? "los cuerpos" : malas + " de los cuerpos")
                          + " (pasa con personas muy encimadas): para esta foto usá 🖍️ Dibujo o ✨ Maniquí con IA." : "";
      // De izquierda a derecha: A, B, C… (para el maniquí)
      //    y cada una con su color (A rosa, B celeste, C verde, D amarillo), igual en el calco y en el maniquí
      const idx = personas.map((_, k) => k).sort((a, b) => (personas[a][23].x+personas[a][24].x) - (personas[b][23].x+personas[b][24].x));
      const orden = idx.map(k => personas[k]);
      if(masks.length === personas.length) masks = idx.map(k => masks[k]);
      // Caras del detector de caras, pero sólo las que caen sobre una persona (la máscara) y de un tamaño
      // razonable: así no se tapan como "cara" una mano, un libro o un almohadón.
      const enPersona = (x, y) => !masks.length || masks.some(mk => (mk.data[Math.min(mk.height-1, Math.floor(y*mk.height/H))*mk.width
                                                   + Math.min(mk.width-1, Math.floor(x*mk.width/W))] || 0) > .5);
      const carasMp = ((await buscarCaras(img, W, H)) || []).filter(c => c.r < 0.22*Math.min(W, H) && enPersona(c.x, c.y));
      const vistas = {calco: sinGente ? null : calcar(img, masks, carasMp && carasMp.length ? carasMp : caras, W, H), m3d: null,
                      dibujo: dibujar(img, carasMp && carasMp.length ? carasMp : caras, W, H)};
      let modo = "dibujo";
      const mostrar = async m => {
        if(m === "m3d" && !vistas.m3d){
          est("Armando el maniquí 3D…");
          try{ vistas.m3d = renderManiqui(await cargarThree(), orden, W, H); }
          catch(e){ est("No pude armar el maniquí 3D (" + (e && e.message || e) + "). Usá el calco."); return; }
        }
        modo = m; const cv = vistas[m];
        cv.style.cssText = "width:100%;height:auto;border-radius:10px";
        $("p3-lienzo").innerHTML = ""; $("p3-lienzo").appendChild(cv);
        $("p3-m-dibujo").style.outline = m === "dibujo" ? "2px solid var(--rose-deep,#e0607e)" : "";
        $("p3-m-calco").style.outline = m === "calco" ? "2px solid var(--rose-deep,#e0607e)" : "";
        $("p3-m-3d").style.outline = m === "m3d" ? "2px solid var(--rose-deep,#e0607e)" : "";
        est(({calco:"Calco", m3d:"Maniquí 3D", dibujo:"Dibujo"})[m] + (sinGente ? " (no encontré personas: sólo hay dibujo)." : " de " + personas.length + (personas.length === 1 ? " persona." : " personas."))
            + " Elegí el que mejor copie la toma." + aviso);
      };
      $("p3-m-dibujo").onclick = () => mostrar("dibujo");
      if(sinGente){ $("p3-m-calco").style.display = $("p3-m-3d").style.display = "none"; }
      $("p3-m-calco").onclick = () => mostrar("calco");
      $("p3-m-3d").onclick = () => mostrar("m3d");
      await mostrar("dibujo");
      // CUÁNTO OCUPAN en el cuadro (de la cabeza a los pies, en % del alto y del ancho): con números
      // la IA respeta la distancia de la foto; con "lejos" o "chiquita" en un texto largo, no.
      const medirEncuadre = () => {
        const pts = [];
        orden.forEach(lm => lm.forEach(q => { if((q.visibility == null ? 1 : q.visibility) > .3) pts.push(q); }));
        if(!pts.length) return null;
        const ys = pts.map(q => q.y), xs = pts.map(q => q.x);
        let y0 = Math.min(...ys), y1 = Math.max(...ys);
        y0 = Math.max(0, y0 - (y1 - y0) * .07);             // la coronilla está arriba de los ojos
        y1 = Math.min(1, y1 + (y1 - y0) * .02);
        const pc = v => Math.round(Math.max(0, Math.min(1, v)) * 100);
        return {arriba: pc(y0), abajo: pc(y1), izq: pc(Math.min(...xs)), der: pc(Math.max(...xs)),
                alto: pc(y1 - y0)};
      };
      $("p3-usar").disabled = false;
      $("p3-usar").onclick = () => {
        const guia = vistas[modo].toDataURL("image/jpeg", .9);
        ov.remove(); resolve({modo:"3d", guia, personas: personas.length, tipo: modo, encuadre: medirEncuadre()});
      };
    }catch(err){
      console.warn("calco", err);
      est("No pude calcar en este navegador (" + (err && err.message || err) + "). Usá ✨ Maniquí con IA.");
    }
  });
}
window.Pose3D = {copiarToma};
"""
