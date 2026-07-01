# ============================================================================
# ML × TN SYNC — Sistema de sincronización Mercado Libre × Tienda Nube
# ============================================================================
#
# ARQUITECTURA DE APPS DE MERCADO LIBRE:
# --------------------------------------
# Tenemos 5 apps registradas en devcenter. Cada una tiene un trabajo único
# para no competir por rate limit (ML rate-limita por Client ID + endpoint).
#
# ┌─────────────────────┬──────────────────────────────────────────────────┐
# │ APP                 │ TRABAJO                                          │
# ├─────────────────────┼──────────────────────────────────────────────────┤
# │ Mi Sync             │ OAuth, sync items, preguntas/mensajes, publicar  │
# │ (ML_CLIENT_ID)      │ items entre TN y ML. Recibe webhooks Q&M.        │
# ├─────────────────────┼──────────────────────────────────────────────────┤
# │ Ml Sync Duplicar    │ SOLO duplicar items entre cuentas de ML.         │
# │ (ML_CLIENT_ID_PUB)  │ NO recibe webhooks.                              │
# ├─────────────────────┼──────────────────────────────────────────────────┤
# │ STOCK_0 (Lenceria)  │ Refresh stock + Facturador + Listar ventas       │
# │ STOCK_1 (Shampoo)   │ de SU cuenta. Recibe webhooks orders+stock.      │
# │ STOCK_2 (Avellaneda)│                                                  │
# └─────────────────────┴──────────────────────────────────────────────────┘
#
# HELPERS PARA OBTENER TOKEN SEGÚN APP:
#   fresh_token(i)       → token de Mi Sync para cuenta i
#   fresh_token_pub(i)   → token de Ml Sync Duplicar para cuenta i
#   fresh_token_stock(i) → token de STOCK_i para cuenta i
#
# WEBHOOKS (handler en /webhook/ml):
#   orders_v2, stock-locations → procesamos en batch (STOCK token)
#   questions, messages        → solo log (consultable después)
#   items, items_prices        → IGNORADOS (no afectan stock)
#
# RATE LIMIT:
# Si vemos 429 constantes, lo más probable es que algún flujo nuevo esté
# usando la app equivocada. Verificar que cada función use el token de la
# app correcta según la tabla de arriba.
# ============================================================================

from fastapi import FastAPI, HTTPException, Request, Depends, BackgroundTasks
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
import httpx, asyncio, json, os, time, secrets, re
import datetime
import base64
from pathlib import Path
from ml_gateway import init_gateway, ml_call, PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW
import events
import jobs
try:
    import afip
    _afip_module_ok = True
except Exception as _e:
    print(f"[afip] No se pudo importar modulo afip: {_e}")
    _afip_module_ok = False
    afip = None
try:
    import pdf_factura
    _pdf_module_ok = True
except Exception as _e:
    print(f"[pdf] No se pudo importar pdf_factura: {_e}")
    _pdf_module_ok = False
    pdf_factura = None

async def token_refresh_loop():
    """Refresca tokens de ML automáticamente cada 5 horas"""
    # Refrescar al arrancar también
    await asyncio.sleep(5)  # esperar 5 segundos para que arranque todo
    await refresh_all_tokens()
    while True:
        await asyncio.sleep(5 * 3600)  # esperar 5 horas
        await refresh_all_tokens()

async def refresh_all_tokens():
    """Refrescar todos los tokens de ML (usa lock para no pisar a fresh_token)"""
    for i, acc in enumerate(ST.get("accounts", [])):
        try:
            if acc.get("refresh"):
                lock = _get_token_lock(f"main_{i}")
                async with lock:
                    # Si ya esta fresco (otro lo refresco), saltar
                    if time.time() <= acc.get("expiry", 0) - 600:
                        continue
                    async with httpx.AsyncClient(timeout=15) as c:
                        r = await c.post("https://api.mercadolibre.com/oauth/token",
                            data={"grant_type":"refresh_token","client_id":ML_APP_ID,
                                  "client_secret":ML_SECRET,"refresh_token":acc["refresh"]},
                            headers={"Content-Type":"application/x-www-form-urlencoded"})
                        td = r.json()
                    if "access_token" in td:
                        acc["token"] = td["access_token"]
                        acc["refresh"] = td.get("refresh_token", acc["refresh"])
                        acc["expiry"] = time.time() + td.get("expires_in", 21600) - 300
                        acc["token_ok"] = True
                        save_state()
                        print(f"Token refreshed OK for account {i}: {acc.get('name','')}")
                    else:
                        acc["token_ok"] = False
                        acc["expiry"] = 0
                        save_state()
                        print(f"Token refresh FAILED for account {i}: {td}")
        except Exception as e:
            print(f"Token refresh error account {i}: {e}")

async def process_webhook_queue():
    """DESHABILITADO — los webhooks se procesan directo en _process_webhook_async.
    Esta cola legacy ya no se usa. La funcion queda como no-op por compatibilidad
    con el lifespan, pero no hace nada."""
    while True:
        await asyncio.sleep(3600)  # dormir 1 hora, no hacer nada


async def keepalive_loop():
    """Ping cada 4 minutos para mantener Railway activo"""
    await asyncio.sleep(60)
    while True:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                await c.get(f"{os.getenv('APP_URL','http://localhost:8000')}/health")
        except:
            pass
        await asyncio.sleep(240)


async def daily_stock_refresh_loop():
    """Refresca el stock de las 3 cuentas todos los dias a las 3 AM Argentina (UTC-3).
    Solo lee de ML y actualiza el cache local. NUNCA escribe a ML.
    Usa la logica filtrada de refresh_stock (solo items activos con stock > 0).
    """
    import datetime as _dt
    # Esperar 5 minutos despues de arranque para que todo se estabilice
    await asyncio.sleep(300)
    while True:
        try:
            # Calcular cuanto falta hasta proximas 3 AM (Argentina = UTC-3)
            now_utc = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
            # 3 AM Argentina = 6 AM UTC
            target_utc = now_utc.replace(hour=6, minute=0, second=0, microsecond=0)
            if target_utc <= now_utc:
                target_utc = target_utc + _dt.timedelta(days=1)
            wait_seconds = (target_utc - now_utc).total_seconds()
            print(f"[cron] Proximo refresh diario en {wait_seconds/3600:.1f} horas")
            await asyncio.sleep(wait_seconds)

            # Ejecutar refresh para cada cuenta
            events.info("⏰ Cron 3AM: iniciando refresh diario de stock", source="cron")
            for i, acc in enumerate(ST.get("accounts", [])):
                uid = str(acc.get("uid", ""))
                if not uid:
                    continue
                name = acc.get("name", f"cuenta {i}")
                try:
                    events.info(f"⏰ Cron: refrescando {name}", source="cron", seller=uid)
                    # Reusar la logica de refresh_stock (solo activos con stock>0)
                    if uid in _refresh_running:
                        events.warn(f"⏰ Cron: {name} ya tiene refresh corriendo, skip", source="cron")
                        continue
                    _refresh_running.add(uid)
                    try:
                        token = await fresh_token_stock(i)
                        hdrs = {"Authorization": f"Bearer {token}"}
                        products = get_cached_products(uid) or []
                        if not products:
                            continue
                        def needs_refresh(p):
                            if p.get("status") != "active":
                                return False
                            total = p.get("available_quantity", 0) or 0
                            for v in (p.get("variations") or []):
                                total += v.get("available_quantity", 0) or 0
                            return total > 0
                        filtered = [p for p in products if needs_refresh(p)]
                        all_ids = [p["id"] for p in filtered]
                        prod_map = {p["id"]: p for p in products}
                        refreshed = 0
                        consecutive_429 = 0
                        for x in range(0, len(all_ids), 20):
                            batch = all_ids[x:x+20]
                            try:
                                r = await ml_call(seller_uid=uid, method="GET",
                                    url=f"{ML_API}/items?ids={','.join(batch)}&attributes=id,price,available_quantity,status,variations",
                                    headers=hdrs, priority=PRIORITY_LOW, timeout=30)
                                if r.status_code == 429:
                                    consecutive_429 += 1
                                    if consecutive_429 >= 3:
                                        events.err(f"⏰ Cron: {name} abortado tras 3x429", source="cron")
                                        break
                                    await asyncio.sleep(30)
                                    continue
                                consecutive_429 = 0
                                if r.status_code == 200:
                                    for item in r.json():
                                        if item.get("code") == 200:
                                            b = item["body"]
                                            iid = b.get("id")
                                            if iid in prod_map:
                                                prod_map[iid]["price"] = b.get("price", prod_map[iid].get("price"))
                                                prod_map[iid]["available_quantity"] = b.get("available_quantity", prod_map[iid].get("available_quantity"))
                                                prod_map[iid]["status"] = b.get("status", prod_map[iid].get("status"))
                                                if b.get("variations"):
                                                    var_map = {str(v["id"]): v for v in b["variations"]}
                                                    for v in prod_map[iid].get("variations", []):
                                                        sv = var_map.get(str(v.get("id")))
                                                        if sv:
                                                            v["available_quantity"] = sv.get("available_quantity", v.get("available_quantity"))
                                                refreshed += 1
                            except Exception as e:
                                print(f"[cron] {name} batch error: {e}")
                            await asyncio.sleep(2)  # cron mas conservador, 2s entre batches
                        set_cached_products(uid, list(prod_map.values()))
                        events.ok(f"⏰ Cron {name}: {refreshed}/{len(filtered)} items actualizados", source="cron")
                    finally:
                        _refresh_running.discard(uid)
                    # Pausa entre cuentas
                    await asyncio.sleep(60)
                except Exception as e:
                    events.err(f"⏰ Cron {name} error: {str(e)[:120]}", source="cron")
            events.ok("⏰ Cron 3AM: refresh diario completado", source="cron")
        except Exception as e:
            print(f"[cron] daily_stock_refresh_loop error: {e}")
            # En caso de error, esperar 1 hora antes de reintentar
            await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app):
    init_gateway(get_redis)
    jobs.init(get_redis)
    # Cargar sesiones persistidas de Redis (sobreviven reinicios)
    _load_sessions()
    sess_count = sum(1 for v in SESSIONS.values() if v > time.time())
    print(f"[lifespan] Sesiones cargadas: {sess_count} activas")
    if _afip_module_ok and afip:
        try:
            afip.init_redis(get_redis)
            print("[lifespan] AFIP modulo cargado")
        except Exception as _e:
            print(f"[lifespan] AFIP init error: {_e}")
    events.info("Sistema iniciado", source="system")
    print("[lifespan] ML Gateway inicializado")
    asyncio.create_task(token_refresh_loop())
    asyncio.create_task(process_webhook_queue())
    asyncio.create_task(keepalive_loop())
    asyncio.create_task(daily_stock_refresh_loop())
    asyncio.create_task(_sessions_persist_loop())
    asyncio.create_task(auto_answer_worker_loop())
    # MODO EMERGENCIA: order_batch_worker_loop deshabilitado
    # asyncio.create_task(order_batch_worker_loop())
    yield


async def _sessions_persist_loop():
    """Cada 5 minutos guarda las sesiones activas en Redis.
    Asi sobreviven reinicios de Railway sin que el usuario tenga que loguear de nuevo."""
    await asyncio.sleep(30)
    while True:
        try:
            _save_sessions()
        except Exception as e:
            print(f"[sessions] persist error: {e}")
        await asyncio.sleep(300)  # 5 min

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# --- Editor de etiquetas LUMA (modulo aislado, reutiliza get_redis y auth) ---
from etiquetas_router import router as etiquetas_router
app.include_router(etiquetas_router)

# --- Videos IA con Veo 3.1 (modulo aislado, usa GEMINI_API_KEY) ---
from videos_ia import router as videos_ia_router
app.include_router(videos_ia_router)

ML_APP_ID     = os.getenv("ML_CLIENT_ID")
ML_APP_ID_PUB = os.getenv("ML_CLIENT_ID_PUB", "")
ML_SECRET_PUB = os.getenv("ML_CLIENT_SECRET_PUB", "")
# Apps de stock — una por cuenta, rate limit separado
ML_STOCK_IDS = [
    os.getenv("ML_CLIENT_ID_STOCK_0", ""),
    os.getenv("ML_CLIENT_ID_STOCK_1", ""),
    os.getenv("ML_CLIENT_ID_STOCK_2", ""),
]
ML_STOCK_SECRETS = [
    os.getenv("ML_CLIENT_SECRET_STOCK_0", ""),
    os.getenv("ML_CLIENT_SECRET_STOCK_1", ""),
    os.getenv("ML_CLIENT_SECRET_STOCK_2", ""),
]
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
ML_SECRET     = os.getenv("ML_CLIENT_SECRET")
APP_URL       = os.getenv("APP_URL", "https://mltn-sync-production.up.railway.app")
REDIRECT_URI  = f"{APP_URL}/auth/callback"
ADMIN_EMAIL   = os.getenv("ADMIN_EMAIL", "admin@sync.com")
ADMIN_PASS    = os.getenv("ADMIN_PASSWORD")
ML_API        = "https://api.mercadolibre.com"


# ============================================================================
# HELPER UNIVERSAL: requests directos a ML con jitter + retry 429
# ============================================================================
# Para casos donde NO se puede usar el gateway (facturador, callbacks OAuth,
# uploads de PDF a ML, etc). Mismo manejo de 429 que el gateway pero standalone.
#
# Uso:
#   r = await ml_http_request(
#       method="GET",
#       url=f"{ML_API}/orders/{order_id}",
#       headers={"Authorization": f"Bearer {token}"},
#       client=optional_client,  # si vas a hacer varios requests reusa el client
#   )

async def ml_http_request(method: str, url: str, headers: dict = None,
                          json_body: dict = None, files: dict = None,
                          data: dict = None, timeout: int = 30,
                          max_retries: int = 3, client: httpx.AsyncClient = None,
                          source_label: str = "direct"):
    """
    Hace un request HTTP a ML respetando 429 con backoff exponencial + jitter.
    Si recibe Retry-After, lo respeta con jitter ±25%.
    Si no, hace backoff exponencial (5s, 10s, 20s) con jitter ±25%.
    """
    import random as _r
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout)
    try:
        last_response = None
        for attempt in range(max_retries):
            try:
                kwargs = {"headers": headers or {}}
                if json_body is not None:
                    kwargs["json"] = json_body
                if data is not None:
                    kwargs["data"] = data
                if files is not None:
                    kwargs["files"] = files
                r = await client.request(method, url, **kwargs)
                last_response = r
                if r.status_code == 429:
                    retry_after_h = r.headers.get("Retry-After") or r.headers.get("retry-after")
                    jitter_factor = _r.uniform(0.75, 1.25)
                    if retry_after_h:
                        try:
                            base = int(retry_after_h)
                        except:
                            base = 10
                    else:
                        # Backoff exponencial
                        base = min(5 * (2 ** attempt), 60)
                    wait_time = max(2, int(base * jitter_factor))
                    try:
                        events.warn(f"429 [{method}] attempt {attempt+1}/{max_retries} - esperando {wait_time}s (jitter)",
                                    source=source_label)
                    except:
                        pass
                    if attempt < max_retries - 1:
                        await asyncio.sleep(wait_time)
                        continue
                    return r
                return r
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt < max_retries - 1:
                    backoff = _r.uniform(2, 5) * (attempt + 1)
                    await asyncio.sleep(backoff)
                    continue
                raise
        return last_response
    finally:
        if owns_client:
            await client.aclose()




def queue_webhook(topic: str, resource: str, user_id: str):
    """Encolar webhook en Redis para procesar despues"""
    try:
        r = get_redis()
        if r:
            import json as _json
            item = _json.dumps({"topic": topic, "resource": resource, "user_id": user_id, "ts": time.time()})
            r.rpush("mltn:webhook_queue", item)
    except Exception as e:
        print(f"queue_webhook error: {e}")

# ── RATE LIMITER CON REDIS COOLDOWN (como los grandes integradores) ──────────
import random as _random

_ml_async_lock = None

def _redis_cooldown_key(account_idx: int = -1):
    if account_idx >= 0: return f"mltn:ml_cooldown:{account_idx}"
    return "mltn:ml_cooldown"

def is_ml_cooling(account_idx: int = -1) -> bool:
    try:
        r = get_redis()
        if not r: return False
        if account_idx >= 0:
            return bool(r.exists(f"mltn:ml_cooldown:{account_idx}"))
        return bool(r.exists("mltn:ml_cooldown"))  # cooldown global
    except: pass
    return False

def set_ml_cooling(seconds: int, account_idx: int = -1):
    try:
        r = get_redis()
        if r:
            if account_idx >= 0:
                r.setex(f"mltn:ml_cooldown:{account_idx}", seconds, "1")
            else:
                r.setex("mltn:ml_cooldown", seconds, "1")
            print(f"ML cooldown activado por {seconds}s (acc={account_idx})")
    except: pass

async def _get_async_lock():
    global _ml_async_lock
    if _ml_async_lock is None:
        _ml_async_lock = asyncio.Lock()
    return _ml_async_lock

# Token bucket en memoria: 30 req/min para background, 60 req/min para manual
_ml_tokens = 30.0
_ml_last_refill = 0.0

async def _wait_for_token(manual: bool = False):
    global _ml_tokens, _ml_last_refill
    lock = await _get_async_lock()
    async with lock:
        now = time.time()
        rate = 1.0 if manual else 0.5  # tokens/seg: 60/min manual, 30/min bg
        elapsed = now - _ml_last_refill
        max_tokens = 60.0 if manual else 30.0
        _ml_tokens = min(max_tokens, _ml_tokens + elapsed * rate)
        _ml_last_refill = now
        if _ml_tokens >= 1.0:
            _ml_tokens -= 1.0
            return 0
        wait = (1.0 - _ml_tokens) / rate
        _ml_tokens = 0
        return wait

async def ml_request(method: str, url: str, headers: dict, timeout: int = 25,
                     retry_429: bool = True, manual: bool = False, **kwargs):
    """Request a ML con Token Bucket + Redis cooldown global"""
    # Verificar cooldown de Redis antes de cualquier request
    if is_ml_cooling():
        try:
            r = get_redis()
            ttl = r.ttl(_redis_cooldown_key()) if r else 30
        except:
            ttl = 30
        jitter = _random.uniform(0.5, 2.0) + _random.uniform(0, min(ttl * 0.15, 8))
        total_wait = max(ttl, 1) + jitter
        print(f"ML cooling activo — esperando {total_wait:.1f}s (ttl={ttl}s jitter={jitter:.1f}s)")
        await asyncio.sleep(total_wait)

    # Esperar token disponible
    wait = await _wait_for_token(manual)
    if wait > 0:
        await asyncio.sleep(wait)

    max_attempts = 5 if retry_429 else 1
    for attempt in range(max_attempts):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10)) as c:
                r = await c.request(method, url, headers=headers, **kwargs)
            if r.status_code == 429:
                if not retry_429:
                    print(f"ML 429 skip [{method} {url[30:60]}]")
                    return r
                # Respetar Retry-After header si ML lo manda
                retry_after = r.headers.get("Retry-After") or r.headers.get("retry-after")
                if retry_after:
                    try:
                        cooling = int(retry_after) + int(_random.uniform(1, 5))
                    except:
                        cooling = min(int((2 ** attempt) * _random.uniform(10, 25)), 120)
                else:
                    cooling = min(int((2 ** attempt) * _random.uniform(10, 25)), 120)
                set_ml_cooling(cooling)
                print(f"ML 429 [{method} {url[30:60]}] attempt {attempt+1} — cooldown {cooling}s (Retry-After={retry_after})")
                await asyncio.sleep(cooling)
                continue
            return r
        except Exception as e:
            if attempt < max_attempts - 1:
                await asyncio.sleep(_random.uniform(2, 6))
                continue
            raise
    return r

# Locks para evitar race conditions al refrescar tokens.
# Si dos requests piden refrescar el mismo token a la vez, el segundo espera
# al primero y usa el token nuevo (en vez de un refresh_token ya invalidado).
_token_refresh_locks = {}

def _get_token_lock(key: str) -> asyncio.Lock:
    if key not in _token_refresh_locks:
        _token_refresh_locks[key] = asyncio.Lock()
    return _token_refresh_locks[key]


async def fresh_token_stock(i: int) -> str:
    """Token de la app de stock para la cuenta i — rate limit independiente"""
    if i >= len(ML_STOCK_IDS) or not ML_STOCK_IDS[i] or not ML_STOCK_SECRETS[i]:
        return await fresh_token(i)  # fallback a app principal
    acc = ST["accounts"][i]
    token = acc.get(f"token_stock_{i}", "")
    expiry = acc.get(f"token_stock_expiry_{i}", 0)
    if token and time.time() < expiry:
        return token
    refresh = acc.get(f"refresh_stock_{i}", "")
    if not refresh:
        return await fresh_token(i)
    # Lock por cuenta para evitar race en el refresh
    lock = _get_token_lock(f"stock_{i}")
    async with lock:
        # Doble-check
        if acc.get(f"token_stock_{i}") and time.time() < acc.get(f"token_stock_expiry_{i}", 0):
            return acc[f"token_stock_{i}"]
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post("https://api.mercadolibre.com/oauth/token",
                    data={"grant_type":"refresh_token","client_id":ML_STOCK_IDS[i],
                          "client_secret":ML_STOCK_SECRETS[i],"refresh_token":refresh},
                    headers={"Content-Type":"application/x-www-form-urlencoded"})
                d = r.json()
            if "access_token" in d:
                acc[f"token_stock_{i}"] = d["access_token"]
                acc[f"token_stock_expiry_{i}"] = time.time() + d.get("expires_in", 21600) - 300
                acc[f"refresh_stock_{i}"] = d.get("refresh_token", refresh)
                save_state()
                return acc[f"token_stock_{i}"]
        except:
            pass
    return await fresh_token(i)  # fallback

async def ml_manual(method: str, url: str, headers: dict, timeout: int = 30, **kwargs):
    """Request manual (duplicar/publicar) — token bucket de mayor velocidad"""
    return await ml_request(method, url, headers, timeout, retry_429=True, manual=True, **kwargs)

SESSIONS = {}

def _save_sessions():
    try:
        r = get_redis()
        # 30 dias TTL para que las sesiones sobrevivan reinicios del server
        if r: r.setex("mltn:sessions", 86400 * 30, json.dumps({k:v for k,v in SESSIONS.items() if v > time.time()}))
    except: pass

def _load_sessions():
    global SESSIONS
    try:
        r = get_redis()
        if r:
            raw = r.get("mltn:sessions")
            if raw: SESSIONS.update(json.loads(raw))
    except: pass


_stock_semaphores = {}


async def ml_stock_request(method: str, url: str, headers: dict, account_idx: int = 0, timeout: int = 25, **kwargs):
    """Request usando app de stock — semaforo envuelve el request completo"""
    global _stock_semaphores
    if account_idx not in _stock_semaphores:
        _stock_semaphores[account_idx] = asyncio.Semaphore(2)  # max 2 concurrentes por cuenta
    sem = _stock_semaphores[account_idx]

    for attempt in range(5):
        try:
            # Respetar cooldown por cuenta antes de intentar
            if is_ml_cooling(account_idx):
                r = get_redis()
                ttl = r.ttl(f"mltn:ml_cooldown:{account_idx}") if r else 10
                wait = max(ttl, 1) + _random.uniform(1, 3)
                print(f"Stock acc={account_idx} en cooldown, esperando {wait:.0f}s...")
                await asyncio.sleep(wait)

            async with sem:  # semaforo envuelve el request completo
                await asyncio.sleep(_random.uniform(0.8, 1.4))  # pacing real
                async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10)) as c:
                    r = await c.request(method, url, headers=headers, **kwargs)

            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After") or r.headers.get("retry-after")
                try:
                    delay = int(retry_after) + int(_random.uniform(2, 5)) if retry_after else min((2 ** attempt) * _random.uniform(10, 20), 60)
                except:
                    delay = min((2 ** attempt) * 20, 180)
                set_ml_cooling(int(delay), account_idx)  # guardar cooldown en Redis
                print(f"Stock 429 acc={account_idx} [{method}] attempt {attempt+1} cooldown={delay:.0f}s")
                await asyncio.sleep(delay)
                continue
            return r
        except Exception as e:
            if attempt < 4:
                await asyncio.sleep(_random.uniform(3, 8))
                continue
            raise
    return r

# Cliente HTTP global reutilizable — evita abrir/cerrar conexiones en cada request
_http_client: httpx.AsyncClient = None

async def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(25, connect=10),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=30)
        )
    return _http_client

def get_redis():
    url = os.getenv("REDIS_URL", "")
    if not url:
        return None
    try:
        import redis
        if url.startswith("rediss://"):
            r = redis.from_url(url, decode_responses=True, socket_timeout=5,
                               ssl_cert_reqs=None)
        else:
            r = redis.from_url(url, decode_responses=True, socket_timeout=5)
        r.ping()
        return r
    except Exception as e:
        print(f"Redis error: {e}")
        return None


async def redis_delete_pattern_async(pattern: str) -> int:
    """Borra todas las claves que matchean un patron SIN bloquear el event loop.

    El comando KEYS y los scans grandes bloquean Redis y congelan el server
    (causa de 'Failed to fetch'). Esta funcion corre el scan+delete en un
    thread aparte con asyncio.to_thread, asi el event loop sigue atendiendo
    las peticiones del usuario mientras Redis trabaja.
    """
    def _work():
        r = get_redis()
        if not r:
            return 0
        deleted = 0
        # scan_iter es incremental (no bloquea Redis como KEYS)
        batch = []
        for k in r.scan_iter(match=pattern, count=300):
            batch.append(k)
            if len(batch) >= 200:
                deleted += r.delete(*batch)
                batch = []
        if batch:
            deleted += r.delete(*batch)
        return deleted
    try:
        return await asyncio.to_thread(_work)
    except Exception as e:
        print(f"redis_delete_pattern_async error: {e}")
        return 0


async def redis_op_async(fn):
    """Ejecuta una operacion Redis sincrona en un thread aparte.
    Uso: await redis_op_async(lambda r: r.get('key'))
    Evita que operaciones Redis lentas congelen el event loop."""
    def _work():
        r = get_redis()
        if not r:
            return None
        return fn(r)
    try:
        return await asyncio.to_thread(_work)
    except Exception as e:
        print(f"redis_op_async error: {e}")
        return None


def load_state():
    r = get_redis()
    if r:
        try:
            raw = r.get("mltn:state")
            if raw:
                return json.loads(raw)
        except:
            pass
    if Path("state.json").exists():
        return json.loads(Path("state.json").read_text())
    return {"accounts": [], "tn": {}, "log": [], "links": []}

def save_state():
    r = get_redis()
    data = json.dumps(ST)
    if r:
        try:
            r.set("mltn:state", data)
            return
        except:
            pass
    Path("state.json").write_text(data)

try:
    ST = load_state()
except:
    ST = {"accounts": [], "tn": {}, "log": [], "links": []}
if "links" not in ST: ST["links"] = []
if "accounts" not in ST: ST["accounts"] = []

def auth(req: Request):
    t = req.headers.get("X-Session-Token", "")
    if not t or t not in SESSIONS or SESSIONS[t] < time.time():
        raise HTTPException(401, "No autorizado.")
    # Sliding window: cada request extiende la sesion otros 30 dias
    SESSIONS[t] = time.time() + 86400 * 30
    return t

@app.get("/health")
def health():
    return {"ok": True}

# ============================================================================
# EVENTS STREAM (consola en vivo del frontend)
# ============================================================================
@app.get("/api/events/stream")
async def events_stream(request: Request):
    """Server-Sent Events - eventos en tiempo real para la consola."""
    from fastapi.responses import StreamingResponse
    # Auth manual: header o query param (EventSource estandar no manda headers)
    t = request.headers.get("X-Session-Token", "") or request.query_params.get("token", "")
    if not t or t not in SESSIONS or SESSIONS[t] < time.time():
        raise HTTPException(401, "No autorizado")

    async def generate():
        # Mandar historial primero
        history = events.get_history(limit=50)
        for ev in history:
            yield f"data: {json.dumps(ev)}\n\n"
        # Subscribe a eventos nuevos
        queue = await events.subscribe()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    # Keepalive cada 15s para evitar timeout del proxy
                    yield ": keepalive\n\n"
        finally:
            events.unsubscribe(queue)

    return StreamingResponse(generate(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@app.get("/api/events/history")
def events_history(limit: int = 100, level: str = None, source: str = None, _=Depends(auth)):
    """Historial de eventos."""
    return {"events": events.get_history(limit=limit, level=level, source=source),
            "subscribers": events.subscriber_count()}


# ============================================================================
# JOBS API
# ============================================================================
@app.get("/api/jobs")
def list_jobs(limit: int = 20, _=Depends(auth)):
    """Listar jobs recientes."""
    return {"jobs": jobs.list_recent(limit=limit)}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, _=Depends(auth)):
    """Estado de un job especifico."""
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(404, "Job no encontrado")
    return j


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, _=Depends(auth)):
    """Marcar job como cancelado (la task background debe chequear)."""
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(404)
    if j["status"] == "running":
        jobs.update(job_id, status="cancel_requested")
        return {"ok": True}
    return {"ok": False, "msg": "Job no esta corriendo"}


# ============================================================================
# AFIP - FACTURADOR
# ============================================================================
@app.post("/api/afip/test")
async def afip_test(account_idx: int = 1, _=Depends(auth)):
    """
    Test minimo: solo intenta autenticarse con WSAA para una cuenta.
    NO emite facturas. Solo verifica que cert+key funcionan.
    """
    if not _afip_module_ok or not afip:
        raise HTTPException(500, "Modulo AFIP no esta disponible (revisar requirements.txt)")
    if not afip.AfipClient.has_credentials_for(account_idx):
        return {"ok": False, "error": f"No hay credenciales AFIP configuradas para la cuenta {account_idx}",
                "type": "NoCredentials"}
    try:
        events.info(f"AFIP test cuenta {account_idx}: iniciando autenticacion WSAA...", source="afip")
        client = afip.AfipClient.from_env(account_idx=account_idx)
        await client.init()
        # Llamar a dummy para confirmar que el TA funciona
        st = await client.dummy()
        events.ok(f"AFIP test OK cuenta {account_idx}. Server status: {st}", source="afip")
        return {
            "ok": True,
            "ambiente": "PRODUCCION" if client.prod else "HOMOLOGACION",
            "cuit": client.cuit,
            "punto_venta": client.punto_venta,
            "token_first_20": (client._token or "")[:20],
            "token_expiration": client._ta_expiration,
            "server_status": st,
        }
    except afip.AfipError as e:
        events.err(f"AFIP test FAILED: {e}", source="afip")
        return {"ok": False, "error": str(e), "type": "AfipError"}
    except Exception as e:
        events.err(f"AFIP test EXCEPCION: {e}", source="afip")
        return {"ok": False, "error": str(e), "type": type(e).__name__}


# ============================================================================
# AFIP - CONFIGURACION MULTI-CUENTA
# ============================================================================
# Cada cuenta de ML tiene su propio set de credenciales AFIP en Railway:
#   AFIP_CERT_0/1/2, AFIP_KEY_0/1/2, AFIP_CUIT_0/1/2, AFIP_PUNTO_VENTA_0/1/2
# Y usa su propia app de ML para no chocar:
#   STOCK_0=Lenceria, STOCK_1=Shampooshir, STOCK_2=Avellaneda
# La cuenta 1 (Shirly) tiene tambien las legacy AFIP_CERT, AFIP_KEY, etc.


def afip_account_info(account_idx: int) -> dict:
    """Devuelve info de una cuenta para el facturador."""
    if account_idx < 0 or account_idx >= len(ST.get("accounts", [])):
        return None
    acc = ST["accounts"][account_idx]
    has_afip = _afip_module_ok and afip and afip.AfipClient.has_credentials_for(account_idx)
    return {
        "idx": account_idx,
        "name": acc.get("name", f"Cuenta {account_idx}"),
        "uid": str(acc.get("uid", "")),
        "afip_configured": has_afip,
    }


async def _afip_get_token_for(account_idx: int) -> str:
    """Obtiene token de ML usando la app STOCK_N de esa cuenta (independiente)."""
    return await fresh_token_stock(account_idx)


@app.get("/api/afip/cuentas")
def afip_listar_cuentas(_=Depends(auth)):
    """Lista cuentas disponibles para facturar y si tienen credenciales AFIP."""
    out = []
    for i in range(len(ST.get("accounts", []))):
        info = afip_account_info(i)
        if info:
            out.append(info)
    return {"cuentas": out}


@app.get("/api/afip/ventas")
async def afip_listar_ventas(date_from: str, date_to: str, account_idx: int = 1, _=Depends(auth)):
    """
    Lista ventas de una cuenta ML entre date_from y date_to (YYYY-MM-DD).
    Agrupa por pack_id (carrito = una sola factura con varios items).
    Marca cuales ya estan facturadas.

    account_idx: 0=Lenceria, 1=Shampooshir, 2=Avellaneda (default 1)
    """
    if not _afip_module_ok:
        raise HTTPException(500, "Modulo AFIP no disponible")
    if account_idx < 0 or account_idx >= len(ST.get("accounts", [])):
        raise HTTPException(400, "Cuenta invalida")
    seller_uid = str(ST["accounts"][account_idx].get("uid", ""))
    if not seller_uid:
        raise HTTPException(400, "Cuenta sin UID")

    # Convertir fechas a formato ML
    try:
        df = datetime.datetime.strptime(date_from, "%Y-%m-%d")
        dt = datetime.datetime.strptime(date_to, "%Y-%m-%d") + datetime.timedelta(days=1)
        date_from_ml = df.strftime("%Y-%m-%dT00:00:00.000-03:00")
        date_to_ml = dt.strftime("%Y-%m-%dT00:00:00.000-03:00")
    except Exception as e:
        raise HTTPException(400, f"Fecha invalida: {e}")

    try:
        token = await _afip_get_token_for(account_idx)
    except Exception as e:
        raise HTTPException(500, f"Error obteniendo token ML: {e}")

    hdrs = {"Authorization": f"Bearer {token}"}
    ventas = []
    offset = 0
    # Cliente HTTP propio del facturador (NO va por el gateway que comparte con duplicador)
    async with httpx.AsyncClient(timeout=30) as afip_http:
        while True:
            url = (f"{ML_API}/orders/search"
                   f"?seller={seller_uid}"
                   f"&order.date_created.from={date_from_ml}"
                   f"&order.date_created.to={date_to_ml}"
                   f"&order.status=paid"
                   f"&limit=50&offset={offset}")
            try:
                r = await ml_http_request("GET", url, headers=hdrs, client=afip_http,
                                          source_label="afip", max_retries=3)
            except Exception as e:
                events.err(f"Error de red listando ventas: {e}", source="afip")
                break
            if r.status_code == 429:
                # El helper ya hizo retries con jitter. Si seguimos en 429, abortar.
                events.err(f"429 persistente listando ventas tras 3 retries. Abortar carga.", source="afip")
                break
            if r.status_code != 200:
                events.err(f"Error listando ventas (cuenta {account_idx}): {r.status_code} {r.text[:200]}", source="afip")
                break
            d = r.json()
            results = d.get("results", [])
            for order in results:
                ventas.append(order)
            total = d.get("paging", {}).get("total", 0)
            offset += len(results)
            if len(results) == 0 or offset >= total or offset > 1000:
                break
            # Pequeño spacing entre paginas para no saturar
            await asyncio.sleep(0.3)

    # ─── CACHEAR ORDERS EN REDIS (TTL 1 hora) ────────────────────────────
    # Al facturar leemos de Redis, no de ML → evita N requests adicionales
    try:
        _redis_cache = get_redis()
        if _redis_cache:
            for order in ventas:
                oid = str(order.get("id"))
                if oid:
                    _redis_cache.setex(f"mltn:order_cache:{oid}", 3600, json.dumps(order))
    except Exception as e:
        print(f"[afip] cache orders error: {e}")

    facturados_ids = afip.facturas_listar_ids()

    # ─── AGRUPAR POR PACK_ID ─────────────────────────────────────────────
    # Si una orden tiene pack_id, todas las ordenes con ese mismo pack_id
    # se unen en una sola "venta" (con varios items).
    # Si no tiene pack_id, queda como venta individual (clave = order_id).
    groups = {}  # key = pack_id o order_id (para individuales)
    for order in ventas:
        order_id = str(order.get("id"))
        pack_id = order.get("pack_id")
        key = str(pack_id) if pack_id else order_id
        if key not in groups:
            groups[key] = {
                "key": key,
                "is_pack": bool(pack_id),
                "pack_id": str(pack_id) if pack_id else None,
                "order_ids": [],
                "orders": [],
                "buyer": order.get("buyer", {}) or {},
                "billing": (order.get("buyer", {}) or {}).get("billing_info") or {},
                "first_date": order.get("date_created", ""),
                "total": 0,
                "items_count": 0,
                "titulos": [],
                "currency": order.get("currency_id", "ARS"),
            }
        g = groups[key]
        g["order_ids"].append(order_id)
        g["orders"].append(order)
        g["total"] += float(order.get("total_amount", 0) or 0)
        items = order.get("order_items", []) or []
        g["items_count"] += len(items)
        for it in items:
            t = it.get("item", {}).get("title", "")
            if t:
                g["titulos"].append(t)
        # Si tiene fecha mas vieja, ajustar (para ordenar)
        if order.get("date_created", "") < g["first_date"]:
            g["first_date"] = order.get("date_created", "")

    # ─── ARMAR OUTPUT ────────────────────────────────────────────────────
    out = []
    for key, g in groups.items():
        buyer = g["buyer"]
        billing = g["billing"]
        # La factura se guarda con el primer order_id como key
        invoice_key = g["order_ids"][0]
        factura_existente = afip.factura_get(invoice_key) if invoice_key in facturados_ids else None
        # Si no esta facturada con el primer order_id, chequear si algun otro tiene factura
        if not factura_existente:
            for oid in g["order_ids"]:
                if oid in facturados_ids:
                    factura_existente = afip.factura_get(oid)
                    invoice_key = oid
                    break
        titulo_resumen = g["titulos"][0] if g["titulos"] else ""
        if len(g["titulos"]) > 1:
            titulo_resumen = f"{titulo_resumen[:50]}... (+{len(g['titulos'])-1} items)"
        out.append({
            "id": invoice_key,
            "key": key,
            "is_pack": g["is_pack"],
            "pack_id": g["pack_id"],
            "order_ids": g["order_ids"],
            "order_count": len(g["order_ids"]),
            "date": g["first_date"],
            "buyer_nickname": buyer.get("nickname", ""),
            "buyer_name": f"{buyer.get('first_name','')} {buyer.get('last_name','')}".strip(),
            "doc_type": billing.get("doc_type", ""),
            "doc_number": billing.get("doc_number", ""),
            "business_name": billing.get("business_name", ""),
            "total": g["total"],
            "currency": g["currency"],
            "items_count": g["items_count"],
            "titulo": titulo_resumen,
            "already_invoiced": factura_existente is not None,
            "cae": (factura_existente or {}).get("cae", ""),
            "factura_nro": (factura_existente or {}).get("nro", ""),
            "factura_tipo": (factura_existente or {}).get("tipo_letra", (factura_existente or {}).get("tipo", "")),
            "has_pdf": bool((factura_existente or {}).get("pdf_b64")),
            "ml_uploaded": bool((factura_existente or {}).get("ml_fiscal_doc_id")),
        })
    # Ordenar por fecha desc
    out.sort(key=lambda x: x.get("date",""), reverse=True)
    return {"ventas": out, "total": len(out), "from": date_from, "to": date_to}


@app.post("/api/afip/facturar/start")
async def afip_facturar_start(req: Request, _=Depends(auth)):
    """Inicia un job en background para facturar los grupos seleccionados.
    Cada grupo puede tener 1 o N order_ids (carrito unificado)."""
    if not _afip_module_ok:
        raise HTTPException(500, "Modulo AFIP no disponible")
    b = await req.json()
    account_idx = int(b.get("account_idx", 1))
    if account_idx < 0 or account_idx >= len(ST.get("accounts", [])):
        raise HTTPException(400, "Cuenta invalida")
    if not afip.AfipClient.has_credentials_for(account_idx):
        raise HTTPException(400, f"No hay credenciales AFIP configuradas para la cuenta {account_idx}")
    seller_uid = str(ST["accounts"][account_idx].get("uid", ""))
    account_name = ST["accounts"][account_idx].get("name", f"acc{account_idx}")
    # Formato nuevo: groups = [{"order_ids":[...], "key":"..."}, ...]
    # Formato viejo (compatibilidad): order_ids = [...]
    groups = b.get("groups") or []
    if not groups:
        old_ids = b.get("order_ids", [])
        groups = [{"order_ids":[oid]} for oid in old_ids]
    upload_to_ml = b.get("upload_to_ml", True)
    fecha_facturacion = b.get("fecha_facturacion", "")
    if not groups:
        raise HTTPException(400, "Sin grupos a facturar")

    # Validar fecha
    fecha_cbte_afip = None
    if fecha_facturacion:
        try:
            f_dt = datetime.datetime.strptime(fecha_facturacion, "%Y-%m-%d")
            hoy = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            diff = abs((f_dt - hoy).days)
            if diff > 5:
                raise HTTPException(400, f"AFIP solo permite fechas dentro de los 5 dias previos/siguientes. Pediste {diff} dias de diferencia.")
            fecha_cbte_afip = f_dt.strftime("%Y%m%d")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, f"Fecha invalida ({fecha_facturacion}): {e}")

    job_id = jobs.create("afip_facturar", {
        "groups_count": len(groups),
        "upload_to_ml": upload_to_ml,
        "fecha_facturacion": fecha_facturacion,
        "account_idx": account_idx,
        "account_name": account_name,
    })
    jobs.update(job_id, total=len(groups), status="running")
    fecha_msg = f" con fecha {fecha_facturacion}" if fecha_facturacion else ""
    events.info(f"Inicio facturacion AFIP [{account_name}]: {len(groups)} grupo(s){fecha_msg}" + (" (con subida a ML)" if upload_to_ml else " (sin subir a ML)"),
                source="afip", job_id=job_id)
    asyncio.create_task(_do_afip_facturar_job(job_id, groups, upload_to_ml, fecha_cbte_afip, account_idx, seller_uid))
    return {"ok": True, "job_id": job_id, "total": len(groups)}


async def _do_afip_facturar_job(job_id: str, groups: list, upload_to_ml: bool = True,
                                fecha_cbte_afip: str = None, account_idx: int = 1,
                                seller_uid: str = ""):
    """Procesa la facturacion en background. Cada group factura una sola factura
    con todos los items de las ordenes del grupo (carrito unificado)."""
    try:
        client = afip.AfipClient.from_env(account_idx=account_idx)
        await client.init()
        events.ok(f"AFIP autenticado, CUIT {client.cuit}, PV {client.punto_venta}",
                  source="afip", job_id=job_id)
    except Exception as e:
        events.err(f"No se pudo autenticar con AFIP: {e}", source="afip", job_id=job_id)
        jobs.finish(job_id, status="error", error=str(e))
        return

    try:
        token = await _afip_get_token_for(account_idx)
    except Exception as e:
        events.err(f"No se pudo obtener token ML: {e}", source="afip", job_id=job_id)
        jobs.finish(job_id, status="error", error=str(e))
        return

    hdrs = {"Authorization": f"Bearer {token}"}
    total_grupos = len(groups)

    for idx, group in enumerate(groups):
        # Chequear cancelacion
        current = jobs.get(job_id)
        if current and current.get("status") == "cancel_requested":
            events.warn(f"Job cancelado por usuario en {idx}/{total_grupos}",
                        source="afip", job_id=job_id)
            jobs.finish(job_id, status="cancelled")
            return

        order_ids_in_group = group.get("order_ids", [])
        if not order_ids_in_group:
            continue
        # invoice_key = primer order_id (es la clave de almacenamiento)
        invoice_key = str(order_ids_in_group[0])
        is_pack = len(order_ids_in_group) > 1
        group_label = f"PACK[{len(order_ids_in_group)}]" if is_pack else "Orden"

        item_result = {"id": invoice_key, "ok": False,
                       "title": f"{group_label} {invoice_key}", "msg": "",
                       "order_ids": order_ids_in_group}

        # Si alguna del grupo ya fue facturada, marcar y seguir
        already_invoiced_id = None
        for oid in order_ids_in_group:
            if afip.factura_get(str(oid)):
                already_invoiced_id = str(oid)
                break
        if already_invoiced_id:
            existing = afip.factura_get(already_invoiced_id)
            item_result["ok"] = True
            item_result["msg"] = f"Ya facturada CAE {existing.get('cae','')[:10]}..."
            item_result["already"] = True
            jobs.add_item(job_id, item_result)
            continue

        events.info(f"━━━ {group_label} {idx+1}/{total_grupos}: {','.join(order_ids_in_group)} ━━━",
                    source="afip", job_id=job_id)

        try:
            # 1. Bajar TODAS las orders del grupo (intentando cache primero)
            all_orders = []
            _redis_cache = get_redis()
            for oid in order_ids_in_group:
                order_data = None
                # Intentar leer del cache primero
                if _redis_cache:
                    try:
                        cached = _redis_cache.get(f"mltn:order_cache:{oid}")
                        if cached:
                            order_data = json.loads(cached)
                            events.info(f"  ✓ Orden {oid} (cache)", source="afip", job_id=job_id)
                    except Exception:
                        pass
                # Si no estaba en cache, pedirla a ML (helper con jitter, NO gateway)
                if not order_data:
                    events.info(f"  → GET /orders/{oid} (ML)", source="afip", job_id=job_id)
                    try:
                        r = await ml_http_request("GET", f"{ML_API}/orders/{oid}",
                                                  headers=hdrs, source_label="afip", max_retries=3)
                        if r.status_code != 200:
                            raise Exception(f"ML {r.status_code} en orden {oid}")
                        order_data = r.json()
                        if _redis_cache:
                            try:
                                _redis_cache.setex(f"mltn:order_cache:{oid}", 3600, json.dumps(order_data))
                            except: pass
                    except Exception as e:
                        raise
                if order_data:
                    all_orders.append(order_data)

            # 2. Consolidar datos (buyer del primero, sumar totales, juntar items)
            main_order = all_orders[0]
            buyer = main_order.get("buyer", {}) or {}
            total_consolidado = 0.0
            for o in all_orders:
                total_consolidado += float(o.get("total_amount", 0) or 0)
            if total_consolidado <= 0:
                raise Exception(f"Total invalido: {total_consolidado}")
            item_result["title"] = f"{buyer.get('nickname','?')} - ${total_consolidado:.2f}" + (f" ({len(all_orders)} ordenes)" if is_pack else "")

            # 3. Decidir A o B (basado en el buyer del grupo)
            decision = await afip.decidir_tipo_factura(client, buyer)
            tipo_str = "A" if decision["tipo_cbte"] == afip.CBTE_FACTURA_A else "B"
            events.info(f"  ℹ {decision['motivo']}", source="afip", job_id=job_id)

            # 4. Emitir factura UNICA por la suma total
            fecha_msg = f" con fecha {fecha_cbte_afip[6:8]}/{fecha_cbte_afip[4:6]}/{fecha_cbte_afip[0:4]}" if fecha_cbte_afip else ""
            events.info(f"  → Emitiendo Factura {tipo_str} por ${total_consolidado:.2f}{fecha_msg}" + (f" (consolidando {len(all_orders)} ordenes)" if is_pack else ""),
                        source="afip", job_id=job_id)
            cae_data = await client.emitir_factura(
                tipo_cbte=decision["tipo_cbte"],
                doc_tipo=decision["doc_tipo"],
                doc_nro=decision["doc_nro"],
                imp_total=total_consolidado,
                cond_iva_receptor=decision["cond_iva_receptor"],
                fecha_cbte=fecha_cbte_afip,
            )

            # 5. Guardar
            factura_data = {
                **cae_data,
                "tipo_letra": tipo_str,
                "order_id": invoice_key,
                "order_ids": order_ids_in_group,
                "is_pack": is_pack,
                "pack_id": group.get("pack_id"),
                "razon_social": decision["razon_social"],
                "doc_tipo": decision["doc_tipo"],
                "doc_nro": decision["doc_nro"],
                "buyer_nickname": buyer.get("nickname", ""),
                "ts": time.time(),
                "account_idx": account_idx,
            }

            # 6. Generar PDF (con items de TODAS las ordenes del grupo)
            pdf_bytes = None
            if _pdf_module_ok and pdf_factura:
                try:
                    events.info(f"  → Generando PDF...", source="afip", job_id=job_id)
                    # Sumar costo de envio de todas las ordenes
                    shipping_cost = 0
                    for o in all_orders:
                        for pay in (o.get("payments") or []):
                            sc = float(pay.get("shipping_cost", 0) or 0)
                            if sc:
                                shipping_cost += sc
                                break

                    # Items de TODAS las ordenes
                    pdf_items = []
                    for o in all_orders:
                        order_id_for_item = str(o.get("id"))
                        for oit in (o.get("order_items") or []):
                            item_info = oit.get("item", {}) or {}
                            pdf_items.append({
                                "id": item_info.get("id", ""),
                                "title": item_info.get("title", ""),
                                "quantity": oit.get("quantity", 1),
                                "unit_price": oit.get("unit_price", 0),
                                "variation_attributes": item_info.get("variation_attributes") or [],
                                "order_id_ref": order_id_for_item,
                            })

                    # order_id para mostrar en el PDF
                    pdf_order_id_str = ", ".join(order_ids_in_group) if is_pack else invoice_key

                    pdf_bytes = pdf_factura.generar_pdf_factura(
                        factura=factura_data,
                        decision=decision,
                        items=pdf_items,
                        envio_costo=shipping_cost,
                        order_id=pdf_order_id_str,
                        buyer_nickname=buyer.get("nickname", ""),
                    )
                    factura_data["pdf_b64"] = base64.b64encode(pdf_bytes).decode("ascii")
                    events.ok(f"  ✓ PDF generado ({len(pdf_bytes)//1024} KB)",
                              source="afip", job_id=job_id)
                except Exception as e:
                    events.warn(f"  ⚠ No se pudo generar PDF: {str(e)[:150]}",
                                source="afip", job_id=job_id)

            # 7. Subir PDF a ML (al pack_id del grupo, o a la orden si no es pack)
            if pdf_bytes and upload_to_ml:
                try:
                    upload_target = group.get("pack_id") or invoice_key
                    events.info(f"  → Subiendo PDF a ML (pack {upload_target})...",
                                source="afip", job_id=job_id)
                    upload_url = f"{ML_API}/packs/{upload_target}/fiscal_documents"
                    files = {
                        "fiscal_document": (
                            f"factura_{tipo_str}_{cae_data['nro']:08d}.pdf",
                            pdf_bytes,
                            "application/pdf",
                        )
                    }
                    headers_up = {"Authorization": f"Bearer {token}"}
                    # Subir con jitter automatico (3 retries en 429 con jitter)
                    up_r = await ml_http_request("POST", upload_url, headers=headers_up,
                                                  files=files, timeout=60,
                                                  source_label="afip", max_retries=3)
                    if up_r.status_code in (200, 201):
                        try:
                            ml_resp = up_r.json()
                            ml_fiscal_id = (ml_resp.get("ids") or [None])[0]
                            factura_data["ml_fiscal_doc_id"] = ml_fiscal_id
                            events.ok(f"  ✓ PDF subido a ML, id={ml_fiscal_id}",
                                      source="afip", job_id=job_id)
                        except:
                            events.ok(f"  ✓ PDF subido a ML",
                                      source="afip", job_id=job_id)
                        # El PDF ya esta en ML, no lo guardo en Redis para ahorrar espacio
                        if "pdf_b64" in factura_data:
                            del factura_data["pdf_b64"]
                        factura_data["pdf_purgado"] = True
                    else:
                        events.warn(f"  ⚠ ML rechazo el PDF: {up_r.status_code} {up_r.text[:200]}",
                                    source="afip", job_id=job_id)
                        factura_data["ml_upload_error"] = f"{up_r.status_code}: {up_r.text[:200]}"
                except Exception as e:
                    events.warn(f"  ⚠ Error subiendo PDF a ML: {str(e)[:150]}",
                                source="afip", job_id=job_id)
                    factura_data["ml_upload_error"] = str(e)[:200]
            elif pdf_bytes and not upload_to_ml:
                events.info(f"  ⊘ Subida a ML deshabilitada por el usuario",
                            source="afip", job_id=job_id)

            # 8. Guardar la factura con TODOS los order_ids como claves
            # (asi al recargar la lista cualquier orden del pack aparece como facturada)
            for oid in order_ids_in_group:
                afip.factura_save(str(oid), factura_data)

            item_result["ok"] = True
            item_result["msg"] = f"★ Factura {tipo_str} N°{cae_data['nro']:08d} - CAE {cae_data['cae']}"
            item_result["cae"] = cae_data["cae"]
            item_result["nro"] = cae_data["nro"]
            item_result["tipo"] = tipo_str
            events.ok(f"  ★ Factura {tipo_str} N°{cae_data['nro']} CAE {cae_data['cae']}",
                      source="afip", job_id=job_id)

        except afip.AfipError as e:
            item_result["msg"] = f"AFIP: {str(e)[:200]}"
            events.err(f"  ✗ AFIP rechazo: {str(e)[:150]}",
                       source="afip", job_id=job_id)
        except Exception as e:
            item_result["msg"] = f"Error: {str(e)[:150]}"
            events.err(f"  ✗ Excepcion: {str(e)[:150]}",
                       source="afip", job_id=job_id)

        jobs.add_item(job_id, item_result)
        # Pacing entre facturas - ML es estricto con uploads consecutivos
        await asyncio.sleep(1.2)

    events.ok(f"━━━ Facturacion terminada ━━━", source="afip", job_id=job_id)
    jobs.finish(job_id, status="done")


@app.get("/api/afip/factura/{order_id}")
def afip_factura_get(order_id: str, _=Depends(auth)):
    """Devuelve datos de una factura emitida (sin el PDF, demasiado pesado)."""
    if not _afip_module_ok:
        raise HTTPException(500)
    f = afip.factura_get(order_id)
    if not f:
        raise HTTPException(404, "Factura no encontrada")
    # Quitar el PDF base64 del response (pesado)
    out = {k: v for k, v in f.items() if k != "pdf_b64"}
    out["has_pdf"] = bool(f.get("pdf_b64"))
    return out


@app.get("/api/afip/pdf/{order_id}")
def afip_pdf_get(order_id: str, token: str = None, _=None):
    """Devuelve el PDF de la factura para descarga.
    Auth manual por query param para que se pueda abrir en una pestaña nueva."""
    from fastapi.responses import Response
    # Auth manual (igual que SSE)
    t = token or ""
    if not t or t not in SESSIONS or SESSIONS[t] < time.time():
        raise HTTPException(401, "No autorizado")
    if not _afip_module_ok:
        raise HTTPException(500)
    f = afip.factura_get(order_id)
    if not f:
        raise HTTPException(404, "Factura no encontrada")
    if not f.get("pdf_b64"):
        if f.get("pdf_purgado") and f.get("ml_fiscal_doc_id"):
            raise HTTPException(410, "PDF no disponible localmente (ya fue entregado al comprador via ML). Consultalo desde la cuenta de ML.")
        raise HTTPException(404, "PDF no disponible")
    try:
        pdf_bytes = base64.b64decode(f["pdf_b64"])
    except Exception as e:
        raise HTTPException(500, f"Error decodificando PDF: {e}")
    filename = f"factura_{f.get('tipo_letra','B')}_{f.get('nro',0):08d}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'}
    )


@app.get("/api/afip/stats")
def afip_stats(date_from: str = None, date_to: str = None, account_idx: int = None, _=Depends(auth)):
    """Metricas de facturacion: total facturado, cantidad A/B, etc.
    Si account_idx viene, filtra solo facturas de esa cuenta."""
    if not _afip_module_ok:
        raise HTTPException(500)
    r = get_redis()
    if not r:
        return {"error": "Redis no disponible"}

    # Obtener todas las facturas (limitado a 500 para no saturar)
    try:
        keys = []
        for _k in r.scan_iter(match="mltn:afip_factura:*", count=300):
            keys.append(_k)
            if len(keys) >= 500:
                break
    except Exception as e:
        return {"error": f"Redis error: {e}"}

    total_facturado = 0.0
    total_neto = 0.0
    total_iva = 0.0
    cant_a = 0
    cant_b = 0
    cant_uploaded = 0
    cant_errores_upload = 0
    by_day = {}
    by_month = {}
    facturas_recientes = []

    df_ts = None
    dt_ts = None
    if date_from:
        try:
            df_ts = datetime.datetime.strptime(date_from, "%Y-%m-%d").timestamp()
        except: pass
    if date_to:
        try:
            dt_ts = (datetime.datetime.strptime(date_to, "%Y-%m-%d") + datetime.timedelta(days=1)).timestamp()
        except: pass

    for k in keys:
        try:
            raw = r.get(k)
            if not raw:
                continue
            f = json.loads(raw)
            # Filtrar por cuenta si se especifico
            if account_idx is not None and f.get("account_idx") is not None and f.get("account_idx") != account_idx:
                continue
            # IGNORAR NOTAS DE CREDITO en el calculo de facturado
            if f.get("es_nc"):
                continue
            # Usar la FECHA DEL COMPROBANTE AFIP (YYYYMMDD) como referencia, no ts de emision
            # Asi cuando facturás con fecha pasada/futura se cuenta correctamente
            fecha_str = f.get("fecha", "")  # "20260515" formato AFIP
            fecha_dt = None
            if fecha_str and len(fecha_str) == 8:
                try:
                    fecha_dt = datetime.datetime.strptime(fecha_str, "%Y%m%d")
                except:
                    pass
            # Fallback al ts si no hay fecha
            if not fecha_dt:
                ts = f.get("ts", 0)
                fecha_dt = datetime.datetime.fromtimestamp(ts) if ts else None
            if not fecha_dt:
                continue
            fecha_ts = fecha_dt.timestamp()

            if df_ts and fecha_ts < df_ts:
                continue
            if dt_ts and fecha_ts >= dt_ts:
                continue

            tot = float(f.get("imp_total", 0))
            total_facturado += tot
            total_neto += float(f.get("imp_neto", 0))
            total_iva += float(f.get("imp_iva", 0))
            letra = f.get("tipo_letra", "B")
            if letra == "A": cant_a += 1
            else: cant_b += 1
            if f.get("ml_fiscal_doc_id"): cant_uploaded += 1
            if f.get("ml_upload_error"): cant_errores_upload += 1

            day_key = fecha_dt.strftime("%Y-%m-%d")
            month_key = fecha_dt.strftime("%Y-%m")
            ts = fecha_ts  # para el sort
            by_day[day_key] = by_day.get(day_key, 0) + tot
            by_month[month_key] = by_month.get(month_key, 0) + tot

            facturas_recientes.append({
                "order_id": f.get("order_id"),
                "nro": f.get("nro"),
                "tipo": letra,
                "total": tot,
                "ts": ts,
                "cae": f.get("cae"),
                "buyer": f.get("buyer_nickname", ""),
                "razon_social": f.get("razon_social", ""),
                "ml_uploaded": bool(f.get("ml_fiscal_doc_id")),
            })
        except Exception:
            continue

    facturas_recientes.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return {
        "total_facturado": round(total_facturado, 2),
        "total_neto": round(total_neto, 2),
        "total_iva": round(total_iva, 2),
        "cantidad": cant_a + cant_b,
        "cant_a": cant_a,
        "cant_b": cant_b,
        "cant_pdf_uploaded": cant_uploaded,
        "cant_upload_errors": cant_errores_upload,
        "by_day": by_day,
        "by_month": by_month,
        "facturas_recientes": facturas_recientes[:50],
    }


@app.post("/api/afip/nota_credito")
async def afip_emitir_nc(req: Request, _=Depends(auth)):
    """Emitir Nota de Credito para anular una factura.
    NO sube a ML automaticamente (por decision del usuario).
    """
    if not _afip_module_ok:
        raise HTTPException(500, "Modulo AFIP no disponible")
    b = await req.json()
    order_id = b.get("order_id", "")
    motivo = (b.get("motivo") or "Anulacion").strip()
    if not order_id:
        raise HTTPException(400, "Falta order_id")

    # Buscar factura original
    factura_orig = afip.factura_get(order_id)
    if not factura_orig:
        raise HTTPException(404, "Factura original no encontrada")
    if factura_orig.get("nc_emitida"):
        return {"ok": False, "msg": "Esta factura ya tiene Nota de Credito emitida",
                "nc": factura_orig.get("nc_emitida")}

    events.info(f"Emitiendo NC para factura {factura_orig.get('tipo_letra','B')} N°{factura_orig.get('nro',0):08d}",
                source="afip")

    # Determinar account_idx desde la factura original (o usar el del request)
    account_idx_nc = b.get("account_idx", factura_orig.get("account_idx", 1))

    try:
        client = afip.AfipClient.from_env(account_idx=account_idx_nc)
        await client.init()
    except Exception as e:
        raise HTTPException(500, f"Error AFIP auth: {e}")

    # Tipo de NC segun tipo de factura original
    tipo_orig = factura_orig.get("tipo", afip.CBTE_FACTURA_B)
    if tipo_orig == afip.CBTE_FACTURA_A:
        tipo_nc = afip.CBTE_NOTA_CREDITO_A
        tipo_letra_nc = "A"
    else:
        tipo_nc = afip.CBTE_NOTA_CREDITO_B
        tipo_letra_nc = "B"

    try:
        cbte_asoc = {
            "tipo": tipo_orig,
            "punto_venta": factura_orig.get("punto_venta", 3),
            "nro": factura_orig.get("nro"),
            "cuit": client.cuit,
            "fecha": factura_orig.get("fecha", ""),
        }
        cae_data = await client.emitir_factura(
            tipo_cbte=tipo_nc,
            doc_tipo=factura_orig.get("doc_tipo", afip.DOC_CONSUMIDOR_FINAL),
            doc_nro=factura_orig.get("doc_nro", 0),
            imp_total=factura_orig.get("imp_total", 0),
            cond_iva_receptor=5,  # Default consumidor final si no tenemos el dato
            cbte_asoc=cbte_asoc,
        )
        events.ok(f"★ NC {tipo_letra_nc} N°{cae_data['nro']:08d} CAE {cae_data['cae']}", source="afip")

        nc_data = {
            **cae_data,
            "tipo_letra": tipo_letra_nc,
            "es_nc": True,
            "factura_anulada_id": order_id,
            "factura_anulada_nro": factura_orig.get("nro"),
            "motivo": motivo,
            "ts": time.time(),
            "razon_social": factura_orig.get("razon_social", ""),
            "doc_tipo": factura_orig.get("doc_tipo"),
            "doc_nro": factura_orig.get("doc_nro"),
            "buyer_nickname": factura_orig.get("buyer_nickname", ""),
        }

        # Generar PDF de la NC
        if _pdf_module_ok and pdf_factura:
            try:
                pdf_items = [{
                    "id": "ANULACION",
                    "title": f"Anulación Factura {factura_orig.get('tipo_letra','B')} N°{factura_orig.get('nro',0):08d}",
                    "quantity": 1,
                    "unit_price": factura_orig.get("imp_total", 0),
                }]
                decision_nc = {
                    "tipo_cbte": tipo_nc,
                    "doc_tipo": nc_data["doc_tipo"],
                    "doc_nro": nc_data["doc_nro"],
                    "cond_iva_receptor": 5,
                    "razon_social": nc_data["razon_social"],
                }
                pdf_bytes = pdf_factura.generar_pdf_factura(
                    factura=nc_data,
                    decision=decision_nc,
                    items=pdf_items,
                    envio_costo=0,
                    order_id=f"NC de orden {order_id} - Motivo: {motivo}",
                    buyer_nickname=factura_orig.get("buyer_nickname", ""),
                )
                nc_data["pdf_b64"] = base64.b64encode(pdf_bytes).decode("ascii")
            except Exception as e:
                events.warn(f"No se pudo generar PDF de NC: {e}", source="afip")

        # Guardar NC con clave nc_{order_id}
        nc_key = f"nc_{order_id}"
        afip.factura_save(nc_key, nc_data)
        # Marcar la factura original
        factura_orig["nc_emitida"] = nc_key
        afip.factura_save(order_id, factura_orig)

        return {
            "ok": True,
            "nc_key": nc_key,
            "tipo_letra": tipo_letra_nc,
            "nro": cae_data["nro"],
            "cae": cae_data["cae"],
        }
    except afip.AfipError as e:
        events.err(f"AFIP rechazo NC: {e}", source="afip")
        return {"ok": False, "error": str(e)}
    except Exception as e:
        events.err(f"Error emitiendo NC: {e}", source="afip")
        return {"ok": False, "error": str(e)}


@app.post("/api/login")
async def login(req: Request):
    b = await req.json()
    if b.get("email","").lower() != ADMIN_EMAIL.lower() or b.get("password","") != ADMIN_PASS:
        raise HTTPException(401, "Email o contrasena incorrectos.")
    t = secrets.token_hex(32)
    # Sesion de 30 dias. Se renueva en cada request (sliding window).
    SESSIONS[t] = time.time() + 86400 * 30
    _save_sessions()  # persistir en Redis para sobrevivir reinicios
    return {"token": t, "ok": True}

@app.post("/api/logout")
def logout(s=Depends(auth)):
    SESSIONS.pop(s, None)
    _save_sessions()
    return {"ok": True}

@app.get("/auth/login")
def ml_login():
    return RedirectResponse(
        f"https://auth.mercadolibre.com.ar/authorization"
        f"?response_type=code&client_id={ML_APP_ID}&redirect_uri={REDIRECT_URI}"
    )

@app.get("/debug/env")
async def debug_env(_=Depends(auth)):
    """Debug: ver variables de stock configuradas (requiere auth)"""
    return {
        "stock_0_id": os.getenv("ML_CLIENT_ID_STOCK_0","")[:8]+"..." if os.getenv("ML_CLIENT_ID_STOCK_0") else "MISSING",
        "stock_1_id": os.getenv("ML_CLIENT_ID_STOCK_1","")[:8]+"..." if os.getenv("ML_CLIENT_ID_STOCK_1") else "MISSING",
        "stock_2_id": os.getenv("ML_CLIENT_ID_STOCK_2","")[:8]+"..." if os.getenv("ML_CLIENT_ID_STOCK_2") else "MISSING",
        "pub_id": os.getenv("ML_CLIENT_ID_PUB","")[:8]+"..." if os.getenv("ML_CLIENT_ID_PUB") else "MISSING",
        "ML_STOCK_IDS": [x[:8]+"..." if x else "EMPTY" for x in ML_STOCK_IDS],
    }

@app.get("/ml/auth_stock/{i}")
async def ml_auth_stock(i: int):
    """Iniciar OAuth con la app de stock para la cuenta i"""
    client_id = os.getenv(f"ML_CLIENT_ID_STOCK_{i}", "")
    print(f"auth_stock {i}: client_id='{client_id}' env_keys={[k for k in os.environ.keys() if 'STOCK' in k]}")
    if not client_id:
        return {"error": f"ML_CLIENT_ID_STOCK_{i} no configurado", "env_keys": [k for k in os.environ.keys() if 'STOCK' in k], "direct": os.getenv("ML_CLIENT_ID_STOCK_1","MISSING")}
    if i >= len(ST["accounts"]):
        raise HTTPException(404)
    redirect = f"{APP_URL}/ml/callback_stock/{i}"
    url = f"https://auth.mercadolibre.com.ar/authorization?response_type=code&client_id={client_id}&redirect_uri={redirect}"
    return RedirectResponse(url)

@app.get("/ml/callback_stock/{i}")
async def ml_callback_stock(i: int, code: str = "", error: str = ""):
    """Callback OAuth de la app de stock"""
    if error or not code:
        return RedirectResponse(f"{APP_URL}/?error=stock_token_failed")
    try:
        redirect = f"{APP_URL}/ml/callback_stock/{i}"
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post("https://api.mercadolibre.com/oauth/token",
                data={"grant_type":"authorization_code","client_id":ML_STOCK_IDS[i],
                      "client_secret":ML_STOCK_SECRETS[i],"code":code,"redirect_uri":redirect},
                headers={"Content-Type":"application/x-www-form-urlencoded","Accept":"application/json"})
        print(f"Stock callback {i}: status={r.status_code} body={r.text[:200]}")
        try:
            d = r.json()
        except:
            d = {}
        if "access_token" in d:
            acc = ST["accounts"][i]
            acc[f"token_stock_{i}"] = d["access_token"]
            acc[f"token_stock_expiry_{i}"] = time.time() + d.get("expires_in", 21600) - 300
            acc[f"refresh_stock_{i}"] = d.get("refresh_token","")
            save_state()
            print(f"Token stock OK cuenta {i}: {acc.get('name','')}")
            return RedirectResponse(f"{APP_URL}/?stock_auth=ok&acc={i}")
        return RedirectResponse(f"{APP_URL}/?error=stock_token_failed")
    except Exception as e:
        return RedirectResponse(f"{APP_URL}/?error=stock_token_failed&msg={str(e)[:50]}")

@app.get("/ml/auth_pub/{i}")
async def ml_auth_pub(i: int):
    """Iniciar OAuth con la app de publicacion para la cuenta i"""
    if not ML_APP_ID_PUB:
        return {"error": "ML_APP_ID_PUB no configurado en Railway"}
    if i >= len(ST["accounts"]):
        raise HTTPException(404)
    redirect = f"{APP_URL}/ml/callback_pub"
    url = f"https://auth.mercadolibre.com.ar/authorization?response_type=code&client_id={ML_APP_ID_PUB}&redirect_uri={redirect}&state={i}"
    return RedirectResponse(url)

@app.get("/ml/callback_pub")
async def ml_callback_pub(code: str = "", state: str = "0", error: str = ""):
    """Callback OAuth de la app de publicacion"""
    if error or not code:
        return RedirectResponse(f"{APP_URL}/?error=pub_token_failed")
    try:
        i = int(state)
        redirect = f"{APP_URL}/ml/callback_pub"
        print(f"ML callback_pub: exchanging code for token...")
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post("https://api.mercadolibre.com/oauth/token",
                data={"grant_type":"authorization_code","client_id":ML_APP_ID_PUB,
                      "client_secret":ML_SECRET_PUB,"code":code,"redirect_uri":redirect},
                headers={"Content-Type":"application/x-www-form-urlencoded",
                         "Accept":"application/json"})
        print(f"ML callback_pub status={r.status_code} body={r.text[:300]}")
        try:
            d = r.json()
        except Exception:
            d = {}
        if "access_token" in d:
            acc = ST["accounts"][i]
            acc["token_pub"] = d["access_token"]
            acc["token_pub_expiry"] = time.time() + d.get("expires_in", 21600) - 300
            acc["refresh_pub"] = d.get("refresh_token","")
            save_state()
            print(f"Token pub OK para cuenta {i}: {acc.get('name','')}")
            return RedirectResponse(f"{APP_URL}/?pub_auth=ok&acc={i}")
        err = d.get("message", d.get("error", f"status {r.status_code}"))
        print(f"Token pub FAIL: {err}")
        return RedirectResponse(f"{APP_URL}/?error=pub_token_failed&msg={err[:50]}")
    except Exception as e:
        print(f"Token pub exception: {e}")
        return RedirectResponse(f"{APP_URL}/?error=pub_token_failed&msg={str(e)[:50]}")

@app.get("/auth/callback")
async def ml_callback(code: str = None, error: str = None):
    if not code:
        return RedirectResponse(f"{APP_URL}/?error=auth_failed")
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post("https://api.mercadolibre.com/oauth/token",
            data={"grant_type":"authorization_code","client_id":ML_APP_ID,
                  "client_secret":ML_SECRET,"code":code,"redirect_uri":REDIRECT_URI},
            headers={"Content-Type":"application/x-www-form-urlencoded"})
        try:
            td = r.json() if r.content else {}
        except Exception:
            td = {}
    if "access_token" not in td:
        err = td.get("message", td.get("error", f"HTTP {r.status_code} - codigo expirado o invalido"))
        print(f"ML callback error: {err} | status={r.status_code} | body={r.text[:200]}")
        return RedirectResponse(f"{APP_URL}/?error=token_failed&msg={err}")
    token = td["access_token"]
    uid = str(td.get("user_id",""))
    async with httpx.AsyncClient(timeout=10) as c:
        ur = await c.get(f"{ML_API}/users/{uid}", headers={"Authorization":f"Bearer {token}"})
        info = ur.json()
    name = info.get("nickname", f"Cuenta {len(ST['accounts'])+1}")
    for acc in ST["accounts"]:
        if acc["uid"] == uid:
            acc.update({"token":token,"refresh":td.get("refresh_token",""),
                        "expiry":time.time()+td.get("expires_in",21600)-300})
            save_state()
            return RedirectResponse(f"{APP_URL}/?success=reconnected")
    if len(ST["accounts"]) >= 4:
        return RedirectResponse(f"{APP_URL}/?error=max_accounts")
    ST["accounts"].append({"name":name,"uid":uid,"token":token,
                           "refresh":td.get("refresh_token",""),
                           "expiry":time.time()+td.get("expires_in",21600)-300})
    save_state()
    return RedirectResponse(f"{APP_URL}/?success=connected")

async def fresh_token(i: int) -> str:
    acc = ST["accounts"][i]
    # Si el token todavia es valido, devolverlo sin lock (rapido)
    if time.time() <= acc.get("expiry", 0):
        return acc["token"]
    if not acc.get("refresh"):
        return acc.get("token", "")
    # Token vencido: tomar lock para que solo UN refresh ocurra por cuenta
    lock = _get_token_lock(f"main_{i}")
    async with lock:
        # Doble-check: quiza otro request ya lo refresco mientras esperabamos el lock
        if time.time() <= acc.get("expiry", 0):
            return acc["token"]
        try:
            async with httpx.AsyncClient(timeout=15) as _c:
                r = await _c.post("https://api.mercadolibre.com/oauth/token",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    data={"grant_type":"refresh_token","client_id":ML_APP_ID,
                          "client_secret":ML_SECRET,"refresh_token":acc["refresh"]})
            td = r.json()
            if "access_token" in td:
                acc["token"] = td["access_token"]
                acc["refresh"] = td.get("refresh_token", acc["refresh"])
                acc["expiry"] = time.time() + td.get("expires_in",21600) - 300
                acc["token_ok"] = True
                save_state()
            else:
                # Refresh fallo — marcar como vencido
                acc["token_ok"] = False
                acc["expiry"] = 0
                save_state()
        except Exception as e:
            acc["token_ok"] = False
            save_state()
    return acc["token"]

async def fresh_token_pub(i: int) -> str:
    """Token de la app de publicacion — rate limit separado del de ventas"""
    if not ML_APP_ID_PUB or not ML_SECRET_PUB:
        return await fresh_token(i)  # fallback si no hay app pub
    acc = ST["accounts"][i]
    token = acc.get("token_pub", "")
    expiry = acc.get("token_pub_expiry", 0)
    # Si todavia es valido, devolver sin lock
    if token and time.time() < expiry:
        return token
    if not acc.get("refresh_pub"):
        return await fresh_token(i)
    # Token vencido: lock por cuenta para evitar race
    lock = _get_token_lock(f"pub_{i}")
    async with lock:
        # Doble-check
        if acc.get("token_pub") and time.time() < acc.get("token_pub_expiry", 0):
            return acc["token_pub"]
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post("https://api.mercadolibre.com/oauth/token",
                    data={"grant_type":"refresh_token","client_id":ML_APP_ID_PUB,
                          "client_secret":ML_SECRET_PUB,"refresh_token":acc["refresh_pub"]},
                    headers={"Content-Type":"application/x-www-form-urlencoded"})
                d = r.json()
            if "access_token" in d:
                acc["token_pub"] = d["access_token"]
                acc["token_pub_expiry"] = time.time() + d.get("expires_in", 21600) - 300
                acc["refresh_pub"] = d.get("refresh_token", acc["refresh_pub"])
                save_state()
                return acc["token_pub"]
        except:
            pass
    # Sin token pub — fallback al token principal
    return await fresh_token(i)

@app.get("/api/state")
def get_state(_=Depends(auth)):
    return {
        "ml_accounts": [{"name":a["name"],"user_id":a["uid"],
                         "token_ok": time.time() < a.get("expiry",0)} for a in ST["accounts"]],
        "tn_connected": bool(ST["tn"].get("store_id")),
        "tn_store_id": ST["tn"].get("store_id",""),
        "last_sync": None,
        "sync_log": ST["log"][-50:],
        "links": ST["links"]
    }

@app.delete("/api/ml/{i}")
def remove_ml(i: int, _=Depends(auth)):
    if i < 0 or i >= len(ST["accounts"]):
        raise HTTPException(404)
    ST["accounts"].pop(i)
    save_state()
    return {"ok": True}

@app.get("/tn/authorize")
async def tn_authorize(client_id: str, client_secret: str, code: str):
    """Intercambiar code de TN por access_token automáticamente"""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post("https://www.tiendanube.com/apps/authorize/token",
                headers={"Content-Type": "application/json"},
                json={"client_id": client_id, "client_secret": client_secret,
                      "grant_type": "authorization_code", "code": code})
            d = r.json()
        if "access_token" in d:
            ST["tn"]["store_id"] = str(d.get("user_id", ""))
            ST["tn"]["token"] = d["access_token"]
            save_state()
            return RedirectResponse(url="/?success=tn_connected")
        else:
            return {"error": "No se obtuvo access_token", "response": d}
    except Exception as e:
        return {"error": str(e)}

@app.get("/tn/callback")
async def tn_callback(code: str = "", error: str = ""):
    """Callback de TN — recibe el code y lo intercambia por access_token automáticamente"""
    if error or not code:
        return RedirectResponse(url="/?error=tn_auth_failed")
    try:
        TN_CLIENT_ID = os.getenv("TN_CLIENT_ID", "27952")
        TN_CLIENT_SECRET = os.getenv("TN_CLIENT_SECRET")
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post("https://www.tiendanube.com/apps/authorize/token",
                headers={"Content-Type": "application/json"},
                json={"client_id": TN_CLIENT_ID, "client_secret": TN_CLIENT_SECRET,
                      "grant_type": "authorization_code", "code": code})
            d = r.json()
        if "access_token" in d:
            ST["tn"]["store_id"] = str(d.get("user_id", ""))
            ST["tn"]["token"] = d["access_token"]
            save_state()
            return RedirectResponse(url="/?success=tn_connected")
        else:
            print(f"TN callback error: {d}")
            return RedirectResponse(url="/?error=tn_token_failed")
    except Exception as e:
        print(f"TN callback exception: {e}")
        return RedirectResponse(url="/?error=tn_token_failed")

@app.post("/api/tn/connect")
async def connect_tn(req: Request, _=Depends(auth)):
    b = await req.json()
    ST["tn"] = {"store_id": b.get("store_id",""), "token": b.get("token","")}
    save_state()
    return {"ok": True}

SYNC_RUNNING = {}

def redis_products_key(uid): return f"mltn:products:{uid}"
def redis_status_key(uid): return f"mltn:sync_status:{uid}"

def get_cached_products(uid):
    r = get_redis()
    if r:
        try:
            raw = r.get(redis_products_key(uid))
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict) and data.get("chunked"):
                    products = []
                    for i in range(data.get("chunks", 2)):
                        chunk_raw = r.get(redis_products_key(uid) + f":{i}")
                        if chunk_raw:
                            products.extend(json.loads(chunk_raw))
                    return products if products else []
                if isinstance(data, list):
                    return data
                return []
        except Exception as e:
            print(f"Cache read error: {e}")
    return []

def slim_product(p):
    """Cache guarda estructura completa incluyendo precio y stock real"""
    slim = {
        "id": p.get("id",""),
        "title": p.get("title",""),
        "price": p.get("price", 0),
        "available_quantity": p.get("available_quantity", 0),
        "status": p.get("status",""),
        "thumbnail": p.get("thumbnail",""),
        "category_id": p.get("category_id",""),
        "permalink": p.get("permalink",""),
        "currency_id": p.get("currency_id","ARS"),
        "family_name": p.get("family_name",""),
        "user_product_id": p.get("user_product_id",""),
        "accepts_mercadopago": p.get("accepts_mercadopago", True),
        "date_created": p.get("date_created",""),
        "_has_variations": p.get("_has_variations",False),
        "_variation_count": p.get("_variation_count",0),
    }
    # Guardar URLs de fotos para publicar en TN sin llamar a ML
    pics = p.get("pictures", [])
    if pics:
        slim["pictures"] = [{"url": pic.get("url","").replace("http://","https://")} for pic in pics[:12] if pic.get("url")]
    elif p.get("thumbnail"):
        # Convertir thumbnail a imagen de mayor resolucion
        slim["pictures"] = [{"url": p["thumbnail"].replace("-I.jpg","-O.jpg").replace("http://","https://")}]
    if p.get("variations"):
        slim["variations"] = [{
            "id": v.get("id",""),
            "available_quantity": v.get("available_quantity", 0),
            "price": v.get("price", p.get("price", 0)),
            "_attrs": v.get("_attrs",{}),
            "attribute_combinations": v.get("attribute_combinations",[]),
        } for v in p["variations"][:50]]
    return slim

def set_cached_products(uid, products):
    r = get_redis()
    if r:
        try:
            slimmed = [slim_product(p) for p in products]
            data = json.dumps(slimmed)
            # Si supera 4MB partir en chunks
            if len(data) > 4_000_000:
                chunk_size = len(slimmed) // 2
                r.set(redis_products_key(uid) + ":0", json.dumps(slimmed[:chunk_size]))
                r.set(redis_products_key(uid) + ":1", json.dumps(slimmed[chunk_size:]))
                r.set(redis_products_key(uid), json.dumps({"chunked": True, "chunks": 2, "total": len(slimmed)}))
            else:
                r.delete(redis_products_key(uid) + ":0")
                r.delete(redis_products_key(uid) + ":1")
                r.set(redis_products_key(uid), data)
            ts = int(time.time())
            r.set(redis_status_key(uid), json.dumps({"status":"done","total":len(products),"ts":ts}))
            r.set(f"mltn:cache_ts:{uid}", str(ts))  # timestamp separado para chequeo rapido
            print(f"Cache saved: {len(products)} products, {len(data)//1024}KB")
        except Exception as e:
            print(f"Cache save error: {e}")

def set_sync_status(uid, status, total=0, fetched=0):
    r = get_redis()
    if r:
        try:
            r.set(redis_status_key(uid), json.dumps({"status":status,"total":total,"fetched":fetched,"ts":int(time.time())}))
        except: pass

def get_sync_status(uid):
    r = get_redis()
    if r:
        try:
            raw = r.get(redis_status_key(uid))
            if raw: return json.loads(raw)
        except: pass
    return None

async def do_sync_products(i: int, uid: str, token: str):
    """Sync rapido y completo: trae activos + pausados + cerrados en paralelo"""
    set_sync_status(uid, "fetching_ids", total=0, fetched=0)
    try:
        token = await fresh_token_stock(i)
        hdrs = {"Authorization": f"Bearer {token}"}

        # PASO 1: Obtener IDs de TODOS los estados en paralelo
        # ML devuelve solo activos por default — hay que pedir cada estado por separado
        all_ids = []
        
        _sem_ids = asyncio.Semaphore(1)

        async def fetch_all_ids_for_status(status_filter):
            """
            Activos: scan via app de stock (rapido, sin 429)
            Pausados/Cerrados: paginacion normal via app principal (rate limit separado)
            """
            ids = []
            try:
                if status_filter == "active":
                    # SCAN via app de stock
                    url = f"{ML_API}/users/{uid}/items/search?search_type=scan&limit=100&status=active"
                    async with _sem_ids:
                        r = await ml_call(seller_uid=uid, method="GET", url=url, headers=hdrs, priority=PRIORITY_LOW, timeout=25)
                    if r.status_code != 200:
                        print(f"Sync {uid} active scan error {r.status_code}")
                        return ids
                    d = r.json()
                    total = d.get("paging", {}).get("total", 0)
                    ids.extend(d.get("results", []))
                    scroll_id = d.get("scroll_id")
                    print(f"Sync {uid} status=active: total={total} scroll={'OK' if scroll_id else 'NO'}")
                    while scroll_id and len(ids) < total:
                        await asyncio.sleep(0.5)
                        scroll_url = f"{ML_API}/users/{uid}/items/search?search_type=scan&scroll_id={scroll_id}&limit=100&status=active"
                        async with _sem_ids:
                            r2 = await ml_call(seller_uid=uid, method="GET", url=scroll_url, headers=hdrs, priority=PRIORITY_LOW, timeout=25)
                        if r2.status_code != 200:
                            print(f"Sync {uid} active scroll error {r2.status_code}")
                            break
                        d2 = r2.json()
                        new_ids = d2.get("results", [])
                        if not new_ids:
                            break
                        ids.extend(new_ids)
                        scroll_id = d2.get("scroll_id")
                        print(f"Sync {uid} status=active: {len(ids)}/{total}")
                else:
                    # PAGINACION NORMAL via app principal — para pausados y cerrados
                    main_token = await fresh_token(i)
                    main_hdrs = {"Authorization": f"Bearer {main_token}"}
                    offset = 0
                    while True:
                        url = f"{ML_API}/users/{uid}/items/search?limit=100&offset={offset}&status={status_filter}"
                        r = await ml_call(seller_uid=uid, method="GET", url=url, headers=main_hdrs, priority=PRIORITY_LOW, timeout=20)
                        if r.status_code != 200:
                            print(f"Sync {uid} {status_filter} pag error {r.status_code}")
                            break
                        d = r.json()
                        total = d.get("paging", {}).get("total", 0)
                        page_ids = d.get("results", [])
                        if not page_ids:
                            break
                        ids.extend(page_ids)
                        if offset == 0:
                            print(f"Sync {uid} status={status_filter}: total={total}")
                        offset += 100
                        if offset >= total:
                            break
                        await asyncio.sleep(0.5)
                return ids
            except Exception as e:
                print(f"fetch_all_ids_for_status error status={status_filter}: {e}")
                return ids

        # Traer estados secuencialmente para no multiplicar presion x3
        for status_f in ("active", "paused", "closed"):
            try:
                r_ids = await fetch_all_ids_for_status(status_f)
                if isinstance(r_ids, list):
                    all_ids.extend(r_ids)
            except Exception as e:
                print(f"Sync {uid}: error trayendo {status_f}: {e}")
        all_ids = list(dict.fromkeys(all_ids))  # dedup
        total_ml = len(all_ids)
        
        print(f"Sync {uid}: {total_ml} IDs totales (activos+pausados+cerrados)")
        set_sync_status(uid, "fetching_ids", total=total_ml, fetched=total_ml)

        if not all_ids:
            # Reintentar hasta 3 veces — puede ser 429 momentaneo
            for retry in range(3):
                wait = 30 * (retry + 1)
                print(f"Sync {uid}: 0 productos, esperando {wait}s y reintentando...")
                await asyncio.sleep(wait)
                token = await fresh_token_stock(i)
                hdrs = {"Authorization": f"Bearer {token}"}
                r2_active = await ml_call(seller_uid=uid, method="GET", url=f"{ML_API}/users/{uid}/items/search?limit=100&offset=0", headers=hdrs, priority=PRIORITY_LOW, timeout=25)
                if r2_active.status_code == 200:
                    ids2 = r2_active.json().get("results", [])
                    total2 = r2_active.json().get("paging", {}).get("total", 0)
                    if ids2:
                        all_ids = ids2
                        total_ml = total2
                        set_sync_status(uid, "fetching_ids", total=total_ml, fetched=len(all_ids))
                        break
            if not all_ids:
                set_sync_status(uid, "error: no se encontraron productos")
                SYNC_RUNNING.pop(uid, None)
                return

        # PASO 2: Separar nuevos de existentes (delta sync)
        existing = get_cached_products(uid) or []
        existing_map = {p["id"]: p for p in existing}
        pending_ids = [iid for iid in all_ids if iid not in existing_map]
        refresh_ids = [iid for iid in all_ids if iid in existing_map]
        print(f"Sync {uid}: {len(pending_ids)} nuevos, {len(refresh_ids)} a refrescar stock")
        set_sync_status(uid, "fetching_details", total=total_ml, fetched=len(refresh_ids))

        # PASO 3: Detalles de nuevos en paralelo (5 batches x 20 = 100 productos a la vez)
        sem_det = asyncio.Semaphore(3)  # bajado de 5 — menos presion en ML
        fetched_count = len(existing_map)
        ATTRS = "id,title,price,available_quantity,status,thumbnail,currency_id,family_name,user_product_id"  # liviano — sin variaciones ni atributos

        # Token de la app principal para multiget de detalles — rate limit separado del scan
        main_token = await fresh_token(i)
        main_hdrs = {"Authorization": f"Bearer {main_token}"}

        async def fetch_detail_batch(batch):
            nonlocal fetched_count
            async with sem_det:
                try:
                    r = await ml_call(seller_uid=uid, method="GET",
                        url=f"{ML_API}/items?ids={','.join(batch)}&attributes={ATTRS}",
                        headers=main_hdrs, priority=PRIORITY_LOW, timeout=25)
                    if r.status_code == 200:
                        items = []
                        for item in r.json():
                            if item.get("code") == 200:
                                b = item["body"]
                                for v in b.get("variations", []):
                                    v["_attrs"] = {a["name"]: a.get("value_name","") for a in v.get("attribute_combinations", [])}
                                b["_has_variations"] = len(b.get("variations", [])) > 0
                                b["_variation_count"] = len(b.get("variations", []))
                                # NO sobreescribir family_name con title — dejar vacio si ML no la manda
                                # El frontend agrupa por user_product_id primero
                                items.append(b)
                        fetched_count += len(items)
                        set_sync_status(uid, "fetching_details", total=total_ml, fetched=fetched_count)
                        return items
                except Exception as e:
                    print(f"Detail batch error: {e}")
                return []

        pending_batches = [pending_ids[x:x+20] for x in range(0, len(pending_ids), 20)]
        # Completamente secuencial — un batch a la vez, sin gather
        for batch_idx, batch in enumerate(pending_batches):
            items = await fetch_detail_batch(batch)
            for b in items:
                existing_map[b["id"]] = b
            if batch_idx % 20 == 0 and batch_idx > 0:
                set_cached_products(uid, list(existing_map.values()))
                print(f"Sync {uid}: guardado parcial {len(existing_map)} productos")
            await asyncio.sleep(0.5)
        async def refresh_stock_batch(batch):
            nonlocal fetched_count
            async with sem_det:
                try:
                    r = await ml_call(seller_uid=uid, method="GET",
                        url=f"{ML_API}/items?ids={','.join(batch)}&attributes=id,price,available_quantity,status",
                        headers=hdrs, priority=PRIORITY_LOW, timeout=20)
                    if r.status_code == 200:
                        for item in r.json():
                            if item.get("code") == 200:
                                b = item["body"]
                                iid = b.get("id")
                                if iid in existing_map:
                                    existing_map[iid]["price"] = b.get("price", existing_map[iid].get("price", 0))
                                    existing_map[iid]["available_quantity"] = b.get("available_quantity", existing_map[iid].get("available_quantity", 0))
                                    existing_map[iid]["status"] = b.get("status", existing_map[iid].get("status", ""))
                                    if b.get("variations"):
                                        var_map = {str(v["id"]): v for v in b["variations"]}
                                        for v in existing_map[iid].get("variations", []):
                                            vid = str(v.get("id"))
                                            if vid in var_map:
                                                v["available_quantity"] = var_map[vid].get("available_quantity", v.get("available_quantity", 0))
                                                v["price"] = var_map[vid].get("price", v.get("price", 0))
                        fetched_count += len(batch)
                        set_sync_status(uid, "fetching_details", total=total_ml, fetched=fetched_count)
                except Exception as e:
                    print(f"Refresh batch error: {e}")

        refresh_batches = [refresh_ids[x:x+20] for x in range(0, len(refresh_ids), 20)]
        # Completamente secuencial — un batch a la vez
        for batch_idx, batch in enumerate(refresh_batches):
            await refresh_stock_batch(batch)
            await asyncio.sleep(0.5)
        final_products = list(existing_map.values())
        set_cached_products(uid, final_products)
        set_sync_status(uid, "done", total=len(final_products), fetched=len(final_products))
        print(f"Sync completo {uid}: {len(final_products)} productos")

    except Exception as e:
        import traceback
        set_sync_status(uid, f"error: {str(e)}")
        print(f"Sync error {uid}: {traceback.format_exc()[:300]}")
    finally:
        SYNC_RUNNING.pop(uid, None)

@app.post("/api/sync/all")
async def sync_all_accounts(background_tasks: BackgroundTasks, _=Depends(auth)):
    """Sincronizar todas las cuentas en secuencia — una por una"""
    async def run_all():
        for i, acc in enumerate(ST.get("accounts", [])):
            uid = acc.get("uid", "")
            if uid in SYNC_RUNNING:
                SYNC_RUNNING.pop(uid, None)
            token = await fresh_token_stock(i)
            SYNC_RUNNING[uid] = True
            print(f"Sync secuencial: iniciando cuenta {i} ({acc.get('name','')})")
            await do_sync_products(i, uid, token)
            print(f"Sync secuencial: cuenta {i} terminada, esperando 10s...")
            await asyncio.sleep(10)  # pausa entre cuentas
    background_tasks.add_task(run_all)
    return {"ok": True, "msg": "Sync secuencial iniciado"}

@app.post("/api/ml/{i}/sync")
async def start_sync(i: int, background_tasks: BackgroundTasks, request: Request, _=Depends(auth)):
    if i < 0 or i >= len(ST["accounts"]):
        raise HTTPException(404)
    acc = ST["accounts"][i]
    uid = acc["uid"]
    # Si viene clear=1, borrar cache antes de sincronizar
    clear = request.query_params.get("clear", "0")
    if clear == "1":
        r = get_redis()
        if r:
            keys = list(r.scan_iter(match=f"mltn:products:{uid}*", count=300))
            if keys:
                for _i in range(0, len(keys), 200):
                    r.delete(*keys[_i:_i+200])
            r.delete(f"mltn:sync_status:{uid}")
            r.delete(f"mltn:cache_ts:{uid}")
        print(f"Cache borrado para {uid} antes de sync completo")
    if uid in SYNC_RUNNING:
        SYNC_RUNNING.pop(uid, None)
    token = await fresh_token_stock(i)
    SYNC_RUNNING[uid] = True
    background_tasks.add_task(do_sync_products, i, uid, token)
    return {"ok": True, "msg": "Sincronizacion iniciada"}

_dup_jobs = {}

@app.get("/api/duplicate/status/{job_id}")
async def dup_status(job_id: str, _=Depends(auth)):
    return _dup_jobs.get(job_id, {"status": "not_found"})

@app.post("/api/ml/{i}/refresh_stock")
async def refresh_stock(i: int, background_tasks: BackgroundTasks, _=Depends(auth)):
    """Actualizar price y stock de productos en cache.
    Filtra solo productos activos con stock > 0 para reducir carga sobre ML."""
    if i < 0 or i >= len(ST["accounts"]):
        raise HTTPException(404)
    uid = ST["accounts"][i]["uid"]

    # Lock para evitar refresh dobles
    if uid in _refresh_running:
        return {"ok": False, "msg": "Ya hay un refresh corriendo para esta cuenta"}
    _refresh_running.add(uid)

    async def do_refresh():
        try:
            token = await fresh_token_stock(i)
            hdrs = {"Authorization": f"Bearer {token}"}
            products = get_cached_products(uid) or []
            if not products:
                return

            # ─── FILTRAR: activos y pausados (incluyendo stock 0) ───
            # IMPORTANTE: incluimos items con stock 0 porque son los que el
            # usuario repone. Si los excluyeramos, reponer stock nunca se
            # reflejaria. Solo omitimos los "closed" (cerrados) que no cambian.
            def needs_refresh(p):
                st = p.get("status", "")
                # Refrescar activos y pausados. Omitir solo cerrados.
                return st in ("active", "paused")

            filtered = [p for p in products if needs_refresh(p)]
            events.info(f"Refresh stock {ST['accounts'][i].get('name','?')}: {len(filtered)}/{len(products)} items (activos+pausados, incluye stock 0)",
                        source="refresh", seller=uid)

            all_ids = [p["id"] for p in filtered]
            prod_map = {p["id"]: p for p in products}  # mapa completo para escribir
            refreshed = 0
            batch_size = 20
            total_batches = (len(all_ids) + batch_size - 1) // batch_size
            consecutive_429 = 0

            for x in range(0, len(all_ids), batch_size):
                batch = all_ids[x:x+batch_size]
                batch_num = (x // batch_size) + 1
                try:
                    r = await ml_call(seller_uid=uid, method="GET",
                        url=f"{ML_API}/items?ids={','.join(batch)}&attributes=id,price,available_quantity,status,variations",
                        headers=hdrs, priority=PRIORITY_LOW, timeout=30)
                    if r.status_code == 429:
                        consecutive_429 += 1
                        events.warn(f"Refresh stock {batch_num}/{total_batches}: 429 ({consecutive_429} consecutivos)",
                                    source="refresh", seller=uid)
                        # Si 3 consecutivos, abortar — algo está mal
                        if consecutive_429 >= 3:
                            events.err(f"Refresh stock abortado tras 3 x 429 consecutivos. Reanudará automáticamente.",
                                       source="refresh", seller=uid)
                            break
                        # Esperar más de lo normal
                        await asyncio.sleep(30)
                        continue
                    consecutive_429 = 0
                    if r.status_code == 200:
                        for item in r.json():
                            if item.get("code") == 200:
                                b = item["body"]
                                iid = b.get("id")
                                if iid in prod_map:
                                    prod_map[iid]["price"] = b.get("price", prod_map[iid].get("price"))
                                    prod_map[iid]["available_quantity"] = b.get("available_quantity", prod_map[iid].get("available_quantity"))
                                    prod_map[iid]["status"] = b.get("status", prod_map[iid].get("status"))
                                    if b.get("variations"):
                                        var_map = {str(v["id"]): v for v in b["variations"]}
                                        for v in prod_map[iid].get("variations", []):
                                            if str(v.get("id")) in var_map:
                                                v["available_quantity"] = var_map[str(v["id"])].get("available_quantity", v.get("available_quantity"))
                                                v["price"] = var_map[str(v["id"])].get("price", v.get("price"))
                                    refreshed += 1
                except Exception as e:
                    events.warn(f"Refresh stock batch {batch_num} error: {str(e)[:100]}",
                                source="refresh", seller=uid)
                # Pacing mas conservador entre batches
                await asyncio.sleep(1.5)
            set_cached_products(uid, list(prod_map.values()))
            events.ok(f"Refresh stock terminado: {refreshed}/{len(filtered)} actualizados",
                      source="refresh", seller=uid)
            print(f"Refresh stock OK {uid}: {refreshed} productos actualizados")
        except Exception as e:
            print(f"Refresh stock error: {e}")
            events.err(f"Refresh stock error: {str(e)[:150]}", source="refresh", seller=uid)
        finally:
            _refresh_running.discard(uid)

    background_tasks.add_task(do_refresh)
    return {"ok": True, "msg": "Actualizando stock en background (solo items con stock > 0)..."}

@app.post("/api/ml/{i}/item/{item_id}/refresh")
async def refresh_single_item(i: int, item_id: str, _=Depends(auth)):
    """Refrescar un item y todos los items de la misma familia desde ML"""
    if i < 0 or i >= len(ST["accounts"]):
        raise HTTPException(404)
    acc = ST["accounts"][i]
    uid = acc["uid"]
    try:
        token = await fresh_token_stock(i)
        hdrs = {"Authorization": f"Bearer {token}"}
        attrs = "id,title,price,available_quantity,status,thumbnail,category_id,variations,permalink,date_created,currency_id,accepts_mercadopago,user_product_id,family_name"
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"{ML_API}/items/{item_id}?attributes={attrs}", headers=hdrs)
        print(f"Refresh {item_id}: HTTP {r.status_code}")
        if r.status_code != 200:
            print(f"Refresh {item_id} error body: {r.text[:200]}")
            return {"ok": False, "msg": f"ML error {r.status_code}: {r.text[:100]}"}
        # Log user_product_id and variations count to understand structure
        _tmp = r.json()
        print(f"Refresh {item_id}: variations={len(_tmp.get('variations',[]))}, user_product_id={_tmp.get('user_product_id','N/A')}, family_name={_tmp.get('family_name','N/A')}, keys={list(_tmp.keys())[:15]}")
        item = r.json()
        # Si el item no tiene variantes en el response, buscarlas separado
        if not item.get("variations"):
            try:
                async with httpx.AsyncClient(timeout=15) as c2:
                    rv = await c2.get(f"{ML_API}/items/{item_id}/variations", headers=hdrs)
                if rv.status_code == 200:
                    variations = rv.json()
                    if isinstance(variations, list):
                        item["variations"] = variations
                    elif isinstance(variations, dict):
                        item["variations"] = variations.get("variations", [])
                print(f"Refresh {item_id}: fetched {len(item.get('variations',[]))} variations separately")
            except Exception as ve:
                print(f"Refresh {item_id}: variations fetch error: {ve}")
        for v in item.get("variations", []):
            v["_attrs"] = {a["name"]: a.get("value_name","") for a in v.get("attribute_combinations", [])}
        item["_has_variations"] = len(item.get("variations", [])) > 0
        item["_variation_count"] = len(item.get("variations", []))
        if not item.get("family_name"):
            item["family_name"] = item.get("title", "")
        # Buscar TODOS los items de la misma familia (refresca todas las variantes)
        family_name = item.get("family_name", "")
        refreshed_siblings = 0
        if family_name and family_name != item.get("title", ""):
            try:
                async with httpx.AsyncClient(timeout=20) as c_fam:
                    # Buscar items de esta familia via search — limit generoso por si hay muchas variantes
                    sr = await c_fam.get(
                        f"{ML_API}/users/{uid}/items/search?q={family_name[:50]}&limit=50",
                        headers=hdrs
                    )
                    if sr.status_code == 200:
                        family_ids = [iid for iid in sr.json().get("results", []) if iid != item_id]
                        # Traer detalles en batches de 20
                        for bstart in range(0, len(family_ids), 20):
                            batch = family_ids[bstart:bstart+20]
                            rb = await c_fam.get(
                                f"{ML_API}/items?ids={','.join(batch)}&attributes={attrs}",
                                headers=hdrs
                            )
                            if rb.status_code == 200:
                                for wrap in rb.json():
                                    sib = wrap.get("body", {}) if isinstance(wrap, dict) else {}
                                    sib_fn = sib.get("family_name", "")
                                    # Solo aceptar hermanos reales de la familia
                                    if sib.get("id") and sib_fn == family_name:
                                        for v in sib.get("variations", []):
                                            v["_attrs"] = {a["name"]: a.get("value_name","") for a in v.get("attribute_combinations", [])}
                                        sib["_has_variations"] = len(sib.get("variations", [])) > 0
                                        sib["_variation_count"] = len(sib.get("variations", []))
                                        refreshed_siblings += 1
                                        # Agregar al prod_map
                                        items_to_save = locals().get("_family_items", [])
                                        items_to_save.append(sib)
                                        locals()["_family_items"] = items_to_save
            except Exception as fe:
                print(f"Refresh family error {item_id}: {fe}")

        # Update in cache
        products = get_cached_products(uid) or []
        prod_map = {p["id"]: p for p in products}
        prod_map[item_id] = item
        # Agregar hermanos de la familia si los trajimos
        if family_name and refreshed_siblings > 0:
            # Re-fetch del scope local no funciona bien — hacer el save directo
            async with httpx.AsyncClient(timeout=20) as c_fam2:
                sr2 = await c_fam2.get(
                    f"{ML_API}/users/{uid}/items/search?q={family_name[:50]}&limit=50",
                    headers=hdrs
                )
                if sr2.status_code == 200:
                    family_ids2 = [iid for iid in sr2.json().get("results", []) if iid != item_id]
                    for bstart in range(0, len(family_ids2), 20):
                        batch = family_ids2[bstart:bstart+20]
                        rb2 = await c_fam2.get(
                            f"{ML_API}/items?ids={','.join(batch)}&attributes={attrs}",
                            headers=hdrs
                        )
                        if rb2.status_code == 200:
                            for wrap in rb2.json():
                                sib = wrap.get("body", {}) if isinstance(wrap, dict) else {}
                                if sib.get("id") and sib.get("family_name") == family_name:
                                    for v in sib.get("variations", []):
                                        v["_attrs"] = {a["name"]: a.get("value_name","") for a in v.get("attribute_combinations", [])}
                                    sib["_has_variations"] = len(sib.get("variations", [])) > 0
                                    sib["_variation_count"] = len(sib.get("variations", []))
                                    prod_map[sib["id"]] = sib
        print(f"Refresh {item_id}: actualizado + {refreshed_siblings} hermanos de familia")
        set_cached_products(uid, list(prod_map.values()))
        return {"ok": True, "item": item}
    except Exception as e:
        return {"ok": False, "msg": str(e)}

@app.delete("/api/ml/{i}/cache")
async def clear_cache(i: int, _=Depends(auth)):
    if i < 0 or i >= len(ST["accounts"]):
        raise HTTPException(404)
    uid = ST["accounts"][i]["uid"]
    r = get_redis()
    if r:
        keys = list(r.scan_iter(match=f"mltn:products:{uid}*", count=300))
        if keys:
            for _i in range(0, len(keys), 200):
                r.delete(*keys[_i:_i+200])
        r.delete(f"mltn:sync_status:{uid}")
    return {"ok": True, "uid": uid}

@app.get("/api/ml/{i}/sync/status")
def sync_status(i: int, _=Depends(auth)):
    if i < 0 or i >= len(ST["accounts"]):
        raise HTTPException(404)
    uid = ST["accounts"][i]["uid"]
    status = get_sync_status(uid)
    running = uid in SYNC_RUNNING
    return {"running": running, "status": status}

async def do_refresh_stock_bg(i: int, uid: str, hdrs_stock=None):
    """Refresh liviano de stock en background"""
    try:
        token = await fresh_token_stock(i)
        hdrs = {"Authorization": f"Bearer {token}"}
        products = get_cached_products(uid) or []
        if not products:
            return
        all_ids = [p["id"] for p in products]
        batches = [all_ids[x:x+20] for x in range(0, len(all_ids), 20)]
        existing_map = {p["id"]: p for p in products}
        for batch in batches[:10]:  # max 10 batches = 200 items
            try:
                r = await ml_call(seller_uid=uid, method="GET",
                    url=f"{ML_API}/items?ids={','.join(batch)}&attributes=id,price,available_quantity,status",
                    headers=hdrs, priority=PRIORITY_LOW, timeout=20)
                if r.status_code == 200:
                    for item in r.json():
                        if item.get("code") == 200:
                            b = item["body"]
                            iid = b.get("id")
                            if iid in existing_map:
                                existing_map[iid]["available_quantity"] = b.get("available_quantity", 0)
                                existing_map[iid]["price"] = b.get("price", existing_map[iid].get("price", 0))
                await asyncio.sleep(1)
            except:
                break
        set_cached_products(uid, list(existing_map.values()))
    except Exception as e:
        print(f"do_refresh_stock_bg error: {e}")

_refresh_running = set()  # uid -> bool para evitar refresh dobles

async def _auto_refresh_stock(i: int, uid: str):
    """Auto-refresh de stock en background — liviano, sin bloquear la respuesta"""
    try:
        token = await fresh_token_stock(i)
        hdrs = {"Authorization": f"Bearer {token}"}
        products = get_cached_products(uid) or []
        if not products:
            return
        # Solo refrescar activos primero (los mas importantes)
        active = [p["id"] for p in products if p.get("status") == "active"]
        all_ids = active + [p["id"] for p in products if p.get("status") != "active"]
        batches = [all_ids[x:x+20] for x in range(0, len(all_ids), 20)]
        existing_map = {p["id"]: p for p in products}
        refreshed = 0
        for batch in batches[:15]:  # max 300 items
            if is_ml_cooling(i):
                break
            try:
                r = await ml_call(seller_uid=uid, method="GET",
                    url=f"{ML_API}/items?ids={','.join(batch)}&attributes=id,price,available_quantity,status",
                    headers=hdrs, priority=PRIORITY_LOW, timeout=20)
                if r.status_code == 200:
                    for item in r.json():
                        if item.get("code") == 200:
                            b = item["body"]
                            iid = b.get("id")
                            if iid in existing_map:
                                existing_map[iid]["available_quantity"] = b.get("available_quantity", 0)
                                existing_map[iid]["price"] = b.get("price", existing_map[iid].get("price", 0))
                                existing_map[iid]["status"] = b.get("status", existing_map[iid].get("status", ""))
                                if b.get("variations"):
                                    var_map = {str(v["id"]): v for v in b["variations"]}
                                    for v in existing_map[iid].get("variations", []):
                                        vid = str(v.get("id"))
                                        if vid in var_map:
                                            v["available_quantity"] = var_map[vid].get("available_quantity", 0)
                                            v["price"] = var_map[vid].get("price", v.get("price", 0))
                                refreshed += 1
                await asyncio.sleep(1)
            except:
                break
        set_cached_products(uid, list(existing_map.values()))
        print(f"Auto-refresh OK {uid}: {refreshed} items")
    except Exception as e:
        print(f"Auto-refresh error {uid}: {e}")
    finally:
        _refresh_running.discard(uid)

@app.get("/api/ml/{i}/products")
async def get_products(i: int, page: int = 1, limit: int = 50,
                       status: str = "all", search: str = "", refresh: str = "0", _=Depends(auth)):
    if i < 0 or i >= len(ST["accounts"]):
        raise HTTPException(404)
    uid = ST["accounts"][i]["uid"]
    products = get_cached_products(uid) or []

    if not products:
        return {"products": [], "total": 0, "synced": False,
                "msg": "Productos no sincronizados. Presiona Sincronizar."}

    # Auto-refresh deshabilitado temporalmente
    # if uid not in _refresh_running and not is_ml_cooling(i): ...

    all_products = products if isinstance(products, list) else []
    if status != "all":
        all_products = [p for p in all_products if p.get("status","") == status]
    if search:
        s = search.lower()
        all_products = [p for p in all_products if s in p.get("title","").lower()]
    total = len(all_products)
    if limit >= 9999:
        return {"products": all_products, "items": all_products, "total": total, "synced": True, "page": 1, "limit": total}
    start = (page-1)*limit
    page_products = all_products[start:start+limit]
    return {"products": page_products, "items": page_products, "total": total, "synced": True, "page": page, "limit": limit}

@app.post("/api/ml/{i}/fetch_new")
async def fetch_new_items(i: int, _=Depends(auth)):
    """Trae solo los items nuevos que no estan en cache — sin borrar lo existente"""
    if i < 0 or i >= len(ST["accounts"]):
        raise HTTPException(404)
    uid = ST["accounts"][i]["uid"]
    try:
        # App de stock para listar IDs, app principal para detalles — no competir entre si
        stock_token = await fresh_token_stock(i)
        stock_hdrs = {"Authorization": f"Bearer {stock_token}"}
        main_token = await fresh_token(i)
        main_hdrs = {"Authorization": f"Bearer {main_token}"}
        products = get_cached_products(uid) or []
        existing_ids = {p["id"] for p in products}
        new_items = []
        async with httpx.AsyncClient(timeout=30) as c:
            # Ordenar por start_time_desc — los mas recientes primero
            for offset in range(0, 200, 50):
                r = await c.get(
                    f"{ML_API}/users/{uid}/items/search?status=active&limit=50&offset={offset}&sort=start_time_desc",
                    headers=stock_hdrs)
                if r.status_code != 200:
                    break
                ids = r.json().get("results", [])
                if not ids:
                    break
                missing = [iid for iid in ids if iid not in existing_ids]
                if not missing:
                    break  # ya no hay nuevos, parar
                for batch_start in range(0, len(missing), 20):
                    batch = missing[batch_start:batch_start+20]
                    r2 = await c.get(
                        f"{ML_API}/items?ids={','.join(batch)}&attributes=id,title,price,available_quantity,status,thumbnail,currency_id,family_name,user_product_id",
                        headers=main_hdrs)
                    if r2.status_code == 200:
                        for wrap in r2.json():
                            item = wrap.get("body", wrap) if isinstance(wrap, dict) else {}
                            if item.get("id") and item["id"] not in existing_ids:
                                new_items.append(item)
                                existing_ids.add(item["id"])
        if new_items:
            products = new_items + products  # nuevos primero
            set_cached_products(uid, products)
        return {"ok": True, "nuevos": len(new_items), "total": len(products)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/api/ml/{i}/health")
async def get_items_health(i: int, ids: str = "", _=Depends(auth)):
    """Obtener health/calidad de un batch de items"""
    if i < 0 or i >= len(ST["accounts"]):
        raise HTTPException(404)
    if not ids:
        return {"health": {}}
    # Usamos STOCK (app por cuenta). Es lectura de items.
    token = await fresh_token_stock(i)
    id_list = ids.split(",")[:20]
    health_map = {}
    async with httpx.AsyncClient(timeout=20) as c:
        for item_id in id_list:
            try:
                r = await c.get(f"{ML_API}/items/{item_id}/health",
                               headers={"Authorization": f"Bearer {token}"})
                if r.status_code == 200:
                    health_map[item_id] = r.json().get("health", None)
            except Exception:
                pass
            await asyncio.sleep(0.2)
    return {"health": health_map}

@app.get("/api/tn/products")
async def get_tn_products(_=Depends(auth)):
    if not ST["tn"].get("store_id"):
        raise HTTPException(400, "TN no conectada.")
    tn = ST["tn"]
    hdrs = {"Authentication":f"bearer {tn['token']}","Content-Type":"application/json"}
    all_p = []
    async with httpx.AsyncClient(timeout=30) as c:
        for pg in range(1, 50):
            r = await c.get(f"https://api.tiendanube.com/v1/{tn['store_id']}/products?page={pg}&per_page=50", headers=hdrs)
            d = r.json()
            if not isinstance(d, list) or not d:
                break
            all_p.extend(d)
            if len(d) < 50:
                break
            await asyncio.sleep(0.1)
    return {"products": all_p, "total": len(all_p)}

@app.post("/api/links/add")
async def add_link(req: Request, _=Depends(auth)):
    b = await req.json()
    key = b["ml_item_id"] + ("_"+b["ml_variation_id"] if b.get("ml_variation_id") else "")
    # Reusar SKU interno si ya existe un link para este item
    existing = next((l for l in ST["links"]
                     if (l["ml_item_id"]+("_"+l.get("ml_variation_id","") if l.get("ml_variation_id") else "")) == key), None)
    if existing and existing.get("sku_interno"):
        b["sku_interno"] = existing["sku_interno"]
    elif not b.get("sku_interno"):
        # Generar SKU interno unico: MLTN- + timestamp + random
        b["sku_interno"] = f"MLTN-{int(time.time())}-{secrets.token_hex(4).upper()}"
    ST["links"] = [l for l in ST["links"]
                   if (l["ml_item_id"]+("_"+l.get("ml_variation_id","") if l.get("ml_variation_id") else "")) != key]
    b["created_at"] = int(time.time())
    ST["links"].append(b)
    save_state()
    print(f"Link creado: {b['ml_item_id']} SKU={b['sku_interno']}")
    return {"ok": True, "sku_interno": b["sku_interno"]}

@app.post("/api/links/enrich")
async def enrich_links(_=Depends(auth)):
    """Enriquecer links viejos con títulos y nombres de cuenta"""
    enriched = 0
    for link in ST.get("links", []):
        if link.get("ml_title"):
            continue  # ya tiene título
        try:
            acc_idx = link.get("ml_account_index", 0)
            token = await fresh_token(acc_idx)
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"{ML_API}/items/{link['ml_item_id']}?attributes=title",
                    headers={"Authorization": f"Bearer {token}"})
                if r.status_code == 200:
                    link["ml_title"] = r.json().get("title", link["ml_item_id"])
                    link["ml_account_name"] = ST["accounts"][acc_idx].get("name","ML") if acc_idx < len(ST["accounts"]) else "ML"
                    enriched += 1
        except Exception:
            pass
    save_state()
    return {"enriched": enriched}

@app.post("/api/links/clear_all")
async def clear_all_links(_=Depends(auth)):
    """Borrar TODOS los enlaces existentes"""
    count = len(ST.get("links", []))
    ST["links"] = []
    save_state()
    print(f"Links borrados: {count}")
    return {"ok": True, "deleted": count}

@app.post("/api/links/remove")
async def remove_link(req: Request, _=Depends(auth)):
    b = await req.json()
    key = b["ml_item_id"] + ("_"+b.get("ml_variation_id","") if b.get("ml_variation_id") else "")
    ST["links"] = [l for l in ST["links"]
                   if (l["ml_item_id"]+("_"+l.get("ml_variation_id","") if l.get("ml_variation_id") else "")) != key]
    save_state()
    return {"ok": True}

@app.post("/api/sync/manual")
async def sync(_=Depends(auth)):
    if not ST["tn"].get("store_id"):
        raise HTTPException(400, "TN no conectada.")
    tn = ST["tn"]
    tn_hdrs = {"Authentication":f"bearer {tn['token']}","Content-Type":"application/json"}
    results = []
    for link in ST["links"]:
        idx = link.get("ml_account_index", 0)
        if idx >= len(ST["accounts"]):
            continue
        token = await fresh_token(idx)
        ml_hdrs = {"Authorization": f"Bearer {token}"}
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(f"{ML_API}/items/{link['ml_item_id']}", headers=ml_hdrs)
                item = r.json()
                var_id = link.get("ml_variation_id")
                if var_id:
                    v = next((x for x in item.get("variations",[]) if str(x["id"])==str(var_id)), None)
                    price = str(v.get("price", item.get("price",0))) if v else str(item.get("price",0))
                    stock = v.get("available_quantity",0) if v else item.get("available_quantity",0)
                else:
                    price = str(item.get("price",0))
                    stock = item.get("available_quantity",0)
                pid = link["tn_product_id"]
                vid = link.get("tn_variant_id")
                if not vid:
                    rp = await c.get(f"https://api.tiendanube.com/v1/{tn['store_id']}/products/{pid}", headers=tn_hdrs)
                    vid = (rp.json().get("variants") or [{}])[0].get("id")
                if vid:
                    r2 = await c.put(f"https://api.tiendanube.com/v1/{tn['store_id']}/products/{pid}/variants/{vid}",
                                     headers=tn_hdrs, json={"price":price,"stock":stock})
                    ok = r2.status_code in (200,201)
                else:
                    ok = False
                results.append({"title": item.get("title",""), "ok": ok})
                ST["log"].append({"ts":int(time.time()),"action":"sync","product":item.get("title",""),"status":"ok" if ok else "error"})
            await asyncio.sleep(0.2)
        except Exception as e:
            results.append({"title": link["ml_item_id"], "ok": False, "action": str(e)})
    save_state()
    return {"results": results}

@app.post("/api/publish")
async def publish(req: Request, _=Depends(auth)):
    b = await req.json()
    item_ids = b.get("item_ids", [])
    chart_override = b.get("chart_override", {})
    idx = b.get("ml_account_index", 0)
    target = b.get("target", "tn")  # "tn" o "ml"
    target_ml_idx = b.get("target_ml_index", 0)
    print(f"Publish: target={target} item_ids={item_ids} idx={idx} agrupar={b.get('agrupar')}")

    # Publicar en ML (duplicar a otra cuenta)
    if target == "ml":
        if target_ml_idx >= len(ST["accounts"]):
            raise HTTPException(400, "Cuenta ML destino no existe.")
        from_t = await fresh_token(idx)
        to_t = await fresh_token(target_ml_idx)
        results = []
        async with httpx.AsyncClient(timeout=30) as c:
            for iid in item_ids:
                try:
                    r = await c.get(f"{ML_API}/items/{iid}", headers={"Authorization":f"Bearer {from_t}"})
                    item = r.json()
                    payload = {"title":item["title"],"category_id":item.get("category_id",""),
                               "price":item.get("price",0),"currency_id":item.get("currency_id","ARS"),
                               "available_quantity":item.get("available_quantity",0),
                               "listing_type_id":item.get("listing_type_id","gold_special"),
                               "condition":item.get("condition","new"),
                               "pictures":[{"source":p["url"]} for p in (item.get("pictures") or [])[:12]],
                               "attributes":item.get("attributes",[])}
                    if item.get("variations"):
                        payload["variations"] = item["variations"]
                    r2 = await c.post(f"{ML_API}/items", headers={"Authorization":f"Bearer {to_t}"}, json=payload)
                    ok = r2.status_code in (200,201)
                    results.append({"id":iid,"title":item.get("title",""),"ok":ok,
                                    "msg":"Publicado en ML" if ok else r2.json().get("message","Error")})
                    ST["log"].append({"ts":int(time.time()),"action":"publish_ml","product":item.get("title",""),"status":"ok" if ok else "error"})
                    await asyncio.sleep(1)
                except Exception as e:
                    results.append({"id":iid,"ok":False,"msg":str(e)})
        save_state()
        return {"results": results}

    # Publicar en TiendaNube
    if not ST["tn"].get("store_id"):
        raise HTTPException(400, "TN no conectada.")
    token = await fresh_token(idx)
    ml_hdrs = {"Authorization": f"Bearer {token}"}
    tn = ST["tn"]
    tn_hdrs = {"Authentication":f"bearer {tn['token']}","Content-Type":"application/json"}
    agrupar = b.get("agrupar", False)
    results = []

    async with httpx.AsyncClient(timeout=30) as c:

        if agrupar:
            # Usar cache local en vez de llamar a ML — sin rate limit
            uid = ST["accounts"][idx]["uid"] if idx < len(ST["accounts"]) else ""
            cached = {p["id"]: p for p in (get_cached_products(uid) or [])}
            all_items = []
            print(f"Publish TN agrupar: {len(item_ids)} items (desde cache)")
            for iid in item_ids:
                if iid in cached:
                    item = dict(cached[iid])
                    item["_desc"] = item.get("title", "")
                    # Construir URL de foto desde thumbnail (sin request extra)
                    thumb = item.get("thumbnail", "")
                    if thumb:
                        # Convertir thumbnail a URL de imagen completa
                        pic_url = thumb.replace("-I.jpg", "-O.jpg").replace("http://", "https://")
                        item["pictures"] = [{"url": pic_url}]
                    all_items.append(item)
                    print(f"  Item {iid} desde cache: vars={len(item.get('variations',[]))} pics={len(item.get('pictures',[]))}")
                else:
                    # Fallback a ML si no está en cache
                    try:
                        stock_token = await fresh_token_stock(idx)
                        stock_hdrs = {"Authorization": f"Bearer {stock_token}"}
                        r = await c.get(f"{ML_API}/items/{iid}?attributes=id,title,price,available_quantity,variations,attributes,pictures,category_id,currency_id", headers=stock_hdrs)
                        if r.status_code == 200 and r.content:
                            item = r.json()
                            item["_desc"] = item.get("title","")
                            all_items.append(item)
                        else:
                            results.append({"id": iid, "title": iid, "ok": False, "msg": f"ML error {r.status_code} (no en cache)"})
                    except Exception as e:
                        results.append({"id": iid, "title": iid, "ok": False, "msg": str(e)})

            # Agrupar por MODEL o family_name (items UPtin)
            grupos = {}
            for item in all_items:
                model_attr = next((a for a in (item.get("attributes") or []) if a.get("id") == "MODEL"), None)
                if model_attr and model_attr.get("value_name"):
                    key = model_attr.get("value_name")
                elif item.get("family_name"):
                    key = item.get("family_name")
                elif item.get("user_product_id"):
                    key = item.get("user_product_id")
                else:
                    key = item["id"]
                if key not in grupos:
                    grupos[key] = []
                grupos[key].append(item)

            print(f"Publish TN grupos: {list(grupos.keys())}")
            for model_key, items_grupo in grupos.items():
                try:
                    base = items_grupo[0]
                    # Título base: cortar después del número de modelo
                    _t = base.get("title","")
                    _m = next((a.get("value_name","") for a in (base.get("attributes") or []) if a.get("id")=="MODEL"), "")
                    if _m and _m in _t:
                        title = _t[:_t.index(_m)+len(_m)].strip()
                    elif " - " in _t:
                        title = _t.rsplit(" - ", 1)[0].strip()
                    else:
                        title = _t
                    desc = base.get("_desc", title)

                    # Armar variantes TN
                    variants = []
                    for item in items_grupo:
                        item_variations = item.get("variations", [])
                        if item_variations:
                            # Item con variaciones internas (modelo viejo ML)
                            for v in item_variations:
                                combo = {a.get("id",""): a.get("value_name","") for a in v.get("attribute_combinations",[])}
                                color = combo.get("COLOR","")
                                size = combo.get("SIZE","") or combo.get("Talle","") or combo.get("Size","")
                                values = []
                                if color: values.append({"es": color})
                                if size: values.append({"es": size})
                                tv = {
                                    "price": str(v.get("price", item.get("price", base.get("price",0)))),
                                    "stock_management": True,
                                    "stock": v.get("available_quantity", 0),
                                }
                                if values: tv["values"] = values
                                variants.append(tv)
                        else:
                            # Item sin variaciones — UPtin: cada item ES una variante
                            attrs = {a["id"]: a for a in (item.get("attributes") or [])}
                            color = attrs.get("COLOR",{}).get("value_name","")
                            size = (attrs.get("SIZE",{}).get("value_name","") or
                                   attrs.get("SIZE",{}).get("value_id",""))
                            # Extraer del titulo si no hay atributos
                            if not color and not size:
                                import re as _re2
                                _title = item.get("title","")
                                _fam = item.get("family_name","")
                                if _fam and _fam in _title:
                                    suffix = _title[len(_fam):].strip()
                                    _m = _re2.search(r"(?:XL/XXL|XL-XXL|M/L|M-L|L/XL|L-XL|S/M|S-M|2XL|3XL|XL|XXL|XS|[SMLX]+)$", suffix, _re2.IGNORECASE)
                                    if _m:
                                        size = _m.group(0).strip()
                                        color = suffix[:_m.start()].strip().rstrip(" -")
                                    else:
                                        color = suffix.strip()
                            values = []
                            if color: values.append({"es": color})
                            if size: values.append({"es": size})
                            v = {
                                "price": str(item.get("price", base.get("price",0))),
                                "stock_management": True,
                                "stock": item.get("available_quantity", 0),
                            }
                            if values: v["values"] = values
                            variants.append(v)

                    # Imágenes — desde cache (sin llamar a ML, sin rate limit)
                    pics = []
                    seen = set()
                    for item in items_grupo:
                        # Primero fotos del cache (slim_product las guarda)
                        for pic in (item.get("pictures") or []):
                            url = (pic.get("url","") or "").replace("http://","https://")
                            if url and url not in seen:
                                pics.append({"src": url})
                                seen.add(url)
                        if len(pics) >= 10: break
                    # Fallback a thumbnail si no hay fotos en cache
                    if not pics:
                        for item in items_grupo:
                            thumb = item.get("thumbnail","")
                            if thumb:
                                url = thumb.replace("-I.jpg","-O.jpg").replace("http://","https://")
                                if url not in seen:
                                    pics.append({"src": url})
                                    seen.add(url)
                            if len(pics) >= 10: break

                    # Deduplicar variantes por combinacion de valores
                    seen_vals = set()
                    variants_dedup = []
                    for tv in variants:
                        key = tuple(sorted(v.get("es","") for v in (tv.get("values") or [])))
                        if key not in seen_vals:
                            seen_vals.add(key)
                            variants_dedup.append(tv)
                    print(f"  Variants: {len(variants)} -> {len(variants_dedup)} dedup")
                    # TN necesita atributos definidos si hay values
                    tn_attrs = []
                    if any(tv.get("values") for tv in variants_dedup):
                        sample = next((tv for tv in variants_dedup if tv.get("values")), {})
                        if len(sample.get("values",[])) >= 2:
                            tn_attrs = ["Color", "Talle"]
                        elif len(sample.get("values",[])) == 1:
                            first_val = sample["values"][0].get("es","")
                            # Detectar si es color o talle
                            talles = {"s","m","l","xl","xxl","s/m","m/l","l/xl","xl/xxl","2xl","3xl","unico"}
                            tn_attrs = ["Talle"] if first_val.lower() in talles else ["Color"]
                    payload = {
                        "name": {"es": title},
                        "description": {"es": desc},
                        "published": True,
                        "attributes": tn_attrs,
                        "variants": variants_dedup,
                        "images": pics[:10],
                    }
                    pr = await c.post(f"https://api.tiendanube.com/v1/{tn['store_id']}/products",
                                      headers=tn_hdrs, json=payload)
                    ok = pr.status_code in (200,201)
                    msg = f"Publicado con {len(variants)} variantes" if ok else pr.json().get("description","Error")
                    results.append({"id": base["id"], "title": title, "ok": ok, "msg": msg})
                    ST["log"].append({"ts":int(time.time()),"action":"publish_tn_group","product":title,"status":"ok" if ok else "error"})
                    await asyncio.sleep(1)
                except Exception as e:
                    results.append({"id": model_key, "title": model_key, "ok": False, "msg": str(e)})

        else:
            # Publicar uno por uno (comportamiento original)
            for iid in item_ids:
                try:
                    r = await c.get(f"{ML_API}/items/{iid}", headers=ml_hdrs)
                    item = r.json()
                    dr = await c.get(f"{ML_API}/items/{iid}/description", headers=ml_hdrs)
                    desc = dr.json().get("plain_text", item.get("title",""))
                    variations = item.get("variations",[])
                    if variations:
                        variants = [{"price":str(v.get("price",item.get("price",0))),
                                     "stock_management":True,"stock":v.get("available_quantity",0),
                                     "values":[{"es":a["value_name"]} for a in v.get("attribute_combinations",[])]}
                                    for v in variations]
                    else:
                        variants = [{"price":str(item.get("price",0)),"stock_management":True,
                                     "stock":item.get("available_quantity",0)}]
                    payload = {"name":{"es":item["title"]},"description":{"es":desc},
                               "published":True,"variants":variants,
                               "images":[{"src":p["url"]} for p in (item.get("pictures") or [])[:5]]}
                    pr = await c.post(f"https://api.tiendanube.com/v1/{tn['store_id']}/products",
                                      headers=tn_hdrs, json=payload)
                    ok = pr.status_code in (200,201)
                    results.append({"id":iid,"title":item.get("title",""),"ok":ok,
                                     "msg":"Publicado" if ok else pr.json().get("description","Error")})
                    ST["log"].append({"ts":int(time.time()),"action":"publish","product":item.get("title",""),
                                      "status":"ok" if ok else "error"})
                    await asyncio.sleep(0.3)
                except Exception as e:
                    results.append({"id":iid,"ok":False,"msg":str(e)})
    save_state()
    return {"results": results}


def normalize_size_label(s: str) -> str:
    """Normalizar etiqueta de talle para comparacion flexible.

    Match exacto por string completo (como VLOOKUP en Excel).
    Saca espacios y unifica mayusculas/separadores, pero MANTIENE parentesis
    porque algunas guias usan formato "M(ETIQXL)" donde el contenido entre
    parentesis es parte del identificador del talle.
    """
    if not s: return ""
    s = str(s).strip().upper()
    s = s.replace(" / ", "-").replace("/", "-")
    s = s.replace(" - ", "-").replace(" ", "")
    return s


async def ai_fix_duplicate_error(
    error_msg: str,
    error_status: int,
    payload: dict,
    orig_item: dict,
    dest_chart_rows: dict = None,
) -> dict:
    """Usa Claude para analizar un error de ML al duplicar y proponer un fix.

    Args:
        error_msg: Mensaje de error de ML (ej "Attribute [SIZE] is missing")
        error_status: HTTP status (400, 422, etc)
        payload: El payload que se mando a ML y fue rechazado
        orig_item: El item original que se esta duplicando (datos completos)
        dest_chart_rows: Talles disponibles en la guia destino (opcional)

    Returns:
        dict con:
          - "action": "modify_payload" | "abort" | "skip"
          - "patched_payload": nuevo payload modificado (si action=modify_payload)
          - "reason": explicacion en espanol de que se hizo
    """
    ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    if not ANTHROPIC_KEY:
        return {"action": "abort", "reason": "ANTHROPIC_API_KEY no configurada"}

    # Resumir el item original sin enviar todo (ahorrar tokens)
    orig_attrs_summary = {a.get("id"): a.get("value_name") for a in (orig_item.get("attributes") or [])}
    orig_title = orig_item.get("title", "")
    orig_category = orig_item.get("category_id", "")
    orig_variations = []
    for v in (orig_item.get("variations") or [])[:5]:
        var_attrs = {a.get("id"): a.get("value_name") for a in (v.get("attribute_combinations") or [])}
        orig_variations.append(var_attrs)

    # Resumir el payload
    payload_attrs = {a.get("id"): a.get("value_name") for a in (payload.get("attributes") or [])}

    # Talles disponibles en guia destino
    chart_summary = ""
    if dest_chart_rows:
        chart_summary = "\nTalles disponibles en guia destino: " + str(list(dest_chart_rows.keys())[:20])

    prompt = f"""Sos un experto en la API de Mercado Libre Argentina. Un duplicador automatico de items intentó publicar un item y ML lo rechazó. Tu tarea es analizar el error y proponer un fix al payload.

ERROR DE ML (status {error_status}):
{error_msg}

ITEM ORIGINAL (datos relevantes):
- Titulo: {orig_title}
- Categoria: {orig_category}
- Atributos: {json.dumps(orig_attrs_summary, ensure_ascii=False)[:1000]}
- Variations: {json.dumps(orig_variations, ensure_ascii=False)[:500] if orig_variations else "ninguna"}
{chart_summary}

PAYLOAD QUE FUE RECHAZADO (atributos enviados):
{json.dumps(payload_attrs, ensure_ascii=False)[:1500]}

INSTRUCCIONES:
1. Analiza el error de ML.
2. Identifica QUE atributo falta, sobra o esta mal.
3. Razona si el dato necesario esta en algun lugar del item original (titulo, attributes, variations) o en la guia destino.
4. Si podes resolver, devolve un JSON con la lista de atributos a AGREGAR/MODIFICAR/REMOVER.
5. Si no podes resolver con seguridad, devolve action="abort".

REGLAS:
- Si falta SIZE pero el titulo tiene un talle al final (ej "Xl (etiq 3xl)"), agregar SIZE con el formato exacto de la guia destino (ej "XL(ETIQ3XL)" si asi esta en la guia).
- Si falta SIZE_GRID_ROW_ID, buscar en la guia destino el row_id que corresponda al SIZE.
- No inventes datos. Si no hay informacion suficiente, action="abort".

RESPONDE SOLAMENTE CON UN JSON VALIDO, sin texto adicional, en este formato:
{{
  "action": "modify_payload" | "abort",
  "add_or_modify_attrs": [{{"id": "SIZE", "value_name": "XL(ETIQ3XL)"}}],
  "remove_attr_ids": ["BRAND"],
  "reason": "explicacion en espanol de que cambie y por que"
}}"""

    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                "https://api.anthropic.com/v1/messages",
                headers={"Content-Type": "application/json",
                         "x-api-key": ANTHROPIC_KEY,
                         "anthropic-version": "2023-06-01"},
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 1500,
                    "messages": [{"role": "user", "content": prompt}],
                }
            )
        if r.status_code != 200:
            return {"action": "abort", "reason": f"API IA error {r.status_code}"}
        ai_resp = r.json()
        text = ""
        for blk in ai_resp.get("content", []):
            if blk.get("type") == "text":
                text += blk.get("text", "")
        # Sacar fences markdown si los hay
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r'^```(?:json)?\s*\n?', '', text)
            text = re.sub(r'\n?```\s*$', '', text)
        text = text.strip()
        try:
            ai_decision = json.loads(text)
        except:
            return {"action": "abort", "reason": f"IA respondio invalido: {text[:200]}"}

        if ai_decision.get("action") != "modify_payload":
            return {"action": "abort", "reason": ai_decision.get("reason", "IA no pudo resolver")}

        # Aplicar el patch al payload
        new_payload = json.loads(json.dumps(payload))  # deep copy
        attrs = list(new_payload.get("attributes", []))
        # Remover atributos pedidos
        remove_ids = set(ai_decision.get("remove_attr_ids", []))
        if remove_ids:
            attrs = [a for a in attrs if a.get("id") not in remove_ids]
        # Agregar/modificar
        for new_a in ai_decision.get("add_or_modify_attrs", []):
            new_id = new_a.get("id")
            if not new_id:
                continue
            # Si ya existe, reemplazar
            found = False
            for i, a in enumerate(attrs):
                if a.get("id") == new_id:
                    attrs[i] = new_a
                    found = True
                    break
            if not found:
                attrs.append(new_a)
        new_payload["attributes"] = attrs

        return {
            "action": "modify_payload",
            "patched_payload": new_payload,
            "reason": ai_decision.get("reason", "Fix aplicado por IA"),
        }
    except Exception as e:
        return {"action": "abort", "reason": f"Excepcion IA: {str(e)[:150]}"}


def _build_shipping_from_item(item: dict) -> dict:
    """Construye el shipping para duplicar un item, copiando dimensiones y config completa.

    Copia:
      - mode (me2, custom, not_specified)
      - free_shipping
      - dimensions (ej "30x20x10,500" — largo x ancho x alto, peso en gramos)
      - local_pick_up (si admite retiro en persona)
      - logistic_type (xd_drop_off, fulfillment, etc) — si aplica
    """
    src = item.get("shipping") or {}
    out = {
        "mode": src.get("mode", "me2"),
        "free_shipping": src.get("free_shipping", False),
    }
    # Dimensiones (importantes para que ML calcule colecta y costos)
    if src.get("dimensions"):
        out["dimensions"] = src["dimensions"]
    if src.get("local_pick_up") is not None:
        out["local_pick_up"] = src["local_pick_up"]
    # logistic_type: solo si es xd_drop_off (drop-off). NO copiamos fulfillment
    # porque el destino tendria que tener stock en Full.
    if src.get("logistic_type") in ("xd_drop_off", "default", "self_service"):
        out["logistic_type"] = src["logistic_type"]
    return out


@app.post("/api/duplicate/stream")
async def duplicate_stream(req: Request, _=Depends(auth)):
    """Duplicar con streaming SSE — muestra progreso en tiempo real"""
    from fastapi.responses import StreamingResponse
    b = await req.json()
    from_idx = b.get("from_account", 0)
    to_idx = b.get("to_account", 1)
    status = b.get("status", "active")
    auto_link = b.get("auto_link", False)
    item_ids = b.get("item_ids", [])
    chart_override = b.get("chart_override", {})

    try:
        from_t = await fresh_token_pub(from_idx)  # usar app pub para no competir con webhooks
        to_t = await fresh_token_pub(to_idx)
    except Exception as e:
        async def err_gen():
            yield "data: " + json.dumps({"type":"done","total":0,"error":str(e)}) + "\n\n"
        return StreamingResponse(err_gen(), media_type="text/event-stream")

    # Lock por cuenta destino — evitar 2 duplicaciones simultaneas
    _dup_lock_key = f"mltn:dup_lock:{to_idx}"
    _redis_lock = get_redis()
    if _redis_lock:
        # Limpiar lock viejo si existe (puede quedar de un redeploy)
        _redis_lock.delete(_dup_lock_key)
        _redis_lock.setex(_dup_lock_key, 600, "1")  # lock de 10 min

    async def generate():
        total = len(item_ids)
        ok_count = 0
        yield "data: " + json.dumps({"type":"progress","done":0,"total":total}) + "\n\n"

        # Pre-cargar filas de guias destino UNA SOLA VEZ antes del loop
        _chart_rows_cache = {}  # chart_id -> {normalized_size: {row_id, size}}
        async def get_dest_chart_rows(chart_id):
            if chart_id in _chart_rows_cache:
                return _chart_rows_cache[chart_id]
            try:
                _cr = await ml_call(seller_uid=str(ST['accounts'][to_idx].get('uid','')), method="GET",
                    url=f"{ML_API}/catalog/charts/{chart_id}",
                    headers={"Authorization": f"Bearer {to_t}"}, priority=PRIORITY_MEDIUM)
                if _cr.status_code == 200:
                    rows = {}
                    for _row in (_cr.json().get("rows") or []):
                        _row_size = next((v.get("name","") for a in _row.get("attributes",[]) if a.get("id")=="SIZE" for v in a.get("values",[])), "")
                        if _row_size:
                            rows[normalize_size_label(_row_size)] = {"row_id": _row.get("id"), "size": _row_size}
                    _chart_rows_cache[chart_id] = rows
                    print(f"Chart {chart_id} loaded: {list(rows.keys())}")
                    return rows
            except Exception as _e:
                print(f"Error loading chart {chart_id}: {_e}")
            return {}

        # ─── AUTO-COPIA DE GUIAS DE TALLES ────────────────────────────────────
        # Cache de guias copiadas: chart_id_origen -> chart_id_destino
        _auto_chart_map = {}

        async def auto_copy_chart(orig_chart_id: str):
            """Copia la guia de talles del origen al destino si no existe.
            Devuelve el chart_id de destino (nuevo o reutilizado).
            Cachea el resultado para no copiar la misma guia 100 veces."""
            if not orig_chart_id:
                return None
            # 1. Cache local de esta sesion
            if orig_chart_id in _auto_chart_map:
                return _auto_chart_map[orig_chart_id]
            # 2. Cache persistente en Redis (sobrevive reinicios)
            _redis = get_redis()
            cache_key = f"mltn:chart_copy:{from_idx}_{to_idx}:{orig_chart_id}"
            if _redis:
                try:
                    cached = _redis.get(cache_key)
                    if cached:
                        _auto_chart_map[orig_chart_id] = cached
                        events.info(f"  → Guia {orig_chart_id} ya copiada antes → {cached}", source="duplicate", job_id=job_id)
                        return cached
                except: pass
            # 3. Bajar la guia original
            try:
                events.info(f"  → Copiando guia {orig_chart_id} de origen a destino...", source="duplicate", job_id=job_id)
                r_orig = await ml_call(seller_uid=str(ST['accounts'][from_idx].get('uid','')), method="GET",
                    url=f"{ML_API}/catalog/charts/{orig_chart_id}",
                    headers={"Authorization": f"Bearer {from_t}"}, priority=PRIORITY_MEDIUM)
                if r_orig.status_code != 200:
                    events.warn(f"  ⚠ No se pudo bajar la guia origen: {r_orig.status_code}", source="duplicate", job_id=job_id)
                    return orig_chart_id  # fallback: usar la misma
                chart = r_orig.json()
                # Armar payload para crear en destino
                payload = {
                    "site_id": chart.get("site_id", "MLA"),
                    "names": chart.get("names") or {"es_AR": chart.get("name", "Guia de talles")},
                    "domain_id": chart.get("domain_id"),
                    "attributes": chart.get("attributes", []),
                    "rows": chart.get("rows", []),
                }
                # Limpiar IDs de las rows (ML genera nuevos)
                for row in payload["rows"]:
                    row.pop("id", None)
                payload = {k: v for k, v in payload.items() if v is not None}
                # Crear en destino
                r_new = await ml_call(seller_uid=str(ST['accounts'][to_idx].get('uid','')), method="POST",
                    url=f"{ML_API}/catalog/charts",
                    headers={"Authorization": f"Bearer {to_t}", "Content-Type": "application/json"},
                    json=payload, priority=PRIORITY_MEDIUM)
                if r_new.status_code in (200, 201):
                    new_id = r_new.json().get("id")
                    if new_id:
                        new_id = str(new_id)
                        _auto_chart_map[orig_chart_id] = new_id
                        if _redis:
                            try:
                                # Cache 90 dias - las guias no cambian seguido
                                _redis.setex(cache_key, 86400 * 90, new_id)
                            except: pass
                        events.ok(f"  ✓ Guia copiada: {orig_chart_id} → {new_id}", source="duplicate", job_id=job_id)
                        return new_id
                events.warn(f"  ⚠ Error creando guia destino: {r_new.status_code} {r_new.text[:200]}", source="duplicate", job_id=job_id)
                return orig_chart_id  # fallback
            except Exception as e:
                events.err(f"  ⚠ Error auto_copy_chart: {str(e)[:120]}", source="duplicate", job_id=job_id)
                return orig_chart_id

        for idx, iid in enumerate(item_ids):
            result = {"type": "result", "index": idx, "id": iid, "ok": False, "title": iid, "msg": ""}
            try:
                # Usar ml_manual para GET — respeta rate limiter
                _fetch_r = await ml_call(seller_uid=str(ST['accounts'][from_idx].get('uid','')), method="GET",
                    url=f"{ML_API}/items/{iid}?attributes=id,title,price,available_quantity,category_id,pictures,variations,attributes,shipping,sale_terms,listing_type_id,family_name",
                    headers={"Authorization": f"Bearer {from_t}"}, priority=PRIORITY_MEDIUM)
                if _fetch_r.status_code != 200:
                    result["msg"] = f"Error {_fetch_r.status_code} obteniendo item"
                    yield "data: " + json.dumps(result) + "\n\n"
                    continue
                item = _fetch_r.json()
                result["title"] = item.get("title", iid)
                # SIZE_GRID_ID con override
                item_attrs = [a for a in (item.get("attributes") or []) if a.get("value_name") and a.get("id") not in ("ITEM_CONDITION","SELLER_SKU","SIZE_GRID_ID","SIZE_GRID_ROW_ID","SIZE","CATALOG_PRODUCT_ID","HAS_BIDS","WARRANTY_TYPE","WARRANTY_TIME","FILTRABLE_SIZE","IS_EMERGING_BRAND")]
                _orig_cid = str(next((a.get("value_name","") for a in (item.get("attributes") or []) if a.get("id")=="SIZE_GRID_ID"), "") or "")
                events.info(f"  → SIZE_GRID_ID original: {_orig_cid or '(NO TIENE)'}", source="duplicate", job_id=job_id)
                # Diagnostico: ver dimensiones del paquete
                _dims_attrs = [a for a in (item.get("attributes") or []) if a.get("id","").startswith(("PACKAGE_","SELLER_PACKAGE_"))]
                if _dims_attrs:
                    _dims_str = ", ".join(f"{a['id'].replace('SELLER_PACKAGE_','').replace('PACKAGE_','')}={a.get('value_name','')}" for a in _dims_attrs)
                    events.info(f"  → Dimensiones paquete: {_dims_str}", source="duplicate", job_id=job_id)
                _ship_dims = (item.get("shipping") or {}).get("dimensions")
                if _ship_dims:
                    events.info(f"  → Dimensiones envio: {_ship_dims}", source="duplicate", job_id=job_id)
                _manual = chart_override.get("manual","")
                _dest_cid = _manual or chart_override.get(_orig_cid) or chart_override.get(str(_orig_cid),"")
                # Si no hay override manual, copiar guia automaticamente
                if not _dest_cid and _orig_cid:
                    _dest_cid = await auto_copy_chart(_orig_cid)
                if not _dest_cid:
                    _dest_cid = _orig_cid  # fallback final
                events.info(f"  → Guia destino: {_dest_cid} (manual={bool(_manual)})", source="duplicate", job_id=job_id)
                print(f"SIZE_GRID: orig={_orig_cid} manual={_manual} dest={_dest_cid}")
                if _dest_cid:
                    item_attrs.append({"id":"SIZE_GRID_ID","value_name":str(_dest_cid)})
                    # Mapear SIZE_GRID_ROW_ID buscando por nombre de talle en guia destino
                    import re as _re
                    # Extraer talle del titulo
                    _title = item.get("title","")
                    # MATCH EXACTO COMO EXCEL VLOOKUP:
                    # Capturar todo el final del titulo incluyendo parentesis si los hay.
                    # Ej: "... Celeste Xl (etiq 3xl)" -> "XL(ETIQ3XL)"
                    # Ej: "... Celeste M (etiq Xl)"  -> "M(ETIQXL)"
                    # Ej: "... Camiseta XL"          -> "XL" (sin parentesis)
                    # Patron: talle al final + opcionalmente "(... )"
                    _m_full = _re.search(
                        r'\b((?:XS|S|M|L|XL|XXL|2XL|3XL|4XL|5XL|\d+XL|\d{1,3})(?:[\s/\-]+(?:XS|S|M|L|XL|XXL|2XL|3XL|4XL|5XL|\d+XL|\d{1,3}))?)\s*(\([^)]*\))?\s*$',
                        _title, _re.IGNORECASE
                    )
                    if _m_full:
                        _talle_real = _m_full.group(1).strip().upper().replace(" ", "")
                        _etiq = _m_full.group(2) or ""
                        _etiq_clean = _etiq.strip().upper().replace(" ", "") if _etiq else ""
                        _size_val = _talle_real + _etiq_clean
                    else:
                        _size_val = ""

                    # FALLBACK: si no se encontro en el titulo, buscar en los attributes
                    # originales del item (SIZE viene del fabricante)
                    if not _size_val:
                        _orig_size_attr = next((a for a in (item.get("attributes") or []) if a.get("id") == "SIZE"), None)
                        if _orig_size_attr:
                            _size_val = (_orig_size_attr.get("value_name") or "").strip().upper()
                            if _size_val:
                                events.info(f"  → Talle obtenido del atributo SIZE original: '{_size_val}'", source="duplicate", job_id=job_id)

                    # FALLBACK 2: buscar en variations si tiene
                    if not _size_val and item.get("variations"):
                        for _var in item["variations"]:
                            for _va in (_var.get("attribute_combinations") or []):
                                if _va.get("id") == "SIZE":
                                    _size_val = (_va.get("value_name") or "").strip().upper()
                                    if _size_val:
                                        events.info(f"  → Talle obtenido de variation: '{_size_val}'", source="duplicate", job_id=job_id)
                                        break
                            if _size_val:
                                break

                    if _size_val:
                        events.info(f"  → Talle final: '{_size_val}' (titulo limpio: '{_title_clean[-40:]}')", source="duplicate", job_id=job_id)
                    else:
                        events.warn(f"  ⚠ No se encontro talle en titulo, atributos ni variations. ML va a rechazar.", source="duplicate", job_id=job_id)

                    # Buscar row en guia destino — usar cache (cargado una vez antes del loop)
                    _dest_rows = await get_dest_chart_rows(_dest_cid)

                    _dest_row_id = None
                    _dest_size = _size_val
                    if _dest_rows and _size_val:
                        _match = _dest_rows.get(normalize_size_label(_size_val))
                        if _match:
                            _dest_row_id = _match["row_id"]
                            _dest_size = _match["size"]
                            events.ok(f"  ✓ Talle mapeado: {_size_val} → row_id {_dest_row_id}", source="duplicate", job_id=job_id)
                        else:
                            events.warn(f"  ⚠ Talle '{_size_val}' NO existe en guia destino. Disponibles: {list(_dest_rows.keys())[:10]}", source="duplicate", job_id=job_id)
                    elif not _dest_rows:
                        events.warn(f"  ⚠ No se pudo cargar guia destino {_dest_cid}", source="duplicate", job_id=job_id)

                    if _dest_row_id:
                        item_attrs.append({"id":"SIZE_GRID_ROW_ID","value_name":_dest_row_id})
                        item_attrs.append({"id":"SIZE","value_name":_dest_size})
                    elif _size_val:
                        # Fallback: mandar SIZE sin ROW_ID
                        item_attrs.append({"id":"SIZE","value_name":_size_val})
                        events.warn(f"  ⚠ Fallback: enviando SIZE={_size_val} sin SIZE_GRID_ROW_ID (puede que ML lo rechace)", source="duplicate", job_id=job_id)
                else:
                    events.warn(f"  ⚠ Item sin SIZE_GRID_ID, no se puede mapear talle", source="duplicate", job_id=job_id)
                _fam = (item.get("family_name") or "").strip() or item.get("title","")[:60]
                payload = {
                    **({"title": item.get("title","")} if not _fam else {}),
                    "category_id": item.get("category_id",""),
                    "price": item.get("price", 0),
                    "available_quantity": item.get("available_quantity", 0),
                    "listing_type_id": item.get("listing_type_id","gold_special"),
                    "condition": "new",
                    "currency_id": item.get("currency_id","ARS"),
                    "pictures": [{"source": p["url"].replace("http://","https://")} for p in (item.get("pictures") or [])[:12]],
                    "sale_terms": [s for s in item.get("sale_terms",[]) if s.get("id") in ("WARRANTY_TYPE","WARRANTY_TIME")],
                    "shipping": _build_shipping_from_item(item),
                    "attributes": item_attrs,
                    "family_name": _fam,
                }
                # Validar family_name — no puede estar vacio
                if not payload["family_name"]:
                    payload["family_name"] = item.get("title","")[:60]
                if item.get("variations"):
                    payload["variations"] = [{
                        "attribute_combinations": v.get("attribute_combinations",[]),
                        "price": v.get("price", item.get("price",0)),
                        "available_quantity": v.get("available_quantity",0),
                    } for v in item["variations"]]
                print(f"Dup payload {iid}: title={payload.get('title','')[:40]} family={payload.get('family_name','')[:30]}")
                r2 = await ml_call(seller_uid=str(ST['accounts'][to_idx].get('uid','')), method="POST",
                    url=f"{ML_API}/items",
                    headers={"Authorization": f"Bearer {to_t}", "Content-Type": "application/json"},
                    priority=PRIORITY_MEDIUM, json_body=payload)
                if r2.status_code in (200, 201):
                    new_id = r2.json().get("id","")
                    result["ok"] = True
                    result["new_id"] = new_id
                    result["msg"] = f"OK -> {new_id}"
                    print(f"Dup OK: {iid} -> {new_id}")
                    ok_count += 1
                    if auto_link and new_id:
                        ST.setdefault("links",[]).append({
                            "ml_item_id": iid, "ml_acc_idx": from_idx,
                            "ml_account_name": ST["accounts"][from_idx].get("name",""),
                            "ml_title": item.get("title",""),
                            "tn_product_id": new_id, "tn_acc_idx": to_idx,
                            "auto_linked": True, "family_name": item.get("title","")
                        })
                else:
                    err = r2.json() if r2.content else {}
                    causes = err.get("cause",[])
                    real_errors = [c2 for c2 in causes if c2.get("type") == "error"]
                    # Include invalid field names in error message
                    invalid = [c2.get("values", c2.get("code","")) for c2 in causes if "invalid" in str(c2.get("code","")).lower()]
                    if invalid:
                        msg = f"body.invalid_fields: {invalid[:3]}"
                    elif real_errors:
                        msg = ", ".join([c2.get("message",c2.get("code","")) for c2 in real_errors[:2]])
                    else:
                        msg = err.get("message", f"Error {r2.status_code}: {str(err)[:100]}")
                    print(f"Dup ERROR {iid}: {msg} | full: {str(err)[:300]}")
                    result["msg"] = msg
            except Exception as e:
                result["msg"] = str(e)[:100]
            yield "data: " + json.dumps(result) + "\n\n"
            await asyncio.sleep(2.5)  # ritmo controlado entre publicaciones
        save_state()
        if _redis_lock: _redis_lock.delete(_dup_lock_key)
        yield "data: " + json.dumps({"type":"done","total":total,"ok":ok_count}) + "\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream",
        headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})


# ============================================================================
# DUPLICATE EN BACKGROUND CON JOBS
# ============================================================================
@app.post("/api/duplicate/start")
async def duplicate_start(req: Request, _=Depends(auth)):
    """Inicia duplicacion en background. Retorna job_id para hacer polling."""
    b = await req.json()
    from_idx = b.get("from_account", 0)
    to_idx = b.get("to_account", 1)
    item_ids = b.get("item_ids", [])
    if not item_ids:
        raise HTTPException(400, "Sin items para duplicar")

    from_name = ST["accounts"][from_idx].get("name", f"acc{from_idx}") if from_idx < len(ST["accounts"]) else f"acc{from_idx}"
    to_name = ST["accounts"][to_idx].get("name", f"acc{to_idx}") if to_idx < len(ST["accounts"]) else f"acc{to_idx}"

    job_id = jobs.create("duplicate", {
        "from_idx": from_idx, "to_idx": to_idx,
        "from_name": from_name, "to_name": to_name,
        "item_ids": item_ids
    })
    jobs.update(job_id, total=len(item_ids), status="running")
    events.info(f"Duplicacion iniciada: {len(item_ids)} items {from_name} → {to_name}",
                source="duplicate", job_id=job_id)

    # Lanzar en background
    asyncio.create_task(_do_duplicate_job(job_id, b))
    return {"ok": True, "job_id": job_id, "total": len(item_ids)}


async def _do_duplicate_job(job_id: str, b: dict):
    """Ejecuta la duplicacion en background, escribiendo progreso al job."""
    from_idx = b.get("from_account", 0)
    to_idx = b.get("to_account", 1)
    item_ids = b.get("item_ids", [])
    chart_override = b.get("chart_override", {})
    auto_link = b.get("auto_link", False)
    status_target = b.get("status", "active")
    total = len(item_ids)

    events.info(f"⚙ Refrescando tokens para origen y destino...", source="duplicate", job_id=job_id)
    try:
        from_t = await fresh_token_pub(from_idx)
        to_t = await fresh_token_pub(to_idx)
        from_uid = str(ST["accounts"][from_idx].get("uid",""))
        to_uid = str(ST["accounts"][to_idx].get("uid",""))
        from_name = ST["accounts"][from_idx].get("name","?")
        to_name = ST["accounts"][to_idx].get("name","?")
        events.ok(f"✓ Tokens OK. Origen={from_name} ({from_uid}), Destino={to_name} ({to_uid})",
                  source="duplicate", job_id=job_id)
    except Exception as e:
        events.err(f"✗ Error obteniendo tokens: {e}", source="duplicate", job_id=job_id)
        jobs.finish(job_id, status="error", error=f"Token error: {e}")
        return

    # Cache de filas de guias destino
    _chart_rows_cache = {}

    async def get_dest_chart_rows(chart_id):
        if chart_id in _chart_rows_cache:
            events.debug(f"  Guia {chart_id} (cacheada)", source="duplicate", job_id=job_id)
            return _chart_rows_cache[chart_id]
        events.info(f"  → Descargando guia de talles {chart_id} de {to_name}...",
                    source="duplicate", job_id=job_id)
        try:
            _cr = await ml_call(seller_uid=to_uid, method="GET",
                url=f"{ML_API}/catalog/charts/{chart_id}",
                headers={"Authorization": f"Bearer {to_t}"},
                priority=PRIORITY_MEDIUM)
            if _cr.status_code == 200:
                rows = {}
                for _row in (_cr.json().get("rows") or []):
                    _row_size = next((v.get("name","") for a in _row.get("attributes",[])
                                      if a.get("id")=="SIZE" for v in a.get("values",[])), "")
                    if _row_size:
                        rows[normalize_size_label(_row_size)] = {"row_id": _row.get("id"), "size": _row_size}
                _chart_rows_cache[chart_id] = rows
                size_list = ", ".join(list(rows.keys())[:8])
                events.ok(f"  ✓ Guia {chart_id} cargada: {len(rows)} talles [{size_list}]",
                          source="duplicate", job_id=job_id)
                return rows
            else:
                events.warn(f"  ✗ Guia {chart_id} respondio {_cr.status_code}",
                            source="duplicate", job_id=job_id)
        except Exception as e:
            events.err(f"  ✗ Error cargando chart {chart_id}: {e}", source="duplicate", job_id=job_id)
        return {}

    for idx, iid in enumerate(item_ids):
        # Chequeo de cancelacion
        current = jobs.get(job_id)
        if current and current.get("status") == "cancel_requested":
            events.warn(f"⏹ Job cancelado por el usuario en item {idx+1}/{total}",
                        source="duplicate", job_id=job_id)
            jobs.finish(job_id, status="cancelled", error="Cancelado por el usuario")
            return

        events.info(f"━━━ Item {idx+1}/{total}: {iid} ━━━", source="duplicate", job_id=job_id)
        item_result = {"id": iid, "ok": False, "title": iid, "msg": ""}
        try:
            events.info(f"  → GET /items/{iid} (descargando datos del producto origen)",
                        source="duplicate", job_id=job_id)
            _fetch_r = await ml_call(seller_uid=from_uid, method="GET",
                url=f"{ML_API}/items/{iid}?attributes=id,title,price,available_quantity,category_id,pictures,variations,attributes,shipping,sale_terms,listing_type_id,family_name,currency_id,condition",
                headers={"Authorization": f"Bearer {from_t}"},
                priority=PRIORITY_MEDIUM)
            if _fetch_r.status_code != 200:
                events.err(f"  ✗ Error {_fetch_r.status_code} obteniendo item",
                           source="duplicate", job_id=job_id)
                item_result["msg"] = f"Error {_fetch_r.status_code} obteniendo item"
                jobs.add_item(job_id, item_result)
                continue

            item = _fetch_r.json()
            item_result["title"] = item.get("title", iid)
            n_pics = len(item.get("pictures") or [])
            n_attrs = len(item.get("attributes") or [])
            n_vars = len(item.get("variations") or [])
            price = item.get("price", 0)
            cat = item.get("category_id", "?")
            events.ok(f"  ✓ Item descargado: '{item.get('title','')[:60]}' | ${price} | cat={cat} | {n_pics} fotos, {n_attrs} attrs, {n_vars} variantes",
                      source="duplicate", job_id=job_id)

            # Construir payload
            item_attrs = [a for a in (item.get("attributes") or []) if a.get("value_name") and a.get("id") not in (
                "ITEM_CONDITION","SELLER_SKU","SIZE_GRID_ID","SIZE_GRID_ROW_ID","SIZE",
                "CATALOG_PRODUCT_ID","HAS_BIDS","WARRANTY_TYPE","WARRANTY_TIME",
                "FILTRABLE_SIZE","IS_EMERGING_BRAND")]
            _orig_cid = str(next((a.get("value_name","") for a in (item.get("attributes") or []) if a.get("id")=="SIZE_GRID_ID"), "") or "")

            CHART_MAP = {"1555281": {1: "4817544"}}
            _manual = chart_override.get("manual","")
            _dest_cid = _manual or chart_override.get(_orig_cid) or chart_override.get(str(_orig_cid),"")
            if not _dest_cid and _orig_cid in CHART_MAP:
                _dest_cid = CHART_MAP[_orig_cid].get(to_idx, "")
            if not _dest_cid:
                _dest_cid = _orig_cid

            if _orig_cid:
                events.info(f"  ℹ Guia origen: {_orig_cid} → Guia destino: {_dest_cid}" + (" (manual)" if _manual else ""),
                            source="duplicate", job_id=job_id)

            if _dest_cid:
                item_attrs.append({"id":"SIZE_GRID_ID","value_name":str(_dest_cid)})
                import re as _re
                _title = item.get("title","")
                # MATCH EXACTO COMO EXCEL VLOOKUP:
                # Capturar todo el final del titulo incluyendo parentesis si los hay.
                # Ej: "... Celeste Xl (etiq 3xl)" -> "XL(ETIQ3XL)"
                # Ej: "... Celeste M (etiq Xl)"  -> "M(ETIQXL)"
                _m_full = _re.search(
                    r'\b((?:XS|S|M|L|XL|XXL|2XL|3XL|4XL|5XL|\d+XL|\d{1,3})(?:[\s/\-]+(?:XS|S|M|L|XL|XXL|2XL|3XL|4XL|5XL|\d+XL|\d{1,3}))?)\s*(\([^)]*\))?\s*$',
                    _title, _re.IGNORECASE
                )
                if _m_full:
                    _talle_real = _m_full.group(1).strip().upper().replace(" ", "")
                    _etiq = _m_full.group(2) or ""
                    _etiq_clean = _etiq.strip().upper().replace(" ", "") if _etiq else ""
                    _size_val = _talle_real + _etiq_clean
                else:
                    _size_val = ""

                # FALLBACK: buscar en atributos originales
                if not _size_val:
                    _orig_size_attr = next((a for a in (item.get("attributes") or []) if a.get("id") == "SIZE"), None)
                    if _orig_size_attr:
                        _size_val = (_orig_size_attr.get("value_name") or "").strip().upper()
                        if _size_val:
                            events.info(f"  → Talle obtenido del atributo SIZE original: '{_size_val}'",
                                        source="duplicate", job_id=job_id)

                # FALLBACK 2: buscar en variations
                if not _size_val and item.get("variations"):
                    for _var in item["variations"]:
                        for _va in (_var.get("attribute_combinations") or []):
                            if _va.get("id") == "SIZE":
                                _size_val = (_va.get("value_name") or "").strip().upper()
                                if _size_val:
                                    events.info(f"  → Talle obtenido de variation: '{_size_val}'",
                                                source="duplicate", job_id=job_id)
                                    break
                        if _size_val:
                            break

                if _size_val:
                    events.info(f"  → Talle final: '{_size_val}'", source="duplicate", job_id=job_id)
                else:
                    events.warn(f"  ⚠ NO se encontro talle (titulo: '{_title_clean[-40:]}'). ML va a rechazar.",
                                source="duplicate", job_id=job_id)

                _dest_rows = await get_dest_chart_rows(_dest_cid)
                _dest_row_id = None
                _dest_size = _size_val
                if _dest_rows and _size_val:
                    _match = _dest_rows.get(normalize_size_label(_size_val))
                    if _match:
                        _dest_row_id = _match["row_id"]
                        _dest_size = _match["size"]
                        events.ok(f"  ✓ Talle '{_size_val}' matcheado en guia destino → row_id={_dest_row_id}",
                                  source="duplicate", job_id=job_id)
                    else:
                        events.warn(f"  ⚠ Talle '{_size_val}' NO encontrado en guia destino. Disponibles: {list(_dest_rows.keys())[:8]}",
                                    source="duplicate", job_id=job_id)

                if _dest_row_id:
                    item_attrs.append({"id":"SIZE_GRID_ROW_ID","value_name":_dest_row_id})
                    item_attrs.append({"id":"SIZE","value_name":_dest_size})
                elif _size_val:
                    item_attrs.append({"id":"SIZE","value_name":_size_val})
                    events.warn(f"  ⚠ Enviando SIZE sin SIZE_GRID_ROW_ID (puede dar error)",
                                source="duplicate", job_id=job_id)

            _fam = (item.get("family_name") or "").strip() or item.get("title","")[:60]
            payload = {
                **({"title": item.get("title","")} if not _fam else {}),
                "category_id": item.get("category_id",""),
                "price": item.get("price", 0),
                "available_quantity": item.get("available_quantity", 0),
                "listing_type_id": item.get("listing_type_id","gold_special"),
                "condition": "new",
                "currency_id": item.get("currency_id","ARS"),
                "pictures": [{"source": p["url"].replace("http://","https://")} for p in (item.get("pictures") or [])[:12]],
                "sale_terms": [s for s in item.get("sale_terms",[]) if s.get("id") in ("WARRANTY_TYPE","WARRANTY_TIME")],
                "shipping": _build_shipping_from_item(item),
                "attributes": item_attrs,
                "family_name": _fam,
            }
            if not payload["family_name"]:
                payload["family_name"] = item.get("title","")[:60]
            if item.get("variations"):
                payload["variations"] = [{
                    "attribute_combinations": v.get("attribute_combinations",[]),
                    "price": v.get("price", item.get("price",0)),
                    "available_quantity": v.get("available_quantity",0),
                } for v in item["variations"]]

            n_pics_send = len(payload.get("pictures", []))
            n_attrs_send = len(payload.get("attributes", []))
            events.info(f"  → POST /items en {to_name}: family='{_fam[:40]}' | {n_pics_send} fotos | {n_attrs_send} atributos",
                        source="duplicate", job_id=job_id)

            r2 = await ml_call(seller_uid=to_uid, method="POST",
                url=f"{ML_API}/items",
                headers={"Authorization": f"Bearer {to_t}", "Content-Type": "application/json"},
                priority=PRIORITY_MEDIUM, json_body=payload)

            if r2.status_code in (200, 201):
                new_id = r2.json().get("id","")
                item_result["ok"] = True
                item_result["new_id"] = new_id
                item_result["msg"] = f"OK -> {new_id}"
                events.ok(f"  ★ PUBLICADO: {iid} → {new_id}", source="duplicate", job_id=job_id)
                if auto_link and new_id:
                    ST.setdefault("links",[]).append({
                        "ml_item_id": iid, "ml_acc_idx": from_idx,
                        "ml_account_name": ST["accounts"][from_idx].get("name",""),
                        "ml_title": item.get("title",""),
                        "tn_product_id": new_id, "tn_acc_idx": to_idx,
                        "auto_linked": True, "family_name": item.get("title","")
                    })
                    events.info(f"  ⚭ Link creado: {iid} ↔ {new_id}",
                                source="duplicate", job_id=job_id)
            else:
                err = r2.json() if r2.content else {}
                causes = err.get("cause", [])
                real_errors = [c for c in causes if c.get("type") == "error"]
                if real_errors:
                    msg = ", ".join([c.get("message", c.get("code","")) for c in real_errors[:2]])
                else:
                    msg = err.get("message", f"Error {r2.status_code}")

                # ─── INTERVENCION DE IA ───────────────────────────────────────
                # Si es error 400/422 y tenemos clave de IA, intentar arreglar
                ai_max_retries = 3
                ai_fixed = False
                if r2.status_code in (400, 422) and os.getenv("ANTHROPIC_API_KEY"):
                    events.warn(f"  ✗ Error {r2.status_code}: {msg[:100]} — consultando IA...",
                                source="duplicate", job_id=job_id)
                    current_payload = payload
                    for _ai_attempt in range(1, ai_max_retries + 1):
                        try:
                            fix = await ai_fix_duplicate_error(
                                error_msg=msg,
                                error_status=r2.status_code,
                                payload=current_payload,
                                orig_item=item,
                                dest_chart_rows=_dest_rows if '_dest_rows' in dir() else None,
                            )
                        except Exception as _aie:
                            events.err(f"  ⚠ IA error: {str(_aie)[:120]}", source="duplicate", job_id=job_id)
                            break

                        if fix.get("action") != "modify_payload":
                            events.warn(f"  ⚠ IA no pudo resolver: {fix.get('reason','')[:150]}",
                                        source="duplicate", job_id=job_id)
                            break

                        events.info(f"  🤖 IA intento {_ai_attempt}/{ai_max_retries}: {fix.get('reason','')[:200]}",
                                    source="duplicate", job_id=job_id)
                        current_payload = fix["patched_payload"]
                        # Reintentar POST con payload arreglado
                        try:
                            r3 = await ml_call(seller_uid=str(ST['accounts'][to_idx].get('uid','')),
                                method="POST", url=f"{ML_API}/items",
                                headers={"Authorization": f"Bearer {to_pub_t}",
                                         "Content-Type": "application/json"},
                                json=current_payload, priority=PRIORITY_LOW)
                        except Exception as _re_e:
                            events.err(f"  ⚠ Error reintento: {str(_re_e)[:120]}",
                                       source="duplicate", job_id=job_id)
                            continue

                        if r3.status_code in (200, 201):
                            new_item = r3.json()
                            item_result["ok"] = True
                            item_result["new_id"] = new_item.get("id")
                            item_result["msg"] = f"OK (arreglado por IA: {fix.get('reason','')[:80]})"
                            events.ok(f"  ✓ ARREGLADO POR IA en intento {_ai_attempt}: nuevo id {new_item.get('id')}",
                                      source="duplicate", job_id=job_id)
                            ai_fixed = True
                            break
                        # No funciono, actualizar msg y reintentar
                        err3 = r3.json() if r3.content else {}
                        causes3 = err3.get("cause", [])
                        real3 = [c for c in causes3 if c.get("type") == "error"]
                        msg = ", ".join([c.get("message", c.get("code","")) for c in real3[:2]]) if real3 else err3.get("message", f"Error {r3.status_code}")
                        events.warn(f"  ⚠ IA intento {_ai_attempt} fallo: {msg[:100]}",
                                    source="duplicate", job_id=job_id)

                if not ai_fixed:
                    item_result["msg"] = msg[:200]
                    events.err(f"  ✗ FALLO: {r2.status_code} - {msg[:120]}",
                               source="duplicate", job_id=job_id)
        except Exception as e:
            item_result["msg"] = f"Exception: {str(e)[:150]}"
            events.err(f"  ✗ EXCEPCION: {str(e)[:120]}", source="duplicate", job_id=job_id)

        jobs.add_item(job_id, item_result)

    save_state()
    events.ok(f"━━━ Duplicacion terminada ━━━", source="duplicate", job_id=job_id)
    jobs.finish(job_id, status="done")

@app.post("/api/duplicate")
async def duplicate(req: Request, background_tasks: BackgroundTasks, _=Depends(auth)):
    b = await req.json()
    from_idx = b.get("from_account", 0)
    to_idx = b.get("to_account", 1)
    status = b.get("status", "active")
    auto_link = b.get("auto_link", False)
    agrupar = b.get("agrupar", False)
    explotar = b.get("explotar", False)
    try:
        from_t = await fresh_token_pub(from_idx)  # app publicar — rate limit independiente
        to_t = await fresh_token_pub(to_idx)
    except Exception as e:
        raise HTTPException(400, f"Error de token: {str(e)}")

    # Guardar job_id para polling
    import uuid as _uuid
    job_id = str(_uuid.uuid4())[:8]
    _dup_jobs[job_id] = {"status": "running", "results": []}

    # Cache de guías de talles ya copiadas en esta sesión: {chart_id_origen: chart_id_destino}
    size_chart_map = {}

    async def copy_size_chart(c, chart_id: str):
        """Copia una guía de talles de cuenta origen a destino. Devuelve el nuevo ID."""
        if chart_id in size_chart_map:
            return size_chart_map[chart_id]
        try:
            # Obtener la guía original
            r = await c.get(f"{ML_API}/size_charts/{chart_id}",
                           headers={"Authorization": f"Bearer {from_t}"})
            if r.status_code != 200:
                return None
            chart = r.json()

            # Obtener el user_id de la cuenta destino
            me_r = await c.get(f"{ML_API}/users/me",
                               headers={"Authorization": f"Bearer {to_t}"})
            to_uid = me_r.json().get("id")

            # Armar payload para crear la guía en destino
            payload = {
                "site_id": chart.get("site_id", "MLA"),
                "name": chart.get("name", "Guía de talles"),
                "category_id": chart.get("category_id"),
                "domain_id": chart.get("domain_id"),
                "attributes": chart.get("attributes", []),
                "rows": chart.get("rows", []),
            }
            # Remover claves None
            payload = {k: v for k, v in payload.items() if v is not None}

            r2 = await c.post(f"{ML_API}/size_charts",
                             headers={"Authorization": f"Bearer {to_t}"},
                             json=payload)
            if r2.status_code in (200, 201):
                new_chart_id = r2.json().get("id")
                size_chart_map[chart_id] = new_chart_id
                return new_chart_id
            else:
                # Si falla la creación, intentar reutilizar la misma (si es pública/compartida)
                size_chart_map[chart_id] = chart_id
                return chart_id
        except Exception:
            return chart_id  # fallback: usar el mismo ID

    # Detectar si cuenta destino es user_product_seller y preparar carga de guías
    dest_is_up = False
    dest_charts_cache = {}  # chart_id -> {chart_id, rows: {size_name: row_id}}
    # Cargar overrides de guías seleccionadas por el usuario
    chart_override = b.get("chart_override", {}) or {}
    try:
        r_redis = get_redis()
        if r_redis:
            raw = r_redis.get("mltn:chart_override")
            if raw:
                redis_override = json.loads(raw)
                if isinstance(redis_override, dict):
                    chart_override = {**redis_override, **chart_override}
    except Exception:
        pass
    # Hardcode conocidos: guía origen -> guía destino por cuenta
    KNOWN_CHARTS = {
        # LENCERIA pijamas (4788364) -> SHAMPOOSHIR pijamas (5127137)
        "4788364": {"chart_id": "5127137", "rows": {"XL-2XL": "5127137:1", "3XL-4XL": "5127137:2"}},
        # También mapear 4666038 por si acaso
        "4666038": {"chart_id": "5127137", "rows": {"XL-2XL": "5127137:1", "3XL-4XL": "5127137:2"}},
    }

    async def get_dest_user_info():
        nonlocal dest_is_up
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{ML_API}/users/me", headers={"Authorization": f"Bearer {to_t}"})
            try:
                data = r.json() if r.content else {}
            except Exception:
                data = {}
            tags = data.get("tags", [])
            dest_is_up = "user_product_seller" in tags

    async def load_dest_chart(orig_chart_id: str, domain_id: str = "", brand: str = ""):
        """Buscar guía de talles equivalente en cuenta destino"""
        cache_key = f"{orig_chart_id}"
        if cache_key in dest_charts_cache:
            return dest_charts_cache[cache_key]
        # Verificar override del usuario (por ID específico o "manual" para cualquiera)
        override_key = orig_chart_id if orig_chart_id in chart_override else ("manual" if "manual" in chart_override else None)
        if override_key:
            orig_chart_id = override_key  # reusar lógica abajo
        if orig_chart_id in chart_override:
            override_id = chart_override[orig_chart_id]
            try:
                async with httpx.AsyncClient(timeout=10) as c:
                    r = await c.get(f"{ML_API}/catalog/charts/{override_id}",
                                   headers={"Authorization": f"Bearer {to_t}"})
                    if r.status_code == 200:
                        row_map = {}
                        for row in (r.json().get("rows") or []):
                            rid = row.get("id","")
                            sv = next((v.get("name","") for a in row.get("attributes",[])
                                       if a.get("id")=="SIZE" for v in a.get("values",[])), "")
                            if sv and rid:
                                row_map[sv] = rid
                        result = {"chart_id": override_id, "rows": row_map}
                        dest_charts_cache[cache_key] = result
                        return result
            except Exception:
                pass
        # Usar mapeo conocido si existe
        if orig_chart_id in KNOWN_CHARTS:
            result = KNOWN_CHARTS[orig_chart_id]
            dest_charts_cache[cache_key] = result
            return result
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                # Para UP siempre buscar en las guías del vendedor destino
                # Para no-UP: intentar leer la guía origen directamente
                if not dest_is_up:
                    r = await c.get(f"{ML_API}/catalog/charts/{orig_chart_id}",
                                   headers={"Authorization": f"Bearer {to_t}"})
                    if r.status_code == 200:
                        chart = r.json()
                        # Solo usar si es del vendedor destino
                        if str(chart.get("seller_id","")) == str(to_uid if False else ""):
                            pass  # skip, we don't have to_uid yet
                        row_map = {}
                        for row in (chart.get("rows") or []):
                            rid = row.get("id","")
                            size_val = next((v.get("name","") for a in row.get("attributes",[])
                                            if a.get("id")=="SIZE" for v in a.get("values",[])), "")
                            if size_val and rid:
                                row_map[size_val] = rid
                        result = {"chart_id": orig_chart_id, "rows": row_map}
                        dest_charts_cache[cache_key] = result
                        return result

                # Buscar guías del vendedor destino por dominio
                me_r = await c.get(f"{ML_API}/users/me", headers={"Authorization": f"Bearer {to_t}"})
                to_uid = me_r.json().get("id","")

                # Buscar guías de la cuenta destino — primero con brand, luego sin brand
                charts = []
                for search_payload in [
                    {"site_id":"MLA","seller_id": to_uid, "domain_id": domain_id or "BRAS",
                     "attributes":[{"id":"GENDER","values":[{"name":"Mujer"}]},{"id":"BRAND","values":[{"name": brand or ""}]}]},
                    {"site_id":"MLA","seller_id": to_uid, "domain_id": domain_id or "BRAS",
                     "attributes":[{"id":"GENDER","values":[{"name":"Mujer"}]}]},
                    {"site_id":"MLA","seller_id": to_uid, "domain_id": domain_id or "BRAS"},
                ]:
                    search_r = await c.post(f"{ML_API}/catalog/charts/search",
                        headers={"Authorization": f"Bearer {to_t}", "Content-Type": "application/json"},
                        json=search_payload)
                    if search_r.status_code == 200:
                        charts = search_r.json().get("charts", [])
                        if charts:
                            break

                # Elegir la guía que tenga el talle que necesitamos
                best = None
                # Primero buscar la guía que ya tenga el size_val en sus rows
                for ch in charts:
                    for row in (ch.get("rows") or []):
                        sv = next((v.get("name","") for a in row.get("attributes",[])
                                   if a.get("id")=="SIZE" for v in a.get("values",[])), "")
                        if sv == size_val:
                            best = ch
                            break
                    if best:
                        break
                # Si ninguna tiene el talle, tomar la que coincida por brand
                if not best:
                    for ch in charts:
                        ch_name = ch.get("names",{}).get("MLA","").upper()
                        if brand and brand.upper() in ch_name:
                            best = ch
                            break
                if not best and charts:
                    best = charts[0]

                if best:
                    # Cargar los rows de la mejor guía
                    r2 = await c.get(f"{ML_API}/catalog/charts/{best['id']}",
                                    headers={"Authorization": f"Bearer {to_t}"})
                    if r2.status_code == 200:
                        chart = r2.json()
                        row_map = {}
                        for row in (chart.get("rows") or []):
                            rid = row.get("id","")
                            size_val = next((v.get("name","") for a in row.get("attributes",[])
                                            if a.get("id")=="SIZE" for v in a.get("values",[])), "")
                            if size_val and rid:
                                row_map[size_val] = rid
                        result = {"chart_id": str(best["id"]), "rows": row_map}
                        dest_charts_cache[cache_key] = result
                        return result
        except Exception:
            pass
        return None

    async def copy_chart_to_dest(orig_chart_id: str, domain_id: str = ""):
        """Copiar guía de talles de cuenta origen a cuenta destino"""
        cache_key = f"copy_{orig_chart_id}"
        if cache_key in dest_charts_cache:
            return dest_charts_cache[cache_key]
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                # Leer guía original con token origen
                r = await c.get(f"{ML_API}/catalog/charts/{orig_chart_id}",
                               headers={"Authorization": f"Bearer {from_t}"})
                if r.status_code != 200:
                    return None
                orig = r.json()

                # Armar payload para crear la guía en destino
                new_chart = {
                    "names": orig.get("names", {"MLA": "Guía de talles"}),
                    "domain_id": orig.get("domain_id") or domain_id or "BRAS",
                    "site_id": orig.get("site_id", "MLA"),
                    "main_attribute": {"attributes": [{"site_id": "MLA", "id": orig.get("main_attribute_id", "SIZE")}]},
                    "attributes": orig.get("attributes", []),
                    "rows": []
                }
                if orig.get("measure_type"):
                    new_chart["measure_type"] = orig["measure_type"]

                # Copiar rows — limpiar IDs
                for row in (orig.get("rows") or []):
                    new_row = {"attributes": []}
                    for a in row.get("attributes", []):
                        new_row["attributes"].append({
                            "id": a["id"],
                            "values": a.get("values", [])
                        })
                    new_chart["rows"].append(new_row)

                r2 = await c.post(f"{ML_API}/catalog/charts",
                                 headers={"Authorization": f"Bearer {to_t}", "Content-Type": "application/json"},
                                 json=new_chart)

                if r2.status_code in (200, 201):
                    new_chart_data = r2.json()
                    new_id = str(new_chart_data.get("id",""))
                    # Cargar los rows del nuevo chart
                    row_map = {}
                    for row in (new_chart_data.get("rows") or []):
                        rid = row.get("id","")
                        size_val2 = next((v.get("name","") for a in row.get("attributes",[])
                                         if a.get("id")=="SIZE" for v in a.get("values",[])), "")
                        if size_val2 and rid:
                            row_map[size_val2] = rid
                    result = {"chart_id": new_id, "rows": row_map}
                    dest_charts_cache[cache_key] = result
                    dest_charts_cache[orig_chart_id] = result  # también cachear por ID original
                    return result
        except Exception:
            pass
        return None

    await get_dest_user_info()

    results = []
    item_ids = b.get("item_ids", [])

    # Si explotar=True, expandir variantes como productos separados
    if explotar:
        new_item_ids = []
        for iid in item_ids:
            try:
                async with httpx.AsyncClient(timeout=15) as c:
                    r = await c.get(f"{ML_API}/items/{iid}", headers={"Authorization": f"Bearer {from_t}"})
                    if r.status_code == 200:
                        item = r.json()
                        variations = item.get("variations", [])
                        if variations:
                            # Crear un item por cada variante
                            for v in variations:
                                attrs = {a["name"]: a["value_name"] for a in v.get("attribute_combinations", [])}
                                # Buscar talle con cualquier key posible
                                talle = (attrs.get("Talle") or attrs.get("Tamaño") or 
                                        attrs.get("Size") or attrs.get("Talla") or
                                        next((v for k,v in attrs.items() if "tall" in k.lower() or "size" in k.lower() or "tamaño" in k.lower()), ""))
                                color = (attrs.get("Color") or 
                                        next((v for k,v in attrs.items() if "color" in k.lower()), ""))
                                print(f"Variante attrs: {attrs}, talle={talle}, color={color}")
                                suffix = " - ".join(filter(None, [talle, color]))
                                new_title = f"{item['title']} - {suffix}" if suffix else item["title"]
                                price = v.get("price") or item.get("price", 0)
                                stock = v.get("available_quantity", item.get("available_quantity", 0))
                                # Construir atributos — tomar del item original
                                item_attrs = [a for a in (item.get("attributes") or []) 
                                              if a.get("id") not in ("SIZE_GRID_ID", "SIZE", "COLOR")]
                                # Agregar SIZE y COLOR de esta variante
                                if talle:
                                    item_attrs.append({"id": "SIZE", "value_name": talle})
                                if color:
                                    item_attrs.append({"id": "COLOR", "value_name": color})
                                # Agregar family_name requerido por ML
                                item_attrs.append({"id": "FAMILY_NAME", "value_name": item.get("title", "")[:60]})
                                # Agregar SIZE_GRID_ID con override o el original
                                orig_chart_id = str(next((a.get("value_name","") for a in (item.get("attributes") or []) if a.get("id")=="SIZE_GRID_ID"), "") or "")
                                dest_chart_id = chart_override.get(orig_chart_id) or chart_override.get("manual", "")
                                if dest_chart_id:
                                    item_attrs.append({"id": "SIZE_GRID_ID", "value_name": str(dest_chart_id)})
                                elif orig_chart_id:
                                    item_attrs.append({"id": "SIZE_GRID_ID", "value_name": orig_chart_id})
                                payload = {
                                    "title": new_title,
                                    "category_id": item.get("category_id"),
                                    "price": price,
                                    "currency_id": item.get("currency_id", "ARS"),
                                    "available_quantity": stock,
                                    "buying_mode": "buy_it_now",
                                    "listing_type_id": item.get("listing_type_id", "gold_special"),
                                    "condition": item.get("condition", "new"),
                                    "pictures": [{"source": p["url"]} for p in (item.get("pictures") or [])[:12]],
                                    "attributes": item_attrs,
                                    "family_name": item.get("title","")
                                }
                                await asyncio.sleep(1)  # evitar rate limit de ML
                                r2 = None
                                for attempt in range(3):
                                    async with httpx.AsyncClient(timeout=30) as c2:
                                        r2 = await c2.post(f"{ML_API}/items",
                                            headers={"Authorization": f"Bearer {to_t}"},
                                            json=payload)
                                    if r2.status_code == 429:
                                        await asyncio.sleep((attempt+1) * 10)
                                        continue
                                    break
                                ok = r2.status_code in (200, 201) if r2 else False
                                try:
                                    err_body = r2.json() if r2 else {}
                                except:
                                    err_body = {}
                                if not ok:
                                    print(f"ML error {r2.status_code if r2 else 'None'}: {json.dumps(err_body)[:300]}")
                                cause_list = err_body.get("cause", [])
                                err_msg = cause_list[0].get("message","") if cause_list else err_body.get("message","Error")
                                results.append({"id": iid, "title": new_title, "ok": ok,
                                    "msg": "Publicado" if ok else err_msg})
                        else:
                            new_item_ids.append(iid)
                    else:
                        new_item_ids.append(iid)
            except Exception as e:
                results.append({"id": iid, "title": iid, "ok": False, "msg": str(e)})
        # Los items sin variantes se procesan normal
        item_ids = new_item_ids
        if not item_ids:
            return {"results": results}

    # Si agrupar=True, obtener todos los items primero y agrupar por MODEL
    if agrupar:
        async with httpx.AsyncClient(timeout=30) as c:
            # Bajar todos los items
            all_items = []
            for iid in item_ids:
                try:
                    r = await c.get(f"{ML_API}/items/{iid}", headers={"Authorization": f"Bearer {from_t}"})
                    item = r.json()
                    if "error" not in item:
                        all_items.append(item)
                    await asyncio.sleep(1)
                except Exception:
                    pass

            # Agrupar por MODEL o family_name (items UPtin)
            grupos = {}
            for item in all_items:
                model_attr = next((a for a in (item.get("attributes") or []) if a.get("id") == "MODEL"), None)
                if model_attr and model_attr.get("value_name"):
                    key = model_attr.get("value_name")
                elif item.get("family_name"):
                    key = item.get("family_name")
                elif item.get("user_product_id"):
                    key = item.get("user_product_id")
                else:
                    key = item["id"]
                if key not in grupos:
                    grupos[key] = []
                grupos[key].append(item)

            # Procesar cada grupo
            for model_key, items_grupo in grupos.items():
                try:
                    base_item = items_grupo[0]

                    # Armar variaciones combinando COLOR + SIZE de cada item
                    variations = []
                    for item in items_grupo:
                        attrs = {a["id"]: a for a in (item.get("attributes") or [])}
                        color_id = attrs.get("COLOR", {}).get("value_id")
                        color_name = attrs.get("COLOR", {}).get("value_name")
                        size_id = attrs.get("SIZE", {}).get("value_id")
                        size_name = attrs.get("SIZE", {}).get("value_name")
                        combinations = []
                        if color_id or color_name:
                            c_attr = {"id": "COLOR"}
                            if color_id: c_attr["value_id"] = color_id
                            else: c_attr["value_name"] = color_name
                            combinations.append(c_attr)
                        if size_id or size_name:
                            s_attr = {"id": "SIZE"}
                            if size_id: s_attr["value_id"] = size_id
                            else: s_attr["value_name"] = size_name
                            combinations.append(s_attr)
                        v = {
                            "price": item.get("price", base_item.get("price", 0)),
                            "available_quantity": item.get("available_quantity", 0),
                            "attribute_combinations": combinations,
                            "picture_ids": [p["id"] for p in (item.get("pictures") or [])[:3] if p.get("id")],
                        }
                        variations.append(v)

                    # Limpiar atributos del item base
                    EXCLUDED_ATTRS = {"SELLER_SKU","ITEM_CONDITION","ALPHANUMERIC_MODEL","GTIN",
                                      "PACKAGE_DATA_SOURCE","RELEASE_YEAR","SYI_PYMES_ID",
                                      "FILTRABLE_SIZE","SIZE_GRID_ROW_ID","SIZE_GRID_ID","COLOR","SIZE"}
                    brand_val = next((a.get("value_name","") for a in (base_item.get("attributes") or []) if a.get("id")=="BRAND"), "")
                    model_val2 = next((a.get("value_name","") for a in (base_item.get("attributes") or []) if a.get("id")=="MODEL"), "")
                    attrs_clean = []
                    for a in (base_item.get("attributes") or []):
                        aid = a.get("id","")
                        if aid in EXCLUDED_ATTRS: continue
                        if aid in ("BRAND","MODEL"):
                            vn = a.get("value_name")
                            if vn: attrs_clean.append({"id":aid,"value_name":vn})
                            continue
                        if a.get("value_id"):
                            attrs_clean.append({"id": aid, "value_id": a["value_id"]})
                        elif a.get("value_name"):
                            attrs_clean.append({"id": aid, "value_name": a["value_name"]})
                    family2 = f"{brand_val} {model_val2}".strip() or base_item.get("title","")[:60]
                    attrs_clean.append({"id": "family_name", "value_name": family2})
                    # Agregar dimensiones si se ingresaron
                    if dims:
                        if dims.get("h"): attrs_clean.append({"id":"SELLER_PACKAGE_HEIGHT","value_name":f'{int(dims["h"])} cm'})
                        if dims.get("w"): attrs_clean.append({"id":"SELLER_PACKAGE_WIDTH","value_name":f'{int(dims["w"])} cm'})
                        if dims.get("l"): attrs_clean.append({"id":"SELLER_PACKAGE_LENGTH","value_name":f'{int(dims["l"])} cm'})
                        if dims.get("p"): attrs_clean.append({"id":"SELLER_PACKAGE_WEIGHT","value_name":f'{int(float(dims["p"])*1000)} g'})
                        if dims.get("ph"): attrs_clean.append({"id":"PRODUCT_HEIGHT","value_name":f'{int(dims["ph"])} cm'})
                        if dims.get("pw"): attrs_clean.append({"id":"PRODUCT_WIDTH","value_name":f'{int(dims["pw"])} cm'})
                        if dims.get("pl"): attrs_clean.append({"id":"PRODUCT_LENGTH","value_name":f'{int(dims["pl"])} cm'})
                        if dims.get("pp"): attrs_clean.append({"id":"PRODUCT_WEIGHT","value_name":f'{int(float(dims["pp"])*1000)} g'})

                    # Título base: cortar después del número de modelo
                    _t = base_item.get("title", "")
                    if model_val2 and model_val2 in _t:
                        title = _t[:_t.index(model_val2)+len(model_val2)].strip()
                    elif " - " in _t:
                        title = _t.rsplit(" - ", 1)[0].strip()
                    else:
                        title = _t

                    payload = {
                        "title": title,
                        "category_id": base_item.get("category_id", ""),
                        "price": base_item.get("price", 0),
                        "currency_id": base_item.get("currency_id", "ARS"),
                        "available_quantity": 0,
                        "listing_type_id": base_item.get("listing_type_id", "gold_special"),
                        "condition": base_item.get("condition", "new"),
                        "pictures": [{"source": p["url"]} for p in (base_item.get("pictures") or [])[:12]],
                        "attributes": attrs_clean,
                        "variations": variations,
                    }
                    if base_item.get("sale_terms"):
                        payload["sale_terms"] = base_item["sale_terms"]

                    # Intentar con retry 429
                    r2 = None
                    for attempt in range(3):
                        r2 = await ml_call(seller_uid=str(ST['accounts'][to_idx].get('uid','')), method="POST", url=f"{ML_API}/items", headers={"Authorization": f"Bearer {to_t}", "Content-Type": "application/json"}, priority=PRIORITY_MEDIUM, json_body=payload)
                        if r2.status_code == 429:
                            await asyncio.sleep(30 * (attempt + 1))
                            continue
                        break

                    ok = r2.status_code in (200, 201)
                    new_id = None
                    if ok:
                        new_id = r2.json().get("id")
                        msg = f"Agrupado OK ({len(items_grupo)} variantes)"
                        if status == "paused" and new_id:
                            await c.put(f"{ML_API}/items/{new_id}",
                                headers={"Authorization": f"Bearer {to_t}"},
                                json={"status": "paused"})
                    elif r2.status_code == 429:
                        msg = "Rate limit ML (429)"
                    else:
                        try:
                            err = r2.json()
                            causes = err.get("cause", [])
                            msg = ", ".join([cx.get("code","") for cx in causes[:3]]) if causes else err.get("message", f"Error {r2.status_code}")
                        except Exception:
                            msg = f"Error {r2.status_code}"

                    results.append({"id": base_item["id"], "title": title, "ok": ok, "msg": msg, "new_id": new_id, "variantes": len(items_grupo)})
                    ST["log"].append({"ts": int(time.time()), "action": "duplicate_group", "product": title, "status": "ok" if ok else "error"})
                    await asyncio.sleep(1)

                except Exception as e:
                    results.append({"id": model_key, "title": model_key, "ok": False, "msg": str(e)})

        save_state()
        return {"results": results}

    # Modo normal: duplicar uno por uno
    async with httpx.AsyncClient(timeout=30) as c:
        for iid in item_ids:
            try:
                # Fetch con retry en caso de rate limit
                r = None
                for attempt in range(3):
                    r = await c.get(f"{ML_API}/items/{iid}", headers={"Authorization": f"Bearer {from_t}"})
                    if r.status_code == 429:
                        await asyncio.sleep((attempt+1) * 15)
                        continue
                    break
                try:
                    item = r.json()
                except:
                    results.append({"id": iid, "title": iid, "ok": False, "msg": "Rate limit ML (429) — esperá unos minutos"})
                    await asyncio.sleep(30)
                    continue
                if "error" in item:
                    results.append({"id": iid, "title": iid, "ok": False, "msg": item.get("message", "Error ML")})
                    continue

                # Limpiar variaciones
                variations_clean = []
                for v in (item.get("variations") or []):
                    vc = {
                        "attribute_combinations": v.get("attribute_combinations", []),
                        "price": v.get("price", item.get("price", 0)),
                        "available_quantity": v.get("available_quantity", 0),
                    }
                    if v.get("picture_ids"):
                        vc["picture_ids"] = v["picture_ids"]
                    variations_clean.append(vc)

                # Atributos a excluir
                EXCLUDED_ATTRS = {
                    "SELLER_SKU","ITEM_CONDITION","ALPHANUMERIC_MODEL","GTIN",
                    "PACKAGE_DATA_SOURCE","RELEASE_YEAR","SYI_PYMES_ID",
                    "FILTRABLE_SIZE","SIZE_GRID_ROW_ID","SIZE_GRID_ID"
                }
                # Extraer BRAND y MODEL primero
                brand_val = next((a.get("value_name","") for a in (item.get("attributes") or []) if a.get("id")=="BRAND"), "")
                model_val = next((a.get("value_name","") for a in (item.get("attributes") or []) if a.get("id")=="MODEL"), "")
                size_val = next((a.get("value_name") or str(a.get("value_id","")) for a in (item.get("attributes") or []) if a.get("id")=="SIZE"), "")
                orig_chart_id = next((a.get("value_name") or a.get("value_id","") for a in (item.get("attributes") or []) if a.get("id")=="SIZE_GRID_ID"), None)
                orig_row_id = next((a.get("value_name","") for a in (item.get("attributes") or []) if a.get("id")=="SIZE_GRID_ROW_ID"), None)

                attrs_clean = []
                for a in (item.get("attributes") or []):
                    aid = a.get("id","")
                    if aid in EXCLUDED_ATTRS: continue
                    if aid in ("BRAND","MODEL"):
                        vn = a.get("value_name")
                        if vn: attrs_clean.append({"id":aid,"value_name":vn})
                        continue
                    if aid == "SIZE":
                        if dest_is_up:
                            # UP: SIZE como value_name
                            if size_val: attrs_clean.append({"id":"SIZE","value_name":size_val})
                        else:
                            if a.get("value_id"): attrs_clean.append({"id":"SIZE","value_id":a["value_id"]})
                            elif a.get("value_name"): attrs_clean.append({"id":"SIZE","value_name":a["value_name"]})
                        continue
                    if dest_is_up:
                        # Para UP: siempre value_name
                        vn = a.get("value_name")
                        if vn:
                            attrs_clean.append({"id": aid, "value_name": vn})
                    else:
                        if a.get("value_id"):
                            attrs_clean.append({"id": aid, "value_id": a["value_id"]})
                        elif a.get("value_name"):
                            attrs_clean.append({"id": aid, "value_name": a["value_name"]})

                # Agregar SIZE_GRID_ID y SIZE_GRID_ROW_ID
                if orig_chart_id:
                    # Para UP: buscar guía en cuenta destino por su ID
                    if dest_is_up:
                        # Obtener domain_id de la categoría
                        cat_domain = ""
                        try:
                            async with httpx.AsyncClient(timeout=10) as cc:
                                dr = await cc.get(f"{ML_API}/categories/{item.get('category_id','')}", 
                                                  headers={"Authorization": f"Bearer {from_t}"})
                                cat_domain = dr.json().get("domain_id","")
                        except Exception:
                            pass
                        dest_chart = await load_dest_chart(str(orig_chart_id), cat_domain, brand_val)
                        if not dest_chart:
                            # Intentar copiar la guía automáticamente
                            dest_chart = await copy_chart_to_dest(str(orig_chart_id), cat_domain)
                        if dest_chart:
                            attrs_clean.append({"id":"SIZE_GRID_ID","value_name":str(dest_chart["chart_id"])})
                            row_id = dest_chart["rows"].get(size_val)
                            if not row_id:
                                # Agregar el talle faltante via POST /catalog/charts/{id}/rows
                                try:
                                    async with httpx.AsyncClient(timeout=20) as cc:
                                        # Leer guía original para copiar el row con todos sus atributos
                                        orig_chart_r = await cc.get(
                                            f"{ML_API}/catalog/charts/{orig_chart_id}",
                                            headers={"Authorization": f"Bearer {from_t}"}
                                        )
                                        orig_row_data = None
                                        if orig_chart_r.status_code == 200:
                                            for r in (orig_chart_r.json().get("rows") or []):
                                                sv = next((v.get("name","") for a in r.get("attributes",[])
                                                           if a.get("id")=="SIZE" for v in a.get("values",[])), "")
                                                if sv == size_val:
                                                    orig_row_data = r
                                                    break
                                        # Armar payload del nuevo row limpiando IDs
                                        if orig_row_data:
                                            new_row_attrs = []
                                            for a in orig_row_data.get("attributes", []):
                                                new_row_attrs.append({"id": a["id"], "values": a.get("values", [])})
                                        else:
                                            new_row_attrs = [{"id": "SIZE", "values": [{"name": size_val}]}]
                                        add_r = await cc.post(
                                            f"{ML_API}/catalog/charts/{dest_chart['chart_id']}/rows",
                                            headers={"Authorization": f"Bearer {to_t}", "Content-Type": "application/json"},
                                            json={"attributes": new_row_attrs}
                                        )
                                        if add_r.status_code in (200, 201):
                                            added = add_r.json()
                                            row_id = added.get("id", "")
                                            if row_id:
                                                dest_chart["rows"][size_val] = row_id
                                except Exception:
                                    pass
                            if row_id:
                                attrs_clean.append({"id":"SIZE_GRID_ROW_ID","value_name":row_id})
                            else:
                                results.append({"id": iid, "title": item.get("title", iid), "ok": False,
                                    "msg": f"⚠️ El talle '{size_val}' no existe en la guía de talles de la cuenta destino.",
                                    "error_type": "missing_size",
                                    "missing_size": size_val,
                                    "chart_id": dest_chart["chart_id"],
                                    "ml_talles_url": "https://www.mercadolibre.com.ar/moda/talles/"})
                                continue
                        else:
                            results.append({"id": iid, "title": item.get("title", iid), "ok": False,
                                "msg": f"⚠️ No hay guía de talles para '{brand_val}' en la cuenta destino. SIZE_GRID_ID origen: {orig_chart_id}",
                                "error_type": "missing_chart",
                                "domain_id": cat_domain,
                                "brand": brand_val,
                                "orig_chart_id": str(orig_chart_id),
                                "ml_talles_url": "https://www.mercadolibre.com.ar/moda/talles/"})
                            continue
                    else:
                        attrs_clean.append({"id":"SIZE_GRID_ID","value_name":str(orig_chart_id)})
                        if orig_row_id:
                            attrs_clean.append({"id":"SIZE_GRID_ROW_ID","value_name":orig_row_id})

                # family_name
                family = f"{brand_val} {model_val}".strip() or item.get("title","")[:60]

                # Título base: cortar después del modelo
                raw_title = item.get("title","")
                if model_val and model_val in raw_title:
                    base_title = raw_title[:raw_title.index(model_val)+len(model_val)].strip()
                elif " - " in raw_title:
                    base_title = raw_title.rsplit(" - ", 1)[0].strip()
                else:
                    base_title = raw_title.strip()

                if dest_is_up:
                    payload = {
                        "family_name": base_title[:60],
                        "category_id": item.get("category_id", ""),
                        "price": item.get("price", 0),
                        "currency_id": item.get("currency_id", "ARS"),
                        "available_quantity": item.get("available_quantity", 0),
                        "listing_type_id": item.get("listing_type_id", "gold_special"),
                        "condition": item.get("condition", "new"),
                        "pictures": [{"source": p["url"].replace("http://","https://")} for p in (item.get("pictures") or [])[:12]],
                        "attributes": attrs_clean,
                    }
                else:
                    payload = {
                        "title": base_title[:60],
                        "category_id": item.get("category_id", ""),
                        "price": item.get("price", 0),
                        "currency_id": item.get("currency_id", "ARS"),
                        "available_quantity": item.get("available_quantity", 0) if not variations_clean else 0,
                        "listing_type_id": item.get("listing_type_id", "gold_special"),
                        "condition": item.get("condition", "new"),
                        "pictures": [{"source": p["url"].replace("http://","https://")} for p in (item.get("pictures") or [])[:12]],
                        "attributes": attrs_clean,
                    }
                    if variations_clean:
                        payload["variations"] = variations_clean
                    if item.get("sale_terms"):
                        payload["sale_terms"] = item["sale_terms"]

                # Gateway maneja retry interno en 429
                r2 = await ml_call(seller_uid=str(ST['accounts'][to_idx].get('uid','')), method="POST",
                    url=f"{ML_API}/items",
                    headers={"Authorization": f"Bearer {to_t}", "Content-Type": "application/json"},
                    priority=PRIORITY_MEDIUM, json_body=payload)

                ok = r2.status_code in (200, 201)

                new_id = None
                if ok:
                    new_id = r2.json().get("id")
                    msg = "Duplicado OK"
                    if status == "paused" and new_id:
                        await c.put(f"{ML_API}/items/{new_id}",
                            headers={"Authorization": f"Bearer {to_t}"},
                            json={"status": "paused"})
                    if auto_link and new_id:
                        # Guardar family_name para poder matchear webhooks de la misma familia
                        fam = item.get("family_name","") or item.get("title","")[:60]
                        link = {"ml_item_id": iid, "ml_acc_idx": from_idx,
                                "tn_product_id": new_id, "tn_acc_idx": to_idx,
                                "auto_linked": True, "ml_title": item.get("title",""),
                                "family_name": fam}
                        if "links" not in ST: ST["links"] = []
                        ST["links"].append(link)
                elif r2.status_code == 429:
                    msg = "Rate limit ML (429) — esperá unos minutos y volvé a intentar"
                else:
                    try:
                        err = r2.json()
                        causes = err.get("cause", [])
                        # Solo mostrar errores reales, ignorar warnings
                        real_errors = [c2 for c2 in causes if c2.get("type") == "error"]
                        msg = ", ".join([c2.get("message", c2.get("code","")) for c2 in real_errors[:3]]) if real_errors else err.get("message", f"Error {r2.status_code}")
                    except Exception:
                        msg = f"Error {r2.status_code}"

                results.append({"id": iid, "title": item.get("title", iid), "ok": ok, "msg": msg, "new_id": new_id})
                ST["log"].append({"ts": int(time.time()), "action": "duplicate", "product": item.get("title", ""), "status": "ok" if ok else "error"})
                await asyncio.sleep(1)
            except Exception as e:
                results.append({"id": iid, "title": iid, "ok": False, "msg": str(e)})
    save_state()
    if "job_id" in dir():
        _dup_jobs.get(job_id, {})  # job_id exists in scope
    return {"results": results, "job_id": locals().get("job_id","")}

@app.post("/api/admin/reset_cache_selectivo")
async def reset_cache_selectivo(_=Depends(auth)):
    """Borra cache de Redis SELECTIVAMENTE.

    Borra:
      - mltn:webhook_dedup:*       (dedups de 60s)
      - mltn:order_pack_dedup:*    (dedups de packs)
      - mltn:order_cache:*         (cache de orders)
      - mltn:gateway:cooldown:*    (cooldowns por 429)
      - mltn:gateway:stats:*       (stats del gateway)
      - mltn:webhook_queue         (cola de webhooks pendientes)
      - mltn:job:*                 (jobs viejos)

    NO BORRA:
      - mltn:afip_factura:*        (facturas AFIP emitidas)
      - mltn:afip_ta:*             (TAs AFIP, expiran solos)
      - mltn:products:*            (cache de productos, sino hay que re-sincronizar)
      - tokens OAuth en state
    """
    r = get_redis()
    if not r:
        return {"ok": False, "error": "Redis no disponible"}

    patrones = [
        "mltn:webhook_dedup:*",
        "mltn:order_pack_dedup:*",
        "mltn:order_cache:*",
        "mltn:gateway:cooldown:*",
        "mltn:gateway:stats:*",
        "mltn:job:*",
    ]
    borrados = {}
    total = 0
    try:
        for patron in patrones:
            # Helper async que corre en thread aparte (no congela el server)
            n = await redis_delete_pattern_async(patron)
            borrados[patron] = n
            total += n
        # Vaciar la cola de webhooks (operacion rapida)
        try:
            qlen = await redis_op_async(lambda rr: rr.llen("mltn:webhook_queue"))
            if qlen and qlen > 0:
                await redis_op_async(lambda rr: rr.delete("mltn:webhook_queue"))
                borrados["mltn:webhook_queue (cola)"] = qlen
                total += qlen
        except Exception:
            pass
        # Limpiar el buffer in-memory del batch de orders tambien
        try:
            _order_batch_buffer.clear()
            _order_batch_processed.clear()
        except Exception:
            pass
        events.ok(f"Reset cache selectivo: {total} claves borradas", source="admin")
        return {"ok": True, "borrados": borrados, "total": total}
    except Exception as e:
        events.err(f"Reset cache error: {str(e)[:120]}", source="admin")
        return {"ok": False, "error": str(e)[:200]}


@app.post("/webhook/ml")
async def webhook_ml(request: Request, background_tasks: BackgroundTasks):
    """MODO EMERGENCIA: Solo responde 200 OK.
    NO procesa nada para evitar 429.

    Los webhooks llegan pero se descartan. El stock NO se actualiza por webhook.
    Para actualizar stock manualmente, usar el boton "Refrescar stock" o esperar
    el cron de las 3 AM.

    Cuando se desee reactivar el procesamiento normal, restaurar el codigo
    original de webhook_ml + _process_webhook_async.
    """
    return {"status": "ok", "emergency_mode": True}


# ─── PROCESAMIENTO DE WEBHOOKS — DESHABILITADO EN MODO EMERGENCIA ─────────
# Las funciones _process_webhook_async, _enqueue_order_for_batch y
# order_batch_worker_loop quedan definidas pero NO se llaman desde el
# endpoint /webhook/ml. El batch worker SIGUE corriendo pero recibe 0
# webhooks, asi que no procesa nada.

# In-memory dedup ultra rapido para responder OK al webhook lo mas rapido posible
_webhook_mem_dedup = {}  # {key: timestamp}
_webhook_mem_dedup_last_clean = [0]


def _webhook_mem_dedup_clean(now: float):
    """Limpia entries viejos del dedup in-memory. Llamada cada >5s."""
    if now - _webhook_mem_dedup_last_clean[0] < 5:
        return
    _webhook_mem_dedup_last_clean[0] = now
    cutoff = now - 60  # 60s TTL
    expired = [k for k, t in _webhook_mem_dedup.items() if t < cutoff]
    for k in expired:
        _webhook_mem_dedup.pop(k, None)


async def _process_webhook_async(topic: str, resource: str, user_id):
    """Procesa el webhook en background. El endpoint ya respondio 200 OK."""
    # ─── DEDUP PERSISTENTE EN REDIS ──────────────────────────────────────
    # Por si el server se reinicia y se pierde el dedup in-memory.
    try:
        if topic and resource and user_id:
            _r = get_redis()
            if _r:
                dedup_key = f"mltn:webhook_dedup:{topic}:{resource}:{user_id}"
                was_set = _r.set(dedup_key, "1", nx=True, ex=60)
                if not was_set:
                    print(f"Webhook duplicado ignorado: topic={topic} resource={resource} user_id={user_id}")
                    return
    except Exception as _e:
        print(f"Webhook dedup error (continuando igual): {_e}")

    print(f"Webhook ML: topic={topic} resource={resource} user_id={user_id}")

    # ─── DISPATCH SEGUN TOPIC ───────────────────────────────────────────
    if topic in ("stock-locations", "stock_locations"):
        if resource and user_id:
            await update_stock_from_webhook(resource, str(user_id))
    elif topic == "items":
        pass  # IGNORADO
    elif topic in ("items_prices",):
        pass  # IGNORADO
    elif topic in ("orders", "orders_v2"):
        if resource and user_id:
            _enqueue_order_for_batch(resource, str(user_id))
    elif topic in ("questions", "messages"):
        pass  # Solo log, no procesar


# ─── BATCH PROCESSOR PARA WEBHOOKS DE ORDERS ─────────────────────────────
# En vez de procesar cada webhook orders_v2 al instante (lo que dispara
# 1-2 requests a ML por cada uno), los acumulamos 30 segundos y procesamos
# en bloque. Una rafaga de 10 ventas en 30s = solo 10 procesamientos
# espaciados, no 10 paralelos.

_order_batch_buffer = []  # list[(resource, user_id_str, ts)]
_order_batch_lock = asyncio.Lock()
_order_batch_processed = set()  # dedup interno por resource en buffer
ORDER_BATCH_INTERVAL = 30  # segundos


def _enqueue_order_for_batch(resource: str, user_id_str: str):
    """Encola un webhook de orden para procesar en el proximo batch."""
    # Dedup en memoria: si ya esta en el buffer, no agregar otra vez
    if resource in _order_batch_processed:
        return
    _order_batch_processed.add(resource)
    _order_batch_buffer.append((resource, user_id_str, time.time()))
    print(f"[batch] Encolado: {resource} (buffer={len(_order_batch_buffer)})")


async def order_batch_worker_loop():
    """Worker que procesa el batch de webhooks de orders cada X segundos.
    Procesa con pacing entre items para no saturar."""
    await asyncio.sleep(10)  # esperar a que el sistema arranque
    while True:
        try:
            await asyncio.sleep(ORDER_BATCH_INTERVAL)
            # Tomar snapshot del buffer y vaciarlo
            async with _order_batch_lock:
                if not _order_batch_buffer:
                    continue
                snapshot = list(_order_batch_buffer)
                _order_batch_buffer.clear()
                _order_batch_processed.clear()
            print(f"[batch] Procesando {len(snapshot)} orden(es) en batch")
            # Procesar uno por uno con pacing
            for resource, uid_str, ts in snapshot:
                try:
                    await refresh_items_from_order(resource, uid_str)
                except Exception as e:
                    print(f"[batch] Error procesando {resource}: {e}")
                # Pacing entre cada uno: 2 segundos
                await asyncio.sleep(2)
            print(f"[batch] Batch completado")
        except Exception as e:
            print(f"[batch] order_batch_worker_loop error: {e}")
            await asyncio.sleep(60)



async def refresh_items_from_order(resource: str, user_id_str: str):
    """Cuando llega una orden nueva, refresca los items que se vendieron.
    Solo actualiza el cache local. Nunca escribe a ML.

    Dedup por pack_id: si ya procesamos una orden de ese pack en los ultimos
    60s, ignoramos (porque toda la info del carrito ya esta actualizada)."""
    try:
        # resource = "/orders/2000016432584836"
        order_id = resource.rstrip("/").split("/")[-1]
        acc_idx = next((j for j,a in enumerate(ST.get("accounts",[])) if str(a.get("uid",""))==user_id_str), -1)
        if acc_idx < 0:
            return

        try:
            token = await fresh_token_stock(acc_idx)
        except Exception as e:
            print(f"refresh_items_from_order token error: {e}")
            return

        hdrs = {"Authorization": f"Bearer {token}"}
        # Pequeño delay para dar tiempo a ML a actualizar stock interno
        await asyncio.sleep(2)

        # Bajar la orden para saber qué items se vendieron Y el pack_id
        try:
            r = await ml_http_request("GET", f"{ML_API}/orders/{order_id}",
                                       headers=hdrs, source_label="webhook",
                                       max_retries=2, timeout=15)
            if r.status_code != 200:
                return
            order = r.json()
        except Exception as e:
            print(f"refresh_items_from_order fetch order error: {e}")
            return

        # ─── DEDUP POR PACK_ID ──────────────────────────────────────────────
        # Si la orden pertenece a un carrito (pack_id), checamos si ya procesamos
        # otro order del mismo pack en los ultimos 60s. Si si, ignoramos.
        pack_id = order.get("pack_id")
        if pack_id:
            _r = get_redis()
            if _r:
                pack_key = f"mltn:order_pack_dedup:{user_id_str}:{pack_id}"
                was_set = _r.set(pack_key, "1", nx=True, ex=60)
                if not was_set:
                    print(f"refresh_items_from_order: pack {pack_id} ya procesado, skip orden {order_id}")
                    return

        # IDs unicos de los items vendidos
        sold_ids = set()
        for oit in (order.get("order_items") or []):
            item = oit.get("item") or {}
            iid = item.get("id")
            if iid:
                sold_ids.add(iid)

        if not sold_ids:
            return

        # Refresh cada item (multi-get en un solo request)
        try:
            ids_csv = ",".join(sorted(sold_ids))
            attrs = "id,price,available_quantity,status,variations"
            rb = await ml_http_request("GET",
                f"{ML_API}/items?ids={ids_csv}&attributes={attrs}",
                headers=hdrs, source_label="webhook",
                max_retries=2, timeout=15)
            if rb.status_code != 200:
                return
            updates = rb.json() if isinstance(rb.json(), list) else []
        except Exception as e:
            print(f"refresh_items_from_order multiget error: {e}")
            return

        # Actualizar cache local
        products = get_cached_products(user_id_str) or []
        if not products:
            return
        prod_map = {p["id"]: p for p in products}
        updated = 0
        for wrap in updates:
            body = wrap.get("body", {}) if isinstance(wrap, dict) else {}
            if wrap.get("code") != 200:
                continue
            iid = body.get("id")
            if not iid or iid not in prod_map:
                continue
            prod_map[iid]["price"] = body.get("price", prod_map[iid].get("price"))
            prod_map[iid]["available_quantity"] = body.get("available_quantity", prod_map[iid].get("available_quantity"))
            prod_map[iid]["status"] = body.get("status", prod_map[iid].get("status"))
            if body.get("variations"):
                var_map = {str(v["id"]): v for v in body["variations"]}
                for v in prod_map[iid].get("variations", []):
                    sv = var_map.get(str(v.get("id")))
                    if sv:
                        v["available_quantity"] = sv.get("available_quantity", v.get("available_quantity"))
            updated += 1

        if updated > 0:
            set_cached_products(user_id_str, list(prod_map.values()))
            pack_msg = f" (pack {pack_id})" if pack_id else ""
            events.info(f"Webhook venta{pack_msg}: actualizado stock de {updated} item(s) de orden {order_id}",
                        source="webhook", seller=user_id_str)
            print(f"refresh_items_from_order {order_id}: {updated} items actualizados en cache")
    except Exception as e:
        print(f"refresh_items_from_order error: {e}")


async def update_stock_from_webhook(resource: str, user_id_str: str):
    """Actualizar stock de un user_product en el cache cuando llega webhook stock-locations"""
    try:
        # resource = /user-products/MLAUXXXXXXX/stock
        # Buscar la cuenta que corresponde a este user_id
        acc_idx = next((j for j,a in enumerate(ST.get("accounts",[])) if str(a.get("uid",""))==user_id_str), -1)
        if acc_idx < 0:
            return
        uid = ST["accounts"][acc_idx].get("uid","")
        # Extraer user_product_id del resource
        import re as _re
        m = _re.search(r'/user-products/([^/]+)/stock', resource)
        if not m:
            return
        upid = m.group(1)
        # Buscar en cache los items con ese user_product_id
        products = get_cached_products(uid) or []
        matching = [p for p in products if p.get("user_product_id") == upid]
        if not matching:
            return
        # Traer stock actualizado de ML
        token = await fresh_token_stock(acc_idx)
        hdrs = {"Authorization": f"Bearer {token}"}
        item_ids = [p["id"] for p in matching]
        r = await ml_call(seller_uid=user_id_str, method="GET",
            url=f"{ML_API}/items?ids={','.join(item_ids)}&attributes=id,available_quantity,price,status",
            headers=hdrs, priority=PRIORITY_HIGH, timeout=15)
        if r.status_code == 200:
            prod_map = {p["id"]: p for p in products}
            for item in r.json():
                if item.get("code") == 200:
                    b = item["body"]
                    iid = b.get("id")
                    if iid in prod_map:
                        prod_map[iid]["available_quantity"] = b.get("available_quantity", 0)
                        prod_map[iid]["price"] = b.get("price", prod_map[iid].get("price", 0))
                        prod_map[iid]["status"] = b.get("status", prod_map[iid].get("status", ""))
            set_cached_products(uid, list(prod_map.values()))
            print(f"Stock webhook: updated {len(item_ids)} items for user_product {upid}")
    except Exception as e:
        print(f"update_stock_from_webhook error: {e}")

async def process_ml_order(resource: str, seller_uid: int):
    """Procesar una orden de ML y sincronizar stock entre cuentas"""
    # Verificar cooldown antes de procesar
    if is_ml_cooling():
        print(f"Webhook orden: ML en cooldown, encolando para despues")
        queue_webhook("orders_v2", resource, str(seller_uid))
        return
    try:
        # Encontrar qué cuenta es
        acc_idx = None
        for i, acc in enumerate(ST.get("accounts", [])):
            if int(acc.get("uid", 0)) == seller_uid:
                acc_idx = i
                break
        if acc_idx is None:
            print(f"Webhook: cuenta {seller_uid} no encontrada")
            return
        # Obtener detalle de la orden
        token = await fresh_token_stock(acc_idx)
        order_id = resource.strip("/").split("/")[-1]
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{ML_API}/orders/{order_id}",
                headers={"Authorization": f"Bearer {token}"})
            if r.status_code != 200:
                print(f"Webhook: error obteniendo orden {order_id}: {r.status_code}")
                return
            order = r.json()
        # Procesar cada item vendido
        for order_item in order.get("order_items", []):
            item_id = order_item.get("item", {}).get("id", "")
            qty_sold = order_item.get("quantity", 0)
            variation_id = order_item.get("item", {}).get("variation_id")
            if not item_id or not qty_sold:
                continue
            print(f"Webhook: item {item_id} vendido x{qty_sold} en cuenta {acc_idx}")
            # Extraer modelo del título
            async with httpx.AsyncClient(timeout=10) as c:
                ri = await c.get(f"{ML_API}/items/{item_id}?attributes=title,attributes",
                    headers={"Authorization": f"Bearer {token}"})
                if ri.status_code != 200:
                    continue
                item_data = ri.json()
            title = item_data.get("title", "")
            model = extract_model(title)
            if not model:
                print(f"Webhook: no se encontró modelo en '{title}'")
                continue
            print(f"Webhook: sincronizando modelo '{model}' x{qty_sold}")
            # Descontar stock en las otras cuentas
            await sync_stock_by_model(model, qty_sold, acc_idx, item_id)
    except Exception as e:
        print(f"Webhook error: {e}")

def extract_model(title: str) -> str:
    """Extraer número de modelo del título (ej: '15228', '2002')"""
    import re
    # Buscar números de 4+ dígitos que parecen modelos
    matches = re.findall(r'\b(\d{4,6})\b', title)
    return matches[0] if matches else ""

async def sync_stock_by_model(model: str, qty_sold: int, sold_acc_idx: int, sold_item_id: str):
    """Descontar stock en todas las cuentas que tienen el mismo modelo"""
    for i, acc in enumerate(ST.get("accounts", [])):
        if i == sold_acc_idx:
            continue  # saltar la cuenta donde se vendió

        try:
            token = await fresh_token(i)
            uid = acc.get("uid", "")
            prods = get_cached_products(uid)
            if not prods:
                continue
            # Buscar items con el mismo modelo
            matching = [p for p in prods if extract_model(p.get("title","")) == model]
            for prod in matching:
                item_id = prod.get("id","")
                current_stock = prod.get("available_quantity", 0)
                new_stock = max(0, current_stock - qty_sold)
                # Actualizar stock en ML
                async with httpx.AsyncClient(timeout=10) as c:
                    r = await c.put(f"{ML_API}/items/{item_id}",
                        headers={"Authorization": f"Bearer {token}",
                                 "Content-Type": "application/json"},
                        json={"available_quantity": new_stock})
                if r.status_code in (200, 201):
                    print(f"Stock sync OK: {item_id} cuenta {i}: {current_stock} -> {new_stock}")
                    # Actualizar cache local
                    prod["available_quantity"] = new_stock
                else:
                    print(f"Stock sync ERROR: {item_id} cuenta {i}: {r.status_code} {r.text[:100]}")
            if matching:
                # Guardar cache actualizado
                set_cached_products(uid, prods)
        except Exception as e:
            print(f"sync_stock_by_model error cuenta {i}: {e}")

async def process_ml_item_change(resource: str, seller_uid: int):
    """Cuando cambia un item en LENCERIA, propagar precio/stock via enlaces"""
    try:
        master_acc = ST.get("accounts", [{}])[0]
        if int(master_acc.get("uid", 0)) != seller_uid:
            return

        item_id = resource.strip("/").split("/")[-1]
        token = await fresh_token(0)

        # Obtener datos actuales del item en LENCERIA
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{ML_API}/items/{item_id}?attributes=title,price,available_quantity,variations",
                headers={"Authorization": f"Bearer {token}"})
            if r.status_code != 200:
                print(f"No se pudo obtener item {item_id}: {r.status_code}")
                return
            item = r.json()

        price = item.get("price", 0)
        stock = item.get("available_quantity", 0)
        title = item.get("title", "")
        model = extract_model(title)
        print(f"Item change: {item_id} '{title[:50]}' modelo='{model}' precio={price} stock={stock}")

        # METODO 1: Buscar por enlaces directos
        links = ST.get("links", [])
        linked = [l for l in links if l.get("ml_item_id") == item_id and l.get("tn_product_id")]
        print(f"  Links directos para {item_id}: {len(linked)}")

        # Si no hay links directos, buscar por modelo o family_name
        if not linked:
            if model:
                family_linked = [l for l in links
                    if extract_model(l.get("ml_title","")) == model
                    and l.get("tn_product_id")]
                if family_linked:
                    print(f"  Links de familia modelo '{model}': {len(family_linked)}")
                    linked = family_linked
            # Si sigue sin links, buscar por family_name del item en ML
            if not linked:
                try:
                    async with httpx.AsyncClient(timeout=10) as c:
                        rf = await c.get(f"{ML_API}/items/{item_id}?attributes=family_name,title",
                            headers={"Authorization": f"Bearer {token}"})
                        if rf.status_code == 200:
                            fam = rf.json().get("family_name","")
                            if fam:
                                fam_linked = [l for l in links
                                    if l.get("family_name") == fam and l.get("tn_product_id")]
                                if not fam_linked:
                                    # Buscar por titulo similar
                                    fam_linked = [l for l in links
                                        if fam in (l.get("family_name","") or l.get("ml_title","")[:60])
                                        and l.get("tn_product_id")]
                                if fam_linked:
                                    print(f"  Links por family_name '{fam[:40]}': {len(fam_linked)}")
                                    linked = fam_linked
                except Exception as e:
                    print(f"  family_name lookup error: {e}")

        # Obtener variantes del item origen para poder matchear
        src_variations = item.get("variations", [])
        print(f"  Item origen tiene {len(src_variations)} variantes")

        for link in linked:
            dest_id = link.get("tn_product_id")
            dest_acc_idx = link.get("tn_acc_idx", link.get("ml_account_index"))
            if dest_acc_idx is None:
                continue
            try:
                dest_token = await fresh_token_stock(dest_acc_idx)
                async with httpx.AsyncClient(timeout=15) as c:
                    # Obtener item destino con sus variantes
                    rd = await c.get(f"{ML_API}/items/{dest_id}?attributes=variations,available_quantity,price",
                        headers={"Authorization": f"Bearer {dest_token}"})
                    if rd.status_code != 200:
                        print(f"  No se pudo obtener destino {dest_id}: {rd.status_code}")
                        continue
                    dest_item = rd.json()
                    dest_variations = dest_item.get("variations", [])

                    if src_variations and dest_variations:
                        # Matchear variantes por atributos (COLOR + SIZE)
                        updated_vars = []
                        for sv in src_variations:
                            sv_attrs = {a["id"]: a.get("value_name","") for a in sv.get("attribute_combinations",[])}
                            sv_stock = sv.get("available_quantity", 0)
                            sv_price = sv.get("price", price)
                            # Buscar variante equivalente en destino
                            for dv in dest_variations:
                                dv_attrs = {a["id"]: a.get("value_name","") for a in dv.get("attribute_combinations",[])}
                                if sv_attrs == dv_attrs:
                                    updated_vars.append({"id": dv["id"], "available_quantity": sv_stock, "price": sv_price})
                                    break
                        if updated_vars:
                            r = await c.put(f"{ML_API}/items/{dest_id}",
                                headers={"Authorization": f"Bearer {dest_token}", "Content-Type": "application/json"},
                                json={"variations": updated_vars})
                            if r.status_code in (200, 201):
                                print(f"  Link sync OK: {dest_id} cuenta {dest_acc_idx} -> {len(updated_vars)} variantes actualizadas")
                            elif "has_bids" in r.text:
                                print(f"  Link sync SKIP: {dest_id} en subasta")
                            else:
                                print(f"  Link sync ERROR: {dest_id}: {r.status_code} {r.text[:150]}")
                        else:
                            print(f"  No se encontraron variantes matching en {dest_id}")
                    else:
                        # Sin variantes — actualizar item directo
                        r = await c.put(f"{ML_API}/items/{dest_id}",
                            headers={"Authorization": f"Bearer {dest_token}", "Content-Type": "application/json"},
                            json={"price": price, "available_quantity": stock})
                        if r.status_code in (200, 201):
                            print(f"  Link sync OK: {dest_id} cuenta {dest_acc_idx} -> precio={price} stock={stock}")
                        elif "has_bids" in r.text:
                            print(f"  Link sync SKIP: {dest_id} en subasta")
                        else:
                            print(f"  Link sync ERROR: {dest_id}: {r.status_code} {r.text[:100]}")
            except Exception as e:
                print(f"  Link sync error {dest_id}: {e}")

        # METODO 2: Fallback por modelo si no hay links
        if not linked and model:
            print(f"  Sin links directos, buscando por modelo '{model}'...")
            for i, acc in enumerate(ST.get("accounts", [])):
                if i == 0:
                    continue
                try:
                    to_token = await fresh_token(i)
                    uid = acc.get("uid", "")
                    prods = get_cached_products(uid) or []
                    matching = [p for p in prods if extract_model(p.get("title","")) == model]
                    print(f"  Cuenta {i} ({acc.get('name','')}): {len(matching)} con modelo '{model}'")
                    for prod in matching:
                        pid = prod.get("id","")
                        async with httpx.AsyncClient(timeout=10) as c:
                            r = await c.put(f"{ML_API}/items/{pid}",
                                headers={"Authorization": f"Bearer {to_token}", "Content-Type": "application/json"},
                                json={"price": price, "available_quantity": stock})
                        if r.status_code in (200, 201):
                            print(f"  Modelo sync OK: {pid} cuenta {i}")
                        elif "has_bids" in r.text:
                            print(f"  Modelo sync SKIP: {pid} en subasta")
                        else:
                            print(f"  Modelo sync ERROR: {pid}: {r.status_code} {r.text[:100]}")
                except Exception as e:
                    print(f"  Modelo sync error cuenta {i}: {e}")
        
        # Propagar a TiendaNube si está conectada
        if ST.get("tn", {}).get("store_id") and ST.get("tn", {}).get("token"):
            await sync_item_to_tn(model, price, stock)
    
    except Exception as e:
        print(f"process_ml_item_change error: {e}")

async def sync_item_to_tn(model: str, price: float, stock: int):
    """Sincronizar precio y stock con TiendaNube por modelo"""
    try:
        tn_store = ST["tn"]["store_id"]
        tn_token = ST["tn"]["token"]
        links = ST.get("links", [])
        # Buscar en cache de productos de LENCERIA items con ese modelo
        master_uid = ST.get("accounts", [{}])[0].get("uid","")
        prods = get_cached_products(master_uid)
        matching_ids = [p["id"] for p in (prods or []) if extract_model(p.get("title","")) == model]
        # Buscar links que correspondan a esos items
        for link in links:
            if link.get("ml_item_id") in matching_ids:
                tn_prod_id = link.get("tn_product_id")
                tn_var_id = link.get("tn_variant_id")
                if not tn_prod_id:
                    continue
                headers = {"Authentication": f"Bearer {tn_token}",
                           "Content-Type": "application/json",
                           "User-Agent": "MLTNSync/1.0"}
                async with httpx.AsyncClient(timeout=10) as c:
                    if tn_var_id:
                        await c.put(f"https://api.tiendanube.com/v1/{tn_store}/products/{tn_prod_id}/variants/{tn_var_id}",
                            headers=headers, json={"price": str(price), "stock": stock})
                    else:
                        await c.put(f"https://api.tiendanube.com/v1/{tn_store}/products/{tn_prod_id}",
                            headers=headers, json={"price": str(price)})
    except Exception as e:
        print(f"sync_item_to_tn error: {e}")


@app.post("/api/ai/analyze")
async def ai_analyze(req: Request, _=Depends(auth)):
    """Analizar imagen con Claude y devolver datos del producto"""
    try:
        b = await req.json()
        image_base64 = b.get("image_base64", "")
        image_type = b.get("image_type", "image/jpeg")
        
        prompt = """Sos un experto en publicaciones de MercadoLibre Argentina, especializado en ropa y lencería.
Analizá esta imagen de un producto y respondé SOLO con JSON válido (sin markdown) con esta estructura:
{
  "tipo_prenda": "descripción del tipo de prenda",
  "titulo_sugerido": "título para ML máximo 60 caracteres, descriptivo y comercial",
  "colores_detectados": ["color1", "color2"],
  "colores_sugeridos": ["otros colores típicos de esta prenda"],
  "talles_sugeridos": ["S/M", "L/XL", "2XL/3XL"],
  "descripcion": "descripción comercial del producto en 2-3 oraciones",
  "genero": "Mujer/Hombre/Unisex",
  "preguntas": ["pregunta1 que necesito hacerle al vendedor para completar la publicación", "pregunta2"],
  "categoria_ml": "categoría sugerida para ML"
}"""

        response = await httpx.AsyncClient(timeout=30).post(
            "https://api.anthropic.com/v1/messages",
            headers={"Content-Type": "application/json", "x-api-key": os.getenv("ANTHROPIC_API_KEY", ""),
                     "anthropic-version": "2023-06-01"},
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1000,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": image_type, "data": image_base64}},
                        {"type": "text", "text": prompt}
                    ]
                }]
            }
        )
        
        data = response.json()
        print(f"Anthropic response status={response.status_code} keys={list(data.keys())}")
        if "error" in data:
            return {"ok": False, "error": f"Anthropic API: {data['error'].get('message', str(data['error']))}"}
        if "content" not in data:
            return {"ok": False, "error": f"Respuesta inesperada: {str(data)[:200]}"}
        text = data["content"][0]["text"].strip()
        import json as json_mod
        clean = text.replace("```json", "").replace("```", "").strip()
        result = json_mod.loads(clean)
        return {"ok": True, "analysis": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/api/ai/analyze_url")
async def ai_analyze_url(req: Request, _=Depends(auth)):
    """Analizar producto desde una URL usando web scraping + Claude"""
    try:
        b = await req.json()
        url = b.get("url", "").strip()
        if not url:
            return {"ok": False, "error": "URL requerida"}
        
        # 1. Detectar si es ML para usar API directamente
        import re
        ml_match = re.search(r'MLA\d+', url)
        if ml_match and "mercadolibre" in url:
            item_id = ml_match.group(0)
            # Usar API de ML con la cuenta 0
            token = await fresh_token(0)
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(f"{ML_API}/items/{item_id}", headers={"Authorization": f"Bearer {token}"})
                dr = await c.get(f"{ML_API}/items/{item_id}/description", headers={"Authorization": f"Bearer {token}"})
            item = r.json()
            desc = dr.json().get("plain_text", "")
            attrs = {a["id"]: a.get("value_name","") for a in (item.get("attributes") or [])}
            result = {
                "titulo_sugerido": item.get("title",""),
                "descripcion": desc or item.get("title",""),
                "precio": item.get("price", 0),
                "colores": [],
                "talles": [],
                "tipo_prenda": attrs.get("ITEM_TYPE",""),
                "genero": attrs.get("GENDER","Mujer"),
                "marca": attrs.get("BRAND",""),
                "modelo": attrs.get("MODEL",""),
                "imagenes": [p.get("url","") for p in (item.get("pictures") or [])],
                "category_id": item.get("category_id",""),
                "fuente": "MercadoLibre API",
                "item_id_original": item_id
            }
            # Extraer colores y talles de variantes
            for v in (item.get("variations") or []):
                for combo in (v.get("attribute_combinations") or []):
                    if combo.get("id") == "COLOR" and combo.get("value_name") not in result["colores"]:
                        result["colores"].append(combo["value_name"])
                    if combo.get("id") == "SIZE" and combo.get("value_name") not in result["talles"]:
                        result["talles"].append(combo["value_name"])
            # Si no hay variantes, buscar en atributos
            if not result["colores"] and attrs.get("COLOR"):
                result["colores"] = [attrs["COLOR"]]
            if not result["talles"] and attrs.get("SIZE"):
                result["talles"] = [attrs["SIZE"]]
            return {"ok": True, "analysis": result, "source": "ml_api"}
        
        # 2. Para otras URLs: scraping + Claude
        ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
        if not ANTHROPIC_KEY:
            return {"ok": False, "error": "ANTHROPIC_API_KEY no configurada"}
        
        # Hacer scraping de la página
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, 
                                      headers={"User-Agent": "Mozilla/5.0 (compatible; MLTNSync/1.0)"}) as c:
            r = await c.get(url)
            html = r.text[:15000]  # Limitar tamaño
        
        prompt = f"""Analizá este HTML de una página de producto de e-commerce y extraé los datos.
URL: {url}
HTML (primeros 15000 chars):
{html}

Respondé SOLO con JSON válido (sin markdown):
{{
  "titulo_sugerido": "título del producto",
  "descripcion": "descripción completa",
  "precio": 0,
  "colores": ["color1", "color2"],
  "talles": ["S/M", "L/XL"],
  "tipo_prenda": "tipo de prenda",
  "genero": "Mujer/Hombre/Unisex",
  "marca": "marca si aparece",
  "modelo": "número de modelo si aparece",
  "imagenes": ["url_imagen1", "url_imagen2"],
  "fuente": "nombre de la tienda"
}}"""

        async with httpx.AsyncClient(timeout=40) as c:
            response = await c.post(
                "https://api.anthropic.com/v1/messages",
                headers={"Content-Type": "application/json", "x-api-key": ANTHROPIC_KEY,
                         "anthropic-version": "2023-06-01"},
                json={"model": "claude-sonnet-4-20250514", "max_tokens": 1000,
                      "messages": [{"role": "user", "content": prompt}]}
            )
        data = response.json()
        if "error" in data:
            return {"ok": False, "error": data["error"].get("message","Error API")}
        text = data["content"][0]["text"].strip().replace("```json","").replace("```","").strip()
        import json as json_mod
        result = json_mod.loads(text)
        return {"ok": True, "analysis": result, "source": "scraping"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/api/ai/chat")
async def ai_chat(req: Request, _=Depends(auth)):
    """Chat con el asistente publicador IA"""
    try:
        b = await req.json()
        messages = b.get("messages", [])
        context = b.get("context", {})
        
        system = f"""Sos un experto publicador de MercadoLibre Argentina con conocimiento profesional de la API y el algoritmo de ML. Tu especialidad es ropa interior, lenceria y pijamas. Ayudas al vendedor a crear publicaciones perfectas paso a paso.

REGLAS CRITICAS DE ML ARGENTINA:
TITULO: Maximo 60 caracteres. Estructura: Tipo de prenda + Marca + Modelo + Caracteristica. Ej: "Pijama Mujer Polar Peluche Tramado 2002". NO poner precio, talle ni color en el titulo. Sin signos de exclamacion ni mayusculas excesivas.

IMAGENES: gold_special REQUIERE minimo 1 imagen obligatoria (desde feb 2026). Recomendado minimo 3 imagenes por variante. Resolucion minima 500px, recomendado 1200x1200. ML rechaza con HTTP 400 si no hay imagenes en gold_special.

ATRIBUTOS OBLIGATORIOS para ropa/lenceria/pijamas (categoria MLA109255 y similares):
- BRAND: marca del producto (ej: "Sin marca", "Generico", o marca real)
- MODEL: numero de modelo (ej: "2002", "10652")
- GENDER: genero ("Mujer", "Hombre", "Unisex")
- COLOR: color de cada variante
- SIZE: talle de cada variante
- SIZE_GRID_ID: ID de la guia de talles (muy importante para ropa, ej: 5127137)
- SEASON: temporada ("Primavera-Verano", "Otono-Invierno", "Todas las estaciones")
- family_name: nombre de la familia de productos (hasta 60 chars) - OBLIGATORIO para agrupar variantes

DIMENSIONES DEL PAQUETE (obligatorio para ME2 envios):
- SELLER_PACKAGE_HEIGHT: alto en cm (ej: "10 cm")
- SELLER_PACKAGE_WIDTH: ancho en cm (ej: "20 cm") 
- SELLER_PACKAGE_LENGTH: largo en cm (ej: "30 cm")
- SELLER_PACKAGE_WEIGHT: peso en gramos (ej: "300 g")

VARIANTES EN ML:
- Cada combinacion talle+color es UN ITEM SEPARADO en el sistema viejo (precio x variante)
- En el sistema nuevo: 1 publicacion con variaciones internas (attribute_combinations)
- Limite: hasta 250 variantes en moda, 100 en otras categorias
- Las variantes acumulan ventas y mejoran el posicionamiento

TIPOS DE PUBLICACION:
- gold_special (Clasica): 13% comision, maxima exposicion, REQUIERE imagen
- gold_pro (Premium): mayor exposicion
- free (Gratuita): sin costo, minima exposicion

FOTOS POR VARIANTE:
- ML pide minimo 3 fotos de calidad por variante/color
- Fondo blanco o neutro para mejor conversion
- Resolucion recomendada: 1200x1200 pixels
- Maximo 10MB por imagen en formato JPEG

CATEGORIA PREDICTOR: Usar /sites/MLA/domain_discovery/search?q=TITULO para predecir categoria automaticamente.

GUIAS DE TALLES (SIZE_GRID_ID) para lenceria/pijamas:
- Cada cuenta ML tiene sus propias guias de talles con IDs diferentes
- Una guia de talles mejora el posicionamiento y reduce devoluciones
- Talles tipicos lenceria Argentina: S/M, M/L, L/XL, XL/2XL, 2XL/3XL, 3XL/4XL

ERRORES COMUNES Y SOLUCIONES:
- "body.invalid_fields": faltan atributos obligatorios o formato incorrecto
- "family_name missing": falta el campo family_name para agrupar variantes
- "requires_picture": publicacion sin imagen en gold_special
- "VALUE_ADDED_TAX/IMPORT_DUTY required": atributos de IVA para responsables inscriptos
- "has_bids": items en subasta no se pueden modificar
- "Cannot update item status:active": item con ofertas activas
- "Rate limit 429": demasiados requests, esperar antes de reintentar
- "Variant values should not be repeated": TiendaNube necesita atributos definidos en el producto

PARA LENCERIA/PIJAMAS especificamente:
- Categorias comunes MLA: MLA109255 (Pijamas Mujer), MLA1430 (Ropa Interior Mujer)
- Material importante: polar, soft, microfibra, algodon, viscosa
- Siempre preguntar: estampado o liso, con o sin puños, con o sin bolsillos
- Temporada: polar/peluche = Otono-Invierno; liviano = Primavera-Verano

CONTEXTO ACTUAL DEL PRODUCTO:
{json.dumps(context, ensure_ascii=False)}

INSTRUCCIONES DE COMPORTAMIENTO:
1. Hace UNA pregunta a la vez, la MAS IMPORTANTE que falta
2. Orden de preguntas: precio > marca/modelo > colores > talles > guia talles > dimensiones > material/descripcion
3. Cuando tengas: titulo, precio, al menos 1 color y 1 talle → deci exactamente "LISTO para publicar" con resumen
4. Si ya tenés lo minimo (titulo+precio+color+talle) SIEMPRE mostras el boton de publicar aunque falten datos opcionales
5. Si el vendedor corrige algo, actualiza el resumen y deci "LISTO para publicar" de nuevo
6. Si hay error de publicacion, explicalo en terminos simples y deci que falta exactamente
7. Nunca preguntes mas de 1 cosa a la vez
8. Respondé en español argentino, tono amigable y directo
9. Sos eficiente: no repites informacion innecesaria, vas al grano"""

        response = await httpx.AsyncClient(timeout=30).post(
            "https://api.anthropic.com/v1/messages",
            headers={"Content-Type": "application/json", "x-api-key": os.getenv("ANTHROPIC_API_KEY", ""),
                     "anthropic-version": "2023-06-01"},
            json={"model": "claude-sonnet-4-20250514", "max_tokens": 500, "system": system, "messages": messages}
        )
        
        data = response.json()
        reply = data["content"][0]["text"]
        return {"ok": True, "reply": reply}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/api/ai/generate_image")
async def ai_generate_image(req: Request, _=Depends(auth)):
    """Generar imagen 21:9 con 3 poses y cortarla en 3 imágenes separadas"""
    try:
        b = await req.json()
        prompt = b.get("prompt", "")
        reference_base64 = b.get("reference_base64", "")
        reference_type = b.get("reference_type", "image/jpeg")
        
        if not GEMINI_API_KEY:
            return {"ok": False, "error": "GEMINI_API_KEY no configurada"}
        
        from google import genai as google_genai
        from google.genai import types as google_types
        import base64 as b64
        import io
        
        client = google_genai.Client(api_key=GEMINI_API_KEY)
        
        # Prompt para imagen 21:9 con 3 poses lado a lado
        full_prompt = prompt + """
IMPORTANTE: Generá UNA SOLA imagen en formato panorámico 21:9 (muy ancha) que contenga exactamente 3 escenas/poses SEPARADAS de la misma modelo con la misma prenda, dispuestas horizontalmente de izquierda a derecha. Cada escena ocupa exactamente 1/3 del ancho total. Las 3 poses deben ser distintas (frente, perfil, sentada o de espalda). Formato final: imagen panorámica 4K 21:9."""
        
        contents = []
        if reference_base64:
            img_bytes = b64.b64decode(reference_base64)
            contents.append(google_types.Part.from_bytes(data=img_bytes, mime_type=reference_type))
        contents.append(full_prompt)
        
        response = client.models.generate_content(
            model="gemini-3.1-flash-image-preview",
            contents=contents,
            config=google_types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"]
            )
        )
        
        # Obtener imagen generada
        img_data = None
        img_mime = "image/png"
        for part in response.candidates[0].content.parts:
            if hasattr(part, 'inline_data') and part.inline_data:
                img_data = part.inline_data.data
                img_mime = part.inline_data.mime_type
                break
        
        if not img_data:
            return {"ok": False, "error": "No se generó imagen"}
        
        # Cortar la imagen 21:9 en 3 partes iguales
        try:
            from PIL import Image as PILImage
            import io
            
            img = PILImage.open(io.BytesIO(img_data))
            width, height = img.size
            third = width // 3
            
            parts_b64 = []
            for i in range(3):
                left = i * third
                right = left + third
                crop = img.crop((left, 0, right, height))
                # Optimizar para ML (max 10MB) — guardar como JPEG calidad 92
                buf = io.BytesIO()
                crop.convert("RGB").save(buf, format="JPEG", quality=92, optimize=True)
                buf.seek(0)
                size_mb = buf.tell() / (1024*1024)
                # Si pesa más de 9MB, bajar calidad
                if size_mb > 9:
                    buf = io.BytesIO()
                    crop.convert("RGB").save(buf, format="JPEG", quality=75, optimize=True)
                    buf.seek(0)
                parts_b64.append(b64.b64encode(buf.read()).decode())
            
            return {"ok": True, "images_base64": parts_b64, "mime_type": "image/jpeg", "count": 3}
        
        except ImportError:
            # Sin PIL, devolver imagen completa
            img_b64 = b64.b64encode(img_data).decode()
            return {"ok": True, "images_base64": [img_b64], "mime_type": img_mime, "count": 1}
    
    except Exception as e:
        return {"ok": False, "error": str(e)}

async def _upload_pic(token: str, img_b64: str) -> str:
    """Subir una foto a ML y devolver el ID"""
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{ML_API}/pictures/items/upload",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"source": f"data:image/jpeg;base64,{img_b64}"})
            if r.status_code in (200,201):
                return r.json().get("id","")
    except: pass
    return ""

# ── ASISTENTE TECNICO INTERNO ─────────────────────────────────────────────────

TECH_SYSTEM = """Sos el asistente tecnico de ML×TN Sync. Diagnosticas problemas y das instrucciones claras.

ARQUITECTURA:
- Backend: FastAPI en Railway, puerto 8000
- Cache: Upstash Redis
- URL: https://mltn-sync-production.up.railway.app

CUENTAS:
- 0: LENCERIAPORMAYORYMENOR (uid 317994166) - MAESTRA
- 1: SHAMPOOSHIR (uid 662105530)
- 2: SHAMPOOAVELLANEDA (uid 1028899469)
- TiendaNube store_id: 825640

LO QUE YA EXISTE Y FUNCIONA:
- Webhooks ML en /webhook/ml - cuando se vende descuenta stock en otras cuentas por modelo (numero en titulo)
- process_ml_item_change - propaga cambios de LENCERIA a las otras cuentas
- Token refresh automatico cada 5 horas
- Duplicador ML-ML y ML-TN
- Sync manual desde la seccion Sincronizar

PROBLEMAS CONOCIDOS Y SOLUCIONES:
- Stock no sincroniza = los webhooks no estan registrados en el panel de desarrolladores de ML. URL correcta: https://mltn-sync-production.up.railway.app/webhook/ml, topicos: orders_v2, items, stock_locations
- "no se encontraron productos" = token vencido, usar Refrescar tokens
- Cache muestra menos que ML = hacer sync completo desde Sincronizar
- "body.invalid_fields [title]" = titulo vacio al publicar con IA
- Rate limit 429 = esperar 60 segundos

MIS CAPACIDADES REALES (ser honesta es critico):
- Diagnosticar con el contexto del sistema que recibo
- Ejecutar: check_ml_tokens, check_tn, check_redis, cache_stats, refresh_tokens
- Dar instrucciones paso a paso

LO QUE NO PUEDO HACER (NUNCA mentir sobre esto):
- NO puedo modificar codigo
- NO puedo hacer deploy a Railway
- NO puedo crear endpoints nuevos
- Si algo requiere cambio de codigo, digo claramente "esto requiere que el desarrollador lo implemente en el codigo"
- NUNCA finjo que estoy desplegando o implementando algo en tiempo real"""

@app.post("/api/links/ai_chat")
async def links_ai_chat(req: Request, _=Depends(auth)):
    """Asistente de enlaces: busca, matchea variantes y propone links"""
    try:
        b = await req.json()
        messages = b.get("messages", [])
        accounts = ST.get("accounts", [])
        existing_links = {l.get("ml_item_id","") for l in ST.get("links", [])}

        last_msg = messages[-1]["content"] if messages else ""

        # 1. Extraer modelo o ID del mensaje
        import re as re_mod
        model_match = re_mod.search(r'\b(\d{4,6})\b', last_msg)
        mla_match = re_mod.search(r'(MLA\d+)', last_msg)
        search_term = mla_match.group(1) if mla_match else (model_match.group(1) if model_match else None)

        # 2. Si no hay termino, usar Claude para extraerlo
        if not search_term:
            ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY","")
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post("https://api.anthropic.com/v1/messages",
                    headers={"Content-Type":"application/json","x-api-key":ANTHROPIC_KEY,"anthropic-version":"2023-06-01"},
                    json={"model":"claude-haiku-4-5-20251001","max_tokens":100,
                          "messages":[{"role":"user","content":
                            f"Del texto: '{last_msg}', extrae SOLO el numero de modelo (4-6 digitos) o ID de MercadoLibre (MLA...). Responde SOLO con el valor, nada mas. Si no hay ninguno, responde NINGUNO."}]})
                if r.status_code == 200:
                    extracted = r.json()["content"][0]["text"].strip()
                    if extracted != "NINGUNO" and extracted:
                        mla2 = re_mod.search(r'(MLA\d+)', extracted)
                        mod2 = re_mod.search(r'\b(\d{4,6})\b', extracted)
                        if mla2: search_term = mla2.group(1)
                        elif mod2: search_term = mod2.group(1)

        if not search_term:
            return {"ok": True, "reply": "No encontre un numero de modelo o ID de ML en tu mensaje. Dime el modelo (ej: 31729) o el ID (ej: MLA1686865537) del producto que queres enlazar.", "proposals": []}

        # 3. Buscar en todas las cuentas
        found = {}  # {acc_idx: [productos]}
        for i, acc in enumerate(accounts):
            uid = acc.get("uid","")
            prods = get_cached_products(uid) or []
            if mla_match or (search_term and search_term.startswith("MLA")):
                matches = [p for p in prods if p.get("id") == search_term]
            else:
                matches = [p for p in prods if extract_model(p.get("title","")) == search_term]
            if matches:
                found[i] = matches

        # Si no se encontro en cache, buscar directamente en ML via API
        if not found:
            print(f"No encontrado en cache, buscando en ML API para '{search_term}'...")
            for i, acc in enumerate(accounts):
                try:
                    token = await fresh_token(i)
                    hdrs = {"Authorization": f"Bearer {token}"}
                    async with httpx.AsyncClient(timeout=15) as c:
                        if search_term.startswith("MLA"):
                            r = await c.get(f"{ML_API}/items/{search_term}?attributes=id,title,available_quantity,variations,attribute_combinations",
                                headers=hdrs)
                            if r.status_code == 200:
                                item = r.json()
                                uid = acc.get("uid","")
                                # Verificar que este item pertenece a esta cuenta
                                seller_id = str(item.get("seller_id",""))
                                if seller_id == str(uid):
                                    # Procesar variaciones
                                    for v in item.get("variations",[]):
                                        v["_attrs"] = {a["id"]: a.get("value_name","") for a in v.get("attribute_combinations",[])}
                                    item["_has_variations"] = len(item.get("variations",[])) > 0
                                    item["_variation_count"] = len(item.get("variations",[]))
                                    found[i] = [item]
                        else:
                            # Buscar por titulo en ML
                            r = await c.get(f"{ML_API}/users/{acc.get('uid','')}/items/search?q={search_term}&limit=5",
                                headers=hdrs)
                            if r.status_code == 200:
                                ids = r.json().get("results",[])
                                if ids:
                                    r2 = await c.get(f"{ML_API}/items?ids={','.join(ids[:5])}&attributes=id,title,available_quantity,variations,seller_id",
                                        headers=hdrs)
                                    if r2.status_code == 200:
                                        for wrap in r2.json():
                                            item = wrap.get("body", wrap) if isinstance(wrap, dict) else {}
                                            if item.get("id") and extract_model(item.get("title","")) == search_term:
                                                for v in item.get("variations",[]):
                                                    v["_attrs"] = {a["id"]: a.get("value_name","") for a in v.get("attribute_combinations",[])}
                                                item["_has_variations"] = len(item.get("variations",[])) > 0
                                                item["_variation_count"] = len(item.get("variations",[]))
                                                if i not in found: found[i] = []
                                                found[i].append(item)
                except Exception as e:
                    print(f"ML API search error cuenta {i}: {e}")

        if not found:
            return {"ok": True, "reply": "No encontre '" + str(search_term) + "' en ninguna cuenta (ni en cache ni en ML directo). Verifica el ID/modelo y que el producto exista en esas cuentas.", "proposals": []}

        acc_names = {i: acc.get("name","ML"+str(i)) for i, acc in enumerate(accounts)}
        found_summary = []
        for i, prods in found.items():
            for p in prods:
                vars_txt = ""
                if p.get("variations"):
                    vars_txt = f" ({len(p['variations'])} variantes)"
                found_summary.append(f"  [{acc_names[i]}] {p['id']}: {p.get('title','')[:50]}{vars_txt}")

        # 4. Generar propuestas de links
        proposals = []
        acc_idxs = sorted(found.keys())

        if len(acc_idxs) < 2:
            only_acc = acc_idxs[0] if acc_idxs else 0
            reply = "Solo encontre el producto en " + str(acc_names.get(only_acc,"una cuenta")) + ":\n" + "\n".join(found_summary)
            reply += "\n\nNo hay producto equivalente en otras cuentas. Primero duplicalo."
            return {"ok": True, "reply": reply, "proposals": []}

        # Usar cuenta 0 (LENCERIA) como origen si está presente, sino la primera
        src_idx = 0 if 0 in found else acc_idxs[0]
        dest_idxs = [i for i in acc_idxs if i != src_idx]

        for src_prod in found[src_idx]:
            src_vars = src_prod.get("variations", [])
            src_id = src_prod["id"]
            src_title = src_prod.get("title","")

            # Ya tiene link? Skipear
            if src_id in existing_links:
                continue

            for dest_idx in dest_idxs:
                for dest_prod in found[dest_idx]:
                    dest_vars = dest_prod.get("variations", [])
                    dest_id = dest_prod["id"]
                    dest_title = dest_prod.get("title","")

                    if src_vars and dest_vars:
                        # Matchear variante por variante por atributos
                        for sv in src_vars:
                            sv_attrs = sv.get("_attrs", {})
                            if not sv_attrs:
                                sv_attrs = {a.get("id",""): a.get("value_name","") for a in sv.get("attribute_combinations",[])}
                            sv_label = " / ".join(v for v in sv_attrs.values() if v)

                            for dv in dest_vars:
                                dv_attrs = dv.get("_attrs", {})
                                if not dv_attrs:
                                    dv_attrs = {a.get("id",""): a.get("value_name","") for a in dv.get("attribute_combinations",[])}
                                dv_label = " / ".join(v for v in dv_attrs.values() if v)

                                # Match si los atributos son iguales
                                if sv_attrs and dv_attrs:
                                    # Comparar COLOR y SIZE
                                    sv_color = sv_attrs.get("COLOR","").lower()
                                    sv_size = sv_attrs.get("SIZE","").lower()
                                    dv_color = dv_attrs.get("COLOR","").lower()
                                    dv_size = dv_attrs.get("SIZE","").lower()
                                    color_match = (not sv_color and not dv_color) or sv_color == dv_color
                                    size_match = (not sv_size and not dv_size) or sv_size == dv_size
                                    if color_match and size_match:
                                        proposals.append({
                                            "src_id": src_id, "src_title": src_title,
                                            "src_acc": acc_names[src_idx], "src_acc_idx": src_idx,
                                            "src_var_id": str(sv["id"]), "src_var": sv_label,
                                            "dest_id": dest_id, "dest_title": dest_title,
                                            "dest_acc": acc_names[dest_idx], "dest_acc_idx": dest_idx,
                                            "dest_var_id": str(dv["id"]), "dest_var": dv_label,
                                        })
                    else:
                        # Sin variantes — enlazar item completo
                        proposals.append({
                            "src_id": src_id, "src_title": src_title,
                            "src_acc": acc_names[src_idx], "src_acc_idx": src_idx,
                            "src_var_id": None, "src_var": None,
                            "dest_id": dest_id, "dest_title": dest_title,
                            "dest_acc": acc_names[dest_idx], "dest_acc_idx": dest_idx,
                            "dest_var_id": None, "dest_var": None,
                        })

        if not proposals:
            reply = "Encontre el producto en " + str(len(acc_idxs)) + " cuentas:\n" + "\n".join(found_summary)
            reply += "\n\nPero no pude generar propuestas automaticas (puede que ya esten todos enlazados o que las variantes no coincidan en COLOR/SIZE). Usa el modal de enlazar manualmente."
            return {"ok": True, "reply": reply, "proposals": []}

        reply = "Encontre " + str(len(proposals)) + " enlaces posibles para el modelo '" + str(search_term) + "':\n\n"
        reply += "\n".join([
            f"  {p['src_acc']} {p.get('src_var','') or ''} → {p['dest_acc']} {p.get('dest_var','') or ''}"
            for p in proposals[:10]
        ])
        if len(proposals) > 10:
            reply += "\n  ... y " + str(len(proposals)-10) + " mas"
        reply += "\n\nConfirma los que quieras enlazar:"
        return {"ok": True, "reply": reply, "proposals": proposals}

    except Exception as e:
        import traceback
        return {"ok": False, "error": str(e), "trace": traceback.format_exc()[:400]}


@app.post("/api/links/suggest")
async def suggest_link(req: Request, _=Depends(auth)):
    """IA: sugerir producto a enlazar por similitud de titulo"""
    try:
        b = await req.json()
        title = b.get("title","")
        item_id = b.get("item_id","")
        dest_acc_idx = int(b.get("dest_acc_idx", 1))

        if dest_acc_idx >= len(ST["accounts"]):
            return {"match_id": None}

        uid = ST["accounts"][dest_acc_idx]["uid"]
        prods = get_cached_products(uid) or []
        if not prods:
            return {"match_id": None, "msg": "Sin productos en cache"}

        # Extraer modelo del titulo origen
        model = extract_model(title)

        # 1. Match exacto por modelo
        if model:
            for p in prods:
                if extract_model(p.get("title","")) == model:
                    return {"match_id": p["id"], "match_title": p.get("title",""), "method": "modelo"}

        # 2. Match por palabras clave del titulo
        title_words = set(w.lower() for w in title.split() if len(w) > 3)
        best_match = None
        best_score = 0
        for p in prods:
            p_words = set(w.lower() for w in (p.get("title","")).split() if len(w) > 3)
            if not p_words: continue
            common = len(title_words & p_words)
            score = common / max(len(title_words), len(p_words), 1)
            if score > best_score:
                best_score = score
                best_match = p

        if best_match and best_score >= 0.4:
            return {"match_id": best_match["id"], "match_title": best_match.get("title",""), "method": "titulo", "score": round(best_score,2)}

        # 3. Usar Claude para match semántico si hay API key
        ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY","")
        if ANTHROPIC_KEY and prods:
            candidates = [{"id": p["id"], "title": p.get("title","")} for p in prods[:50]]
            prompt = f"Titulo origen: '{title}'. Encontra el producto mas similar de esta lista. Responde SOLO con el id, nada mas:\n" + "\n".join(f"{c['id']}: {c['title']}" for c in candidates)
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post("https://api.anthropic.com/v1/messages",
                    headers={"Content-Type":"application/json","x-api-key":ANTHROPIC_KEY,"anthropic-version":"2023-06-01"},
                    json={"model":"claude-haiku-4-5-20251001","max_tokens":50,"messages":[{"role":"user","content":prompt}]})
                if r.status_code == 200:
                    reply = r.json()["content"][0]["text"].strip()
                    matched = next((p for p in prods if p["id"] == reply), None)
                    if matched:
                        return {"match_id": matched["id"], "match_title": matched.get("title",""), "method": "ia"}

        return {"match_id": None, "msg": "Sin coincidencia"}
    except Exception as e:
        return {"match_id": None, "error": str(e)}

@app.get("/api/links/item/{item_id}")
async def links_for_item(item_id: str):
    """Ver todos los links que tienen este item como origen o destino"""
    links = ST.get("links", [])
    as_origin = [l for l in links if l.get("ml_item_id") == item_id]
    as_dest = [l for l in links if l.get("tn_product_id") == item_id]
    return {"item_id": item_id, "as_origin": as_origin, "as_dest": as_dest, "total_links": len(links)}

@app.post("/api/tech/chat")
async def tech_chat(req: Request, _=Depends(auth)):
    """Asistente tecnico con conocimiento del sistema"""
    try:
        b = await req.json()
        messages = b.get("messages", [])
        include_diag = b.get("include_diag", False)

        # Recopilar estado del sistema para dar contexto
        diag_ctx = ""
        if include_diag:
            try:
                # Estado de tokens
                token_status = []
                for i, acc in enumerate(ST.get("accounts", [])):
                    uid = acc.get("uid", "")
                    token_ok = bool(acc.get("token") or acc.get("access_token"))
                    token_exp = acc.get("expiry", 0)
                    token_expired = token_exp > 0 and time.time() > token_exp
                    cached = get_cached_products(uid)
                    cached_count = len(cached) if cached else 0
                    status_raw = get_redis().get(redis_status_key(uid)) if get_redis() else None
                    status = json.loads(status_raw) if status_raw else {}
                    token_status.append(
                        "  - " + acc.get('name','') + ": token=" + ("OK" if token_ok else "FALTA") +
                        (" (VENCIDO)" if token_expired else "") +
                        ", cache=" + str(cached_count) + " productos" +
                        ", sync=" + status.get('status','?') + "/" + str(status.get('total',0))
                    )
                
                tn = ST.get("tn", {})
                redis_ok = bool(get_redis())
                
                diag_ctx = "ESTADO ACTUAL DEL SISTEMA:\nCuentas ML:\n" + "\n".join(token_status) + "\n" +                     "TiendaNube: store_id=" + str(tn.get('store_id','NO CONECTADA')) +                     ", token=" + ("OK" if tn.get('token') else "FALTA") + "\n" +                     "Redis: " + ("CONECTADO" if redis_ok else "SIN CONEXION") + "\n" +                     "Sync corriendo: " + str(list(SYNC_RUNNING.keys()))
            except Exception as e:
                diag_ctx = "Error obteniendo diagnostico: " + str(e)

        system = TECH_SYSTEM
        if diag_ctx:
            system += "\n\n" + diag_ctx

        ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post("https://api.anthropic.com/v1/messages",
                headers={"Content-Type":"application/json","x-api-key":ANTHROPIC_KEY,"anthropic-version":"2023-06-01"},
                json={"model":"claude-sonnet-4-20250514","max_tokens":1000,
                      "system":system,"messages":messages})
        if r.status_code == 200:
            reply = r.json()["content"][0]["text"]
            return {"ok": True, "reply": reply}
        return {"ok": False, "error": f"API error {r.status_code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/api/tech/autofix")
async def tech_autofix(req: Request, _=Depends(auth)):
    """Auto-fix problemas comunes"""
    try:
        b = await req.json()
        action = b.get("action", "")
        results = []

        if action == "refresh_tokens":
            # Refrescar todos los tokens
            for i, acc in enumerate(ST.get("accounts", [])):
                try:
                    await fresh_token(i)
                    results.append({"acc": acc.get("name",""), "ok": True, "msg": "Token refrescado"})
                except Exception as e:
                    results.append({"acc": acc.get("name",""), "ok": False, "msg": str(e)})

        elif action == "check_redis":
            r = get_redis()
            if r:
                try:
                    r.ping()
                    # NO usar r.keys("mltn:*") porque escanea TODO y bloquea el server.
                    # Usamos dbsize() que es O(1) e instantaneo.
                    total_keys = r.dbsize()
                    results.append({"ok": True, "msg": f"Redis OK, {total_keys} keys totales"})
                except Exception as e:
                    results.append({"ok": False, "msg": str(e)})
            else:
                results.append({"ok": False, "msg": "Redis no configurado"})

        elif action == "check_ml_tokens":
            for i, acc in enumerate(ST.get("accounts", [])):
                try:
                    token = await fresh_token(i)
                    async with httpx.AsyncClient(timeout=10) as c:
                        r = await c.get(f"{ML_API}/users/me", headers={"Authorization": f"Bearer {token}"})
                    ok = r.status_code == 200
                    results.append({"acc": acc.get("name",""), "ok": ok, 
                                    "msg": f"UID: {r.json().get('id','?')}" if ok else f"Error {r.status_code}"})
                except Exception as e:
                    results.append({"acc": acc.get("name",""), "ok": False, "msg": str(e)})

        elif action == "check_tn":
            tn = ST.get("tn", {})
            if not tn.get("store_id"):
                results.append({"ok": False, "msg": "TiendaNube no conectada"})
            else:
                try:
                    async with httpx.AsyncClient(timeout=10) as c:
                        r = await c.get(
                            f"https://api.tiendanube.com/v1/{tn['store_id']}/store",
                            headers={"Authentication": f"bearer {tn['token']}",
                                     "User-Agent": "MLTNSync/1.0 (gabysaade9@gmail.com)"})
                    ok = r.status_code == 200
                    results.append({"ok": ok, "msg": f"TN OK: {r.json().get('name','?')}" if ok else f"Error {r.status_code}: {r.text[:100]}"})
                except Exception as e:
                    results.append({"ok": False, "msg": str(e)})

        elif action == "cache_stats":
            for i, acc in enumerate(ST.get("accounts", [])):
                uid = acc.get("uid", "")
                cached = get_cached_products(uid)
                count = len(cached) if cached else 0
                size = 0
                r = get_redis()
                if r:
                    try:
                        raw = r.get(redis_products_key(uid))
                        size = len(raw) if raw else 0
                    except: pass
                results.append({"acc": acc.get("name",""), "cached": count, "size_kb": size//1024})

        return {"ok": True, "results": results}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/ai/debug_publish")
async def debug_publish(req: Request, _=Depends(auth)):
    """Debug: muestra exactamente que llega al backend antes de publicar"""
    try:
        b = await req.json()
        product = b.get("product", {})
        title = (product.get("titulo") or product.get("titulo_sugerido") or "").strip()[:60]
        price = float(product.get("precio") or 0)
        colors = product.get("colores") or []
        sizes = product.get("talles") or product.get("talles_sugeridos") or []
        brand = (product.get("marca") or "Sin marca").strip()
        images_by_color = b.get("images_by_color", {})
        images_base64 = b.get("images_base64", [])
        channels = b.get("channels", [])
        chart_ids = b.get("chart_ids", {})
        return {
            "ok": True,
            "recibido": {
                "title": title,
                "price": price,
                "colors": colors,
                "sizes": sizes,
                "brand": brand,
                "channels": channels,
                "chart_ids": chart_ids,
                "product_keys": list(product.keys()),
                "product_titulo": product.get("titulo"),
                "product_titulo_sugerido": product.get("titulo_sugerido"),
                "total_fotos_globales": len(images_base64),
                "fotos_por_color": {k: len(v) for k,v in images_by_color.items()},
                "dims": product.get("dims"),
            }
        }
    except Exception as e:
        import traceback
        return {"ok": False, "error": str(e), "trace": traceback.format_exc()[:500]}

async def upload_to_imgbb(b64: str) -> str:
    """Subir imagen a imgbb y retornar URL publica. Fallback a base64 si falla."""
    api_key = os.getenv("IMGBB_API_KEY", "")
    if not api_key:
        return ""  # sin key, usar base64
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post("https://api.imgbb.com/1/upload",
                data={"key": api_key, "image": b64, "expiration": 86400})  # 24hs
            if r.status_code == 200:
                return r.json().get("data", {}).get("url", "")
    except:
        pass
    return ""

@app.post("/api/ai/publish_one")
async def ai_publish_one(req: Request, _=Depends(auth)):
    """Publica en UN solo canal - llamar una vez por canal para mostrar progreso"""
    try:
        b = await req.json()
        product    = b.get("product", {})
        channel    = b.get("channel")     # int (ML idx) o "tn"
        images_by_color = b.get("images_by_color", {})
        images_base64   = b.get("images_base64", [])
        chart_id   = b.get("chart_id", "")
        free_ship  = b.get("free_shipping", False)
        pickup     = b.get("pickup", False)
        garantia   = int(b.get("garantia", 90) or 90)
        sync_link  = b.get("sync_link", False)

        title    = (product.get("titulo") or product.get("titulo_sugerido") or "").strip()[:60]
        price    = float(product.get("precio") or 0)
        stock    = int(product.get("stock_por_variante") or product.get("stock") or 100)
        colors   = [c.strip() for c in (product.get("colores") or []) if str(c).strip()]
        sizes    = [s.strip() for s in (product.get("talles") or product.get("talles_sugeridos") or []) if str(s).strip()]
        brand    = (product.get("marca") or "Sin marca").strip()
        modelo   = (product.get("modelo") or "").strip()
        desc     = (product.get("descripcion") or title).strip()
        cat_id   = product.get("category_id") or "MLA109255"
        gender   = product.get("genero") or "Mujer"
        dims     = product.get("dims") or {}

        if not title:
            return {"ok": False, "msg": "Falta el titulo"}
        if price <= 0:
            return {"ok": False, "msg": "Falta el precio"}

        # ── TiendaNube ───────────────────────────────────────────────────────
        if channel == "tn":
            tn = ST.get("tn", {})
            if not tn.get("store_id"):
                return {"ok": False, "msg": "TN no conectada"}
            tn_hdrs = {
                "Authentication": f"bearer {tn['token']}",
                "Content-Type": "application/json",
                "User-Agent": "MLTNSync/1.0 (gabysaade9@gmail.com)"
            }
            tn_variants = []
            seen = set()
            for color in (colors or []):
                for size in (sizes or []):
                    if (color, size) not in seen:
                        seen.add((color, size))
                        vals = []
                        if color: vals.append({"es": color})
                        if size:  vals.append({"es": size})
                        v = {"price": str(price), "stock_management": True, "stock": stock}
                        if vals: v["values"] = vals
                        tn_variants.append(v)
            if not tn_variants:
                tn_variants = [{"price": str(price), "stock_management": True, "stock": stock}]
            all_b64 = []
            for imgs in images_by_color.values(): all_b64.extend(imgs)
            if not all_b64: all_b64 = images_base64
            tn_images = [{"src": f"data:image/jpeg;base64,{img}"} for img in all_b64[:20]]
            tn_attrs = []
            if colors and sizes: tn_attrs = ["Color", "Talle"]
            elif colors:         tn_attrs = ["Color"]
            elif sizes:          tn_attrs = ["Talle"]
            tn_payload = {
                "name": {"es": title}, "description": {"es": desc},
                "published": True, "attributes": tn_attrs,
                "variants": tn_variants, "images": tn_images,
            }
            async with httpx.AsyncClient(timeout=30) as cl:
                r = await cl.post(f"https://api.tiendanube.com/v1/{tn['store_id']}/products",
                    headers=tn_hdrs, json=tn_payload)
            ok = r.status_code in (200, 201)
            try: rb = r.json()
            except: rb = {}
            new_id = str(rb.get("id","")) if ok else ""
            if ok and sync_link and new_id:
                pass  # links se manejan del lado del llamador
            save_state()
            return {"ok": ok, "msg": "Publicado" if ok else rb.get("description", r.text[:200]),
                    "new_id": new_id, "channel": "tn"}

        # ── MercadoLibre ─────────────────────────────────────────────────────
        ch_idx = int(channel)
        token  = await fresh_token(ch_idx)
        ch_name = ST["accounts"][ch_idx].get("name", f"ML{ch_idx}") if ch_idx < len(ST["accounts"]) else f"ML{ch_idx}"

        sale_terms = []
        if garantia:
            sale_terms += [{"id":"WARRANTY_TYPE","value_name":"Garantia del vendedor"},
                           {"id":"WARRANTY_TIME","value_name":f"{garantia} dias"}]
        shipping = {"mode":"me2","local_pick_up":pickup,"free_shipping":free_ship}

        ch_ok = 0; ch_err = ""; new_ids = []
        for color in (colors or [""]):
            b64_list = images_by_color.get(color, []) or images_base64
            pics = [{"source": f"data:image/jpeg;base64,{img}"} for img in b64_list[:5]] if b64_list else []
            for size in (sizes or [""]):
                suffix     = " - ".join(filter(None, [color, size]))
                item_title = f"{title} - {suffix}"[:60] if suffix else title
                attrs = [{"id":"BRAND","value_name":brand},{"id":"GENDER","value_name":gender}]
                if modelo:    attrs.append({"id":"MODEL",    "value_name":modelo})
                if color:     attrs.append({"id":"COLOR",    "value_name":color})
                if size:      attrs.append({"id":"SIZE",     "value_name":size})
                if chart_id:  attrs.append({"id":"SIZE_GRID_ID","value_name":str(chart_id)})
                if dims.get("h"): attrs.append({"id":"SELLER_PACKAGE_HEIGHT","value_name":f"{int(float(dims['h']))} cm"})
                if dims.get("w"): attrs.append({"id":"SELLER_PACKAGE_WIDTH", "value_name":f"{int(float(dims['w']))} cm"})
                if dims.get("l"): attrs.append({"id":"SELLER_PACKAGE_LENGTH","value_name":f"{int(float(dims['l']))} cm"})
                if dims.get("p"): attrs.append({"id":"SELLER_PACKAGE_WEIGHT","value_name":f"{int(float(dims['p'])*1000)} g"})
                family  = f"{brand} {modelo}".strip() or title
                payload = {
                    "title": item_title, "category_id": cat_id,
                    "price": price, "currency_id": "ARS",
                    "available_quantity": stock, "buying_mode": "buy_it_now",
                    "listing_type_id": "gold_special", "condition": "new",
                    "pictures": pics, "attributes": attrs,
                    "family_name": family[:60], "shipping": shipping, "sale_terms": sale_terms,
                }
                await asyncio.sleep(1)
                resp = None
                for attempt in range(3):
                    try:
                        async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15)) as cl:
                            resp = await cl.post(f"{ML_API}/items",
                                headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},
                                json=payload)
                    except httpx.TimeoutException:
                        if attempt < 2: await asyncio.sleep(5); continue
                        break
                    if resp.status_code == 429: await asyncio.sleep((attempt+1)*10); continue
                    break
                ok_item = resp.status_code in (200,201) if resp else False
                try: rb = resp.json() if resp else {}
                except: rb = {}
                if ok_item:
                    ch_ok += 1
                    new_id = rb.get("id","")
                    new_ids.append(new_id)
                    if sync_link and new_id:
                        ST["links"].append({
                            "ml_item_id": new_id, "ml_account_index": ch_idx,
                            "ml_account_name": ch_name, "ml_title": item_title,
                            "created_at": int(time.time())
                        })
                else:
                    cause = rb.get("cause",[])
                    ch_err = cause[0].get("message","") if cause else rb.get("message","Error")
                    print(f"ML error {resp.status_code if resp else '?'}: {json.dumps(rb)[:300]}")

        total = max(1,len(colors or [""]))*max(1,len(sizes or [""]))
        save_state()
        return {"ok": ch_ok>0,
                "msg": f"{ch_ok}/{total} items publicados" if ch_ok>0 else ch_err,
                "new_ids": new_ids, "channel": ch_name}
    except Exception as e:
        import traceback
        return {"ok": False, "msg": str(e), "trace": traceback.format_exc()[:300]}

@app.post("/api/ai/publish_product")
async def ai_publish_product(req: Request, _=Depends(auth)):
    try:
        b = await req.json()
        product    = b.get("product", {})
        channels   = b.get("channels", [])
        images_by_color = b.get("images_by_color", {})   # {color: [b64, ...]}
        images_base64   = b.get("images_base64", [])      # fallback global
        sync_ml    = b.get("sync_ml", False)
        sync_tn    = b.get("sync_tn", False)
        chart_ids  = b.get("chart_ids", {})               # {acc_idx_str: chart_id}
        free_ship  = b.get("free_shipping", False)
        pickup     = b.get("pickup", False)
        garantia   = int(b.get("garantia", 90) or 90)

        # ── campos editables ──────────────────────────────────────────────────
        title    = (product.get("titulo") or product.get("titulo_sugerido") or "").strip()[:60]
        price    = float(product.get("precio") or 0)
        stock    = int(product.get("stock_por_variante") or product.get("stock") or 100)
        colors   = [c.strip() for c in (product.get("colores") or []) if str(c).strip()]
        sizes    = [s.strip() for s in (product.get("talles") or product.get("talles_sugeridos") or []) if str(s).strip()]
        brand    = (product.get("marca") or "Sin marca").strip()
        modelo   = (product.get("modelo") or "").strip()
        desc     = (product.get("descripcion") or title).strip()
        cat_id   = product.get("category_id") or "MLA109255"
        gender   = product.get("genero") or "Mujer"
        dims     = product.get("dims") or {}

        print(f"AI publish → title={repr(title)} price={price} colors={colors} sizes={sizes}")

        if not title:
            return {"ok": False, "error": "Falta el titulo. Escribilo en el campo antes de publicar."}
        if price <= 0:
            return {"ok": False, "error": "Falta el precio."}

        ml_idxs  = [c for c in channels if isinstance(c, int)]
        results  = []

        # ── PUBLICAR EN ML ────────────────────────────────────────────────────
        for ch_idx in ml_idxs:
            try:
                token     = await fresh_token(ch_idx)
                chart_id  = chart_ids.get(str(ch_idx)) or chart_ids.get(ch_idx) or ""
                ch_name   = ST["accounts"][ch_idx].get("name", f"ML{ch_idx}") if ch_idx < len(ST["accounts"]) else f"ML{ch_idx}"
                ch_ok     = 0
                ch_err    = ""
                new_ids   = []

                # garantia
                sale_terms = []
                if garantia:
                    sale_terms += [
                        {"id": "WARRANTY_TYPE", "value_name": "Garantia del vendedor"},
                        {"id": "WARRANTY_TIME", "value_name": f"{garantia} dias"},
                    ]

                # shipping
                shipping = {"mode": "me2", "local_pick_up": pickup, "free_shipping": free_ship}

                for color in (colors or [""]):
                    # fotos de este color como source (igual que duplicador)
                    b64_list = images_by_color.get(color, []) or images_base64
                    pics = [{"source": f"data:image/jpeg;base64,{img}"} for img in b64_list[:12]] if b64_list else []

                    for size in (sizes or [""]):
                        suffix     = " - ".join(filter(None, [color, size]))
                        item_title = f"{title} - {suffix}"[:60] if suffix else title

                        attrs = [{"id": "BRAND", "value_name": brand},
                                 {"id": "GENDER", "value_name": gender}]
                        if modelo:  attrs.append({"id": "MODEL",   "value_name": modelo})
                        if color:   attrs.append({"id": "COLOR",   "value_name": color})
                        if size:    attrs.append({"id": "SIZE",    "value_name": size})
                        if chart_id:attrs.append({"id": "SIZE_GRID_ID", "value_name": str(chart_id)})
                        if dims.get("h"): attrs.append({"id": "SELLER_PACKAGE_HEIGHT", "value_name": f"{int(float(dims['h']))} cm"})
                        if dims.get("w"): attrs.append({"id": "SELLER_PACKAGE_WIDTH",  "value_name": f"{int(float(dims['w']))} cm"})
                        if dims.get("l"): attrs.append({"id": "SELLER_PACKAGE_LENGTH", "value_name": f"{int(float(dims['l']))} cm"})
                        if dims.get("p"): attrs.append({"id": "SELLER_PACKAGE_WEIGHT", "value_name": f"{int(float(dims['p'])*1000)} g"})

                        family = f"{brand} {modelo}".strip() or title

                        print(f"POSTING item_title={repr(item_title)} cat={cat_id} price={price} pics={len(pics)}")
                        payload = {
                            "title":              item_title,
                            "category_id":        cat_id,
                            "price":              price,
                            "currency_id":        "ARS",
                            "available_quantity": stock,
                            "buying_mode":        "buy_it_now",
                            "listing_type_id":    "gold_special",
                            "condition":          "new",
                            "pictures":           pics,
                            "attributes":         attrs,
                            "family_name":        family[:60],
                            "shipping":           shipping,
                            "sale_terms":         sale_terms,
                        }

                        await asyncio.sleep(1)
                        resp = None
                        for attempt in range(3):
                            try:
                                async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15)) as cl:
                                    resp = await cl.post(f"{ML_API}/items",
                                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                                        json=payload)
                            except httpx.TimeoutException:
                                print(f"Timeout attempt {attempt+1}")
                                if attempt < 2: await asyncio.sleep(5); continue
                                break
                            if resp.status_code == 429:
                                await asyncio.sleep((attempt+1)*10)
                                continue
                            break

                        ok_item = resp.status_code in (200, 201) if resp else False
                        try: rb = resp.json() if resp else {}
                        except: rb = {}
                        if ok_item:
                            ch_ok += 1
                            new_id = rb.get("id", "")
                            new_ids.append(new_id)
                            if sync_ml and new_id:
                                ST["links"].append({
                                    "ml_item_id": new_id, "ml_account_index": ch_idx,
                                    "ml_account_name": ch_name, "ml_title": item_title,
                                    "created_at": int(time.time())
                                })
                        else:
                            cause = rb.get("cause", [])
                            ch_err = cause[0].get("message","") if cause else rb.get("message","Error")
                            print(f"ML error {resp.status_code if resp else '?'}: {json.dumps(rb)[:300]}")

                total = max(1, len(colors or [""])) * max(1, len(sizes or [""]))
                results.append({
                    "channel": ch_name, "ok": ch_ok > 0,
                    "msg": f"{ch_ok}/{total} items publicados" if ch_ok > 0 else ch_err,
                    "new_ids": new_ids
                })
            except Exception as e:
                results.append({"channel": f"ML{ch_idx}", "ok": False, "msg": str(e)})

        # ── PUBLICAR EN TiendaNube ────────────────────────────────────────────
        if "tn" in channels:
            try:
                tn = ST.get("tn", {})
                if not tn.get("store_id"):
                    results.append({"channel": "TiendaNube", "ok": False, "msg": "TN no conectada"})
                else:
                    tn_hdrs = {
                        "Authentication": f"bearer {tn['token']}",
                        "Content-Type": "application/json",
                        "User-Agent": "MLTNSync/1.0 (gabysaade9@gmail.com)"
                    }
                    tn_variants = []
                    seen = set()
                    for color in (colors or []):
                        for size in (sizes or []):
                            key = (color, size)
                            if key not in seen:
                                seen.add(key)
                                vals = []
                                if color: vals.append({"es": color})
                                if size:  vals.append({"es": size})
                                v = {"price": str(price), "stock_management": True, "stock": stock}
                                if vals: v["values"] = vals
                                tn_variants.append(v)
                    if not tn_variants:
                        tn_variants = [{"price": str(price), "stock_management": True, "stock": stock}]

                    all_b64 = []
                    for imgs in images_by_color.values(): all_b64.extend(imgs)
                    if not all_b64: all_b64 = images_base64
                    tn_images = [{"src": f"data:image/jpeg;base64,{img}"} for img in all_b64[:20]]

                    tn_attrs = []
                    if colors and sizes: tn_attrs = ["Color", "Talle"]
                    elif colors:         tn_attrs = ["Color"]
                    elif sizes:          tn_attrs = ["Talle"]

                    tn_payload = {
                        "name":        {"es": title},
                        "description": {"es": desc},
                        "published":   True,
                        "attributes":  tn_attrs,
                        "variants":    tn_variants,
                        "images":      tn_images,
                    }
                    async with httpx.AsyncClient(timeout=30) as cl:
                        r = await cl.post(
                            f"https://api.tiendanube.com/v1/{tn['store_id']}/products",
                            headers=tn_hdrs, json=tn_payload)
                    ok_tn = r.status_code in (200, 201)
                    try: rb_tn = r.json()
                    except: rb_tn = {}
                    tn_new_id = str(rb_tn.get("id", "")) if ok_tn else ""
                    if ok_tn and sync_tn and tn_new_id:
                        for ml_res in results:
                            for mid in (ml_res.get("new_ids") or []):
                                ST["links"].append({
                                    "ml_item_id": mid, "ml_title": title,
                                    "tn_product_id": tn_new_id, "created_at": int(time.time())
                                })
                    results.append({
                        "channel": "TiendaNube", "ok": ok_tn,
                        "msg": "Publicado" if ok_tn else rb_tn.get("description", r.text[:200] if not ok_tn else ""),
                        "new_ids": [tn_new_id] if tn_new_id else []
                    })
            except Exception as e:
                results.append({"channel": "TiendaNube", "ok": False, "msg": str(e)})

        save_state()
        return {"ok": True, "results": results}
    except Exception as e:
        import traceback
        return {"ok": False, "error": str(e), "trace": traceback.format_exc()[:500]}


@app.post("/api/duplicate/with_chart")
async def dup_with_chart(request: Request, _=Depends(auth)):
    """Guardar override de guía de talles para el duplicador"""
    b = await request.json()
    chart_override = b.get("chart_override", {})
    r = get_redis()
    if r and chart_override:
        r.set("mltn:chart_override", json.dumps(chart_override), ex=3600)
    return {"ok": True}


@app.get("/diag/charts_search")
async def diag_charts_search(domain: str = "BRAS", brand: str = "Maxima", _=Depends(auth)):
    """Buscar guías de talles en cuenta destino (índice 1)"""
    try:
        to_t = await fresh_token(1)
        async with httpx.AsyncClient(timeout=15) as c:
            me_r = await c.get(f"{ML_API}/users/me", headers={"Authorization": f"Bearer {to_t}"})
            to_uid = me_r.json().get("id","")
            r = await c.post(f"{ML_API}/catalog/charts/search",
                headers={"Authorization": f"Bearer {to_t}", "Content-Type": "application/json"},
                json={"site_id":"MLA","seller_id": to_uid, "domain_id": domain,
                      "attributes":[
                          {"id":"GENDER","values":[{"name":"Mujer"}]},
                          {"id":"BRAND","values":[{"name":brand}]}
                      ]})
            return {"uid": to_uid, "domain": domain, "status": r.status_code, "body": r.json()}
    except Exception as e:
        return {"exception": str(e)}

@app.get("/diag/copy_chart/{chart_id}")
async def diag_copy_chart(chart_id: str, _=Depends(auth)):
    """Leer guía con token del dueño"""
    try:
        from_t = await fresh_token(0)
        to_t = await fresh_token(1)
        t2 = await fresh_token(2)
        async with httpx.AsyncClient(timeout=30) as c:
            r0 = await c.get(f"{ML_API}/catalog/charts/{chart_id}", headers={"Authorization": f"Bearer {from_t}"})
            r1 = await c.get(f"{ML_API}/catalog/charts/{chart_id}", headers={"Authorization": f"Bearer {to_t}"})
            r2 = await c.get(f"{ML_API}/catalog/charts/{chart_id}", headers={"Authorization": f"Bearer {t2}"})
            return {
                "with_account_0": {"status": r0.status_code, "chart": r0.json()},
                "with_account_1": {"status": r1.status_code, "chart": r1.json()},
                "with_account_2": {"status": r2.status_code, "chart": r2.json()},
            }
    except Exception as e:
        return {"exception": str(e)}

@app.get("/diag/chart_rows/{chart_id}")
async def diag_chart_rows(chart_id: str, acc: int = 2, _=Depends(auth)):
    """Ver rows de una guía de talles con cuenta específica"""
    try:
        t = await fresh_token(acc)
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{ML_API}/catalog/charts/{chart_id}", headers={"Authorization": f"Bearer {t}"})
            if r.status_code != 200:
                return {"error": r.status_code, "body": r.text}
            chart = r.json()
            rows = []
            for row in (chart.get("rows") or []):
                rid = row.get("id", "")
                size_val = next((v.get("name","") for a in row.get("attributes",[])
                                 if a.get("id")=="SIZE" for v in a.get("values",[])), "")
                rows.append({"row_id": rid, "size": size_val, "raw": row})
            return {"chart_id": chart_id, "name": chart.get("names"), "domain": chart.get("domain_id"), "rows": rows}
    except Exception as e:
        return {"exception": str(e)}

@app.get("/diag/add_row/{chart_id}/{size_val}")
async def diag_add_row(chart_id: str, size_val: str, acc: int = 0, _=Depends(auth)):
    """Probar agregar un talle a una guía de talles"""
    try:
        t = await fresh_token(acc)
        async with httpx.AsyncClient(timeout=20) as c:
            # Intentar agregar el row
            r = await c.post(
                f"{ML_API}/catalog/charts/{chart_id}/rows",
                headers={"Authorization": f"Bearer {t}", "Content-Type": "application/json"},
                json={"attributes": [{"id": "SIZE", "values": [{"name": size_val}]}]}
            )
            return {
                "chart_id": chart_id,
                "size_val": size_val,
                "acc": acc,
                "status": r.status_code,
                "response": r.json() if r.content else {}
            }
    except Exception as e:
        return {"exception": str(e)}


@app.post("/api/duplicate/create_chart")
async def dup_create_chart(request: Request, _=Depends(auth)):
    """Crear guía de talles en cuenta destino copiando desde cuenta origen"""
    b = await request.json()
    orig_chart_id = b.get("orig_chart_id","")
    domain_id = b.get("domain_id","")
    brand = b.get("brand","")
    to_account = int(b.get("to_account", 1))
    try:
        from_t = await fresh_token(0)  # cuenta principal
        to_t = await fresh_token(to_account)
        async with httpx.AsyncClient(timeout=30) as c:
            # Leer guía original
            r = await c.get(f"{ML_API}/catalog/charts/{orig_chart_id}",
                           headers={"Authorization": f"Bearer {from_t}"})
            if r.status_code != 200:
                return {"ok": False, "msg": f"No se pudo leer la guía origen: {r.status_code}"}
            orig = r.json()
            # Crear en destino
            new_chart = {
                "names": orig.get("names", {"MLA": "Guía de talles"}),
                "domain_id": orig.get("domain_id") or domain_id,
                "site_id": "MLA",
                "main_attribute": {"attributes": [{"site_id": "MLA", "id": orig.get("main_attribute_id", "SIZE")}]},
                "attributes": orig.get("attributes", []),
                "rows": [{"attributes": r2.get("attributes",[])} for r2 in (orig.get("rows") or [])]
            }
            if orig.get("measure_type"):
                new_chart["measure_type"] = orig["measure_type"]
            r2 = await c.post(f"{ML_API}/catalog/charts",
                             headers={"Authorization": f"Bearer {to_t}", "Content-Type": "application/json"},
                             json=new_chart)
            if r2.status_code in (200, 201):
                new_id = r2.json().get("id","")
                return {"ok": True, "msg": f"Guía creada con ID {new_id}", "chart_id": new_id}
            return {"ok": False, "msg": f"Error al crear guía: {r2.status_code} — {r2.text[:200]}"}
    except Exception as e:
        return {"ok": False, "msg": str(e)}

@app.post("/api/duplicate/add_size")
async def dup_add_size(request: Request, _=Depends(auth)):
    """Agregar talle faltante a guía de talles en cuenta destino"""
    b = await request.json()
    chart_id = b.get("chart_id","")
    size_val = b.get("size_val","")
    to_account = int(b.get("to_account", 1))
    try:
        to_t = await fresh_token(to_account)
        # Leer guía completa para obtener atributos requeridos de otros rows
        async with httpx.AsyncClient(timeout=20) as c:
            gr = await c.get(f"{ML_API}/catalog/charts/{chart_id}",
                            headers={"Authorization": f"Bearer {to_t}"})
            if gr.status_code != 200:
                return {"ok": False, "msg": f"No se pudo leer la guía: {gr.status_code}"}
            chart_data = gr.json()
            # Copiar estructura de atributos del último row existente y cambiar SIZE
            existing_rows = chart_data.get("rows", [])
            if not existing_rows:
                return {"ok": False, "msg": "La guía no tiene rows existentes para copiar estructura"}
            # Usar el último row como template
            template = existing_rows[-1].get("attributes", [])
            new_attrs = []
            for a in template:
                if a.get("id") == "SIZE":
                    new_attrs.append({"id": "SIZE", "values": [{"name": size_val}]})
                elif a.get("id") == "FILTRABLE_SIZE":
                    # Mantener las equivalencias del template
                    new_attrs.append({"id": "FILTRABLE_SIZE", "values": a.get("values", [])})
                else:
                    # Incrementar levemente las medidas para evitar duplicados
                    vals = a.get("values", [])
                    new_vals = []
                    for v in vals:
                        if v.get("struct"):
                            new_num = v["struct"]["number"] + 5
                            new_vals.append({"name": f"{new_num} {v['struct']['unit']}", "struct": {"number": new_num, "unit": v["struct"]["unit"]}})
                        else:
                            new_vals.append(v)
                    new_attrs.append({"id": a["id"], "values": new_vals})
            add_r = await c.post(f"{ML_API}/catalog/charts/{chart_id}/rows",
                                headers={"Authorization": f"Bearer {to_t}", "Content-Type": "application/json"},
                                json={"attributes": new_attrs})
            if add_r.status_code in (200, 201):
                new_row_id = add_r.json().get("id","")
                return {"ok": True, "msg": f"Talle '{size_val}' agregado", "row_id": new_row_id}
            return {"ok": False, "msg": f"Error: {add_r.status_code} — {add_r.text[:300]}"}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


@app.get("/diag/create_pijama_chart")
async def diag_create_pijama_chart(acc: int = 0, _=Depends(auth)):
    """Crear guía de talles de pijamas con XL-2XL y 3XL-4XL en cuenta destino"""
    try:
        t = await fresh_token(acc)
        async with httpx.AsyncClient(timeout=30) as c:
            payload = {
                "names": {"MLA": "Pijamas Mujer Talles Grandes"},
                "domain_id": "PAJAMAS",
                "site_id": "MLA",
                "main_attribute": {"attributes": [{"site_id": "MLA", "id": "SIZE"}]},
                "attributes": [{"id": "GENDER", "values": [{"id": "339665", "name": "Mujer"}]}],
                "rows": [
                    {"attributes": [
                        {"id": "SIZE", "values": [{"name": "XL-2XL"}]},
                        {"id": "FILTRABLE_SIZE", "values": [{"id": "12917787", "name": "XL"}, {"id": "12917846", "name": "2XL"}]},
                        {"id": "GARMENT_CHEST_WIDTH_FROM", "values": [{"name": "55 cm", "struct": {"number": 55.0, "unit": "cm"}}]},
                        {"id": "GARMENT_CHEST_WIDTH_TO", "values": [{"name": "65 cm", "struct": {"number": 65.0, "unit": "cm"}}]},
                        {"id": "GARMENT_HIP_WIDTH_FROM", "values": [{"name": "55 cm", "struct": {"number": 55.0, "unit": "cm"}}]},
                        {"id": "GARMENT_HIP_WIDTH_TO", "values": [{"name": "65 cm", "struct": {"number": 65.0, "unit": "cm"}}]},
                    ]},
                    {"attributes": [
                        {"id": "SIZE", "values": [{"name": "3XL-4XL"}]},
                        {"id": "FILTRABLE_SIZE", "values": [{"id": "12917837", "name": "3XL"}, {"id": "12918373", "name": "4XL"}]},
                        {"id": "GARMENT_CHEST_WIDTH_FROM", "values": [{"name": "66 cm", "struct": {"number": 66.0, "unit": "cm"}}]},
                        {"id": "GARMENT_CHEST_WIDTH_TO", "values": [{"name": "80 cm", "struct": {"number": 80.0, "unit": "cm"}}]},
                        {"id": "GARMENT_HIP_WIDTH_FROM", "values": [{"name": "66 cm", "struct": {"number": 66.0, "unit": "cm"}}]},
                        {"id": "GARMENT_HIP_WIDTH_TO", "values": [{"name": "80 cm", "struct": {"number": 80.0, "unit": "cm"}}]},
                    ]}
                ]
            }
            r = await c.post(f"{ML_API}/catalog/charts",
                            headers={"Authorization": f"Bearer {t}", "Content-Type": "application/json"},
                            json=payload)
            return {"status": r.status_code, "response": r.json()}
    except Exception as e:
        return {"exception": str(e)}


@app.get("/diag/sizecharts_cat/{category_id}")
async def diag_sizecharts_cat(category_id: str, brand: str = "", gender_id: str = "", _=Depends(auth)):
    """Buscar guía de talles por marca como hace Astroselling"""
    try:
        from_t = await fresh_token(0)
        async with httpx.AsyncClient(timeout=30) as c:
            results = {}
            endpoints = [
                f"/size_charts/search?q={brand}&category_id={category_id}",
                f"/size_charts/search?brand={brand}&category_id={category_id}",
                f"/size_charts/search?q={brand}",
                f"/size_charts?q={brand}&category_id={category_id}",
                f"/size_charts?brand_name={brand}&category_id={category_id}",
            ]
            for ep in endpoints:
                r = await c.get(f"{ML_API}{ep}",
                    headers={"Authorization": f"Bearer {from_t}"})
                results[ep] = {"status": r.status_code, "body": r.text[:500]}
            return {"category_id": category_id, "brand": brand, "results": results}
    except Exception as e:
        return {"exception": str(e)}


async def diag_sizechart(item_id: str):
    if len(ST["accounts"]) < 2:
        return {"error": "Necesitás 2 cuentas"}
    try:
        from_t = await fresh_token(0)
        to_t = await fresh_token(1)
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{ML_API}/items/{item_id}", headers={"Authorization": f"Bearer {from_t}"})
            item = r.json()
            cat_id = item.get("category_id","")
            size_attr = next((a for a in (item.get("attributes") or []) if a.get("id")=="SIZE_GRID_ID"), None)
            chart_id = size_attr.get("value_name") or size_attr.get("value_id") if size_attr else None
            to_uid_r = await c.get(f"{ML_API}/users/me", headers={"Authorization": f"Bearer {to_t}"})
            to_uid = to_uid_r.json().get("id")
            results = {}
            endpoints = [
                f"/size_charts/{chart_id}",
                f"/size_charts/search?category_id={cat_id}",
                f"/users/{to_uid}/size_charts",
                f"/size_charts?category_id={cat_id}&seller_id={to_uid}",
                f"/size_charts?seller_id={to_uid}",
            ]
            for ep in endpoints:
                r2 = await c.get(f"{ML_API}{ep}", headers={"Authorization": f"Bearer {to_t}"})
                results[ep] = {"status": r2.status_code, "body": r2.text[:300]}
            return {"category_id": cat_id, "chart_id": chart_id, "dest_uid": to_uid, "results": results}
    except Exception as e:
        return {"exception": str(e)}

@app.get("/diag/testdup/{item_id}")
async def diag_testdup(item_id: str, _=Depends(auth)):
    if len(ST["accounts"]) < 2:
        return {"error": "Necesitás 2 cuentas"}
    try:
        from_t = await fresh_token(0)
        to_t = await fresh_token(1)
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{ML_API}/items/{item_id}", headers={"Authorization": f"Bearer {from_t}"})
            item = r.json()
            if "title" not in item:
                return {"error": "ML no devolvió el item", "http_status": r.status_code, "response": item}
            SKIP = {"SELLER_SKU","ITEM_CONDITION","ALPHANUMERIC_MODEL","GTIN",
                    "PACKAGE_DATA_SOURCE","RELEASE_YEAR","SYI_PYMES_ID",
                    "FILTRABLE_SIZE","SIZE_GRID_ROW_ID","SIZE_GRID_ID"}
            attrs = []
            brand_name = ""
            model_name = ""
            # Primero extraer BRAND y MODEL
            for a in (item.get("attributes") or []):
                if a.get("id") == "BRAND": brand_name = a.get("value_name","")
                if a.get("id") == "MODEL": model_name = a.get("value_name","")
            # Armar atributos
            for a in (item.get("attributes") or []):
                aid = a.get("id","")
                if aid in SKIP: continue
                if aid in ("BRAND","MODEL"):
                    vn = a.get("value_name")
                    if vn: attrs.append({"id":aid,"value_name":vn})
                    continue
                if aid == "SIZE_GRID_ID":
                    vn = a.get("value_name") or a.get("value_id")
                    if vn: attrs.append({"id":"SIZE_GRID_ID","value_name":str(vn)})
                    continue
                if a.get("value_id"): attrs.append({"id":aid,"value_id":a["value_id"]})
                elif a.get("value_name"): attrs.append({"id":aid,"value_name":a["value_name"]})
            # family_name SIEMPRE requerido por ML
            # Título base: cortar todo lo que viene DESPUÉS del número de modelo en el título
            raw_title = item.get("title","")
            if model_name and model_name in raw_title:
                idx = raw_title.index(model_name) + len(model_name)
                clean_title = raw_title[:idx].strip()
            elif " - " in raw_title:
                clean_title = raw_title.rsplit(" - ", 1)[0].strip()
            else:
                clean_title = raw_title.strip()
            # ML acepta máximo 60 chars pero a veces falla con exactamente 60, usar 59
            clean_title = clean_title[:59].strip()
            import unicodedata
            def strip_accents(s):
                return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
            test_title = strip_accents(clean_title)
            family = model_name or brand_name or clean_title
            # Detectar listing types disponibles para la cuenta destino
            to_uid_r = await c.get(f"{ML_API}/users/me", headers={"Authorization": f"Bearer {to_t}"})
            to_uid = to_uid_r.json().get("id")
            lt_r = await c.get(f"{ML_API}/users/{to_uid}/available_listing_types?category_id={item.get('category_id','')}", 
                               headers={"Authorization": f"Bearer {to_t}"})
            available_lts = [x.get("id") for x in (lt_r.json() if isinstance(lt_r.json(), list) else [])]
            # Usar el mismo listing type del item original si está disponible, si no el mejor disponible
            orig_lt = item.get("listing_type_id","gold_special")
            listing_type = orig_lt if orig_lt in available_lts else (available_lts[0] if available_lts else "gold_special")
            # Verificar si cuenta destino tiene user_product_seller tag
            me_r2 = await c.get(f"{ML_API}/users/{to_uid}", headers={"Authorization": f"Bearer {to_t}"})
            dest_tags = me_r2.json().get("tags", [])
            # SHAMPOOSHIR es user_product_seller — usar modelo nuevo sin title
            # Usar todos los atributos del item original
            up_attrs = []
            for a in (item.get("attributes") or []):
                aid = a.get("id","")
                if aid in ("SELLER_SKU","ITEM_CONDITION","ALPHANUMERIC_MODEL","GTIN",
                           "PACKAGE_DATA_SOURCE","RELEASE_YEAR","SYI_PYMES_ID","FILTRABLE_SIZE"):
                    continue
                if aid == "SIZE":
                    # Para user_product_seller SIZE va como value_name
                    vn = a.get("value_name") or str(a.get("value_id",""))
                    if vn: up_attrs.append({"id":"SIZE","value_name":vn})
                    continue
                if aid == "SIZE_GRID_ID":
                    up_attrs.append({"id":"SIZE_GRID_ID","value_name":"2556917"})
                    continue
                if a.get("value_id"):
                    up_attrs.append({"id":aid,"value_id":a["value_id"]})
                elif a.get("value_name"):
                    up_attrs.append({"id":aid,"value_name":a["value_name"]})
            payload = {
                "family_name": "Pack X3 Corpino Reductor De Algodon Liso Bretel Ancho 1018",
                "category_id": item.get("category_id",""),
                "price": item.get("price",0),
                "currency_id": item.get("currency_id","ARS"),
                "available_quantity": item.get("available_quantity",0),
                "listing_type_id": "gold_special",
                "condition": item.get("condition","new"),
                "pictures": [{"source": p["url"].replace("http://","https://")} for p in (item.get("pictures") or [])[:6]],
                "attributes": up_attrs,
            }
            # Mapa correcto SIZE → ROW_ID para guía 2556917 de SHAMPOOSHIR
            CHART_2556917 = {
                "S/M":"2556917:1","85":"2556917:5","90":"2556917:6","L/XL":"2556917:2",
                "95":"2556917:7","100":"2556917:8","2XL":"2556917:3","105":"2556917:9",
                "110":"2556917:10","3XL":"2556917:4","115":"2556917:11","120":"2556917:12","125":"2556917:13"
            }
            size_val = next((x.get("value_name","") for x in payload["attributes"] if x.get("id")=="SIZE"), "")
            for i, a in enumerate(payload["attributes"]):
                if a.get("id") == "SIZE_GRID_ROW_ID":
                    mapped = CHART_2556917.get(size_val)
                    if mapped:
                        payload["attributes"][i] = {"id":"SIZE_GRID_ROW_ID","value_name":mapped}
                    break
            r2 = await c.post(f"{ML_API}/items", headers={"Authorization": f"Bearer {to_t}"}, json=payload)
            resp = r2.json()
            if r2.status_code in (200,201):
                new_id = resp.get("id")
                await c.delete(f"{ML_API}/items/{new_id}", headers={"Authorization": f"Bearer {to_t}"})
                return {"result": "✅ FUNCIONA sin SIZE_GRID_ID", "new_id": new_id}
            return {"result": "❌ FALLA", "status": r2.status_code, 
                    "causes": resp.get("cause",[]), 
                    "message": resp.get("message"),
                    "title_sent": clean_title,
                    "title_length": len(clean_title),
                    "listing_type_used": listing_type,
                    "available_listing_types": available_lts,
                    "dest_tags": dest_tags,
                    "payload_sent": payload,
                    "full_error": resp}
    except Exception as e:
        return {"exception": str(e)}

@app.get("/dup-test/{item_id}")
async def dup_test(item_id: str, acc: int = 0, _=Depends(auth)):
    """Ver exactamente qué trae ML para un item y qué payload se armaría"""
    token = await fresh_token_pub(acc)
    hdrs = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{ML_API}/items/{item_id}?attributes=id,title,category_id,price,available_quantity,listing_type_id,currency_id,family_name,user_product_id,variations,attributes,pictures,shipping,sale_terms", headers=hdrs)
    if r.status_code != 200:
        return {"error": r.status_code, "body": r.text[:200]}
    item = r.json()
    attrs = item.get("attributes", [])
    size_grid = next((a for a in attrs if a.get("id") == "SIZE_GRID_ID"), None)
    size_attr = next((a for a in attrs if a.get("id") == "SIZE"), None)
    row_attr = next((a for a in attrs if a.get("id") == "SIZE_GRID_ROW_ID"), None)
    blocked = ["ITEM_CONDITION","SELLER_SKU","SIZE_GRID_ID","SIZE_GRID_ROW_ID","CATALOG_PRODUCT_ID","HAS_BIDS","WARRANTY_TYPE","WARRANTY_TIME","FILTRABLE_SIZE","IS_EMERGING_BRAND","IS_TOM_BRAND","IS_HIGHLIGHT_BRAND","PACKAGE_DATA_SOURCE","SYI_PYMES_ID"]
    clean_attrs = [a for a in attrs if a.get("value_name") and a.get("id") not in blocked]
    return {
        "item_id": item_id,
        "title": item.get("title"),
        "family_name": item.get("family_name"),
        "user_product_id": item.get("user_product_id"),
        "category_id": item.get("category_id"),
        "listing_type_id": item.get("listing_type_id"),
        "currency_id": item.get("currency_id"),
        "price": item.get("price"),
        "variations_count": len(item.get("variations", [])),
        "SIZE_GRID_ID": size_grid,
        "SIZE": size_attr,
        "SIZE_GRID_ROW_ID": row_attr,
        "attributes_count": len(attrs),
        "clean_attrs_count": len(clean_attrs),
        "clean_attrs": clean_attrs[:5],
        "all_attr_ids": [a.get("id") for a in attrs],
        "payload_would_send": {
            "family_name": (item.get("family_name") or "").strip() or item.get("title","")[:60],
            "title_included": not bool((item.get("family_name") or "").strip()),
            "SIZE_GRID_ID_needed": bool(size_grid),
            "SIZE_GRID_ID_value": size_grid.get("value_name") if size_grid else None,
        }
    }

@app.get("/diag")
async def diag(_=Depends(auth)):
    r = get_redis()
    redis_ok = False
    try:
        if r: r.ping(); redis_ok = True
    except: pass
    result = {"redis": redis_ok, "accounts": len(ST["accounts"]), "accounts_detail": []}
    for i, acc in enumerate(ST["accounts"]):
        token = acc.get("token","")
        expired = time.time() > acc.get("expiry",0)
        detail = {
            "index": i,
            "name": acc.get("name",""),
            "token_preview": token[:20]+"..." if token else "EMPTY",
            "token_expired": expired,
        }
        # No llamar a ML — usar datos del estado local
        detail["ml_status"] = 200 if (token and not expired) else "expired"
        detail["ml_uid"] = acc.get("uid", "")
        stock_token = acc.get(f"token_stock_{i}", "")
        stock_expiry = acc.get(f"token_stock_expiry_{i}", 0)
        detail["stock_token"] = "OK" if (stock_token and time.time() < stock_expiry) else ("expired" if stock_token else "MISSING")
        result["accounts_detail"].append(detail)
    # TN info
    tn = ST.get("tn", {})
    tn_store_id = tn.get("store_id","")
    tn_token = tn.get("token","")
    result["tn"] = {
        "store_id": tn_store_id,
        "token_preview": tn_token[:15]+"..." if tn_token else "EMPTY",
        "connected": bool(tn_store_id and tn_token)
    }
    # Test TN connection
    if tn_store_id and tn_token:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                rt = await c.get(f"https://api.tiendanube.com/v1/{tn_store_id}/store",
                    headers={"Authentication": f"bearer {tn_token}",
                             "User-Agent": "MLTNSync/1.0 (gabysaade9@gmail.com)"})
                result["tn"]["api_status"] = rt.status_code
                if rt.status_code == 200:
                    result["tn"]["store_name"] = rt.json().get("name",{}).get("es","")
        except Exception as e:
            result["tn"]["api_error"] = str(e)
    # Sync status
    sync_statuses = {}
    for idx, acc in enumerate(ST.get("accounts", [])):
        uid = acc.get("uid","")
        st = get_sync_status(uid)
        sync_statuses[acc.get("name", uid)] = {"running": uid in SYNC_RUNNING, "status": st}
    result["sync"] = sync_statuses
    return result



# ── PREGUNTAS ML ─────────────────────────────────────────────────────────────

QUESTIONS_SYSTEM = """Sos el asistente de atención al cliente de Lencería Casa Shampoo, una empresa argentina que vende lencería, pijamas y ropa interior por MercadoLibre.

SOBRE NOSOTROS:
- Somos una empresa real con personas reales. Podemos cometer errores pero siempre nuestra intención es dar el mejor servicio.
- Tenemos 3 cuentas en ML: LENCERIAPORMAYORYMENOR (principal), SHAMPOOSHIR y SHAMPOOAVELLANEDA.

ENVÍOS:
- CABA y GBA: Flex mismo día. Pedidos antes de las 13hs, las motos salen a las 14hs y entregan hasta las 23hs.
- Todo el país: Mercado Envíos. Colecta corta a las 14hs, los paquetes se llevan a las 15hs.
- Retiro en depósito: Barrio de Flores, CABA. Lunes a viernes de 10 a 15:30hs.

POLÍTICA DE ATENCIÓN:
- Siempre evitar que el comprador abra un reclamo formal. Todo tiene solución.
- Ser directo, humano y sin vueltas. No sonar a bot.
- Si hay un error de nuestra parte, reconocerlo y ofrecer solución inmediata.
- Responder en español argentino informal pero profesional.

STOCK:
- El stock real te lo doy en el contexto de cada pregunta.
- Si el stock está en 0, decirlo sin rodeos y ofrecer alternativa si hay.

REGLAS CRITICAS DE FORMATO:
1. Respondé SOLO la pregunta. Nada más.
2. Sin markdown, sin asteriscos, sin emojis.
3. NUNCA termines con frases como "cualquier consulta avisame", "estoy a tu disposición", "podés consultarme cuando quieras", "quedás a disposición" o similares. La respuesta termina cuando terminaste de responder.
4. Máximo 3 oraciones.
5. Si no sabés algo concreto, respondé exactamente: NECESITO_CONSULTAR: [qué falta saber]
6. Si la pregunta es sobre un problema post-compra, sé empático y ofrecé solución concreta sin cerrar con frases de bot."""

@app.get("/api/ml/{i}/questions")
async def get_questions(i: int, status: str = "unanswered", _=Depends(auth)):
    """Traer preguntas de ML"""
    if i < 0 or i >= len(ST["accounts"]):
        raise HTTPException(404)
    token = await fresh_token(i)
    uid = ST["accounts"][i]["uid"]
    try:
        all_questions = []
        async with httpx.AsyncClient(timeout=20) as c:
            # Traer sin filtro de status — ML a veces ignora el filtro
            r = await c.get(
                f"{ML_API}/questions/search?seller_id={uid}&limit=50&sort_fields=date_created&sort_types=DESC",
                headers={"Authorization": f"Bearer {token}"}
            )
            print(f"Questions {uid}: HTTP {r.status_code} body={r.text[:300]}")
            if r.status_code != 200:
                return {"questions": [], "error": f"ML {r.status_code}: {r.text[:100]}"}
            data = r.json()
            all_questions = data.get("questions", [])
        # Filtrar por status (ML devuelve en MAYUSCULAS) y por fecha (48hs)
        cutoff = time.time() - 48 * 3600
        filtered = []
        for q in all_questions:
            # Filtro de status
            if status and status != "all":
                if q.get("status","").upper() != status.upper():
                    continue
            # Filtro de fecha — parsear ISO 8601
            try:
                import re as _re
                ds = q.get("date_created","")
                ds_clean = _re.sub(r'[+-]\d{2}:\d{2}$', '', ds).replace('T',' ').split('.')[0]
                import datetime
                dt = datetime.datetime.strptime(ds_clean, '%Y-%m-%d %H:%M:%S')
                q_ts = dt.timestamp()
                if q_ts < cutoff:
                    continue
            except:
                pass  # si falla el parseo, incluir igual
            filtered.append(q)
        # Enriquecer con titulo del item (batch)
        item_ids = list({q["item_id"] for q in filtered if q.get("item_id")})
        item_titles = {}
        if item_ids:
            try:
                async with httpx.AsyncClient(timeout=15) as c2:
                    for batch_start in range(0, len(item_ids), 20):
                        batch = item_ids[batch_start:batch_start+20]
                        r2 = await c2.get(
                            f"{ML_API}/items?ids={','.join(batch)}&attributes=id,title",
                            headers={"Authorization": f"Bearer {token}"}
                        )
                        if r2.status_code == 200:
                            for it in r2.json():
                                if it.get("code") == 200:
                                    b = it["body"]
                                    item_titles[b["id"]] = b.get("title","")
            except:
                pass
        for q in filtered:
            iid = q.get("item_id","")
            if iid and iid in item_titles:
                q["item"] = {"title": item_titles[iid]}
        return {"questions": filtered, "total": len(filtered), "total_raw": len(all_questions)}
    except Exception as e:
        return {"questions": [], "error": str(e)}

def _redis_qa_key(item_id: str) -> str:
    return f"mltn:qa_history:{item_id}"

def get_qa_history(item_id: str) -> list:
    """Traer historial de preguntas respondidas de un item desde Redis"""
    try:
        r = get_redis()
        if r:
            raw = r.get(_redis_qa_key(item_id))
            if raw:
                return json.loads(raw)
    except:
        pass
    return []

def save_qa_history(item_id: str, question: str, answer: str):
    """Guardar pregunta+respuesta en Redis para aprendizaje futuro"""
    try:
        r = get_redis()
        if not r:
            return
        history = get_qa_history(item_id)
        # Agregar nueva entrada
        history.append({"q": question, "a": answer, "ts": int(time.time())})
        # Mantener solo las ultimas 30 respuestas por item
        history = history[-30:]
        r.set(_redis_qa_key(item_id), json.dumps(history), ex=90*24*3600)  # 90 dias
    except Exception as e:
        print(f"save_qa_history error: {e}")

@app.post("/api/ml/{i}/questions/{question_id:str}/ai_reply")
async def ai_reply_question(i: int, question_id: str, req: Request, _=Depends(auth)):
    """Generar respuesta IA para una pregunta — con historial de respuestas anteriores"""
    if i < 0 or i >= len(ST["accounts"]):
        raise HTTPException(404)
    try:
        b = await req.json()
    except:
        b = {}
    print(f"ai_reply: i={i} qid={question_id} item={b.get('item_id','')} q={str(b.get('question_text',''))[:40]}")
    question_text = b.get("question_text", "")
    item_id = b.get("item_id", "")

    # Seed de historial solo para este item (una vez, en background)
    token_for_seed = await fresh_token(i)
    asyncio.create_task(seed_qa_history_from_ml(i, item_id, token_for_seed))

    # Traer info del item desde cache (sin llamar a ML)
    stock_info = ""
    item_title = ""
    uid = ST["accounts"][i]["uid"]
    cached = {p["id"]: p for p in (get_cached_products(uid) or [])}
    if item_id in cached:
        p = cached[item_id]
        item_title = p.get("family_name") or p.get("title", "")
        stk = p.get("available_quantity", 0)
        variations = p.get("variations", [])
        if variations:
            stock_parts = []
            for v in variations[:10]:
                attrs = v.get("_attrs", {})
                label = " / ".join(str(val) for val in attrs.values() if val)
                if label and v.get("available_quantity", 0) > 0:
                    stock_parts.append(f"{label}: {v['available_quantity']} u.")
            stock_info = "Stock: " + (", ".join(stock_parts) if stock_parts else "Sin stock")
        else:
            stock_info = f"Stock: {stk} unidades" if stk > 0 else "Sin stock en este momento"
    else:
        stock_info = "Stock: no disponible"

    # Traer historial de preguntas respondidas de este item
    qa_history = get_qa_history(item_id)

    # Construir contexto few-shot con respuestas anteriores
    few_shot = ""
    if qa_history:
        examples = qa_history[-10:]  # ultimas 10 respuestas
        few_shot = "\n\nRESPUESTAS ANTERIORES EN ESTA PUBLICACION (aprendé el estilo y los datos):\n"
        for ex in examples:
            few_shot += f"Pregunta: {ex['q']}\nRespuesta: {ex['a']}\n---\n"

    # Generar respuesta con Claude
    ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    try:
        user_content = f"Producto: {item_title}\n{stock_info}{few_shot}\n\nNueva pregunta del comprador: {question_text}"
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                "https://api.anthropic.com/v1/messages",
                headers={"Content-Type": "application/json", "x-api-key": ANTHROPIC_KEY,
                         "anthropic-version": "2023-06-01"},
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 300,
                    "system": QUESTIONS_SYSTEM,
                    "messages": [{"role": "user", "content": user_content}]
                }
            )
        data = r.json()
        reply = data["content"][0]["text"].strip()
        needs_consult = reply.startswith("NECESITO_CONSULTAR:")
        return {
            "ok": True,
            "reply": reply,
            "needs_consult": needs_consult,
            "stock_info": stock_info,
            "item_title": item_title,
            "history_used": len(qa_history)
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}

async def seed_qa_history_from_ml(i: int, item_id: str, token: str):
    """Traer preguntas ya respondidas de ML y guardarlas en Redis — una sola vez por item"""
    try:
        r = get_redis()
        if not r:
            return
        # Chequear si ya entrenamos este item
        trained_key = f"mltn:qa_trained:{item_id}"
        if r.exists(trained_key):
            return  # ya entrenado, no llamar de nuevo
        # Traer preguntas respondidas de este item
        uid = ST["accounts"][i]["uid"]
        async with httpx.AsyncClient(timeout=20) as c:
            resp = await c.get(
                f"{ML_API}/questions/search?item_id={item_id}&seller_id={uid}&status=ANSWERED&limit=50",
                headers={"Authorization": f"Bearer {token}"}
            )
        if resp.status_code != 200:
            return
        questions = resp.json().get("questions", [])
        saved = 0
        for q in questions:
            if q.get("answer") and q.get("text"):
                save_qa_history(item_id, q["text"], q["answer"]["text"])
                saved += 1
        # Marcar como entrenado — no volver a llamar (expira en 30 dias)
        r.setex(trained_key, 30*24*3600, "1")
        print(f"QA seed {item_id}: {saved} respuestas guardadas")
    except Exception as e:
        print(f"seed_qa_history error {item_id}: {e}")

@app.post("/api/ml/{i}/questions/{question_id:str}/answer")
async def answer_question(i: int, question_id: str, req: Request, _=Depends(auth)):
    """Publicar respuesta a una pregunta en ML"""
    if i < 0 or i >= len(ST["accounts"]):
        raise HTTPException(404)
    b = await req.json()
    answer_text = b.get("text", "")
    question_text = b.get("question_text", "")
    item_id = b.get("item_id", "")
    if not answer_text:
        raise HTTPException(400, "Falta el texto de respuesta")
    token = await fresh_token(i)
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{ML_API}/answers",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"question_id": int(question_id), "text": answer_text}
            )
        ok = r.status_code in (200, 201)
        # Guardar en historial para aprendizaje futuro
        if ok and question_text and item_id:
            save_qa_history(item_id, question_text, answer_text)
        return {"ok": ok, "msg": "Respondido" if ok else r.json().get("message", f"Error {r.status_code}")}
    except Exception as e:
        return {"ok": False, "error": str(e)}






# ═══════════════════════════════════════════════════════════════════════
# AUTO-RESPUESTA DE PREGUNTAS POR IA
# ═══════════════════════════════════════════════════════════════════════
# Cada 2 horas el sistema:
# 1. Trae todas las preguntas sin responder de las 3 cuentas
# 2. Clasifica cada una con IA en: segura / revisar / sensible
# 3. Las SEGURAS las responde automaticamente
# 4. Las otras quedan para revision manual
# 5. Guarda log de la actividad

AUTO_ANSWER_INTERVAL_SECONDS = 7200  # 2 horas

async def classify_question_with_ai(question_text: str, item_title: str, item_info: str = "", qa_history: str = "") -> dict:
    """Clasifica una pregunta y decide si la IA puede responderla sola.

    Returns dict con:
      - safety: "safe" | "review" | "sensitive"
      - reason: explicacion corta
      - suggested_answer: respuesta sugerida (solo si safety="safe")
    """
    ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    if not ANTHROPIC_KEY:
        return {"safety": "review", "reason": "Sin API key de IA"}

    history_block = ""
    if qa_history:
        history_block = f"""
RESPUESTAS ANTERIORES A ESTE PRODUCTO (usa este tono y estilo, son del vendedor real):
{qa_history}
"""

    prompt = f"""Sos el asistente de un vendedor argentino de pijamas/lenceria/indumentaria en Mercado Libre. Respondes preguntas de compradores. Tu objetivo es RESPONDER LA MAYOR CANTIDAD POSIBLE de preguntas vos mismo, con confianza, para ahorrarle trabajo al vendedor. Solo derivas al vendedor lo que realmente lo necesita.

PRODUCTO: {item_title}
INFO DISPONIBLE DEL PRODUCTO:
{item_info[:1200]}
{history_block}
PREGUNTA DEL COMPRADOR: "{question_text}"

CRITERIO DE CLASIFICACION (se generoso con SAFE):

SAFE → responder vos mismo. Incluye TODO esto:
- Disponibilidad / stock ("¿hay stock?", "¿tenés disponible?", "¿queda?")
- Talles ("¿qué talles hay?", "¿tenés M?", "¿hay XL?") → mira las variantes en la info
- Material, color, diseño, género → si esta en la info del producto, RESPONDELO
- Envíos ("¿hacés envíos?", "¿llega a X?", "¿cuánto tarda?") → responde que se envía por Mercado Envíos a todo el país y los tiempos los calcula ML segun la zona
- Precio (si preguntan el precio, esta en la info)
- Medidas aproximadas → si hay guía de talles o info, orientá; si no, decí que se guíen por la tabla de talles de la publicación
- Saludos, agradecimientos, confirmaciones
- Preguntas generales sobre el producto que puedas deducir de la info
- Si tenés DUDA entre SAFE y REVIEW pero la pregunta es informativa y común, elegí SAFE

REVIEW → derivar al vendedor SOLO si:
- Negociación de precio explícita ("¿me lo dejás más barato?", "¿hacés descuento?", "¿el último precio?")
- Combos/packs especiales o cantidades grandes (mayorista)
- Pide algo que NO está en la info y no podés deducir con seguridad
- Pide fotos reales adicionales o video

SENSITIVE → NO responder, es delicado:
- Quejas, reclamos, problemas con un pedido ya hecho
- Mensajes agresivos o groseros
- Cancelaciones, devoluciones, defectos, "me llegó mal"
- Algo urgente sobre una compra existente

TONO de las respuestas SAFE:
- Argentino informal pero respetuoso y cordial
- Breve y claro (2-4 oraciones max)
- Emoji ocasional (😊) sin exagerar
- Si hay respuestas anteriores arriba, IMITÁ ese estilo
- Empezar con "¡Hola!" y cerrar con "¡Saludos!" o "¡Que tengas buen día!"
- NUNCA inventes datos que no estén en la info (si no sabés un dato puntual, es REVIEW)

RESPONDE SOLAMENTE CON JSON valido (sin markdown), formato:
{{
  "safety": "safe" | "review" | "sensitive",
  "reason": "explicacion breve en espanol",
  "suggested_answer": "respuesta lista para enviar (solo si safety=safe, sino vacio)"
}}"""

    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                "https://api.anthropic.com/v1/messages",
                headers={"Content-Type": "application/json",
                         "x-api-key": ANTHROPIC_KEY,
                         "anthropic-version": "2023-06-01"},
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 800,
                    "messages": [{"role": "user", "content": prompt}],
                }
            )
        if r.status_code != 200:
            return {"safety": "review", "reason": f"API error {r.status_code}"}
        ai_resp = r.json()
        text = ""
        for blk in ai_resp.get("content", []):
            if blk.get("type") == "text":
                text += blk.get("text", "")
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r'^```(?:json)?\s*\n?', '', text)
            text = re.sub(r'\n?```\s*$', '', text)
        try:
            return json.loads(text.strip())
        except:
            return {"safety": "review", "reason": "IA respondio invalido"}
    except Exception as e:
        return {"safety": "review", "reason": f"Excepcion IA: {str(e)[:100]}"}


async def auto_answer_questions_for_account(account_idx: int) -> dict:
    """Procesa las preguntas sin responder de una cuenta.
    Devuelve estadisticas: cuantas respondio, cuantas dejo para revisar, errores."""
    if account_idx < 0 or account_idx >= len(ST.get("accounts", [])):
        return {"ok": False, "error": "Cuenta invalida"}

    acc = ST["accounts"][account_idx]
    uid = str(acc.get("uid", ""))
    name = acc.get("name", f"Cuenta {account_idx}")
    stats = {
        "account": name,
        "total": 0,
        "answered": 0,
        "review": 0,
        "sensitive": 0,
        "errors": 0,
        "details": [],
    }

    try:
        token = await fresh_token(account_idx)
        # Traer preguntas sin responder (max 10 por ronda para no saturar)
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                f"{ML_API}/questions/search?seller_id={uid}&status=UNANSWERED&limit=10",
                headers={"Authorization": f"Bearer {token}"}
            )
        if r.status_code != 200:
            return {"ok": False, "error": f"ML {r.status_code}", "account": name}
        questions = r.json().get("questions", [])
        stats["total"] = len(questions)
        if not questions:
            return {"ok": True, **stats}

        cached = {p["id"]: p for p in (get_cached_products(uid) or [])}

        for q in questions:
            qid = q.get("id")
            qtext = q.get("text", "")
            item_id = q.get("item_id", "")
            if not qid or not qtext:
                continue

            # Info del item para contexto
            item_title = ""
            item_info = ""
            if item_id in cached:
                p = cached[item_id]
                item_title = p.get("family_name") or p.get("title", "")
                stk = p.get("available_quantity", 0)
                variations = p.get("variations", [])
                item_info_parts = [
                    f"Titulo completo: {p.get('title','')}",
                    f"Stock total: {stk}",
                    f"Precio: ${p.get('price', '?')}",
                ]
                # Atributos del producto (material, marca, etc) — clave para responder
                attrs_list = p.get("attributes", [])
                for a in attrs_list:
                    aid = a.get("id", "")
                    aval = a.get("value_name", "")
                    if aval and aid not in ("SIZE_GRID_ID", "SIZE_GRID_ROW_ID", "SELLER_SKU"):
                        # Traducir IDs comunes a algo legible
                        nice = {
                            "BRAND": "Marca", "FABRIC_DESIGN": "Diseño",
                            "MATERIAL": "Material", "GENDER": "Género",
                            "AGE_GROUP": "Edad", "MAIN_COLOR": "Color",
                            "SIZE": "Talle", "MODEL": "Modelo",
                            "IS_KIT": "Es kit", "ITEM_CONDITION": "Condición",
                        }.get(aid, aid)
                        item_info_parts.append(f"{nice}: {aval}")
                # Talles disponibles con stock
                if variations:
                    item_info_parts.append("Variantes/talles:")
                    for v in variations[:10]:
                        attrs = v.get("_attrs", {})
                        label = " / ".join(str(val) for val in attrs.values() if val)
                        vstk = v.get("available_quantity", 0)
                        estado = "disponible" if vstk > 0 else "SIN STOCK"
                        item_info_parts.append(f"  - {label}: {vstk} u. ({estado})")
                item_info = "\n".join(item_info_parts)

            # Traer historial de respuestas previas de este item (para aprender el tono)
            qa_history_str = ""
            try:
                if item_id:
                    hist = get_qa_history(item_id)
                    if hist:
                        ej = []
                        for h in hist[:3]:
                            ej.append(f"P: {h.get('question','')[:80]}\nR: {h.get('answer','')[:120]}")
                        if ej:
                            qa_history_str = "\n\n".join(ej)
            except Exception:
                pass

            # Clasificar con IA
            decision = await classify_question_with_ai(qtext, item_title, item_info, qa_history_str)
            safety = decision.get("safety", "review")

            detail = {
                "qid": qid,
                "item_id": item_id,
                "question": qtext[:120],
                "safety": safety,
                "reason": decision.get("reason", "")[:200],
            }

            if safety == "safe":
                answer = decision.get("suggested_answer", "").strip()
                if not answer:
                    detail["status"] = "no answer"
                    stats["review"] += 1
                    stats["details"].append(detail)
                    events.warn(f"[auto_answer] {name}: SAFE pero sin respuesta generada. Pregunta: \"{qtext[:60]}\"",
                                source="auto_answer")
                    continue
                # Responder en ML
                try:
                    async with httpx.AsyncClient(timeout=15) as c2:
                        r2 = await c2.post(
                            f"{ML_API}/answers",
                            headers={"Authorization": f"Bearer {token}",
                                     "Content-Type": "application/json"},
                            json={"question_id": int(qid), "text": answer}
                        )
                    if r2.status_code in (200, 201):
                        stats["answered"] += 1
                        detail["status"] = "answered"
                        detail["answer"] = answer[:200]
                        # Guardar en historial
                        if item_id:
                            try:
                                save_qa_history(item_id, qtext, answer)
                            except: pass
                        events.ok(f"[auto_answer] {name}: ✓ RESPONDIDA \"{qtext[:50]}\" → \"{answer[:60]}\"",
                                  source="auto_answer")
                    else:
                        stats["errors"] += 1
                        detail["status"] = f"error ML {r2.status_code}"
                        events.err(f"[auto_answer] {name}: ✗ ML rechazó respuesta ({r2.status_code}): {r2.text[:100]}",
                                   source="auto_answer")
                except Exception as ea:
                    stats["errors"] += 1
                    detail["status"] = f"exception: {str(ea)[:100]}"
                    events.err(f"[auto_answer] {name}: ✗ error al responder: {str(ea)[:100]}",
                               source="auto_answer")
            elif safety == "review":
                stats["review"] += 1
                detail["status"] = "review"
                events.info(f"[auto_answer] {name}: ⏸ REVISAR \"{qtext[:50]}\" — motivo: {decision.get('reason','')[:120]}",
                            source="auto_answer")
            elif safety == "sensitive":
                stats["sensitive"] += 1
                detail["status"] = "sensitive"
                events.info(f"[auto_answer] {name}: 🔴 SENSIBLE \"{qtext[:50]}\" — motivo: {decision.get('reason','')[:120]}",
                            source="auto_answer")

            stats["details"].append(detail)
            # Pacing entre preguntas
            await asyncio.sleep(1)

    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "account": name, **stats}

    return {"ok": True, **stats}


# Cache de la ultima ejecucion
_auto_answer_last_run = {
    "timestamp": 0,
    "results": [],
}


async def auto_answer_worker_loop():
    """Worker que cada 2 horas procesa preguntas pendientes de las 3 cuentas."""
    # Esperar 5 min al inicio para no pegarle apenas arranca el server
    await asyncio.sleep(300)
    while True:
        try:
            ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
            if not ANTHROPIC_KEY:
                events.warn("[auto_answer] ANTHROPIC_API_KEY no configurada — pausando worker",
                            source="auto_answer")
                await asyncio.sleep(AUTO_ANSWER_INTERVAL_SECONDS)
                continue
            events.info(f"[auto_answer] Iniciando ronda — {len(ST.get('accounts', []))} cuentas",
                        source="auto_answer")
            results = []
            for i in range(len(ST.get("accounts", []))):
                try:
                    r = await auto_answer_questions_for_account(i)
                    results.append(r)
                    summary = f"{r.get('account','?')}: {r.get('answered',0)} respondidas / {r.get('review',0)} revisar / {r.get('sensitive',0)} sensibles / {r.get('errors',0)} errores"
                    events.info(f"[auto_answer] {summary}", source="auto_answer")
                except Exception as e:
                    events.err(f"[auto_answer] Cuenta {i}: {str(e)[:120]}", source="auto_answer")
                # Pacing entre cuentas
                await asyncio.sleep(5)
            _auto_answer_last_run["timestamp"] = time.time()
            _auto_answer_last_run["results"] = results
            # Persistir en Redis para sobrevivir reinicios
            try:
                _rr = get_redis()
                if _rr:
                    _rr.setex("mltn:auto_answer:last_run",
                              86400 * 7,
                              json.dumps(_auto_answer_last_run, ensure_ascii=False))
            except: pass
        except Exception as e:
            events.err(f"[auto_answer] Error en worker: {str(e)[:120]}", source="auto_answer")
        await asyncio.sleep(AUTO_ANSWER_INTERVAL_SECONDS)


@app.get("/api/admin/auto_answer/status")
async def auto_answer_status(_=Depends(auth)):
    """Devuelve el estado de la ultima ronda de auto-respuesta."""
    # Si no hay en memoria, intentar leer de Redis
    if not _auto_answer_last_run["timestamp"]:
        try:
            _rr = get_redis()
            if _rr:
                raw = _rr.get("mltn:auto_answer:last_run")
                if raw:
                    data = json.loads(raw)
                    _auto_answer_last_run.update(data)
        except: pass

    ts = _auto_answer_last_run["timestamp"]
    next_run = ts + AUTO_ANSWER_INTERVAL_SECONDS if ts else 0
    return {
        "ok": True,
        "last_run": ts,
        "last_run_human": datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "Nunca ejecutado",
        "next_run": next_run,
        "next_run_human": datetime.datetime.fromtimestamp(next_run).strftime("%Y-%m-%d %H:%M:%S") if next_run else "?",
        "interval_seconds": AUTO_ANSWER_INTERVAL_SECONDS,
        "results": _auto_answer_last_run["results"],
    }


@app.post("/api/admin/auto_answer/run_now")
async def auto_answer_run_now(_=Depends(auth)):
    """Ejecuta una ronda de auto-respuesta inmediatamente (sin esperar el cron)."""
    ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    if not ANTHROPIC_KEY:
        return {"ok": False, "error": "ANTHROPIC_API_KEY no configurada"}
    results = []
    for i in range(len(ST.get("accounts", []))):
        try:
            r = await auto_answer_questions_for_account(i)
            results.append(r)
        except Exception as e:
            results.append({"ok": False, "account": f"cuenta {i}", "error": str(e)[:200]})
        await asyncio.sleep(2)
    _auto_answer_last_run["timestamp"] = time.time()
    _auto_answer_last_run["results"] = results
    return {"ok": True, "results": results, "ran_at": _auto_answer_last_run["timestamp"]}


# ── ANALYTICS / INTELIGENCIA DE VENTAS ───────────────────────────────────────

@app.get("/api/ml/{i}/item/{item_id}/analytics")
async def item_analytics(i: int, item_id: str, _=Depends(auth)):
    """Score, metricas y comparacion vs competencia de un item"""
    if i < 0 or i >= len(ST["accounts"]):
        raise HTTPException(404)
    token = await fresh_token_stock(i)  # app de stock — no compite con webhooks
    hdrs = {"Authorization": f"Bearer {token}"}
    result = {"item_id": item_id, "score": None, "metrics": {}, "pricing": {}, "suggestions": []}
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            # Metricas de visitas y ventas
            r_visits = await c.get(
                f"{ML_API}/items/{item_id}/visits/time_window?last=7&unit=day&ending=today",
                headers=hdrs)
            visits_7d = 0
            if r_visits.status_code == 200:
                visits_7d = sum(d.get("total", 0) for d in r_visits.json().get("results", []))

            # Health del item
            r_health = await c.get(f"{ML_API}/items/{item_id}/health", headers=hdrs)
            health = None
            if r_health.status_code == 200:
                health = r_health.json().get("health")

            # Datos basicos del item
            r_item = await c.get(
                f"{ML_API}/items/{item_id}?attributes=title,price,available_quantity,status,category_id,sold_quantity",
                headers=hdrs)
            if r_item.status_code != 200:
                return {"error": "Item no encontrado"}
            item = r_item.json()
            title = item.get("title", "")
            price = item.get("price", 0)
            stock = item.get("available_quantity", 0)
            sold = item.get("sold_quantity", 0)
            cat_id = item.get("category_id", "")

            # Buscar competencia — primeros 10 de la categoria con precio similar
            r_search = await c.get(
                f"{ML_API}/sites/MLA/search?category={cat_id}&limit=10&sort=relevance",
                headers=hdrs)
            competitors = []
            if r_search.status_code == 200:
                for p in r_search.json().get("results", []):
                    if p.get("id") != item_id:
                        competitors.append({
                            "id": p.get("id"),
                            "title": p.get("title","")[:60],
                            "price": p.get("price", 0),
                            "sold": p.get("sold_quantity", 0),
                        })

        # Calcular score
        score = 50  # base
        suggestions = []
        pricing_info = {}

        if competitors:
            prices = [c["price"] for c in competitors if c["price"] > 0]
            if prices:
                min_p = min(prices)
                max_p = max(prices)
                avg_p = sum(prices) / len(prices)
                pricing_info = {
                    "my_price": price,
                    "competitor_min": round(min_p, 2),
                    "competitor_max": round(max_p, 2),
                    "competitor_avg": round(avg_p, 2),
                    "competitors": competitors[:5],
                }
                # Posicion de precio
                cheaper = sum(1 for p in prices if p < price)
                position = cheaper + 1
                pricing_info["position"] = f"{position}/{len(prices)+1}"

                # Sugerencias de precio
                if price < avg_p * 0.92:
                    diff = round(avg_p * 0.95 - price, 2)
                    pct = round((diff / price) * 100, 1)
                    suggestions.append({
                        "type": "price_up",
                        "msg": f"Sos de los mas baratos. Podrias subir ${diff:.0f} ({pct}%) sin perder competitividad.",
                        "suggested_price": round(avg_p * 0.95, 2)
                    })
                    score += 15
                elif price > avg_p * 1.10:
                    diff = round(price - avg_p * 1.05, 2)
                    pct = round((diff / price) * 100, 1)
                    suggestions.append({
                        "type": "price_down",
                        "msg": f"Sos de los mas caros. Bajar ${diff:.0f} ({pct}%) te pondria mas competitivo.",
                        "suggested_price": round(avg_p * 1.05, 2)
                    })
                    score -= 10
                else:
                    suggestions.append({"type": "price_ok", "msg": "Tu precio esta bien posicionado."})
                    score += 10

        # Score por visitas
        if visits_7d > 100:
            score += 20
        elif visits_7d > 30:
            score += 10
        elif visits_7d < 5:
            score -= 15
            suggestions.append({"type": "low_visits", "msg": "Muy pocas visitas esta semana. Revisa el titulo y las fotos."})

        # Score por ventas
        if sold > 50:
            score += 20
        elif sold > 10:
            score += 10
        elif sold == 0:
            score -= 20
            suggestions.append({"type": "no_sales", "msg": "Sin ventas registradas. Considera duplicar en otra cuenta o revisar el precio."})

        # Score por health
        if health is not None:
            if health >= 0.8:
                score += 10
            elif health < 0.5:
                score -= 10
                suggestions.append({"type": "low_health", "msg": f"Health bajo ({health:.0%}). Revisa atributos y fotos."})

        # Score por stock
        if stock == 0:
            score -= 30
            suggestions.append({"type": "no_stock", "msg": "Sin stock. La publicacion no aparece en busquedas."})
        elif stock < 5:
            suggestions.append({"type": "low_stock", "msg": f"Stock bajo ({stock} unidades). Considera reponer pronto."})

        score = max(0, min(100, score))

        # Clasificacion
        if score >= 75:
            label = "Explotando"
            emoji = "Fuego"
            color = "#00b894"
        elif score >= 50:
            label = "Estable"
            emoji = "OK"
            color = "#4f9eff"
        elif score >= 25:
            label = "Bajo rendimiento"
            emoji = "Atencion"
            color = "#f39c12"
        else:
            label = "Muerto"
            emoji = "Critico"
            color = "#e17055"

        # Sugerencia de expansion — ver si esta en otras cuentas
        uid = ST["accounts"][i]["uid"]
        for j, acc in enumerate(ST["accounts"]):
            if j == i:
                continue
            other_uid = acc.get("uid", "")
            other_prods = get_cached_products(other_uid) or []
            model_num = ""
            import re as _re
            m = _re.search(r"(\d{4,6})", title)
            if m:
                model_num = m.group(1)
            if model_num:
                has_in_other = any(model_num in p.get("title","") for p in other_prods)
                if not has_in_other and score >= 50:
                    suggestions.append({
                        "type": "expand",
                        "msg": f"Este producto va bien y no esta en {acc.get('name','')}. Considera duplicarlo.",
                        "target_acc": j,
                        "target_name": acc.get("name","")
                    })

        result = {
            "item_id": item_id,
            "title": title,
            "score": score,
            "label": label,
            "emoji": emoji,
            "color": color,
            "metrics": {
                "visits_7d": visits_7d,
                "sold_quantity": sold,
                "stock": stock,
                "health": health,
                "status": item.get("status","")
            },
            "pricing": pricing_info,
            "suggestions": suggestions
        }
    except Exception as e:
        result["error"] = str(e)
    return result

@app.get("/api/ml/{i}/radar")
async def expansion_radar(i: int, _=Depends(auth)):
    """Detectar productos con buen rendimiento que no estan en otras cuentas"""
    if i < 0 or i >= len(ST["accounts"]):
        raise HTTPException(404)
    uid = ST["accounts"][i]["uid"]
    products = get_cached_products(uid) or []
    if not products:
        return {"opportunities": [], "msg": "Sin productos en cache"}

    import re as _re
    # Productos activos con stock
    active = [p for p in products if p.get("status") == "active" and p.get("available_quantity", 0) > 0]

    opportunities = []
    for p in active[:200]:  # limitar para no tardar
        title = p.get("title", "")
        m = _re.search(r"(\d{4,6})", title)
        if not m:
            continue
        model_num = m.group(1)
        missing_in = []
        for j, acc in enumerate(ST["accounts"]):
            if j == i:
                continue
            other_prods = get_cached_products(acc.get("uid","")) or []
            if not any(model_num in op.get("title","") for op in other_prods):
                missing_in.append({"idx": j, "name": acc.get("name","")})
        if missing_in:
            opportunities.append({
                "id": p.get("id"),
                "title": title,
                "price": p.get("price", 0),
                "stock": p.get("available_quantity", 0),
                "model": model_num,
                "missing_in": missing_in
            })

    # Ordenar por precio desc (proxy de valor)
    opportunities.sort(key=lambda x: x["price"], reverse=True)
    return {"opportunities": opportunities[:50], "total": len(opportunities)}


@app.get("/api/ml/{i}/intelligence")
async def intelligence_batch(i: int, _=Depends(auth)):
    """Analizar top 50 FAMILIAS por precio — agrupa UPtin por family_name/user_product_id"""
    if i < 0 or i >= len(ST["accounts"]):
        raise HTTPException(404)
    uid = ST["accounts"][i]["uid"]
    products = get_cached_products(uid) or []
    if not products:
        return {"items": [], "error": "Sin productos en cache. Sincroniza primero."}

    import re as _re

    # Agrupar por familia (user_product_id o family_name para UPtin, id para items viejos)
    active = [p for p in products if p.get("status") == "active"]
    familias = {}
    for p in active:
        fkey = p.get("user_product_id") or p.get("family_name") or p.get("id")
        if not fkey: fkey = p.get("id")
        if fkey not in familias:
            familias[fkey] = []
        familias[fkey].append(p)

    # Construir resumen por familia
    familia_list = []
    for fkey, items in familias.items():
        total_stock = sum(p.get("available_quantity", 0) for p in items)
        if total_stock == 0:
            continue  # saltar familias sin stock
        # Precio representativo: maximo del grupo
        max_price = max(p.get("price", 0) for p in items)
        # Titulo: el del primer item sin el sufijo de variante
        base_title = items[0].get("family_name") or items[0].get("title", "")
        # IDs de todos los items de la familia
        all_ids = [p["id"] for p in items]
        # Item representativo para expansion check
        rep_id = items[0]["id"]
        familia_list.append({
            "fkey": fkey,
            "title": base_title,
            "price": max_price,
            "stock": total_stock,
            "item_count": len(items),
            "ids": all_ids,
            "rep_id": rep_id,
        })

    # Top 50 familias por precio
    top50_fams = sorted(familia_list, key=lambda x: x["price"], reverse=True)[:50]
    # Para batch de ML usar solo el item representativo de cada familia
    top50 = [{"id": f["rep_id"], "_fam": f} for f in top50_fams]

    # Traer sold_quantity solo si no hay cooldown — sino usar 0
    sold_map = {}
    if not is_ml_cooling(i):
        try:
            token = await fresh_token_stock(i)
            hdrs = {"Authorization": f"Bearer {token}"}
            ids_batch = [p["id"] for p in top50]
            async with httpx.AsyncClient(timeout=20) as c:
                for x in range(0, min(len(ids_batch), 40), 20):
                    batch = ids_batch[x:x+20]
                    r = await c.get(
                        f"{ML_API}/items?ids={','.join(batch)}&attributes=id,sold_quantity",
                        headers=hdrs)
                    if r.status_code == 429:
                        print(f"Intelligence: 429 en batch, usando cache")
                        break
                    if r.status_code == 200:
                        for it in r.json():
                            if it.get("code") == 200:
                                b = it["body"]
                                sold_map[b["id"]] = {"sold": b.get("sold_quantity", 0)}
                    await asyncio.sleep(1)
        except Exception as e:
            print(f"Intelligence batch error: {e}")

    results = []
    for p in top50:
        pid = p["id"]
        fam = p.get("_fam", {})
        title = fam.get("title") or pid
        price = fam.get("price", 0)
        stock = fam.get("stock", 0)
        item_count = fam.get("item_count", 1)
        all_ids = fam.get("ids", [pid])
        # sold = suma de ventas de todos los items de la familia
        sold = sum(sold_map.get(iid, {}).get("sold", 0) for iid in all_ids)
        health = sold_map.get(pid, {}).get("health")

        # Score base
        score = 50
        suggestions = []
        actions = []

        # Ventas
        if sold > 100:
            score += 25
        elif sold > 20:
            score += 15
        elif sold > 5:
            score += 5
        elif sold == 0:
            score -= 25
            suggestions.append("Sin ventas registradas")
            actions.append({"type": "review_price", "label": "Revisar precio"})

        # Stock
        if stock == 0:
            score -= 30
            suggestions.append("Sin stock")
        elif stock < 10:
            score -= 5
            suggestions.append(f"Stock bajo ({stock} u.)")
            actions.append({"type": "restock", "label": "Reponer stock"})

        # Health
        if health is not None:
            if health >= 0.8:
                score += 10
            elif health < 0.5:
                score -= 15
                suggestions.append(f"Health bajo ({int(health*100)}%)")
                actions.append({"type": "improve_listing", "label": "Mejorar publicacion"})

        # Expansion — ver si falta en otras cuentas (por family_name o model)
        fam_name = fam.get("fkey", "") or ""
        m = _re.search(r"\b(\d{4,6})\b", title)
        model_num = m.group(1) if m else ""
        missing_in = []
        for j, acc in enumerate(ST["accounts"]):
            if j == i: continue
            other_prods = get_cached_products(acc.get("uid","")) or []
            found = False
            if fam_name:
                found = any(p.get("user_product_id") == fam_name or p.get("family_name") == fam_name for p in other_prods)
            if not found and model_num:
                found = any(model_num in op.get("title","") for op in other_prods)
            if not found:
                missing_in.append({"idx": j, "name": acc.get("name","")})
        if missing_in and score >= 40:
            for mi in missing_in:
                actions.append({"type": "expand", "label": f"Duplicar en {mi['name']}", "target_acc": mi["idx"]})

        score = max(0, min(100, score))

        if score >= 75:
            label, color = "Explotando", "#00b894"
        elif score >= 50:
            label, color = "Estable", "#4f9eff"
        elif score >= 25:
            label, color = "Bajo rendimiento", "#f39c12"
        else:
            label, color = "Muerto", "#e17055"

        results.append({
            "id": pid,
            "ids": all_ids,
            "title": title,
            "price": price,
            "stock": stock,
            "item_count": item_count,
            "sold": sold,
            "health": health,
            "score": score,
            "label": label,
            "color": color,
            "suggestions": suggestions,
            "actions": actions,
            "missing_in": missing_in
        })

    # Ordenar: peor score primero (los que necesitan atencion)
    results.sort(key=lambda x: x["score"])
    return {"items": results, "total": len(results), "analyzed": len(top50)}


@app.get("/debug/item_sample/{i}")
async def debug_item_sample(i: int, _=Depends(auth)):
    """Ver raw de los primeros 5 items del cache para diagnostico"""
    if i < 0 or i >= len(ST["accounts"]):
        raise HTTPException(404)
    uid = ST["accounts"][i]["uid"]
    products = get_cached_products(uid) or []
    # Buscar items que parecen UPtin (sin variaciones, titulo largo)
    uptin = [p for p in products if not p.get("_has_variations") and len(p.get("title","")) > 50][:5]
    normal = [p for p in products if p.get("_has_variations")][:3]
    return {
        "total_cached": len(products),
        "uptin_sample": [{"id":p["id"],"title":p.get("title","")[:60],"user_product_id":p.get("user_product_id",""),"family_name":p.get("family_name","")[:50],"has_vars":p.get("_has_variations")} for p in uptin],
        "normal_sample": [{"id":p["id"],"title":p.get("title","")[:60],"user_product_id":p.get("user_product_id",""),"family_name":p.get("family_name","")[:50],"has_vars":p.get("_has_variations")} for p in normal],
    }

@app.get("/debug/cache_sample")
async def debug_cache_sample(i: int = 0, _=Depends(auth)):
    """Ver muestra del cache sin auth — para diagnostico"""
    if i >= len(ST.get("accounts", [])):
        return {"error": "cuenta no existe"}
    uid = ST["accounts"][i]["uid"]
    products = get_cached_products(uid) or []
    # Buscar items UPtin (sin variaciones, titulo largo con color/talle)
    uptin = [p for p in products if p.get("user_product_id") and not p.get("_has_variations")][:5]
    normal = [p for p in products if p.get("_has_variations")][:3]
    no_upid = [p for p in products if not p.get("user_product_id") and not p.get("_has_variations")][:3]
    return {
        "account": ST["accounts"][i].get("name"),
        "total_cached": len(products),
        "uptin_con_upid": [{"id":p["id"],"title":p.get("title","")[:60],"user_product_id":p.get("user_product_id",""),"family_name":p.get("family_name","")[:40]} for p in uptin],
        "con_variaciones": [{"id":p["id"],"title":p.get("title","")[:60],"vars":p.get("_variation_count",0)} for p in normal],
        "sin_upid_sin_vars": [{"id":p["id"],"title":p.get("title","")[:60],"user_product_id":p.get("user_product_id",""),"family_name":p.get("family_name","")[:40]} for p in no_upid],
    }


@app.get("/debug/top_sales_test")
async def debug_top_sales(i: int = 0, days: int = 7, _=Depends(auth)):
    """Debug sin auth — prueba top_sales rapido"""
    if i >= len(ST.get("accounts", [])):
        return {"error": "cuenta no existe"}
    uid = ST["accounts"][i]["uid"]
    try:
        # Usamos STOCK (app por cuenta). Lectura de orders.
        token = await fresh_token_stock(i)
        hdrs = {"Authorization": f"Bearer {token}"}
        from datetime import datetime, timedelta, timezone
        date_from = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00.000-00:00")
        url = f"{ML_API}/orders/search?seller={uid}&order.date_created.from={date_from}&limit=5&order.status=paid"
        print(f"Debug top_sales URL: {url}")
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(url, headers=hdrs)
        return {
            "status": r.status_code,
            "body_preview": r.text[:500],
            "url": url,
            "uid": uid
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/ml/{i}/top_sales")
async def get_top_sales(i: int, days: int = 30, limit: int = 50, _=Depends(auth)):
    """Traer productos mas vendidos en los ultimos N dias"""
    if i < 0 or i >= len(ST["accounts"]):
        raise HTTPException(404)
    uid = ST["accounts"][i]["uid"]
    try:
        # Usamos STOCK (app por cuenta). Lectura de orders.
        token = await fresh_token_stock(i)
        hdrs = {"Authorization": f"Bearer {token}"}
        from datetime import datetime, timedelta, timezone
        date_from = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00.000-00:00")
        print(f"Top sales {uid}: desde {date_from}")
        sales_count = {}
        offset = 0
        total_orders = 0
        while True:
            url = f"{ML_API}/orders/search?seller={uid}&order.date_created.from={date_from}&limit=50&offset={offset}&order.status=paid"
            r = await ml_call(seller_uid=uid, method="GET", url=url, headers=hdrs, priority=PRIORITY_MEDIUM, timeout=20)
            print(f"Top sales {uid} offset={offset}: status={r.status_code}")
            if r.status_code != 200:
                print(f"Top sales error: {r.status_code} {r.text[:200]}")
                break
            d = r.json()
            orders = d.get("results", [])
            if not orders:
                break
            for order in orders:
                for oi in order.get("order_items", []):
                    item = oi.get("item", {})
                    iid = item.get("id")
                    if not iid:
                        continue
                    qty = oi.get("quantity", 1)
                    price = oi.get("unit_price", 0)
                    if iid not in sales_count:
                        sales_count[iid] = {
                            "id": iid,
                            "title": item.get("title", ""),
                            "qty_sold": 0,
                            "revenue": 0,
                            "orders": 0
                        }
                    sales_count[iid]["qty_sold"] += qty
                    sales_count[iid]["revenue"] += qty * price
                    sales_count[iid]["orders"] += 1
            total_orders += len(orders)
            total = d.get("paging", {}).get("total", 0)
            offset += 50
            if offset >= total or len(orders) < 50:
                break
            await asyncio.sleep(0.3)

        # Enriquecer con datos del cache (thumbnail, precio actual, stock)
        products = get_cached_products(uid) or []
        prod_map = {p["id"]: p for p in products}
        for iid, data in sales_count.items():
            p = prod_map.get(iid, {})
            data["thumbnail"] = p.get("thumbnail", "")
            data["current_price"] = p.get("price", 0)
            data["available_quantity"] = p.get("available_quantity", 0)
            data["status"] = p.get("status", "")
            data["permalink"] = p.get("permalink", "")
            data["family_name"] = p.get("family_name", "")

        ranked = sorted(sales_count.values(), key=lambda x: x["qty_sold"], reverse=True)[:limit]
        return {
            "ok": True,
            "days": days,
            "total_orders": total_orders,
            "unique_items": len(sales_count),
            "top": ranked
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}

# --- Catálogo LUMA Íntima (debe ir ANTES del catch-all de /frontend) ---
from catalogo_router import router as catalogo_router
app.include_router(catalogo_router)

# --- SPRUNKI UNO (debe ir ANTES del catch-all de /frontend) ---
try:
    from sprunki_uno_api import router as uno_router
    app.include_router(uno_router)
except Exception as _uno_e:
    print(f"[sprunki_uno] no se pudo cargar el router: {_uno_e}")

fp = Path("frontend")
if fp.exists():
    from fastapi import Response
    from fastapi.responses import FileResponse
    import os

    @app.get("/manifest.json")
    async def serve_manifest():
        return FileResponse(str(fp / "manifest.json"), media_type="application/manifest+json")

    @app.get("/sw.js")
    async def serve_sw():
        return FileResponse(str(fp / "sw.js"), media_type="application/javascript", headers={"Service-Worker-Allowed": "/"})

    @app.get("/icon-192.png")
    async def serve_icon192():
        return FileResponse(str(fp / "icon-192.png"), media_type="image/png")

    @app.get("/icon-512.png")
    async def serve_icon512():
        return FileResponse(str(fp / "icon-512.png"), media_type="image/png")

    @app.get("/")
    async def serve_index():
        return FileResponse(str(fp / "index.html"))

    @app.get("/{full_path:path}")
    async def serve_static(full_path: str):
        # No interceptar rutas de API ni diag
        if full_path.startswith("api/") or full_path.startswith("diag"):
            from fastapi import HTTPException
            raise HTTPException(404)
        file_path = fp / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        # Fallback a index.html para SPA
        return FileResponse(str(fp / "index.html"))
