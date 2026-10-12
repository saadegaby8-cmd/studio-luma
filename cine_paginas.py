# -*- coding: utf-8 -*-
"""Las páginas del MODO CINE (aparte de cine_luma.py porque son largas)."""

_ESTILO = r"""
  :root{--ink:#ecebf1;--ink-soft:#96919f;--line:#2c2a34;--ivory:#131218;--card:#1b1a21;--card-2:#232128;--rose:#c9a86b;--rose-deep:#d8b878;--ok:#5fae86;--bad:#e0736f}
  *{box-sizing:border-box} body{margin:0;background:var(--ivory);color:var(--ink);font-family:Jost,system-ui,sans-serif;font-size:15px;line-height:1.5}
  h2{font-family:'Bodoni Moda',serif;font-weight:600;font-size:22px;margin:0 0 6px} h3{font-size:16px;margin:0 0 6px;font-weight:500}
  input,textarea,select{width:100%;background:var(--card-2);color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:9px 11px;font:inherit;font-size:14px}
  button{background:var(--card-2);color:var(--ink);border:1px solid var(--line);border-radius:999px;padding:8px 15px;font:inherit;font-size:13.5px;cursor:pointer}
  button.go{background:linear-gradient(150deg,var(--rose-deep),var(--rose));color:#17140d;border:none;font-weight:500} button:disabled{opacity:.45;cursor:default}
  button.sm{padding:5px 11px;font-size:12.5px} button.on{background:var(--rose);color:#17140d;border-color:var(--rose)}
  label{display:block;font-size:12.5px;color:var(--ink-soft);margin:8px 0 3px}
  .hint{color:var(--ink-soft);font-size:12.5px} .mal{color:var(--bad)} .bien{color:var(--ok)}
  .spin{display:inline-block;width:12px;height:12px;border:2px solid var(--ink-soft);border-top-color:var(--rose);border-radius:50%;animation:g 1s linear infinite;vertical-align:-2px} @keyframes g{to{transform:rotate(360deg)}}
"""

PAGINA = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cine · Studio Luma</title>
<link href="https://fonts.googleapis.com/css2?family=Bodoni+Moda:wght@500;600&family=Jost:wght@400;500&display=swap" rel="stylesheet">
<style>""" + _ESTILO + r"""
  main{max-width:1180px;margin:0 auto;padding:16px} .card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:18px;margin-bottom:16px}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:10px} .row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
  .fila{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px}
  .paso{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--rose-deep);margin-bottom:4px}
  .actor{display:grid;grid-template-columns:1.2fr 1.4fr 1fr auto;gap:8px;align-items:end;margin-bottom:6px}
  .grilla{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px;margin-top:10px}
  .pt{background:var(--card-2);border:1px solid var(--line);border-radius:14px;padding:8px;font-size:13px}
  .pt.ok{border-color:var(--ok)} .pt .img{aspect-ratio:9/16;border-radius:10px;background:#0f0e13 center/cover no-repeat;position:relative;display:flex;align-items:center;justify-content:center;color:var(--ink-soft);font-size:12px;text-align:center;padding:8px}
  .pt .num{position:absolute;top:6px;left:6px;background:#000a;border-radius:999px;padding:1px 8px;font-size:12px;color:var(--ink)}
  .pt .t{position:absolute;top:6px;right:6px;background:#000a;border-radius:999px;padding:1px 8px;font-size:12px;color:var(--ink)}
  .pt .gu{position:absolute;bottom:6px;right:6px;width:44px;border-radius:6px;border:1px solid #fff4}
  .pt textarea{font-size:12.5px;margin-top:6px}
  video{width:100%;max-width:380px;border-radius:12px;display:block;margin-top:10px}
  @media(max-width:700px){.row,.row3,.actor{grid-template-columns:1fr}}
</style></head><body><main>
<div class="card">
  <h2>🎞 Cine <small style="font-family:Jost;font-size:12px;color:var(--ink-soft)">v%%VERSION%%</small></h2>
  <p class="hint">De una idea a un <b>corto o un comercial</b>: el director escribe la película, arma el <b>set en 3D</b> (el mundo, los objetos, tus personajes) y el
  <b>recorrido de la cámara</b> (un dron continuo, una grúa…). Dibuja los <b>puntos clave</b> con tus personajes y sus prendas, vos los aprobás, y se filma de punto a punto:
  unidos, <b>un solo plano continuo</b>, con color de cine, la voz y el cartel final.</p>
  <div id="lista" class="hint"></div>
</div>

<div class="card" id="c1">
  <div class="paso">Paso 1</div><h3>La idea</h3>
  <textarea id="idea" rows="4" placeholder="ej: comercial de mi temporada de verano de mallas y bikinis: empieza con el dron muy alto en el cielo, una playa paradisíaca al atardecer; el dron baja a toda velocidad, recorre la isla y se va posando sobre mis personajes con los trajes de baño, todo con efecto cinematográfico"></textarea>
  <div class="row3"><div><label>Qué es</label><select id="tipo"></select></div><div><label>Duración</label><select id="duracion"></select></div>
    <div><label>Voz en off</label><select id="voz"><option value="1">Sí (con la voz del primer personaje)</option><option value="">No, sólo imagen</option></select></div></div>
  <label>🎭 El elenco: cada personaje con su prenda (de Mis prendas)</label>
  <div id="elenco"></div>
  <div class="fila"><button class="sm" id="masActor">＋ Otro personaje</button></div>
  <div class="fila"><button class="go" id="crear">🎬 El director arma la película</button><span id="e1"></span></div>
</div>

<div class="card" id="c2" style="display:none">
  <div class="paso">Paso 2</div><h3 id="titulo">La película</h3>
  <p class="hint" id="logline"></p>
  <div class="row"><div><label>El mundo</label><textarea id="mundo" rows="3"></textarea></div><div><label>La luz</label><select id="luz"></select>
    <div class="fila"><button class="go" id="set">🌍 Abrir el set 3D (objetos, cámara y animatic)</button></div></div></div>
  <p class="hint">Cada punto es un momento del recorrido de la cámara: se dibuja con tus personajes y sus prendas (y con la guía del set 3D si la usaste). Entre un punto y el siguiente la cámara vuela de corrido; un ✂ es un corte.</p>
  <p class="hint" id="modoInfo"></p>
  <div class="fila"><button class="go" id="dibujar">🎨 Dibujar los puntos que faltan</button><button id="aprobarTodos">✓ Aprobar todos</button><button id="rehacer">✍️ Que el director lo rehaga</button><span class="hint" id="costos"></span></div>
  <div id="e2" style="margin-top:8px"></div>
  <div class="grilla" id="puntos"></div>
</div>

<div class="card" id="c3" style="display:none">
  <div class="paso">Paso 3</div><h3>Filmar</h3>
  <p class="hint">Cada tramo arranca exacto en un punto y termina exacto en el siguiente (Kling O3 por fal, con foto de inicio y de final y las caras de tu elenco): unidos, un plano continuo.</p>
  <div class="row"><div><label>Motor de los puntos</label><select id="mCuadros"></select></div><div><label>Motor del video</label><select id="mVideo"></select></div></div>
  <label>🎙️ La voz en off</label><textarea id="guion" rows="3"></textarea>
  <div class="row3"><div><label>Cartel final: título</label><input id="cTitulo"></div><div><label>Cartel final: línea</label><input id="cLinea"></div>
    <div><label>Placa final con tu logo</label><label style="display:flex;gap:6px;align-items:center;margin:0"><input type="checkbox" id="placa" style="width:auto"> Sí, al final (logo grande + título)</label>
      <div class="hint" id="logoInfo"></div><label style="margin:4px 0 0;cursor:pointer;color:var(--rose-deep)"><input type="file" accept="image/*" id="logoFile" style="display:none">⬆ Subir / cambiar mi logo</label></div></div>
  <div class="fila"><button class="go" id="filmar">🎥 Filmar</button><button id="montar">✂️ Volver a unir (sin filmar)</button><span id="e3"></span></div>
  <div id="salida"></div>
</div>
<script>
const API = "%%API%%", PREF = "%%PREFIJO%%"; let CFG = {}, PEL = null, SIGUIENDO = null;
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function api(p, o = {}){ const r = await fetch(API + p, Object.assign({headers: {"Content-Type": "application/json"}}, o)); let d = {}; try{ d = await r.json(); }catch(e){}
  if(!r.ok) throw new Error(d.detail || ("HTTP " + r.status)); return d; }
const post = (p, b) => api(p, {method: "POST", body: JSON.stringify(b || {})});
const put = (p, b) => api(p, {method: "PUT", body: JSON.stringify(b || {})});
const opts = (obj, sel) => Object.entries(obj).map(([k, v]) => `<option value="${k}" ${String(k) === String(sel) ? "selected" : ""}>${esc(v)}</option>`).join("");
const LUCES = {atardecer: "Atardecer dorado", amanecer: "Amanecer", mediodia: "Mediodía (sol fuerte)", nublado: "Nublado", noche: "Noche", estudio: "Estudio"};

function actor(e = {}){
  const d = document.createElement("div"); d.className = "actor";
  const pjs = Object.fromEntries(CFG.personajes.map(x => [x.id, x.nombre]));
  const prs = Object.assign({"": "(sin prenda de la tienda)"}, Object.fromEntries(CFG.prendas.map(x => [x.id, x.nombre])));
  d.innerHTML = `<div><label>Personaje</label><select data-a="pid">${opts(pjs, e.pid)}</select></div><div><label>Prenda</label><select data-a="prenda">${opts(prs, e.prenda || "")}</select></div><div><label>Color</label><select data-a="color"></select></div><button class="sm" data-a="x">✕</button>`;
  const colores = () => { const pr = CFG.prendas.find(x => x.id === d.querySelector('[data-a="prenda"]').value); d.querySelector('[data-a="color"]').innerHTML = opts(Object.fromEntries(((pr || {}).colores || ["—"]).map((c, i) => [i, c || ("Color " + (i + 1))])), e.color || 0); };
  d.querySelector('[data-a="prenda"]').onchange = colores; colores();
  d.querySelector('[data-a="x"]').onclick = () => d.remove();
  $("#elenco").appendChild(d);
}
async function seguir(jid, est){ if(!jid) return; SIGUIENDO = jid; const t0 = Date.now();
  try{ for(;;){ const j = await api("/job/" + jid);
      if(j.estado === "listo"){ est.innerHTML = j.aviso ? `<span class="mal">${esc(j.aviso)}</span>` : '<span class="bien">Listo.</span>'; break; }
      if(j.estado === "error"){ est.innerHTML = `<span class="mal">${esc(j.error || "Falló")}</span>`; break; }
      const s = Math.round((Date.now() - (j.inicio ? j.inicio * 1000 : t0)) / 1000);
      est.innerHTML = `<span class="spin"></span> ${esc(j.paso || "En cola…")} <span class="hint">${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}</span>`;
      if(PEL){ try{ PEL = (await api("/" + PEL.id)).pelicula; pintar(); }catch(e){} }
      await new Promise(r => setTimeout(r, 4000)); } }
  catch(e){ est.textContent = "Falló: " + e.message; }
  SIGUIENDO = null; if(PEL){ PEL = (await api("/" + PEL.id)).pelicula; pintar(); } lista(); }
function pintar(){
  if(!PEL) return; const hay = (PEL.puntos || []).length;
  $("#c2").style.display = hay ? "" : "none"; $("#c3").style.display = hay ? "" : "none";
  $("#titulo").textContent = (PEL.titulo || "La película") + ` · ${PEL.puntos.length} puntos · ${PEL.seg_total} s`;
  $("#logline").textContent = PEL.logline || "";
  if(document.activeElement !== $("#mundo")) $("#mundo").value = PEL.mundo_en || "";
  $("#luz").innerHTML = opts(LUCES, PEL.luz || "atardecer");
  if(document.activeElement !== $("#guion")) $("#guion").value = PEL.guion || "";
  const ci = PEL.cierre || {}; if(document.activeElement !== $("#cTitulo")) $("#cTitulo").value = ci.titulo || ""; if(document.activeElement !== $("#cLinea")) $("#cLinea").value = ci.linea || "";
  $("#placa").checked = PEL.placa !== false;
  $("#logoInfo").innerHTML = CFG.logo ? `<img src="${API}/logo.png?t=${Date.now() % 100000}" style="height:28px;vertical-align:middle;background:#fff2;border-radius:4px;padding:2px"> tu logo` : "Todavía no subiste tu logo.";
  $("#mCuadros").innerHTML = opts(CFG.motores_cuadro || {gemini: "Gemini"}, PEL.motor_cuadros || "gemini");
  $("#mVideo").innerHTML = opts(CFG.motores_video || {fal_kling_o3: "Kling O3 · fal"}, PEL.motor_video || "fal_kling_o3");
  const faltan = (PEL.una_pelicula ? PEL.puntos.slice(0, 1) : PEL.puntos).filter(x => !x.img).length, sinAp = PEL.puntos.filter(x => x.img && !x.aprobado).length;
  $("#costos").textContent = (faltan ? `faltan ${faltan} (~US$${PEL.costo_puntos.toFixed(2)})` : "todos dibujados") + (sinAp ? ` · ${sinAp} sin aprobar` : "");
  const UNA = !!PEL.una_pelicula;
  const listos = PEL.puntos.length && (UNA ? (PEL.puntos[0].img && PEL.puntos[0].aprobado) : PEL.puntos.every(x => x.img && x.aprobado));
  $("#filmar").textContent = listos ? `🎥 Filmar la película (${PEL.seg_total} s · ~US$${PEL.costo_video.toFixed(2)})` : (UNA ? "🎥 Filmar (primero aprobá el primer cuadro)" : "🎥 Filmar (primero aprobá todos los puntos)");
  $("#modoInfo").innerHTML = UNA ? "🎬 <b>Una sola película</b>: sólo se dibuja y se aprueba el <b>primer cuadro</b>. Los demás puntos son la <b>línea de tiempo</b> del prompt (lo que pasa en cada segundo): editalos como texto. El video sale de corrido, sin cortes." : "Punto a punto: cada punto se dibuja y se aprueba, y se filma de uno al siguiente.";
  $("#filmar").disabled = !listos || !!SIGUIENDO; $("#montar").style.display = (PEL.tramos || []).length && PEL.tramos.every(t => t.hecho) ? "" : "none";
  $("#salida").innerHTML = PEL.video_url ? `<video src="${PEL.video_url}" controls playsinline></video><div class="fila"><a href="${PEL.video_url}" download>⬇️ Bajar</a>${PEL.drive ? ` · <a href="${PEL.drive}" target="_blank">En tu Drive</a>` : ""}</div>` : "";
  $("#puntos").innerHTML = PEL.puntos.map((x, i) => `<div class="pt ${x.aprobado ? "ok" : ""}" data-p="${x.id}">
    <div class="img" style="${x.url ? `background-image:url('${x.url}')` : ""}"><span class="num">${x.corte ? "✂ " : ""}${i + 1}</span><span class="t">${x.t} s</span>${x.url ? "" : (x.error ? `<span class="mal">${esc(x.error)}</span>` : (UNA && i ? "📝 En la línea de tiempo (no se dibuja)" : "Sin dibujar"))}${x.guia_url ? `<img class="gu" src="${x.guia_url}" title="guía del set 3D">` : ""}</div>
    ${x.desactualizado ? '<div class="hint mal">Cambió: volvé a dibujarlo (↻).</div>' : ""}${x.nota ? `<div class="hint">${esc(x.nota)}</div>` : ""}
    ${x.qc ? `<div class="hint ${x.qc.ok ? "bien" : "mal"}">🔍 ${x.qc.puntaje}/10${!x.qc.ok && (x.qc.diferencias || []).length ? " · " + esc(x.qc.diferencias.join(" · ")) : ""}</div>` : ""}
    <textarea data-k="es" rows="2">${esc(x.es || "")}</textarea>
    ${i ? `<div class="hint">🎥 ${esc(x.mov_en || "")}</div>` : ""}
    <input data-cor placeholder="qué corregir al rehacer" style="margin-top:6px">
    <div class="fila" style="margin-top:6px">${x.img ? (x.aprobado ? '<span class="bien">✓</span> <button class="sm" data-a="noap">Quitar</button>' : '<button class="sm go" data-a="ap">✓ Aprobar</button>') : ""}
      <button class="sm" data-a="re">${x.img ? "↻" : "🎨"}</button>${i ? `<button class="sm ${x.corte ? "on" : ""}" data-a="corte" title="Corte en vez de vuelo continuo">✂</button>` : ""}</div></div>`).join("");
  document.querySelectorAll(".pt").forEach(el => { const pid = el.dataset.p;
    el.querySelector('[data-k="es"]').onchange = async ev => { try{ PEL = (await put(`/${PEL.id}/punto/${pid}`, {es: ev.target.value})).pelicula; pintar(); }catch(e){ $("#e2").textContent = e.message; } };
    el.querySelectorAll("[data-a]").forEach(b => b.onclick = async () => { try{ const a = b.dataset.a;
      if(a === "ap" || a === "noap"){ PEL = (await post(`/${PEL.id}/punto/${pid}/aprobar`, {ok: a === "ap"})).pelicula; pintar(); }
      if(a === "re"){ const d = await post(`/${PEL.id}/dibujar`, {pids: [pid], correccion: el.querySelector("[data-cor]").value}); seguir(d.job, $("#e2")); }
      if(a === "corte"){ const x = PEL.puntos.find(y => y.id === pid); PEL = (await put(`/${PEL.id}/punto/${pid}`, {corte: !x.corte})).pelicula; pintar(); }
    }catch(e){ $("#e2").innerHTML = `<span class="mal">${esc(e.message)}</span>`; } }); });
}
async function lista(){ try{ const d = await api("/lista");
  $("#lista").innerHTML = d.peliculas.length ? "Tus películas: " + d.peliculas.map(x => `<a href="#" data-l="${x.id}">${esc(x.titulo)}${x.video ? " 🎬" : ""}</a>`).join(" · ") : "";
  document.querySelectorAll("[data-l]").forEach(a => a.onclick = async ev => { ev.preventDefault(); PEL = (await api("/" + a.dataset.l)).pelicula; try{ localStorage.setItem("cine_id", PEL.id); }catch(e){} pintar(); }); }catch(e){} }
$("#masActor").onclick = () => actor();
$("#crear").onclick = async () => { try{
    const elenco = Array.from(document.querySelectorAll(".actor")).map(d => ({pid: d.querySelector('[data-a="pid"]').value, prenda: d.querySelector('[data-a="prenda"]').value, color: +d.querySelector('[data-a="color"]').value}));
    const d = await post("/nueva", {idea: $("#idea").value, tipo: $("#tipo").value, duracion: +$("#duracion").value, voz: !!$("#voz").value, elenco});
    PEL = d.pelicula; try{ localStorage.setItem("cine_id", PEL.id); }catch(e){} pintar(); seguir(d.job, $("#e1")); }
  catch(e){ $("#e1").innerHTML = `<span class="mal">${esc(e.message)}</span>`; } };
$("#rehacer").onclick = async () => { if(!confirm("¿Que el director rehaga la película? Se reemplazan los puntos y sus dibujos.")) return;
  try{ const d = await post(`/${PEL.id}/director`, {idea: $("#idea").value || ""}); seguir(d.job, $("#e2")); }catch(e){ $("#e2").textContent = e.message; } };
$("#dibujar").onclick = async () => { try{ const d = await post(`/${PEL.id}/dibujar`, {}); seguir(d.job, $("#e2")); }catch(e){ $("#e2").innerHTML = `<span class="mal">${esc(e.message)}</span>`; } };
$("#aprobarTodos").onclick = async () => { try{ PEL = (await post(`/${PEL.id}/punto/todos/aprobar`, {ok: true})).pelicula; pintar(); }catch(e){ $("#e2").textContent = e.message; } };
$("#set").onclick = () => window.open(`${PREF}/set?cid=${PEL.id}`, "_blank");
$("#mundo").onchange = async () => { PEL = (await put("/" + PEL.id, {mundo_en: $("#mundo").value})).pelicula; pintar(); };
$("#luz").onchange = async () => { PEL = (await put("/" + PEL.id, {luz: $("#luz").value})).pelicula; pintar(); };
$("#guion").onchange = async () => { PEL = (await put("/" + PEL.id, {guion: $("#guion").value})).pelicula; pintar(); };
$("#cTitulo").onchange = $("#cLinea").onchange = async () => { PEL = (await put("/" + PEL.id, {cierre: {titulo: $("#cTitulo").value, linea: $("#cLinea").value}})).pelicula; pintar(); };
$("#placa").onchange = async () => { PEL = (await put("/" + PEL.id, {placa: $("#placa").checked})).pelicula; pintar(); };
$("#mCuadros").onchange = async () => { PEL = (await put("/" + PEL.id, {motor_cuadros: $("#mCuadros").value})).pelicula; pintar(); };
$("#mVideo").onchange = async () => { PEL = (await put("/" + PEL.id, {motor_video: $("#mVideo").value})).pelicula; pintar(); };
$("#logoFile").onchange = async e => { const f = e.target.files[0]; if(!f) return;
  const imagen = await new Promise((ok, mal) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = mal; r.readAsDataURL(f); });
  try{ await post("/logo", {imagen}); CFG.logo = true; pintar(); $("#e3").innerHTML = '<span class="bien">✓ Logo guardado: va en la placa final (✂️ Volver a unir si ya está filmada).</span>'; }catch(er){ $("#e3").innerHTML = `<span class="mal">${esc(er.message)}</span>`; } };
$("#filmar").onclick = async () => { try{ const d = await post(`/${PEL.id}/filmar`); seguir(d.job, $("#e3")); }catch(e){ $("#e3").innerHTML = `<span class="mal">${esc(e.message)}</span>`; } };
$("#montar").onclick = async () => { try{ const d = await post(`/${PEL.id}/montar`); seguir(d.job, $("#e3")); }catch(e){ $("#e3").innerHTML = `<span class="mal">${esc(e.message)}</span>`; } };
window.addEventListener("message", async e => { if(e.data && e.data.de === "cine_set" && PEL){ PEL = (await api("/" + PEL.id)).pelicula; pintar(); } });
(async () => {
  CFG = await api("/config");
  $("#tipo").innerHTML = opts(CFG.tipos, "comercial"); $("#duracion").innerHTML = opts(Object.fromEntries(CFG.duraciones.map(d => [d, d + " s"])), 20);
  if(!CFG.personajes.length) $("#elenco").innerHTML = '<p class="hint mal">Todavía no tenés personajes con retrato aprobado (Personajes → ficha).</p>'; else actor();
  lista();
  let id = null; try{ id = localStorage.getItem("cine_id"); }catch(e){}
  if(id){ try{ PEL = (await api("/" + id)).pelicula; $("#idea").value = PEL.idea || ""; pintar(); }catch(e){} }
})();
</script></main></body></html>
"""

PAGINA_SET = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Set 3D · Cine</title>
<link href="https://fonts.googleapis.com/css2?family=Bodoni+Moda:wght@500;600&family=Jost:wght@400;500&display=swap" rel="stylesheet">
<style>""" + _ESTILO + r"""
  html,body{height:100%} body{font-size:14px}
  .app{display:grid;grid-template-columns:270px 1fr 290px;grid-template-rows:1fr auto;height:100vh}
  .panel{background:var(--card);border-right:1px solid var(--line);padding:12px;overflow:auto} .panel.der{border-right:none;border-left:1px solid var(--line)}
  h3{font-size:12px;margin:14px 0 6px;color:var(--rose-deep);text-transform:uppercase;letter-spacing:.06em}
  .panel button{margin:0 4px 6px 0;padding:6px 11px;font-size:12.5px}
  .escena{position:relative;display:flex;align-items:center;justify-content:center;background:#0d0c11;overflow:hidden}
  #lienzo{display:block;box-shadow:0 0 0 1px var(--line),0 10px 40px #0008;border-radius:4px}
  .tag{position:absolute;top:10px;left:50%;transform:translateX(-50%);background:#000a;padding:4px 10px;border-radius:999px;font-size:12px;color:var(--ink-soft)}
  #reloj{position:absolute;top:10px;right:14px;color:var(--rose-deep);font-variant-numeric:tabular-nums}
  #cartel{position:absolute;bottom:16px;left:50%;transform:translateX(-50%);background:#000b;padding:6px 12px;border-radius:10px;font-size:13px;max-width:85%;text-align:center}
  .linea{grid-column:1 / span 3;display:flex;gap:6px;padding:8px 10px;background:var(--card);border-top:1px solid var(--line);overflow-x:auto}
  .mom{min-width:110px;background:var(--card-2);border:1px solid var(--line);border-radius:10px;padding:6px 8px;font-size:12px;cursor:pointer}
  .mom div{color:var(--ink-soft);font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis} .mom.on{border-color:var(--rose);box-shadow:0 0 0 1px var(--rose)}
  input[type=range]{width:100%}
  @media(max-width:900px){.app{grid-template-columns:1fr;height:auto}.escena{height:70vh}.linea{grid-column:1}}
</style>
<script type="importmap">{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}</script>
</head><body>
<div class="app">
  <div class="panel">
    <h2>🌍 El set</h2>
    <div class="hint" id="info">Cargando…</div>
    <h3>Objetos</h3><div id="paleta"></div>
    <div class="hint">Tocá un objeto o un personaje para moverlo.</div>
    <div><button id="mMover" class="on">↔ Mover</button><button id="mGirar">⟳ Girar</button><button id="borrar">🗑 Borrar</button></div>
    <label>Tamaño</label><input type="range" id="escala" min="0.1" max="5" step="0.05" value="1">
    <h3>Pose del personaje elegido</h3><div id="poses"></div>
    <h3>La luz</h3><div id="luces"></div>
  </div>
  <div class="escena" id="escena"><canvas id="lienzo"></canvas><div class="tag" id="tag">Vista libre</div><div id="reloj"></div><div id="cartel" style="display:none"></div></div>
  <div class="panel der">
    <h3>La cámara</h3>
    <div><button id="libre">👁 Vista libre (desde afuera)</button></div>
    <div class="hint">Elegí un punto abajo para ver desde su cámara. Mové la vista (arrastrar, rueda) y tocá <b>📍 Guardar la cámara acá</b>.</div>
    <div><button class="go" id="fijar">📍 Guardar la cámara acá</button></div>
    <label>Lente: <span id="lenteV">35</span> mm</label><input type="range" id="lente" min="14" max="120" step="1" value="35">
    <h3>Animatic</h3>
    <div><button id="play">▶ Volar el recorrido</button><button id="grabar">⏺ Bajar en video</button><button id="parar">■</button></div>
    <h3>Usar</h3>
    <button class="go" id="usar" style="width:100%">✓ Usar como guía de todos los puntos</button>
    <div id="estado" class="hint" style="margin-top:8px"></div>
  </div>
  <div class="linea" id="linea"></div>
</div>
<script type="module">
import * as THREE from "three";
import {OrbitControls} from "three/addons/controls/OrbitControls.js";
import {TransformControls} from "three/addons/controls/TransformControls.js";
const API = "%%API%%";
const CID = new URLSearchParams(location.search).get("cid");
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const lienzo = $("#lienzo");
const renderer = new THREE.WebGLRenderer({canvas: lienzo, antialias: true, preserveDrawingBuffer: true, logarithmicDepthBuffer: true});
renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1)); renderer.toneMapping = THREE.ACESFilmicToneMapping;
const scene = new THREE.Scene();
const camara = new THREE.PerspectiveCamera(50, 9 / 16, 0.1, 8000); scene.add(camara);
const mar = new THREE.Mesh(new THREE.PlaneGeometry(9000, 9000), new THREE.MeshStandardMaterial({color: 0x3a9cb8, roughness: 0.25, metalness: 0.1}));
mar.rotation.x = -Math.PI / 2; mar.position.y = -0.05; scene.add(mar);

%%COMUN%%

// ── LA LUZ DEL MUNDO ──
const CIELOS = {atardecer: [0xf3a46b, 0xffb070, [-400, 60, -900], 2.6], amanecer: [0xf6c8c8, 0xffe0c8, [500, 80, -900], 2.0],
  mediodia: [0x8cc8f0, 0xffffff, [100, 600, 200], 2.8], nublado: [0xaab2ba, 0xe8eef2, [0, 500, 0], 1.2],
  noche: [0x101830, 0x9fb0ff, [-200, 300, -400], 0.6], estudio: [0x2a2830, 0xffffff, [3, 6, 5], 2.0]};
const NOMBRE_LUZ = {atardecer: "Atardecer", amanecer: "Amanecer", mediodia: "Mediodía", nublado: "Nublado", noche: "Noche", estudio: "Estudio"};
let LUZ = "atardecer", luces = [];
function luz(k){
  LUZ = CIELOS[k] ? k : "atardecer"; const [cielo, sol, pos, f] = CIELOS[LUZ];
  luces.forEach(o => scene.remove(o)); luces = [];
  scene.background = new THREE.Color(cielo); scene.fog = new THREE.Fog(cielo, 300, 3500);
  const h = new THREE.HemisphereLight(cielo, 0x4a5868, 1.05); const d = new THREE.DirectionalLight(sol, f); d.position.set(...pos);
  scene.add(h, d); luces.push(h, d);
  document.querySelectorAll("[data-luz]").forEach(b => b.classList.toggle("on", b.dataset.luz === LUZ));
}

// ── LOS OBJETOS (formas simples, en metros) ──
const M_ = c => new THREE.MeshStandardMaterial({color: c, roughness: 0.85});
function hacer(tipo){
  const g = new THREE.Group(); const add = (geo, mat, pos, rot) => { const m = new THREE.Mesh(geo, mat); if(pos) m.position.set(...pos); if(rot) m.rotation.set(...rot); g.add(m); return m; };
  if(tipo === "isla"){ const s = add(new THREE.SphereGeometry(30, 40, 16), M_(0xe8d3a2), [0, -2.65, 0]); s.scale.set(1, 0.09, 1); const v = add(new THREE.SphereGeometry(16, 30, 12), M_(0x4f7d3a), [3, -1.67, -10]); v.scale.set(1, 0.12, 1); }
  else if(tipo === "arena"){ const s = add(new THREE.BoxGeometry(40, 0.3, 14), M_(0xe8d3a2), [0, 0.1, 0]); }
  else if(tipo === "mar"){ add(new THREE.PlaneGeometry(300, 300), new THREE.MeshStandardMaterial({color: 0x3aa3b8, roughness: 0.3}), [0, 0.02, 0], [-Math.PI / 2, 0, 0]); }
  else if(tipo === "palmera"){ const t = add(new THREE.CylinderGeometry(0.18, 0.3, 8, 8), M_(0x7a5a3a), [0.6, 4, 0], [0, 0, -0.15]); for(let i = 0; i < 7; i++){ const h = add(new THREE.ConeGeometry(0.5, 4, 4), M_(0x3f7f3a), [1.2 + Math.cos(i) * 1.6, 8, Math.sin(i * 0.9) * 1.6]); h.rotation.set(Math.PI / 2.4, i * 0.9, 0); } }
  else if(tipo === "roca"){ const r = add(new THREE.DodecahedronGeometry(1.4, 0), M_(0x6f6a64), [0, 0.6, 0]); r.scale.set(1.2, 0.7, 1); }
  else if(tipo === "sombrilla"){ add(new THREE.CylinderGeometry(0.04, 0.04, 2.4, 6), M_(0xdddddd), [0, 1.2, 0]); add(new THREE.ConeGeometry(1.4, 0.6, 12), M_(0xf2f2f2), [0, 2.5, 0]); }
  else if(tipo === "reposera"){ add(new THREE.BoxGeometry(0.7, 0.12, 1.9), M_(0xffffff), [0, 0.35, 0]); add(new THREE.BoxGeometry(0.7, 0.12, 0.7), M_(0xffffff), [0, 0.65, -0.8], [0.9, 0, 0]); }
  else if(tipo === "cabana"){ add(new THREE.BoxGeometry(4, 3, 4), M_(0xb08a5a), [0, 1.5, 0]); add(new THREE.ConeGeometry(3.4, 2, 4), M_(0x8a6a3a), [0, 4, 0], [0, Math.PI / 4, 0]); }
  else if(tipo === "barco"){ add(new THREE.BoxGeometry(2.2, 1, 7), M_(0xffffff), [0, 0.4, 0]); add(new THREE.CylinderGeometry(0.08, 0.08, 9, 6), M_(0xdddddd), [0, 5, 0]); add(new THREE.ConeGeometry(2, 7, 3), M_(0xf6f6f6), [0, 5, 0.6]); }
  else if(tipo === "yate"){ add(new THREE.BoxGeometry(6, 2.2, 24), M_(0xf4f4f2), [0, 1.1, 0]); add(new THREE.ConeGeometry(3, 5, 4), M_(0xf4f4f2), [0, 1.1, 14], [Math.PI / 2, Math.PI / 4, 0]); add(new THREE.BoxGeometry(4.5, 2, 10), M_(0xe8e8e6), [0, 3.2, -2]); add(new THREE.BoxGeometry(4.6, 0.6, 10.2), M_(0x30343a), [0, 3.4, -2]); }
  else if(tipo === "tabla_surf"){ const t = add(new THREE.CapsuleGeometry(0.28, 1.6, 4, 12), M_(0xf6f2ea), [0, 0.12, 0], [Math.PI / 2, 0, 0]); t.scale.set(1, 1, 0.18); }
  else if(tipo === "ola"){ const o = add(new THREE.CylinderGeometry(1.6, 1.6, 14, 24, 1, true, 0, Math.PI), new THREE.MeshStandardMaterial({color: 0x4ab0c4, roughness: 0.3, side: THREE.DoubleSide}), [0, 0.8, 0], [0, 0, Math.PI / 2]); }
  else if(tipo === "muelle"){ add(new THREE.BoxGeometry(2.5, 0.25, 30), M_(0x9a7a52), [0, 0.6, 0]); }
  else if(tipo === "sol"){ const s = add(new THREE.SphereGeometry(40, 24, 12), new THREE.MeshBasicMaterial({color: 0xffd28a, fog: false}), [0, 60, 0]); }
  else if(tipo === "arbol"){ add(new THREE.CylinderGeometry(0.25, 0.35, 4, 8), M_(0x6a4a2a), [0, 2, 0]); add(new THREE.SphereGeometry(2.4, 12, 10), M_(0x3f6f35), [0, 5.2, 0]); }
  else if(tipo === "edificio"){ add(new THREE.BoxGeometry(12, 20, 12), M_(0xd8d2c8), [0, 10, 0]); }
  else if(tipo === "mesa"){ add(new THREE.BoxGeometry(1.4, 0.06, 0.8), M_(0xa07a50), [0, 0.75, 0]); add(new THREE.BoxGeometry(0.08, 0.75, 0.08), M_(0xa07a50), [0, 0.37, 0]); }
  else if(tipo === "silla"){ add(new THREE.BoxGeometry(0.45, 0.06, 0.45), M_(0xa07a50), [0, 0.45, 0]); add(new THREE.BoxGeometry(0.45, 0.5, 0.06), M_(0xa07a50), [0, 0.7, -0.2]); }
  else if(tipo === "cama"){ add(new THREE.BoxGeometry(1.6, 0.5, 2.1), M_(0xf2efe8), [0, 0.25, 0]); add(new THREE.BoxGeometry(1.6, 1.1, 0.1), M_(0x8a7a6a), [0, 0.55, -1.05]); }
  else if(tipo === "pared"){ add(new THREE.BoxGeometry(6, 3, 0.2), M_(0xe8e2d8), [0, 1.5, 0]); }
  else if(tipo === "auto"){ add(new THREE.BoxGeometry(1.8, 0.8, 4.3), M_(0xb02a2a), [0, 0.6, 0]); add(new THREE.BoxGeometry(1.6, 0.6, 2.2), M_(0x222222), [0, 1.25, -0.2]); }
  else add(new THREE.BoxGeometry(1, 1, 1), M_(0x999999), [0, 0.5, 0]);
  g.userData.tipo = tipo; return g;
}

// ── ESTADO ──
let PEL = null, ELENCO = [], OBJS = [], CUR = null, LIBRE = true;
const tituloPunto = (x, i) => `${x.corte ? "✂ " : ""}${i + 1} · ${x.t} s`;
function lente(mm){ camara.fov = 2 * THREE.MathUtils.radToDeg(Math.atan(36 / (2 * mm))); camara.updateProjectionMatrix(); $("#lenteV").textContent = mm; $("#lente").value = mm; }
$("#lente").oninput = () => lente(+$("#lente").value);
const orbita = new OrbitControls(camara, lienzo); orbita.enableDamping = true; orbita.maxDistance = 4000;
const gizmo = new TransformControls(camara, lienzo); gizmo.setSize(0.9); scene.add(gizmo);
gizmo.addEventListener("dragging-changed", e => { orbita.enabled = !e.value; if(!e.value) guardarSet(); });
gizmo.addEventListener("objectChange", () => { const P = PERSONAS.find(P => P.raiz === gizmo.object); if(P) P.raiz.userData.base = [P.raiz.position.x, P.raiz.position.z, P.raiz.rotation.y]; });
let SEL = null;
function elegir(o){ SEL = o; gizmo.detach(); if(o){ gizmo.attach(o); gizmo.setMode($("#mGirar").classList.contains("on") ? "rotate" : "translate"); gizmo.showX = gizmo.showZ = !$("#mGirar").classList.contains("on"); gizmo.showY = $("#mGirar").classList.contains("on"); $("#escala").value = o.scale.x; } }
$("#mMover").onclick = () => { $("#mMover").classList.add("on"); $("#mGirar").classList.remove("on"); elegir(SEL); };
$("#mGirar").onclick = () => { $("#mGirar").classList.add("on"); $("#mMover").classList.remove("on"); elegir(SEL); };
$("#borrar").onclick = () => { if(SEL && SEL.userData.tipo){ scene.remove(SEL); OBJS = OBJS.filter(o => o !== SEL); elegir(null); guardarSet(); } };
$("#escala").oninput = () => { if(SEL && SEL.userData.tipo){ const v = +$("#escala").value; SEL.scale.set(v, v, v); } };
$("#escala").onchange = () => guardarSet();
const ray = new THREE.Raycaster(), ptr = new THREE.Vector2(); let abajo = null;
lienzo.addEventListener("pointerdown", e => { abajo = [e.clientX, e.clientY]; });
lienzo.addEventListener("pointerup", e => {
  if(VOLANDO || !abajo || Math.hypot(e.clientX - abajo[0], e.clientY - abajo[1]) > 5 || gizmo.dragging) return;
  const r = lienzo.getBoundingClientRect(); ptr.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  ray.setFromCamera(ptr, camara);
  const metas = []; OBJS.forEach(o => o.traverse(m => { if(m.isMesh){ m.userData.raiz = o; metas.push(m); } }));
  PERSONAS.forEach(P => P.raiz.traverse(m => { if(m.isMesh){ m.userData.raiz = P.raiz; metas.push(m); } }));
  const hit = ray.intersectObjects(metas, false)[0]; elegir(hit ? hit.object.userData.raiz : null);
});

// ── LA LÍNEA DEL DRON ──
let linea = null, marcas = [];
function curvas(){
  const pts = PEL.puntos, grupos = []; let g = [];
  pts.forEach((x, i) => { if(i && x.corte){ grupos.push(g); g = []; } g.push(i); }); grupos.push(g);
  return grupos.filter(g => g.length).map(g => ({idx: g,
    cam: g.length > 1 ? new THREE.CatmullRomCurve3(g.map(i => new THREE.Vector3(...pts[i].cam)), false, "centripetal") : null,
    mira: g.length > 1 ? new THREE.CatmullRomCurve3(g.map(i => new THREE.Vector3(...pts[i].mira)), false, "centripetal") : null}));
}
function pintarRecorrido(){
  if(linea) linea.forEach(l => scene.remove(l)); marcas.forEach(m => scene.remove(m)); linea = []; marcas = [];
  for(const c of curvas()){ if(!c.cam) continue;
    const l = new THREE.Line(new THREE.BufferGeometry().setFromPoints(c.cam.getPoints(200)), new THREE.LineBasicMaterial({color: 0xffd27a, fog: false}));
    scene.add(l); linea.push(l); }
  PEL.puntos.forEach((x, i) => { const m = new THREE.Mesh(new THREE.ConeGeometry(0.8, 2, 4), new THREE.MeshBasicMaterial({color: x.id === CUR ? 0xffffff : 0xffb64d, fog: false}));
    m.position.set(...x.cam); m.lookAt(new THREE.Vector3(...x.mira)); m.rotateX(Math.PI / 2); m.scale.setScalar(Math.max(0.6, x.cam[1] / 40)); scene.add(m); marcas.push(m); });
}
function verPunto(id){
  CUR = id; LIBRE = false; const x = PEL.puntos.find(y => y.id === id); if(!x) return;
  camara.position.set(...x.cam); orbita.target.set(...x.mira); lente(x.lente || 35); orbita.update();
  $("#tag").textContent = "Cámara del punto " + (PEL.puntos.indexOf(x) + 1);
  $("#info").innerHTML = `<b>Punto ${PEL.puntos.indexOf(x) + 1} · ${x.t} s</b><br>${esc(x.es || "")}${x.mov_en ? `<br><span class="hint">🎥 ${esc(x.mov_en)}</span>` : ""}`;
  pintarLineaTiempo(); pintarRecorrido();
}
$("#libre").onclick = () => { LIBRE = true; CUR = null; const b = new THREE.Box3(); OBJS.forEach(o => b.expandByObject(o)); PERSONAS.forEach(P => b.expandByObject(P.raiz)); PEL.puntos.forEach(x => b.expandByPoint(new THREE.Vector3(...x.cam)));
  const c = b.getCenter(new THREE.Vector3()), s = b.getSize(new THREE.Vector3()).length();
  camara.position.set(c.x + s * 0.6, Math.max(40, s * 0.5), c.z + s * 0.6); orbita.target.copy(c); lente(28); orbita.update(); $("#tag").textContent = "Vista libre"; pintarLineaTiempo(); pintarRecorrido(); };
$("#fijar").onclick = () => { if(!CUR) return $("#estado").textContent = "Primero elegí un punto abajo."; const x = PEL.puntos.find(y => y.id === CUR);
  x.cam = camara.position.toArray().map(v => +v.toFixed(2)); x.mira = orbita.target.toArray().map(v => +v.toFixed(2)); x.lente = +$("#lente").value; guardarSet(); pintarRecorrido();
  $("#estado").innerHTML = '<span class="bien">✓ Cámara guardada en el punto.</span>'; };
function pintarLineaTiempo(){
  $("#linea").innerHTML = PEL.puntos.map((x, i) => `<div class="mom ${x.id === CUR ? "on" : ""}" data-m="${x.id}"><b>${tituloPunto(x, i)}</b>${x.guia ? " · 🧍" : ""}<div>${esc(x.es || "")}</div></div>`).join("");
  document.querySelectorAll("[data-m]").forEach(d => d.onclick = () => { if(!VOLANDO) verPunto(d.dataset.m); });
}

// ── GUARDAR EL SET ──
let tG = null;
function guardarSet(){ clearTimeout(tG); tG = setTimeout(async () => {
  const objetos = OBJS.map(o => ({tipo: o.userData.tipo, x: +o.position.x.toFixed(2), z: +o.position.z.toFixed(2), escala: +o.scale.x.toFixed(2), giro: +THREE.MathUtils.radToDeg(o.rotation.y).toFixed(1)}));
  const personajes = PERSONAS.map((P, i) => ({i: P.i, x: +P.raiz.position.x.toFixed(2), y: P.alto || 0, z: +P.raiz.position.z.toFixed(2), giro: +THREE.MathUtils.radToDeg(P.raiz.userData.base ? P.raiz.userData.base[2] : P.raiz.rotation.y).toFixed(1), pose: P.pose || "de_pie"}));
  const puntos = Object.fromEntries(PEL.puntos.map(x => [x.id, {cam: x.cam, mira: x.mira, lente: x.lente}]));
  try{ await fetch(`${API}/${CID}/set`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({objetos, personajes, puntos, luz: LUZ})}); }catch(e){}
}, 500); }

// ── EL VUELO (animatic) ──
let VOLANDO = false;
async function volar(grabar){
  if(VOLANDO) return; VOLANDO = true; gizmo.detach(); orbita.enabled = false; marcas.forEach(m => m.visible = false); (linea || []).forEach(l => l.visible = false);
  let rec = null, partes = [];
  if(grabar && lienzo.captureStream && window.MediaRecorder){ rec = new MediaRecorder(lienzo.captureStream(30), {mimeType: MediaRecorder.isTypeSupported("video/webm;codecs=vp9") ? "video/webm;codecs=vp9" : "video/webm", videoBitsPerSecond: 8e6});
    rec.ondataavailable = ev => { if(ev.data.size) partes.push(ev.data); }; rec.start(); }
  const pts = PEL.puntos, cs = curvas(), fin = pts[pts.length - 1].t + 3; const t0 = performance.now(); $("#cartel").style.display = "";
  await new Promise(listo => { const paso = now => {
    if(!VOLANDO) return listo(); const t = (now - t0) / 1000;
    let k = pts.findIndex((x, i) => i === pts.length - 1 || (t >= x.t && t < pts[i + 1].t)); if(t >= pts[pts.length - 1].t) k = pts.length - 1;
    const g = cs.find(c => c.idx.includes(k)), sig = pts[k + 1];
    if(g && g.cam && sig && !sig.corte){ const j = g.idx.indexOf(k), loc = (t - pts[k].t) / Math.max(0.01, sig.t - pts[k].t), u = (j + loc) / (g.idx.length - 1);
      camara.position.copy(g.cam.getPoint(Math.min(1, u))); camara.lookAt(g.mira.getPoint(Math.min(1, u))); lente(Math.round(pts[k].lente + (sig.lente - pts[k].lente) * loc)); }
    else { const x = pts[k]; const v = new THREE.Vector3(...x.cam), m = new THREE.Vector3(...x.mira); const loc = Math.min(1, (t - x.t) / 3); camara.position.copy(v.lerp(m, 0.08 * loc)); camara.lookAt(m); lente(x.lente || 35); }
    $("#reloj").textContent = `${t.toFixed(1)} s`; $("#cartel").innerHTML = `<b>${k + 1}</b> · ${esc(pts[k].es || "")}`;
    if(t < fin) requestAnimationFrame(paso); else listo(); }; requestAnimationFrame(paso); });
  if(rec){ await new Promise(r => { rec.onstop = r; rec.stop(); }); if(partes.length){ const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob(partes, {type: "video/webm"})); a.download = "vuelo.webm"; a.click(); } }
  $("#cartel").style.display = "none"; $("#reloj").textContent = ""; VOLANDO = false; orbita.enabled = true; marcas.forEach(m => m.visible = true); (linea || []).forEach(l => l.visible = true);
  if(CUR) verPunto(CUR); else $("#libre").onclick();
}
$("#play").onclick = () => volar(false); $("#grabar").onclick = () => volar(true); $("#parar").onclick = () => { VOLANDO = false; };

// ── USAR: la vista de cada punto como guía de su dibujo ──
$("#usar").onclick = async () => {
  gizmo.detach(); marcas.forEach(m => m.visible = false); (linea || []).forEach(l => l.visible = false);
  const previo = renderer.getSize(new THREE.Vector2()); let ok = 0;
  for(const [i, x] of PEL.puntos.entries()){
    $("#estado").innerHTML = `<span class="spin"></span> Guía del punto ${i + 1}…`;
    camara.position.set(...x.cam); camara.lookAt(new THREE.Vector3(...x.mira)); lente(x.lente || 35);
    renderer.setPixelRatio(1); renderer.setSize(720, 1280, false); renderer.render(scene, camara);
    const imagen = lienzo.toDataURL("image/jpeg", 0.9);
    const r = await fetch(`${API}/${CID}/punto/${x.id}/guia`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({imagen})});
    if(r.ok){ ok++; x.guia = true; }
  }
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1)); renderer.setSize(previo.x, previo.y, true);
  marcas.forEach(m => m.visible = true); (linea || []).forEach(l => l.visible = true); pintarLineaTiempo();
  $("#estado").innerHTML = `<span class="bien">✓ ${ok} guías guardadas. Volvé a Cine y tocá "🎨 Dibujar los puntos que faltan".</span>`;
  try{ if(window.opener) window.opener.postMessage({de: "cine_set"}, "*"); }catch(e){}
  if(CUR) verPunto(CUR);
};

// ── TAMAÑO 9:16 ──
function medir(){ const caja = $("#escena").getBoundingClientRect(); let h = caja.height - 24, w = h * 9 / 16; if(w > caja.width - 24){ w = caja.width - 24; h = w * 16 / 9; }
  renderer.setSize(Math.floor(w), Math.floor(h), true); camara.aspect = 9 / 16; camara.updateProjectionMatrix(); }
window.addEventListener("resize", medir);
(function loop(){ requestAnimationFrame(loop); if(!VOLANDO) orbita.update(); renderer.render(scene, camara); })();

// ── ARRANQUE ──
(async () => {
  medir(); lente(35);
  try{
    const r = await fetch(`${API}/${CID}/set`); const d = await r.json(); if(!r.ok) throw new Error(d.detail || "No encuentro la película.");
    PEL = d.pelicula; ELENCO = d.nombres || [];
    $("#paleta").innerHTML = (d.objetos || []).map(k => `<button data-o="${k}">＋ ${k}</button>`).join("");
    document.querySelectorAll("[data-o]").forEach(b => b.onclick = () => { const o = hacer(b.dataset.o); const t = orbita.target; o.position.set(t.x, 0, t.z); scene.add(o); OBJS.push(o); elegir(o); guardarSet(); });
    $("#luces").innerHTML = Object.entries(NOMBRE_LUZ).map(([k, n]) => `<button data-luz="${k}">${n}</button>`).join("");
    document.querySelectorAll("[data-luz]").forEach(b => b.onclick = () => { luz(b.dataset.luz); guardarSet(); });
    $("#poses").innerHTML = Object.entries(POSES).map(([k, [n]]) => `<button data-pose="${k}">${n}</button>`).join("");
    document.querySelectorAll("[data-pose]").forEach(b => b.onclick = () => { const P = PERSONAS.find(P => P.raiz === SEL); if(!P) return $("#estado").textContent = "Tocá un personaje primero."; pose(P, b.dataset.pose); guardarSet(); });
    luz(PEL.luz || "atardecer");
    for(const o of PEL.objetos || []){ const g = hacer(o.tipo); g.position.set(o.x, 0, o.z); g.scale.setScalar(o.escala || 1); g.rotation.y = THREE.MathUtils.degToRad(o.giro || 0); scene.add(g); OBJS.push(g); }
    const colores = [0xd8b878, 0x9fd0e0, 0xe0a0b0, 0xb0e0a0, 0xc0b0f0, 0xf0c090];
    for(const x of PEL.personajes || []){ const P = maniqui(); P.i = x.i; P.raiz.userData.base = [x.x, x.z, THREE.MathUtils.degToRad(x.giro || 0)]; pose(P, x.pose || "de_pie"); P.raiz.position.y += x.y || 0; P.alto = x.y || 0;
      const mat = GRIS.clone(); mat.color.setHex(colores[x.i % colores.length]); P.raiz.traverse(m => { if(m.isMesh) m.material = mat; }); }
    $("#info").textContent = `${PEL.titulo || "La película"}: ${PEL.puntos.length} puntos de cámara. Elegí uno abajo o mirá desde afuera.`;
    pintarLineaTiempo(); $("#libre").onclick();
  }catch(e){ $("#info").innerHTML = `<span class="mal">${esc(e.message)}</span>`; }
})();
</script></body></html>
"""
