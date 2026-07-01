# Studio Luma — Guía para publicarlo (desde la tablet)

Este paquete es tu app de generación de imágenes, **separada de ML×TN Sync**,
lista para correr sola en su propio host y dominio.

## Archivos que van al repo (los 4, en la RAÍZ del repo)
- `main.py` — el cascarón que levanta la app en la raíz "/"
- `imagenes_ia.py` — la app completa (lo que ya venías usando)
- `requirements.txt` — las librerías que instala Railway
- `Procfile` — cómo se arranca la app

## Pasos (todo desde el navegador de la tablet)

### 1) Crear el repositorio en GitHub
- Entrá a GitHub → New repository → nombre `studio-luma` → Create.
- Subí los 4 archivos a la raíz (Add file → Upload files).

### 2) Crear el proyecto en Railway
- Railway → New Project → Deploy from GitHub repo → elegí `studio-luma`.
- **NO lo pongas dentro del proyecto de ML×TN.** Es un proyecto nuevo.
- Railway detecta Python y hace el build solo.

### 3) Agregar Redis (nuevo, propio de Studio Luma)
- Dentro del proyecto → New → Database → Redis.
- Copiá su URL de conexión y cargala como variable `REDIS_URL` (ver paso 4).

### 4) Cargar las variables de entorno (Settings → Variables)
- `REDIS_URL`      = (la del Redis nuevo)
- `GEMINI_API_KEY` = tu API key de Google — **usá una NUEVA, separada** de la
  de tu negocio, así medís el gasto de Studio Luma aparte.
- (Opcionales de Google Drive, solo si vas a usar la galería.)
- `IMAGENES_PREFIX` NO hace falta tocarla: el `main.py` ya la deja en "" (raíz).

### 5) Generar dominio de prueba
- Settings → Networking → Generate Domain.
- Te da algo tipo `studio-luma-production.up.railway.app`. Entrá y probá.

### 6) Conectar tu dominio propio
- Settings → Networking → Custom Domain → escribí tu dominio
  (ej. `app.studioluma.com`).
- Railway te da un registro **CNAME**. Cargalo en el panel DNS de donde
  compraste el dominio. En minutos/horas queda con HTTPS automático.

## Actualizaciones (igual que ML×TN)
- Cambiás archivos → los subís al repo → Railway redeploya solo → hard refresh.

## Importante
- Esta versión **no tiene login**: cualquiera con el link entra. Ideal para
  probar con tus papás. Antes de promocionar a desconocidos hay que sumar
  login + datos por usuario + medición de consumo + cobro (Mercado Pago).
- Mantené las API keys SOLO en las Variables de Railway, nunca dentro de los
  archivos del repo.
