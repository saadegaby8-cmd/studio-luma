# Studio Luma — Guía para publicarlo (desde la tablet)

Este paquete es tu app de generación de imágenes, **separada de ML×TN Sync**,
lista para correr sola en su propio host y dominio.

## Archivos que van al repo (los 6, en la RAÍZ del repo)
- `main.py` — el cascarón que levanta la app en la raíz "/"
- `imagenes_ia.py` — la app de fotos (lo que ya venías usando)
- `videos_luma.py` — los videos de producto (pestaña 🎬 Videos)
- `personajes.py` — tu persona digital (pestaña 👤 Personajes)
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

## Fotos con Seedream: por qué salían todas las poses iguales

Seedream es un editor multi-referencia: **copia la composición de la primera
imagen que recibe**. Le mandábamos el retrato entero del avatar (plano medio,
de frente, mirando a cámara) y todas las tomas del set salían con esa misma
pose aunque el texto pidiera otra. Ahora le va **sólo la cara** del avatar:
Gemini ubica el recuadro una vez (queda en caché por avatar), se recorta con
margen para el pelo, y el prompt le aclara que esa imagen es identidad y nada
más. La pose la manda el texto. Se apaga en Ajustes → "Seedream: mandar sólo
la cara del avatar" si preferís el retrato entero.

Y había dos inconsistencias más entre motores, ya corregidas:
- **La "Pose" escrita en el formulario de Generar pisaba las poses del set.**
  Con Nano Banana la pose forzada de cada toma mandaba; con Seedream mandaba el
  texto del formulario, así que si ahí había algo escrito (o venía de una
  plantilla) TODAS las tomas salían con esa pose. Ahora los dos motores usan
  la misma prioridad: pose forzada del plan > pose escrita > pool por sorteo.
  En una toma del set con pose del plan, la "Pose" del formulario no entra.
- **Seedream no hace paneles.** La toma "21:9 con 2 paneles" del set básico
  salía como UNA foto apaisada con una sola pose (siempre la 0) que después se
  recortaba. Con Seedream cada panel pasa a ser una toma 4:5 con su propia
  pose del pool. El motor queda fijado al crear el set.

**El checker de salida de Seedream.** Cuando fal dice "The content could not be
processed because it contained material flagged by a content checker" y tardó
50 a 65 s, es el checker de SALIDA de ByteDance: la imagen se generó, se revisó
y se tiró. No se apaga desde fal. En la prueba del 14/9 pasaba la toma de
frente y rebotaban 4 de 5 con las poses variadas: en lencería, la acostada en
el piso, la de espalda con la mano en la nuca o la de acomodarse el bretel le
disparan el checker. Por eso:
- En lencería y baño, cada pose del pool usa su **versión de catálogo** (misma
  variedad: parada, sentada, de espalda, caminando, perfil…) con menos riesgo.
  Ajuste "Seedream: poses de catálogo en lencería y baño" (sí por defecto).
- Si igual rebota, la toma se **reintenta sola con la pose segura** en el mismo
  Seedream Pro, antes de sanear el prompt y de caer al modelo de respaldo. Y el
  rechazo del checker ya no dispara los 4 reintentos "a ciegas" que había para
  errores de validación (cada uno de ~60 s): eso era la seguidilla de fallos
  de 50 s en el panel de fal.
- El saneado del prompt también baja los superlativos del cuerpo ("extra
  grande y voluminoso", "volumen marcado"): al checker le pesan más que a la
  foto; queda "talle grande", que es lo que importa para el calce.

**¿Hay algo mejor que Seedream para lencería con poses provocativas?** El
checker de salida de Seedream no se apaga, así que para poses provocativas
hay que ir a un modelo de pesos abiertos, que en fal no tiene checker propio.
Las dos opciones probadas en la app (se cambian en Ajustes → Motor FLUX →
"Modelo try-on", sin tocar código):
- `fal-ai/qwen-image-edit-2511` (Alibaba, Apache 2.0): multi-referencia,
  identidad fuerte, acepta LoRA (se le puede entrenar la avatar), US$0,035
  por megapíxel. Es el que usan los generadores de lencería "sin filtro".
- `fal-ai/flux-2/edit` (Black Forest Labs, pesos abiertos): multi-referencia
  hasta 4 imágenes (la persona + 3 vistas de la prenda), US$0,012/MP.
- `bytedance/seedream/v5/pro/edit`: la mejor calidad de imagen, pero con su
  checker de salida. Queda como opción para poses de catálogo.
Ojo: en Qwen y FLUX, apagar el safety checker de fal (`enable_safety_checker`)
**requiere que la cuenta de fal esté habilitada para contenido sin filtro**;
sin eso fal lo revisa igual. Se pide desde el panel de fal. La app ya manda
a cada motor sólo los parámetros que entiende (guidance y safety_tolerance
sólo a FLUX), le manda a FLUX como mucho 4 referencias, y aplica las poses de
catálogo y el reintento con pose segura sólo cuando el motor es Seedream.

**Breteles duplicados / "producto fantasma" con varias fotos del conjunto.**
Los editores de fal reciben las fotos del producto sin ningún texto entre
medio (a diferencia de Nano Banana, donde cada foto viaja con su rótulo). Con
la foto del corpiño, la de la bombacha y la de espalda, el motor entendía que
eran tres prendas y las sumaba: la modelo salía con 4 breteles en vez de 2 y
con un arnés de más encima del real. Desde v2.41.0 el prompt de fal dice por
número qué es cada foto ("image 2 = FRONT view; image 3 = the BOTTOM piece;
image 4 = BACK view"), que son vistas de UN solo conjunto (un top y una parte
de abajo), que un bretel que aparece en dos fotos se dibuja una sola vez y que
la cantidad de breteles es la de la foto de frente. Usa las etiquetas que le
pusiste a cada foto (frente / abajo / espalda / detalle...), así que conviene
etiquetarlas; sin etiquetas, la foto de espalda igual se marca como espalda
por el plan del set. Con FLUX.2 (4 referencias en total) rotula sólo las 3
fotos que de verdad viajan.

**El rechazo del checker que "moría" sin reintento (v2.41.1).** En producción
el 422 de Seedream trae las dos frases juntas: "Error validating the input" y
"flagged by a content checker". La app miraba primero la de "validating" y
cortaba ahí, así que la pose segura y el saneado del prompt nunca llegaban a
correr. Ahora el checker se detecta primero. Además el saneado cambia marcas
de revistas para adultos que se escriban en la pose ("modelo Playboy de los
70" → "1970s glamour magazine"): el checker las lee como pedido de desnudo
aunque la prenda esté puesta. Y el detalle exacto del rechazo queda en "Ver
diagnóstico".

## Nenas / nenes con modelo: por qué "no andaba" (v2.42.0)

Con modelo (pijamas, ponchos, remeras, buzos, vestidos) la toma se bloqueaba o
salía cualquier cosa por tres motivos que se juntaban:

- **El prompt nombraba justo lo que no queríamos.** Decía "PROHIBIDO: malla,
  bikini, ropa interior, poses sexualizadas" al lado de "una nena de 7 años",
  y arriba iba la instrucción de marca de los Ajustes ("LUMA Íntima, ropa
  interior y prendas íntimas"). Los filtros de imagen leen esas palabras como
  si fueran el pedido, aunque estén en una prohibición, y bloquean. Ahora el
  prompt de kids va sin la instrucción de marca, sin nombrar la marca, y dice
  en positivo lo que sí queremos ("la prenda puesta completa, cara lavada,
  como un catálogo de ropa de chicos").
- **Si el motor estaba en FLUX, la nena iba a Seedream.** Seedream recibía el
  prompt de kids (largo y en castellano) y lo rechazaba ("Error validating
  the input") o lo tiraba su checker. Ahora nenas/nenes van SIEMPRE a Nano
  Banana, esté el motor como esté.
- **El reintento tras un bloqueo repetía el mismo prompt.** Ahora reintenta
  con un prompt mínimo y neutro (450 caracteres: quién, la prenda de la foto,
  pose, escenario).

Además, la regla "malla o ropa interior → prenda sola" miraba también la ficha
automática, que podía decir "no es ropa interior" y mandaba el pijama a prenda
sola sin avisar. Ahora mira sólo lo que escribís vos (Producto, Descripción,
Piezas, Aclaraciones), y si lo manda a prenda sola te dice qué palabra fue
("la palabra "malla" en Piezas") al arrancar y en el resultado.

**El menú de nenas/nenes es el mismo de adultos (v2.43.0).** Al elegir "Nenas /
Nenes" en "¿Qué vas a fotografiar?", el panel Generar queda igual que para
adultos: se esconden los avatares y el tilde "Sin avatar" (el chico lo inventa
siempre la IA), y el cajón de apariencia pregunta nena o nene, edad/talle,
etnia, pelo (rubio, castaño, morocho, pelirrojo, rulos...), ojos, altura para
su edad, peinado y un detalle libre. En Opciones avanzadas se esconde lo que
es de cuerpo adulto (busto, cola, abdomen, contextura, edad, altura, bombacha
haciendo juego). "Generar imágenes" hace una toma 4:5 con una pose de chico al
azar; "Set completo" usa el mismo selector de poses de siempre, pero con las
poses de chico (de pie riéndose, corriendo, sentado jugando, de espaldas,
saltando...) más el producto colgado. No hay más cajón ni botón aparte de
kids, ni set de nenas separado. Las prendas solas (mallas, bikinis, o
cualquier prenda sin modelo) van por la pestaña **Producto**, como siempre.

**Kids respeta el motor de Ajustes (v2.45.0).** En v2.42 había forzado Nano
Banana para nenas/nenes porque Seedream rechazaba el prompt de kids (largo y
en castellano). Ahora, si el motor de Ajustes es FLUX/Seedream, la nena va a
fal con un prompt propio en inglés, corto y limpio (quién es, rasgos, la
prenda de las fotos rotuladas por número, la pose de chico en inglés,
escenario y luz), y en automático el rescate a fal también aplica a kids. Y
si un pedido de kids llega sin decir "con modelo" (página vieja en caché,
plantilla guardada), igual va con modelo: sólo cae a "prenda sola" (maniquí
fantasma) cuando lo que escribiste dice malla/bikini/ropa interior, y en ese
caso "Ver diagnóstico" muestra en la nota de la toma qué palabra fue.
Los restos de "prohibido menores" de la pestaña Producto y del prompt de
producto se sacaron: la prenda sola sirve para adultos o chicos por igual.

## Estilo de foto: ahora también "vintage de revista" (v2.47.0)

El selector **Estilo** (en Generar, arriba de todo) define la vibra de la foto:
la prenda siempre la manda la foto real, el estilo manda la fotografía. Se
usa igual en la foto suelta, en el set de poses y en el set de colores.

Estilos disponibles:
- **Instagram casual realista** (el que viene puesto): foto tomada al pasar
  con un celular, poses sin posar, piel con textura.
- **Catálogo sobrio**: estudio, fondo neutro, luz pareja, colores fieles.
- **Editorial / campaña**: dirección de arte, luz con intención.
- **Vintage de revista (film, grano)** *(nuevo)*: película de 35 mm tipo
  Kodak Portra escaneada. Grano visible y parejo, colores lavados y
  desaturados con dominante cálida, contraste suave, negros levantados y
  lechosos, halos alrededor de las luces fuertes, foco de lente antiguo y
  bordes del cuadro un poco más oscuros. Le prohíbe expresamente el marco
  blanco, las perforaciones de película, la fecha impresa y cualquier texto,
  que es lo que los modelos suelen agregar solos cuando se les pide "vintage".
- **Vintage suave (apenas de film)** *(nuevo)*: lo mismo pero discreto, para
  cuando el vintage completo queda demasiado. Grano fino, un punto menos de
  saturación, contraste medio y nitidez natural.

**Un arreglo que venía de antes:** el estilo elegido llegaba al motor Seedream
como parámetro pero el código nunca lo usaba, así que con Seedream el selector
no hacía nada (sólo funcionaba con Gemini). Ahora cada estilo tiene además una
versión corta en inglés, que es lo que ese motor necesita: su prompt tiene que
ser breve, y el bloque largo en castellano lo confunde y le baja la fidelidad
a la prenda.

## La cámara ahora también se mueve (v2.48.0)

**El problema:** todas las fotos salían frontales. Lo único que cambiaba era la
modelo — de frente, de perfil, de espalda — pero el set se veía siempre igual,
desde el mismo lugar y a la misma altura. Como si la modelo se moviera y el
fotógrafo estuviera clavado en el piso.

**Por qué pasaba:** el listado de poses describe qué hace la modelo y cuánto
cuerpo entra en el cuadro, pero en ningún renglón del prompt se decía dónde
está parada la cámara. Sin esa indicación, los motores la ponen siempre a la
altura de los ojos y de frente, que es su opción por defecto.

**Qué se agregó:** un listado de 9 posiciones de cámara que rota con el mismo
número que las poses, así en un set de 6 fotos hay 6 cámaras distintas y ninguna
se repite:

1. A la altura de los ojos, de frente (la de siempre).
2. Baja, a la altura de la cintura, apenas hacia arriba (contrapicado suave):
   piernas largas, figura imponente.
3. Alta, apenas por encima de su cara, mirando un poco hacia abajo (picado
   suave), como cuando la foto la saca alguien más alto.
4. Corrida a un costado, en diagonal a unos 45°: el lugar se ve en perspectiva.
5. Muy baja, casi apoyada en el piso, apuntando hacia arriba.
6. Bastante alta, picado marcado, como desde una escalera o un balcón.
7. Lejos con teleobjetivo: perspectiva comprimida, el fondo aplanado y pegado
   a ella.
8. Cerca con gran angular leve: más profundidad y se ve más del lugar.
9. Detrás de algo del lugar (hojas, el marco de una puerta, una percha), que
   queda desenfocado en el borde: foto robada, no posada.

**Dos límites puestos a propósito:**

- **La cámara nunca le gana al encuadre.** Si está pedido el encuadre por zona
  (por ejemplo "de la cintura para abajo" para una bombacha), la cámara sólo
  cambia desde dónde se mira; qué parte del cuerpo entra en el cuadro lo sigue
  fijando el encuadre. Cada renglón de cámara lo dice explícitamente.
- **En lencería y mallas, los dos contrapicados fuertes (2 y 5) se reemplazan
  por la cámara neutra.** Son justamente los ángulos que despiertan al checker
  de salida de Seedream y hacen rebotar la imagen.

Funciona en los dos motores: en Nano Banana (tanto en el set de paneles como en
la foto suelta) y en Seedream/FLUX, que necesita la versión corta en inglés.
Si escribís vos la pose a mano, no se agrega cámara: manda lo que vos pediste.

## Reels: el error "Audio duration must be between 5 and 14.8 seconds" (v2.11.0)

MiniMax H3 Max pide entre 5 y 14,8 segundos de voz por tramo. La app ya agregaba
silencio a los tramos cortos, pero igual rebotaba con un 422 en tramos que en la
pantalla figuraban **arriba** del mínimo, tipo "5,1 s".

**Por qué:** el encabezado de un mp3 declara ~0,05 s de más que lo que realmente
suena (es el retardo del codificador). Un tramo que la app mostraba como 5,1 s
decodificaba 5,02 y el decodificador de fal medía todavía un poco menos: no
llegaba a 5 y lo rechazaba. Como para nosotros pasaba el mínimo, no se le
agregaba silencio y no había forma de darse cuenta mirando la pantalla.

**Qué cambió:** el relleno ahora mira los segundos que **decodifica** el archivo
(no los que declara) y agrega silencio con margen de sobra, hasta 5,8 s. Después
verifica que la pista haya quedado larga de verdad antes de mandarla a fal. El
silencio de más no se ve ni se escucha: el tramo se corta al largo real de la
voz cuando se arma el video.

Si te pasó, no hace falta rehacer nada: tocá **Generar reel** de nuevo.

## Comerciales: un módulo nuevo para videos estilo campaña (comerciales v1.0.0)

Pedido: "un video estilo Rip Curl con estas fotos, movimientos lentos". Videos está
hecho para la vidriera blanca (su prompt de IA ordena fondo blanco y velocidad real) y
Reels para el personaje hablando. Así que va un módulo aparte, `comerciales.py`, en
**/comerciales** (pestaña 🎥 Comerciales en Fotos y link en Videos). Dos maneras:

1. **Kling arma el comercial.** Las fotos van como REFERENCIA (hasta 7) a **Kling 3.0
   Omni** en fal (`fal-ai/kling-video/o3/standard/reference-to-video`, y la versión
   Pro): la 1 es la cara de la modelo, la 2 a la 4 más vistas de ella, la 5 a la 7 el
   lugar. Kling inventa las tomas manteniendo a la misma modelo y la misma prenda, en
   **multi-shot**: hasta 6 tomas y 15 s por tanda; 30 s son dos tandas pegadas. Las
   tomas salen de una **plantilla** (Surf estilo Rip Curl, Playa, Urbano: 12 tomas de
   30 s cada una, escritas en inglés para el motor y en castellano para la pantalla) o
   las escribe ella (se traducen en una llamada). Cada toma con sus segundos. Si fal
   rechaza un campo del pedido (422) se prueban variantes cada vez más simples: sin
   negativo, sin fotos de lugar, sin cfg, un solo prompt, sin formato.
   Precio estimado por segundo: Standard 0,084 USD, Pro 0,112 (ajustables por variable
   de entorno). Las rutas del modelo también, por si fal las cambia.
2. **Foto por foto.** Cada foto es una toma, en su orden. Con la **cámara de edición**
   (una deriva lenta distinta por toma: entra, sale, se corre, baja, va a la cara;
   gratis) o con **IA** (image-to-video de fal, Seedance/Wan/LTX/MiniMax, con un prompt
   de LOCACIÓN: conserva el fondo real, cámara lenta, viento, olas; y el clip se estira
   1,5× si se pide cámara lenta).

Para las dos, la terminación: cortes secos o fundidos (corto 0,5 s, largo 1 s, por
negro), **grade de película** (teal y naranja medido sobre las fotos reales para que el
mar no salga amarillo; cálido; frío; blanco y negro), grano fino, viñeta, franjas de cine
opcionales, **placa final** con el nombre de la marca y una línea chica, y la **misma
cortina musical** que se sube en Videos (una por cuenta), con fade final.

**Y con TUS tomas tal cual (v1.1.0).** "¿Kling no sirve para usar con mis tomas?" Sí,
de dos maneras. (a) En **foto por foto**, Kling imagen a video (`kling-video/o3/…/
image-to-video`, Standard o Pro) es ahora el motor por defecto: tu foto es el PRIMER
CUADRO literal y Kling la continúa, con el clip del largo exacto pedido (3 a 8 s) y la
cámara lenta filmada por él, no estirada. (b) En **Kling arma el comercial**, cada toma
puede llevar **una de tus fotos como guía**: esa foto viaja como referencia de imagen y
la toma la nombra (@ImageN) para copiar su encuadre, su lugar y su luz; hasta 3 fotos de
guía distintas por tanda.

**El director (v1.2.0).** "Una IA con skills de comercial que para cada imagen pida qué
movimiento darle, cámara lenta o rápida, o dejarla foto; y que entienda el estilo." En el
modo foto por foto hay un botón **"Que el director decida"**: un modelo de visión con un
brief de director de comerciales (Rip Curl, Billabong, Roxy) mira TODAS las fotos en su
orden y, por cada una, propone **motor** (IA para que cobre vida, o cámara para dejarla
foto con una deriva), **ritmo** (lenta, real o rápida), **segundos**, **qué pasa** en la
toma (en castellano para la pantalla y en inglés para el motor) y **por qué**, en una
línea. Entiende el estilo de dos lados: la plantilla elegida y lo que ella escribe con sus
palabras ("estilo Rip Curl crudo con energía", "romántico y dorado"), que manda. Y define
el **look** completo (grade, tipo de corte, franjas de cine, grano y qué música le iría),
que se aplica solo a la Terminación; todo se puede corregir después. Lo que devuelve el
modelo se acota siempre (una toma por foto, valores válidos, segundos de las listas).
El **ritmo** existe ahora por toma: cambia el prompt de la IA (cámara lenta, velocidad
real o un golpe de energía), el estirado de los clips (1,5×, tal cual o 0,85×) y la
deriva de cámara (apenas, más recorrido, o de golpe frenando como un flash).

**Primer comercial real (v1.1.1).** fal aceptó el pedido, lo puso en cola y recién al pedir
el resultado devolvió `422: Prompt must not exceed 512 characters` (el texto de cada toma
del multi-shot tiene ese tope). Dos arreglos: el prompt por toma se arma corto y se
recorta en palabra entera a 512 como máximo (medido: el peor caso da 512 justo), y las
variantes del pedido se prueban también cuando el rechazo llega con el resultado, no
sólo al enviar.

Probado con las 10 fotos reales de surf por la tubería completa de ffmpeg (10 tomas de
cámara, fundido largo, película, cine, placa, música: 18,5 s en 90 s de CPU) y las
rutas HTTP con un fal simulado que rechaza el primer pedido y acepta la variante.

## ESTA TOMA al tope: pose, plano, cámara y rincón mandan de verdad (v2.61.0)

Pedido: "solucioname el enfoque, las poses y el recorrido del lugar sin cambiar la
modelo ni la prenda". Leí un pedido completo de una toma de set tal como le llega a
Gemini: **20.000 caracteres**, y la pose, el tamaño de plano, la cámara y el rincón
estaban **a la mitad**, después de renglones que los contradecían. Lo que cambia:

1. **El bloque "ESTA TOMA" va arriba**, en el primer cuarto del pedido, justo después
   del cuerpo, la época y lo que ella escribió, y ANTES de la identidad y la prenda. Dice
   explícitamente: "la modelo y la prenda NO cambian, cambia sólo esto". Al final del
   pedido hay una **revisión final** que remite a ese bloque.
2. **Sin renglones que lo contradigan.** La "Puesta en escena" decía "Pose: natural,
   espontánea y relajada" y "Encuadre: cuerpo entero de pies a cabeza" ANTES de la pose
   obligatoria y del plano americano; ahora remiten a ESTA TOMA. El bloque de ENERGÍA
   pedía "manos en el pelo o en la cintura", "variá la mirada", "se apoya, se sienta o
   se recuesta": otra pose encima de la pedida. En la toma individual la energía va
   **dentro** de la pose. Y con una época elegida, el PROHIBIDO del final ya no tacha
   "inventar aros y pulseras": los de la onda sí van.
3. **Rincones de exterior.** Los 10 rincones eran de interior (pared, puerta, ventana,
   mueble, esquina de dos paredes, pasillo). En una pileta, "contra la ventana" no
   significa nada. Si el escenario es al aire libre (pileta, playa, jardín, terraza,
   calle, campo…) se rota por 10 rincones de exterior: al borde del agua, la entrada,
   bajo una palmera, el punto más abierto con el cielo, la reposera o el banco, el piso,
   una fachada o muro, la escalera o desnivel, el camino, entre plantas. Si nombra un
   interior (living, local, habitación) gana el interior. En los dos motores.

Sin época ni lugar, y con la variación automática apagada, el pedido queda como estaba.

## La apoyada muestra el frente de la prenda, y la época es una lista cerrada (v2.60.1)

Dos cosas más del mismo set de los 80:

1. **En la toma "apoyada" salió otra bikini, con otros colores.** Esa pose era "DE
   PERFIL puro": de costado la prenda casi no se ve y el modelo la reinventa. Ahora es
   **de tres cuartos**, con el frente de la prenda entero a la vista y la orden de
   copiar su diseño y colores exactos (en los dos motores, mujer y hombre). Además en
   Seedream el bloque que cuida **dónde va cada color de la prenda** (GARMENT PANELS)
   era un "extra" y, con una época elegida, era el primero que se caía por largo:
   pasa a los esenciales, y el tope del prompt sube de 4.200 a 4.500 (se midió que
   hasta 4.641 anda) para no perder tampoco las aclaraciones de la usuaria. Medido en
   las 140 combinaciones época × pose: el bloque de paneles está en todas.
2. **El maquillaje cambiaba entre fotos (rubor en una, no en otra).** La época pasa a
   ser una **lista cerrada**: cada ítem (peinado, cada elemento del maquillaje, cada
   accesorio, las uñas) va en TODAS las tomas con la misma intensidad; nada se saca en
   una ni se agrega en otra. Y en las tomas siguientes del set se compara **ítem por
   ítem** con la toma previa.

## La época ya no pelea con el pelo del avatar (v2.60.0)

En un set de 4 con "Onda de la modelo: años 80", dos fotos salieron con la onda
(pelo batido, sombra celeste, argollas) y dos con el pelo natural del avatar. El
pedido tenía **dos órdenes contradictorias sobre el pelo**: "copiá EXACTO el pelo de
la foto del avatar / de las tomas previas" y "peinala como en los 80". Y la onda iba
al final del pedido, donde ya se había visto que se ignora. El modelo resolvía la
pelea al azar, toma por toma.

Ahora hay una sola lectura posible, en los dos motores:

- **La onda va al tope del pedido**, junto al tipo de cuerpo, antes que la identidad.
- **El avatar y las tomas previas aportan la PERSONA** (cara, color natural de pelo,
  piel y físico). El rótulo de cada imagen y el bloque de identidad dicen que el
  **peinado, maquillaje, uñas y accesorios NO se copian de esas fotos**: los define
  la onda, aunque en la foto de referencia esté al natural.
- **En las tomas siguientes del set**, el bloque de consistencia pide el mismo
  arreglo que en las tomas previas, y si una salió al natural, que no la imite.
- **Seedream**: la línea STYLING va pegada a la identidad (antes de la prenda) y la
  identidad aclara que el peinado no sale de la foto de referencia.

Sin época elegida, el pedido queda exactamente como estaba.

## Un botón para apagar la variación automática, y las épocas con un solo look (v2.59.0)

Un set de 4 salió mal: la modelo cambiaba de peinado en cada foto, un top salió de
otro color y los ángulos elegidos no se respetaron. Dos causas eran mías:

1. **Cada época ofrecía alternativas** ("pelo batido… *o* una cola alta con
   scrunchie… *o*…" — siete en la de los 80). El modelo elegía una distinta en cada
   toma. Ahora cada época es **un solo look fijo**, y el prompt ordena **el mismo
   peinado, maquillaje y accesorios en todas las tomas del set**.
2. **El tamaño de plano decía que "manda sobre la cámara"**: le estaba diciendo al
   modelo que ignore el ángulo elegido a mano. Ahora dice que el ángulo se respeta
   tal cual y que el plano sólo define cuánto cuerpo entra desde ese ángulo.

Y para que nunca más quedes atada a lo automático, en **Ajustes → Ajustes técnicos**
hay un interruptor nuevo: **"Variación automática por toma (cámara, recorrido,
encuadre)"**. En **No**, la app vuelve a como era antes de la v2.48: la pose trae su
propio encuadre, la cámara va **sólo si la elegís a mano** en el selector, y no se
recorre el lugar. Vale para los dos motores. Viene en **Sí**.

## Si Gemini bloquea una toma de lencería, reintenta con la pose de catálogo (v2.58.0)

Con el diagnóstico completo de un set de colores (4 tomas: pasaron *de perfil* y
*detalle de espalda*, se bloquearon *de frente* y *en el piso*) quedó claro que
**lo nuevo no era el problema**: las dos bloqueadas llevaban cámara de frente y
cuerpo entero, lo más neutro que hay. Lo que las bloqueaba era **el texto de la
pose** en ropa interior:

- *De frente*: "cadera quebrada, una mano jugando con el pelo… media sonrisa **cómplice**".
- *En el piso*: "tirada en el piso (… **la cama**), el entorno **tocando la piel**,
  mirada **a cámara desde abajo**".

Sumado a un cuerpo definido como "busto grande, glúteos grandes", Gemini lo lee
como sugerente y corta.

Para Seedream ya existía una **versión de catálogo** de cada pose delicada. Para
Gemini no: cuando bloqueaba, reintentaba sin las tomas previas pero **con la misma
pose**, y volvía a bloquear.

**Ahora:** si Gemini bloquea una toma de lencería o malla, se reintenta **una sola
vez** con la versión de catálogo de esa pose (de pie derecha y relajada · sentada
en el piso con las piernas plegadas a un costado · etc.) y una expresión tranquila.
Si la pose original pasa, **te queda la original**: la de catálogo sólo aparece
cuando hace falta. En el diagnóstico la toma dice *"reintento con pose de catálogo
(Gemini bloqueó la original)"*.

## Por qué se bloqueaban tomas de lencería (v2.57.0)

Con IMAGE_SAFETY bloqueándose tomas del set de colores, fui a mirar el prompt real.
Aparecieron dos cosas que se me habían pasado al agregar la cámara, el recorrido y
el encuadre.

### El renglón que mantiene el tono de catálogo se estaba cayendo

El prompt de Seedream tiene un tope de 4.200 caracteres. Los bloques se dividen en
**esenciales** (nunca se tiran) y **extras** (se van cayendo de atrás para adelante
hasta entrar). El bloque que dice *"estilo catálogo de lencería moderno tipo
Intimissimi / Aerie, actitud relajada y elegante"* estaba entre los **extras**.

Los renglones nuevos de cámara, parte del lugar y tamaño de plano entraron como
**esenciales** y se comían **1.190 de los 4.200** caracteres. Con una descripción
real de producto, eso empujaba fuera **6 bloques**… y uno de los primeros en caer
era justo el del tono de catálogo. Sin ese renglón, la foto se va para otro lado.

Dos arreglos: el bloque de categoría pasa a **esencial** (no se cae nunca) y las
notas en inglés de los tres renglones nuevos se escribieron **compactas** — de
1.190 a **649** caracteres, sin perder lo que dicen. El castellano, que va a Nano
Banana y no tiene tope, quedó igual de explicado.

### En lencería no había freno para los encuadres cerrados

Cuando armé la rotación de encuadres le puse el freno de lencería a las **cámaras**
(los contrapicados fuertes no salen) pero **me olvidé de los tamaños de plano**. Así
que en ropa interior podía tocarle *"plano corto: el cuadro corta a la altura del
pecho"* o *"cuerpo entero ajustado, llenando el cuadro"* — que es exactamente el
tipo de imagen que hace saltar el filtro.

Ahora en lencería y mallas esos dos quedan fuera de la rotación, y quedan los otros
cuatro (cuerpo entero, de la cintura para arriba, de la rodilla para arriba y ella
chica en el lugar), así que la variedad no se resiente.

## La onda de la modelo: hacer producciones de época (v2.56.0)

Para hacer una producción **de los 80** había que escribirle a mano el pelo, el
maquillaje, las uñas y los aros en el campo de accesorios, y aun así salía a
medias. Ahora hay un selector, **"Onda de la modelo (época)"**, con paquetes
completos y coherentes:

| Estilo | Qué le hace a la modelo |
|---|---|
| Natural | Nada: queda como en la ficha (es lo que viene puesto). |
| **Años 80** | Pelo batido con mucho volumen, sombras celestes o violetas hasta la ceja, rubor en diagonal, labios fucsia, aros grandes, uñas largas rojas. |
| **Años 90** | Lacio con raya al medio, labios marrones con delineado oscuro, cejas finas, choker fino, broches mariposa. |
| **Y2K / 2000** | Mechas claras en la cara, gloss brillante, sombras con glitter, lentes chiquitos ovalados, todo con brillo. |
| **Años 70** | Ondas suaves o afro, párpados en tierra y dorado, piel bronceada, argollas grandes, anteojos redondos. |
| **Años 50 / pin-up** | Rulos marcados y victory rolls, delineado con alita, labios rojo mate, pañuelo al pelo, aros de perla. |
| **Rockera** | Pelo despeinado con textura, ojos ahumados, varios anillos de plata, choker de cuero. |
| **Glam de noche** | Ondas grandes con brillo, ojos ahumados prolijos, iluminador, aros largos que brillan. |
| **Minimal / actual** | Pelo prolijo, cara lavada, aritos chicos dorados, uñas cortas. |
| **Deportiva** | Pelo atado, vincha, cara lavada, reloj deportivo. |

Cada uno define las cuatro cosas: **pelo, maquillaje, uñas y accesorios**.

**Lo que el estilo NO puede tocar** (está escrito en el prompt, en los dos idiomas):

- **La prenda.** Sigue siendo la real del producto, tal cual sus fotos, y tiene
  prohibido agregarle ropa de época encima — nada de camperas, calentadores ni
  chalecos que no estén en las fotos.
- **La cara.** Sigue siendo la misma persona del avatar: cambia el arreglo, no la
  identidad.
- **Lo que vos ya elegiste.** Si pusiste un peinado o escribiste accesorios en la
  ficha, esos mandan sobre los del estilo.

**El lugar y la decoración no los cambia**: eso sale de lo que escribas en
Fondo/escenario. Si querés la producción entera de los 80, poné también el lugar
ahí ("un salón con luces de neón y sillones de cuero").

Anda en los dos motores, en la foto suelta y en el set.

## Encuadres variados, sombra propia y nada de marcas ajenas (v2.55.0)

### Los encuadres eran casi todos iguales

En un set de cuatro salían **tres cuerpos enteros**. El motivo: las poses del
listado traen su propio tamaño de plano ("PLANO MEDIO", "CUERPO ENTERO"), pero
los renglones de **cámara** y de **parte del lugar** que agregamos antes empujan
los dos hacia lo abierto ("todo el ambiente por delante", "el piso ocupa la mayor
parte del cuadro", "de lejos con zoom") y le ganaban al de la pose.

Ahora el tamaño de plano es **su propio renglón obligatorio que rota** con la
toma, y **a la pose se le saca el suyo** para que no haya dos órdenes peleando.
Los seis encuadres que rotan: cuerpo entero · de la cintura para arriba · de la
rodilla para arriba · ella chica en el lugar · del pecho para arriba · cuerpo
entero bien pegado.

Tres excepciones pensadas: no se mete cuando manda el **encuadre de la prenda**
(bombacha, corpiño), cuando la pose **ya es un encuadre** (primer plano de cara,
detalle de prenda, detalle de espalda) o cuando el **ángulo elegido ya decide el
tamaño** ("Bien lejos, todo el lugar" y "Muy cerca, a un paso").

### Ahora proyecta sombra

No había **ninguna** regla de sombra propia: por eso quedaba pegada encima del
fondo, que es de lo que más delata una foto de IA. Ahora tiene que proyectar su
sombra sobre el piso, la pared o el mueble de al lado, con la forma y la dureza
que corresponden a la luz de la escena (sol fuerte = sombra marcada; interior
nublado = sombra suave), más una sombra de contacto más oscura donde el cuerpo
toca una superficie.

### Nada de marcas de otros en la escena

Salió una foto con una **botella de Coca-Cola** al lado de la toalla. No había
nada que lo impidiera: sólo estaba prohibido inventar logos *en la prenda*.
Ahora ningún objeto de la escena puede llevar logos, etiquetas ni nombres de
marcas reales — las botellas, vasos, toallas, bolsos y revistas van lisos o con
un diseño inventado sin texto legible.

### Apoyarse es apoyarse

La regla de contacto existía pero se rompía **de perfil contra algo vertical**:
parecía apoyada contra el marco de una ventana pero el hombro no llegaba a tocar.
Ahora el hombro y el brazo tienen que apoyar de verdad, aplastándose un poco
contra la superficie, y el cuerpo queda inclinado **hacia** el apoyo.

## En el celular ya se ven las opciones avanzadas (v2.54.0)

**El problema medido:** en un celular de 390x664, el encabezado —que queda pegado
arriba— ocupaba **292 px, el 44% de la pantalla**. Las nueve solapas (Generar,
Producto, Variar color, Editor, Avatares, Videos, Personajes, Ajustes,
Presupuesto) se apilaban en **tres renglones**. Con casi media pantalla tapada,
tocar "⚙️ Opciones avanzadas" abría un panel que no llegaba a verse.

**Qué cambió, sólo en pantallas de hasta 560 px:**

- Las solapas van en **un solo renglón que se corre con el dedo** (se ve la
  siguiente asomando, para que se note que hay más).
- El logo, el título y los márgenes del encabezado se achican.
- Al abrir un panel largo (Opciones avanzadas, Elegir poses del set, Set de
  colores) **la pantalla se lleva sola hasta ahí**: antes se abría por debajo de
  lo que estabas viendo y parecía que no había pasado nada.

**Resultado medido:** el encabezado pasó de **292 px a 98 px** (del 44% al 15% de
la pantalla), y al tocar "Opciones avanzadas" el primer control queda a la vista.

En la computadora no cambia nada (verificado a 1200, 820 y 600 px). Las otras
pantallas —Reels, Videos, Personajes— ya estaban bien porque tienen menos
solapas: 98 y 119 px.

**Queda pendiente:** el panel de opciones avanzadas abierto son unas **6
pantallas** de scroll en un celular. Se puede partir en secciones (Producto /
Modelo / Escena / Extras) si molesta.

## La diagonal, el dron, y que el recorrido no repita (v2.53.0)

Tres cosas que salían mal al probarlo:

### La diagonal salía frontal igual

El renglón decía "cámara corrida a un costado, en diagonal a unos 45 grados", y
el modelo movía la cámara pero **dejaba la foto frontal y simétrica**: el ángulo
estaba, la perspectiva no. Ahora el renglón describe **qué tiene que pasar en la
imagen**: las líneas del piso, las paredes y los muebles se van en diagonal hacia
un punto de fuga a un costado, se ven **dos caras** de las cosas (el frente y el
lateral), un hombro queda más cerca de la cámara que el otro, y está prohibido
que salga una foto frontal y simétrica. El "un paso al costado" también dice
ahora que las líneas salen inclinadas.

### "Desde arriba de todo" → "Desde el cielo (dron)"

No se entendía qué era. Ahora es lo que tiene que ser: la cámara **como un dron**,
muy por encima y apuntando hacia abajo. Si está acostada, sentada o en el piso es
**cenital puro** — la cámara justo encima, perpendicular, y **el fondo de la foto
es el piso** (la arena, el pasto, la alfombra, las sábanas), sin horizonte, sin
techo y sin paredes. Si está de pie, igual va muy por encima de su cabeza mirando
fuerte hacia abajo.

### El recorrido repetía el mismo rincón

Dos causas, las dos arregladas:

1. **Las tomas previas le arrastraban el fondo.** En Nano Banana, cada toma nueva
   recibe las anteriores como guía para que sea la misma persona. Ese bloque le
   decía "no copies la pose ni el encuadre"… **pero no decía nada del fondo**. Así
   que copiaba el rincón de la primera toma. Ahora le dice expresamente que lo que
   se ve detrás de ella tiene que ser **claramente distinto** al de las tomas
   previas, y que si se parece, la toma está mal.
2. **Los rincones se parecían entre sí.** "Contra una pared", "corrida a un borde"
   y "en el medio del ambiente" son tres cosas distintas de decir, pero **en una
   foto se ven casi iguales**. Los diez rincones se reescribieron para que cada uno
   diga **qué tiene que VERSE detrás de ella**: la pared llenando el fondo · el
   marco de una puerta con otro ambiente detrás · la ventana con lo que hay afuera ·
   todo el ambiente entre ella y la cámara · el mueble grande ocupando medio cuadro ·
   el piso ocupando casi todo · la esquina donde se juntan dos paredes · los
   escalones yéndose para arriba o para abajo · el pasillo alargándose a los lados ·
   plantas o ropa colgada entrando desenfocadas por delante.

## En tus poses escritas, el ángulo y el recorrido ahora sí valen (v2.52.0)

La v2.51.0 le puso ángulo y recorrido a las poses del listado, pero **en las
poses escritas por vos no se notaba nada**. Mirando el prompt renglón por renglón
aparecieron dos motivos:

1. **Tu pose no tenía prioridad.** Viajaba como un renglón más de la lista de
   puesta en escena (`- Pose: sentada en el mostrador`), mientras que las poses
   del listado van con un encabezado **"POSE DE ESTA TOMA (obligatoria, máxima
   prioridad)"**. Y el ángulo y la parte del lugar quedaban flotando sueltos más
   abajo, sin conexión con la pose, perdidos en un prompt de 11.000 caracteres.
   Ahora los tres van **juntos y en el mismo bloque obligatorio**, con la misma
   fuerza que una pose del listado.

2. **Se contradecían entre sí.** Si escribías "sentada en el mostrador" y el
   recorrido le decía "está en el umbral de una puerta", el modelo tenía dos
   órdenes incompatibles y tiraba una. Ahora, **si tu pose ya dice dónde está**
   (mostrador, espejo, ventana, pared, piso, cama, escalera, vidriera…), el
   recorrido **se calla**: manda lo que escribiste vos. Si tu pose no dice dónde
   ("riéndose con el pelo al viento"), ahí sí se le asigna una parte del lugar.

**De paso:** las poses escritas también rotan el ángulo con 🎲 Variado. Antes
sólo llevaban ángulo si se lo elegías a mano.

## Mis poses, y que el set recorra el lugar (v2.51.0)

### Mis poses: las que escribís vos, guardadas y tildables

Antes había un cuadro de texto donde escribías las poses del set, una por línea,
y **mandaban sobre los tildes**: o usabas las del listado o usabas las tuyas, no
se podían mezclar, no se podían tildar y no tenían ángulo de cámara.

Ahora tus poses son una lista igual que las de arriba:

- Las escribís una vez (podés pegar varias de una, una por línea) y **quedan
  guardadas en tu cuenta**.
- Cada una tiene su **tilde** y su **ángulo de cámara**, igual que las del listado.
- **Se combinan**: podés hacer un set con tres poses del listado y dos tuyas.
- Cada una tiene su tachito para borrarla.
- Si una dice "las prendas colgadas solas en una percha" o algo parecido, esa toma
  sale sin modelo — como ya pasaba antes.

### El set ahora recorre el lugar

**El problema:** con una puesta en escena armada (un local, una casa, la playa),
las seis tomas del set salían **todas en el mismo rincón**. Cambiaba la pose y
cambiaba la cámara, pero el lugar entero quedaba sin usar.

**Qué se agregó:** cada toma del set transcurre en **otra parte del mismo lugar**,
rotando: contra una pared o en una esquina · en el umbral o el marco de una puerta ·
junto a la ventana · al fondo del ambiente · junto al mueble grande que el lugar ya
tenga · en el piso · en el medio, despejada · corrida a un borde · en un escalón o
escalera · cerca de la entrada.

Son **relativos a propósito**: no nombran objetos concretos, describen una parte
del lugar que vos describiste. Y cada uno aclara dos cosas: que es **el mismo
lugar** de las otras tomas (no se cambia de local) y que **el lugar no se amuebla**.

Esto sólo se activa cuando describiste un escenario. Con un fondo liso de estudio
no hay nada que recorrer, y no se dice nada.

### "Apoyada" ya no le mete un mueble al lado

La pose de apoyarse seguía saliendo mal: en vez de llevarla hasta donde hay algo
para apoyarse, el modelo **le agregaba un mueble o una pared** justo al lado, en el
mismo punto donde ya estaba.

Las cuatro poses de apoyo (la de perfil y la "fundida con el ambiente", en el
listado de mujer y en el de hombre, más las dos de Seedream en inglés) ahora dicen
expresamente: **prohibido agregar un mueble, una pared, una baranda o un objeto que
el lugar no tenga; movela HASTA DONDE de verdad hay algo** (la pared, el marco de
una puerta, una columna, el mostrador). Si en todo el lugar no hay nada, no se
apoya en nada.

## 15 ángulos, con nombres que se entienden (v2.50.0)

Los 9 ángulos de la v2.49.0 tenían nombres de fotógrafo ("contrapicado suave",
"teleobjetivo", "gran angular") que no decían nada si no sos fotógrafa. Ahora son
**15** y cada uno se llama por lo que hace el fotógrafo:

| Nombre en el desplegable | Qué hace el fotógrafo |
|---|---|
| Normal, de frente | La de siempre: parado enfrente de ella. |
| Desde más abajo | Se agacha hasta la cintura y apunta un poco para arriba: las piernas se ven largas. |
| De costado, en diagonal | Se corre bastante a un costado: se ve el fondo en perspectiva. |
| Desde más arriba | La saca desde un poco más alto que su cara. Como cuando te la saca alguien más alto. |
| De lejos, con zoom | Se aleja y usa zoom: el fondo queda pegadito atrás y todo más prolijo. |
| Muy cerca, a un paso | Se le pone casi encima: foto íntima, como sacada por una amiga. |
| Desde el piso | Apoya la cámara casi en el piso: se ve el techo o el cielo detrás. |
| Bien lejos, todo el lugar | Se va bien atrás y entra todo el ambiente: ella chiquita adentro del lugar. |
| Desde arriba de todo | Se sube a una escalera o un banquito y la mira bien desde arriba. |
| Un paso al costado | Un pasito al costado: casi de frente pero el lugar deja de verse plano. |
| De cerca, se ve el lugar | Se acerca con un lente que agarra más ancho: se ve más del ambiente. |
| De costado del todo | Se para justo al lado y la toma de perfil, sin cambiarle la pose. |
| De abajo y de costado | Las dos juntas: agachado y corrido. El más "de revista". |
| Agachado, al pecho | Se agacha un poco, a la altura del pecho. Cambio chiquito que ya se nota. |
| Espiando, medio tapada | Desde atrás de unas plantas, una puerta o una percha: parece robada. |

**Además, en la pantalla hay un listado desplegable** ("📷 ¿Qué es cada ángulo?")
con esta misma explicación, tanto en el selector de poses del set como al lado
del campo de la foto suelta. Sale armado del mismo listado que usa el prompt, así
que si mañana se agrega un ángulo aparece solo también ahí.

**El orden importa:** están acomodados para que "🎲 Variado" alterne — alto, bajo,
lejos, cerca — y no te salgan dos parecidos seguidos. Un set de hasta 14 tomas no
repite ningún ángulo.

**En lencería y mallas** son tres los que se saltean cuando rota solo (los que
miran desde abajo, que hacen rebotar la imagen en Seedream). Quedan 12 rotando,
así que la variedad no se resiente. Si los elegís vos, se respetan igual.

## El ángulo de cámara ahora lo elegís vos, toma por toma (v2.49.0)

En la v2.48.0 la cámara empezó a moverse sola, pero no alcanzaba: no había forma
de decir *esta* toma la quiero desde abajo y *esta otra* de lejos. Ahora hay un
**selector de ángulo al lado de cada pose**.

**Dónde está:**

- **Set de poses** (🎬 Elegir poses del set): al lado de cada tilde, su propio
  desplegable de ángulo.
- **Foto suelta** (Generar): un campo "Ángulo de cámara" al lado de "Pose".
- **Regenerar una toma puntual**: un desplegable al lado del de la pose.

**Las opciones son las 9 de siempre** (ojos de frente · contrapicado suave ·
picado suave · diagonal a 45° · desde el piso · picado alto · teleobjetivo de
lejos · gran angular de cerca · foto robada) más **🎲 Variado**, que es lo que
viene puesto: rota sola y no repite ángulo dentro del set.

**Tres cosas que cambiaron por dentro:**

1. **"Variado" ahora rota por posición en el set, no por número de pose.** Antes,
   tildar la pose 1 y la 10 daba el mismo ángulo (hay 9 posiciones de cámara y el
   número de pose daba la vuelta). Ahora la primera toma del set usa el primer
   ángulo, la segunda el segundo, y así: nunca se repite.
2. **En lencería y mallas, "Variado" rota entre los ángulos seguros** en vez de
   mandarlos todos a la cámara neutra. Antes, esquivar los dos contrapicados que
   hacen rebotar la imagen en Seedream hacía que dos tomas salieran desde el
   mismo lugar — justo lo que se quería arreglar. Ahora esquiva esos dos y sigue
   habiendo variedad. **Si elegís vos el ángulo, se respeta igual**, aunque sea
   lencería: es tu decisión (ojo que ahí Seedream puede rebotar la imagen).
3. **Se sacó una regla vieja que clavaba la cámara.** El bloque de física del
   prompt decía "CÁMARA: a la altura de los ojos de un fotógrafo parado (~1,60 m)"
   con un "salvo que se pida otro ángulo" que el modelo ignoraba. **Ésa era la
   razón de fondo por la que todo salía frontal**, y por la que la v2.48.0 se
   notaba tan poco. Ahora esa regla dice que la altura la fija el renglón de
   cámara de la toma, y que la línea del horizonte tiene que corresponder a esa
   altura (cámara baja → se ve el techo; cámara alta → se ve más piso).

Si escribís la pose a mano y además elegís un ángulo, **valen las dos cosas**:
la pose sale tal cual la escribiste y la cámara se para donde pediste. Y el
ángulo nunca cambia el encuadre de la prenda: eso lo sigue mandando el encuadre.

Anda igual en los dos motores, Nano Banana y Seedream/FLUX.

## Encuadre de la prenda (adultos, v2.44.0)

El "Encuadre" escrito viajaba como un renglón más de la puesta en escena, y
cada pose del set trae su propio tamaño de plano ("CUERPO ENTERO", "PLANO
MEDIO") con la orden de respetarlo "exactamente". La pose siempre le ganaba, y
una bombacha salía de pies a cabeza. En Opciones avanzadas hay ahora un
selector **Encuadre de la prenda**: prenda de ABAJO (de la cintura para abajo,
sin cara), prenda de ABAJO de cerca (cintura a rodillas), prenda de ARRIBA (de
la cintura para arriba, con cara), prenda de ARRIBA de cerca (hombros a
cintura), plano medio, o cuerpo entero siempre. Ese encuadre MANDA sobre el
tamaño de plano de cualquier pose: la pose aporta la postura, la orientación y
el gesto, y a las poses del pool se les saca el "CUERPO ENTERO" / "FULL BODY".
Vale para la foto suelta, el set de poses (incluidos los paneles: todos con el
mismo encuadre) y el set de colores, en Nano Banana y en fal. Se guarda en las
plantillas de artículo. El campo de texto libre sigue existiendo como
"Encuadre extra". Si Nano Banana bloquea una toma sin cara (a veces pasa con la
cintura para abajo en lencería), el reintento seguro va sin el encuadre por
zona; con Qwen o FLUX no hay ese problema.

## Videos de producto (pestaña 🎬 Videos, en `/videos`)

Hace el video de vidriera blanca: tu modelo con tu prenda parada en un limbo
blanco infinito —sin paredes, sin esquinas, sin horizonte, sólo la sombra de
contacto en el piso— y la cámara entrando hacia el detalle de la prenda.

Subís la foto de tu publicación (la que YA tiene a la modelo con la prenda
puesta) y sale de ahí: misma cara, mismo cuerpo, misma prenda.

**Son dos etapas, y por eso funciona.** Primero cada toma se dibuja como FOTO
con el mismo motor de imágenes de Luma (plano entero, medio 3/4, espalda, los
macros y el hero). Después cada una de esas fotos es el PRIMER CUADRO
literal del clip, y al modelo de video sólo se le pide el movimiento de cámara.
Los modelos de video no saben ponerle tu prenda a una modelo: si el clip arranca
de un flat-lay, inventan la prenda y la cara. Arrancando de un cuadro que ya es
exactamente lo que querés, sólo tienen que moverlo.

La PRIMERA toma es el ancla: las demás se generan mirándola, y por eso la cara,
la luz y el blanco no cambian de toma en toma. El inspector de prenda (el mismo
de las fotos) revisa esa toma ancla: si la prenda salió distinta de la real, la
rehace antes de gastar un peso en video.

### Varias modelos, varios colores (los "looks")

Un **look** es una modelo con su color. Si querés un video con cuatro modelos,
cada una con la prenda en otro color y cada una haciendo su toma, se prende
**"Varias modelos / varios colores"** arriba de las fotos:

- Cada foto muestra un numerito arriba a la izquierda: tocalo para mandarla al
  look 1, 2, 3 o 4. Tocando la foto (no el numerito) la ponés como la que
  **manda** en su look: de ella salen la cara, el cuerpo y el color.
- A cada look le podés poner nombre ("Coral", "Azul"). Ese nombre viaja al
  prompt como el color de esa toma, que es lo que evita que se le escape el
  color de la toma de al lado.
- Abajo de las tomas aparece **"De qué look sale cada toma"**: una fila por
  toma, en orden, y elegís de qué look sale.
- Hasta 4 looks y 12 fotos por video.

**Por qué hace falta y no alcanza con subir las fotos juntas.** El ancla es una
sola por look, no una por video: sin esto, las cuatro modelos salían con la cara
de la principal, porque todas las tomas miran a la primera. Y las fotos que no
son la principal entran al prompt como *la verdad del diseño de la prenda*, así
que pasarle cuatro colores juntos es pedirle una prenda de cuatro colores a la
vez — de ahí salían los tonos raros. Ahora cada toma ve SÓLO las fotos de su
look.

Ojo con el gasto: el inspector de prenda revisa **una vez por look** (antes era
una sola vez en todo el video), y cada revisión que sale mal paga un cuadro
extra. Con 4 looks eso son, como mucho, 4 cuadros más.

- **Las tomas las elegís vos.** Tocás las que querés y quedan en el orden en que
  las tocaste (el numerito del chip). Hasta 8 por video. Además del plano
  entero, el 3/4, la espalda, la caminata y el hero, están los macros: el del
  **frente** (escote, drapeado, costura), el de la **espalda** (breteles,
  cierre, terminación de atrás) y el de **abajo** (short, bombacha o bikini:
  cintura, ruedo, cómo calza).
- **Y si el detalle que querés no está en la lista, lo escribís vos.** El botón
  "+ Una toma mía" agrega un renglón donde ponés qué se ve, en castellano ("primer
  plano del ruedo del short, de costado"). Ese texto es el encuadre del cuadro
  llave, y para el clip se traduce solo al inglés, que es el idioma en el que los
  motores de video entienden mejor. Hasta 4 tomas tuyas por video.
  El renglón muestra el número de orden de esa toma, o un "–" si quedó afuera de
  la lista: escribir el texto no alcanzaba, había que tener el chip prendido, y
  el texto seguía en pantalla igual. Ahora, si escribís en una que estaba
  afuera, vuelve sola; y si igual quedó afuera, al generar te frena y te avisa
  en vez de sacar el video sin ella.
### Bajarle el precio: quién mueve cada toma

**El motor de video es el 90% de lo que sale un video.** Las 5 tomas de 6s por
defecto son US$0,51 de cuadros y US$4,50 de video. Por eso lo que baja la cuenta
no es ahorrar en las imágenes, es elegir bien quién mueve cada toma. En la lista
**"El video, toma por toma"** cada una tiene su motor:

- **Con IA** (Veo / Wan): mueve a la modelo de verdad — respira, camina, la tela
  se sacude. Es lo único que sirve donde el cuerpo se mueve.
- **Sólo cámara** (US$0): el movimiento lo hace ffmpeg recortando el cuadro,
  como en una mesa de edición. Tarda segundos, no cuesta nada, y el detalle sale
  pixel por pixel de la foto: no hay forma de que invente una costura. La contra
  es que mueve la CÁMARA, no a la modelo — en un macro no se nota, en un plano
  entero la modelo queda congelada y sí se nota (el panel te avisa en esas
  tomas).

Y con **"Mis fotos YA son las tomas"** no se dibuja ningún cuadro: la foto 1 es
la toma 1, la foto 2 la toma 2, y no pagás imágenes. Sirve cuando ya tenés las
fotos hechas en Luma y sólo querés el armado.

Las dos cosas juntas, en un video de 6 tomas con 2 de IA y 4 de cámara:
**US$1,80 contra US$6,01**. El estimador ya lo muestra desglosado antes de
generar.

- **La prenda deja de cambiar de toma en toma.** Antes de dibujar nada, Luma
  mira tus fotos y escribe una ficha de la prenda: el color exacto de cada
  parte, la tela y los detalles. Esa ficha va como TEXTO en todas las tomas —el
  texto no se desvía, una foto de referencia sí se interpreta— y el inspector
  revisa TODAS las tomas, no sólo la primera. Sin esto, un pijama bordó con
  encaje negro salía con la espalda negra satinada y lisa: otra prenda.
- **Marcá de qué lado es cada foto.** Debajo de cada una dice FRENTE, y
  tocándola cambia a PERFIL o ESPALDA. Con eso, el análisis de la prenda sabe
  cuál es la espalda de verdad (y si no le diste ninguna, avisa "deducida, sin
  foto" en vez de inventarla), y el inspector compara una toma de atrás contra
  las fotos de atrás, no contra el frente.
- **Los NO salen del análisis de TU prenda.** El mismo análisis que usa la app
  de fotos devuelve una lista de errores típicos para esa prenda en particular
  ("no inventar encaje atrás", "mantener la escala de la estampa") y esos NO
  viajan en todas las tomas. Y en el video hay un bloque de NO fijo —no cambies
  la cara, no camines en el lugar, nada de cámara lenta— escrito DENTRO del
  prompt y no sólo en el campo de prompt negativo, porque ese campo lo lee Veo
  pero los motores de fal ni lo reciben.
- **La modelo entiende qué está vendiendo.** Cada toma lleva su intención: la de
  espalda está para mostrar los breteles y el cierre, la de abajo para mostrar
  cómo calza en la cadera. La pose, la mirada y las manos tienen que servir a
  eso. Antes salían poses lindas pero mudas, que no señalaban nada.
- **La caminata deja de parecer una cinta de correr.** La cámara está fija en
  trípode y la modelo se ACERCA: arranca chiquita y termina mucho más cerca. Si
  su tamaño en el cuadro no cambia, la toma está mal — y así salía.
- **Transiciones.** Cortes secos (lo que hacen las marcas, y sigue siendo lo que
  viene puesto), fundido a blanco o fundido cruzado, los dos de 0,35s. Sirven
  cuando una toma entera va pegada a un macro: el salto de tamaño pega feo. El
  fundido a blanco, sobre fondo blanco, casi no se nota y es el que mejor tapa
  ese salto.
- **Los macros ya no terminan en una mancha.** Pedían "la tela llenando todo el
  cuadro" y terminaban en una pared de color sin nada que mirar. Ahora cierran
  en un plano corto pero LEGIBLE, donde el detalle todavía se reconoce.
- **Un trabajo nunca queda colgado.** Termina siempre en "listo" o en "error".
  Si algo falla antes de empezar —el KV que no contesta, el disco lleno— ahora
  se ve el error en vez de quedar en "En cola…" para siempre. Y si pasan más de
  4 minutos sin novedades, el panel lo dice y te sugiere frenar: si ya hay tomas
  hechas, te arma el video con esas.
- **Cada toma dura lo que vos querés.** En la lista "El video, toma por toma",
  al lado del motor, elegís los segundos de ESA toma: con IA 4, 6 u 8 (lo que
  aceptan los motores) y con cámara 2 a 6. Una caminata necesita tiempo para
  que la modelo cruce el cuadro; un macro sobre una foto quieta a los 3 segundos
  ya mostró todo. Con un único número para todo el video, o la caminata quedaba
  corta o los macros eternos. El precio y el largo total se actualizan solos.
- **La toma de cámara se iguala a la calidad del motor.** El cuadro llave sale
  en 2K y Seedance Lite entrega 720p: pegadas una al lado de la otra, la
  diferencia canta y el video parece armado con dos cosas distintas. Ahora la de
  cámara se baja a la misma resolución real que entrega el motor. Si querés las
  dos nítidas, el que sube es el motor (Seedance Pro sale a 1080p).
- **El macro del short se toma DESDE EL COSTADO**, a la altura de la cadera. De
  frente y centrado es la toma que más falla: la rechaza el filtro de contenido
  o sale rara. De costado se ve mejor el calce y el ruedo, y no se traba.
### Sacarle el fondo a tus fotos (en vez de generar nada)

El chip **"Sacarle el fondo a mis fotos"** hace lo que harías en Canva, pero
armado: le saca el fondo a cada foto tuya, la pega sobre blanco y le dibuja la
sombra de contacto. **Eso** es cada toma, en orden.

No se dibuja nada. La prenda y la cara son las de tu foto **pixel por pixel**,
así que no hay forma de que cambien el color, el diseño ni la cara — que es todo
lo que veníamos peleando. Y sale **US$0,004 por foto** contra los ~US$0,10 de
dibujar el cuadro de cero.

La contra es una sola, y es grande: **sólo tenés las poses que ya fotografiaste**.
Si no tenés una foto de espalda, no hay toma de espalda. Generar sirve para
inventar tomas que no existen; recortar sirve para que las que sí existen queden
perfectas.

Combinado con "Sólo cámara" en todas las tomas, un video sale por centavos y no
pasa por ningún modelo generativo.

- **Una toma nunca abre el plano.** El motor de video sólo tiene el PRIMER
  cuadro: todo lo que no está ahí lo tiene que inventar, y lo inventa. Si la
  toma arranca cerrada o de espaldas y el movimiento abre para mostrar más, la
  cara que aparece es una cara NUEVA — otra persona. Por eso ahora las tomas
  sólo se acercan, nunca se alejan, y en las que no se ve la cara (los macros y
  la espalda) está prohibido que aparezca. El giro del 3/4 también se achicó:
  girar hasta quedar de frente obligaba a inventar la mitad de la cara que no
  estaba en la foto.
- **La modelo MODELA.** Cada toma tiene una acción concreta —corre el pelo para
  despejar la espalda, sigue la costura con los dedos, cambia el peso de pierna,
  baja la mirada a la prenda y la vuelve a subir— y las manos siempre hacen algo
  con intención, nunca cruzadas adelante. A una modelo no le pagan por ser
  linda: le pagan por vender la prenda.
- **El inspector ahora se ve.** Cada toma muestra su nota (`prenda 9/10`) al
  costado, verde si aprobó y roja con el detalle de las diferencias si no. Antes
  sólo quedaba registro cuando corregía, así que un inspector que no corría y
  uno que aprobaba una prenda equivocada se veían igual: en blanco. Y podés
  subirle la exigencia desde el panel — flojo (7), normal (9) o exigente (10)—
  sin tocar Ajustes. Si te aprueba una prenda que está mal, ponelo en 10.
- **Botón de frenar.** Mientras el trabajo corre hay un ✋ Frenar. Corta entre
  toma y toma: la que está en curso ya se pidió y ya se paga, pero todo lo que
  venía después no se gasta. Si frenás durante los cuadros, no se toca un peso
  de video y los cuadros hechos quedan guardados. Si frenás durante el video, se
  arma igual con las tomas que ya estaban pagas — se entrega y se sube a Drive,
  en vez de tirar a la basura lo que ya se gastó.
- **Mirá los cuadros primero.** El botón "Ver los cuadros primero" genera sólo
  las fotos (centavos) y no toca el video. Si te gustan, generás el video.
- **Tope por video.** Si el video no entra en el tope que pusiste, saca tomas
  del final y te avisa cuáles. El tope MENSUAL de Presupuesto sigue mandando
  igual, y lo gastado se anota aunque el trabajo falle a la mitad.
- **Audio**: mudo (como los videos de las marcas), o con locución argentina y
  subtítulos. La música se sube una vez y queda para todos tus videos.
- **Precio**: se ve antes de generar. Las 5 tomas de 6s que vienen por defecto
  salen ~US$5 en Veo Fast; el mismo video con Wan (necesita `FAL_KEY`), ~US$2.
  Los motores, de más barato a más caro por segundo (a 1080p, que es lo que se
  pide porque el video se entrega en 1080x1920):

  | Motor | US$/s | 5 tomas de 6s |
  |---|---|---|
  | **Seedance Lite** (el que viene puesto) | **0,036** | **1,08** |
  | LTX 2.3 Fast (fal) | 0,04 | 1,20 |
  | Wan 2.6 (fal) | 0,05 | 1,50 |
  | Veo 3.1 Lite | 0,08 | 2,40 |
  | LTX 2.5 Fast (fal) | 0,13 | 3,90 |
  | Seedance Pro (fal) | 0,148 | 4,44 |
  | Veo 3.1 Fast | 0,15 | 4,50 |
  | LTX 2.5 Pro (fal) | 0,17 | 5,10 |
  | MiniMax H3 (fal) | 0,26 | 7,80 |
  | Veo 3.1 | 0,40 | 12,00 |

  **El que viene puesto es Seedance Lite**, porque en la prueba real salió mejor
  que Veo Fast y que Wan — y sale la cuarta parte que el Veo que estaba puesto
  antes. Manda lo que se vio, no lo que decía la ficha técnica. Se cambia desde
  el panel, o para todos los videos con `VIDEOS_MOTOR_DEFAULT`.

  **Los nuevos no son más baratos: son más caros.** LTX 2.5 Pro y MiniMax H3
  salen MÁS que Veo Fast. Si querés probar uno, probalo en UNA toma antes de
  mandarle el video entero: la diferencia se paga por segundo y por toma.

  **Seedance Pro es el mismo Seedance pero a 1080p** en vez de 720p. Si el Lite
  te gustó, ese es el candidato más obvio a mejorarlo: misma familia, cuatro
  veces el precio, el doble de resolución.
- **Si elegís un motor y no se usa, ahora te lo dice.** Antes, un motor que este
  archivo no conocía —el caso típico es el panel viejo que quedó en la caché del
  navegador— caía a Veo Fast en silencio, y el síntoma era "cambié el motor y me
  siguió usando Gemini". Y si elegís un motor de fal sin la key cargada, el
  trabajo se frena ANTES de dibujar los cuadros, que son los que se pagan.

Las tomas que vienen marcadas (caminata → giro → espalda → macro → hero, 30
segundos) salen de medir un video de catálogo real: 6 clips de 5 segundos,
cortes secos y sin audio. El zoom pasa ADENTRO de cada clip, no sólo al cortar:
la toma de espalda arranca con la modelo entera y termina en primer plano de
los breteles.

**Por qué antes se veía "de IA".** Los modelos embellecen solos: alisan la piel,
emparejan la cara, afinan el cuerpo y planchan la tela, y ahí la modelo deja de
parecer una persona. Ahora se les pide lo contrario con nombre y apellido —
poros, lunares, líneas de expresión, el mismo cuerpo de la foto, la tela con sus
arrugas—. En el movimiento pasaba lo mismo por otro lado: salía en cámara lenta,
con la modelo dura como maniquí y los pies patinando sobre el piso. Ahora el clip
va a velocidad real, la modelo respira y parpadea, los pies apoyan de verdad
(talón y punta) y la cámara va sobre slider, firme, en vez del temblequeo de
cámara en mano que le pedíamos antes.

Variables opcionales en Railway:
- `FAL_KEY` — habilita los motores de fal (Wan, Seedance, MiniMax H3).
- `FAL_MINIMAX_MODEL`, `FAL_LTX_PRO_MODEL`, `FAL_LTX_FAST_MODEL`,
  `FAL_LTX23_MODEL` — sólo si fal les cambia la ruta a esos modelos. Igual que
  `FAL_WAN_MODEL` y `FAL_SEEDANCE_MODEL`: se corrige sin tocar el código.
- `VIDEOS_PRECIO_MINIMAX`, `VIDEOS_PRECIO_LTX_PRO`, `VIDEOS_PRECIO_LTX_FAST`,
  `VIDEOS_PRECIO_LTX23` — si fal les cambia el precio. **Los precios salen de la
  documentación pública, no de una factura**: si ves que no coincide con lo que
  te cobran, corregilo acá y el estimador vuelve a decir la verdad.
- `VIDEOS_PREFIX` — si querés los videos en otra ruta que no sea `/videos`.

Dos cosas para tener en cuenta:
- **Veo necesita una key de Google con facturación habilitada.** Sin eso te
  responde 403 y hay que usar Wan o Seedance.
- **Los videos se guardan solos en tu Google Drive** apenas están listos, y el
  panel te dice si entraron de verdad (con el link) o si falló. Antes no había
  UNA línea de Drive en los videos: quedaban sólo en el disco del server, que
  sin un volumen montado en `/data` se borra en CADA deploy. Un video son varios
  dólares: perderlo es pagarlo dos veces.
- **Si Drive no está conectado, te avisa ANTES de generar**, arriba del botón.
  Enterarse después de que el video ya salió —y ya se pagó— no sirve de nada.
  Se conecta en Ajustes → Google Drive.
- **A Drive va el video Y los cuadros**, siempre. Cada cuadro es una foto de
  campaña en 2K o 4K con la modelo en fondo blanco: se paga aparte y sirve sola
  para la publicación. Un frame arrancado del video de 1080p no es lo mismo.

## Personajes (pestaña 👤 Personajes, en `/personajes`)

Tu **persona digital**: una modelo con cara y cuerpo fijos, personalidad propia,
memoria, humor que cambia con los días, y que genera contenido para la marca.
Le hablás como por WhatsApp, se saca fotos con tu ropa, te manda audios con su
voz, habla a cámara en video y cada día te propone qué publicar.

### Cómo se arma (en este orden)

1. **Crear el personaje.** Nombre, edad, ciudad, marca, personalidad, historia,
   cómo habla, qué le gusta y qué nunca hace. Y su apariencia (piel, pelo,
   ojos, contextura…) para generarle la cara. Todo se puede cambiar después
   desde la Ficha.
2. **Aprobar un retrato** (Ficha). Se genera con IA (una imagen 4K, rehacé
   hasta que te guste), o subís una foto (idealmente la original en 4K: se
   guarda a 3200 px casi sin comprimir), o usás uno de los avatares de la
   pestaña Avatares (ojo: los avatares se guardan a 1536 px aunque los hayas
   generado en 4K; si tenés el original en Drive, subilo como retrato). **Atajo:** al crear el personaje podés elegir uno de tus
   avatares directamente: su cara queda como retrato aprobado, y su ficha de
   cuerpo (contextura, altura, edad) completa la apariencia. Sólo falta la
   hoja del paso 3. Cuando aprobás, la app **estudia la cara** y guarda su
   descripción: eso, más el retrato, va en TODAS las fotos y videos que salen
   después. Por eso la cara no cambia.
3. **Generar la hoja de identidad** (Ficha). UNA imagen 4K de 3 paneles
   mirando el retrato: perfil 3/4, cuerpo entero de frente y de espalda. La
   app la corta en 3 y las guarda. Con esto queda fijo también el cuerpo.

### Qué hace

- **Charla.** Le escribís y contesta como ella, en rioplatense, corto. Si le
  pedís una foto, te contesta y aparece el botón **📸 Sacar la foto** con el
  precio: la foto se genera recién cuando tocás. Adjuntá con 📎 la foto real de
  una prenda y pedile que se la ponga: viaja como referencia de producto, con
  las mismas reglas de fidelidad de prenda que la pestaña Fotos.
- **Memoria.** De cada charla guarda hechos nuevos (hasta 40) que ve en la
  Ficha y podés editar. Los usa en las charlas siguientes.
- **Humor, energía y racha.** La energía baja si pasan días sin hablarle y
  sube con la racha de días seguidos. El humor sale de su diario y de la
  charla. Todo eso entra al cerebro: si hace 4 días que no le hablás, lo nota.
- **Hoy.** La primera vez que la abrís cada día escribe su diario (cómo
  amaneció, qué hizo) y **3 propuestas de contenido** concretas, cada una con
  escena, outfit, encuadre y caption listo, y su botón **Hacelo**.
- **🎙️ Escuchar.** Cada respuesta suya se puede oír con su voz (Gemini TTS,
  acento rioplatense; la voz se elige en la Ficha).
- **🗣️ Que hable a cámara.** Sobre una foto de la galería (o el retrato), un
  clip de 8 segundos donde dice la frase que escribas (hasta 22 palabras): Veo
  3.1 pone la voz y mueve los labios en el mismo clip. Si no sabés qué decir,
  "que lo escriba ella".
- **✨ Que se mueva.** De una foto de la galería sale un clip corto (5 o 10 s)
  con movimiento natural: respirar y mirar a cámara, caminar despacio, girar
  y volver, acomodarse el pelo, selfie en el espejo, la cámara que se acerca,
  o lo que escribas vos (en castellano, se traduce solo). Nada raro ni
  exagerado: el prompt lo prohíbe. Van por los motores de fal (Seedance, Wan,
  MiniMax) porque **no rechazan lencería ni bikinis**; Veo sí. La cara, el
  cuerpo, la prenda y el fondo son los de la foto, y el inspector revisa el
  clip al terminar. Es la forma de hacer esos videos de "modelo en ropa
  interior con movimiento natural" que se ven en Instagram, pero con TU
  personaje, no con la cara de una famosa.
- **🕺 Movete vos.** Tu video es la referencia: de él salen el movimiento,
  los gestos, la cámara y el encuadre. Es Wan 2.2 Animate por fal.ai (la misma
  key de fal que usa Videos). El modal va en 3 pasos: **1) tu video**
  (grabate en calza y remera, celular quieto, luz pareja, hasta 20 s);
  **2) cómo está vestida**: con una prenda real que adjuntás (fotos del
  producto) o con la ropa de una foto de ella; **3) el fondo**: lo describís
  y la IA lo crea, subís una foto de un lugar, el de una foto de ella, o el de
  tu video. Al tocar Generar, la app toma un cuadro de tu video y **arma la
  foto de la escena**: ella con esa ropa, en ese fondo, en tu misma postura y
  encuadre (si estás sentado, la sienta y le pone un asiento acorde). Esa
  foto queda en la galería y es la referencia del video, que sale con el
  fondo quieto. Con "el de mi video" es modo Reemplazo: ella entra en tu
  escena y queda tu audio, pero como el motor redibuja el cuadro entero el
  fondo puede "respirar" si el celular no estaba apoyado.
  **El formato también tiene que calzar.** Si tu video es horizontal y la
  escena sale vertical, el motor mete un video apaisado en un cuadro vertical:
  ella queda chiquita y lejos, aparece el borde de tu escritorio estirado y la
  escena se mezcla con la de tu video (pasó en la tercera prueba). Ahora la
  escena se genera en el formato de tu video, y si tu video es horizontal el
  modal ofrece recortarlo a vertical centrado (9:16) para reels; el cuadro
  guía se recorta igual. Lo más simple: grabate en vertical.
  **Por qué la escena tiene que calzar con tu video.** Si la referencia es de
  cuerpo entero parada y vos estás sentado en plano medio, el motor estira tu
  esqueleto para que entre en la foto y los brazos salen como tubos (pasó en
  la primera prueba). Por eso la escena se arma siempre desde un cuadro de tu
  video.
  **Cuánto tarda y la resolución.** La resolución casi no cambia el tiempo
  (ver "Tiempos reales" arriba): lo que manda son los segundos del video.
  Viene puesta 480p; 720p sale mejor y tarda casi lo mismo. El modal muestra un
  cronómetro con el estimado y el estado real de fal (en la cola, dibujando).
  **Si el server se reinicia** (un deploy) con un trabajo a medias, se retoma
  solo desde fal al volver a la galería. Y si un trabajo se perdió del todo,
  en el modal hay "Recuperar de fal": pegás el request id del panel de fal
  (fal.ai → Requests → Copy request id) y lo termina acá.
  **Motores** (se elige en el paso 1):
  *Wan 2.2 Animate* (~US$0,08/s): arma la foto de la escena primero y después
  el video; acepta lencería. *One-to-All Animation* (~US$0,10/s, a
  confirmar): pesos abiertos, transferencia "sin alineación" para cuando tu
  encuadre y el de la foto no calzan; sólo anima sobre el fondo de su foto.
  *Seedance 2.0* y *rápido*: quedan en la lista pero **rechazan videos con
  personas reales** (política de ByteDance, probado el 14/9: "may contain
  likenesses of real people"); con tu video no sirven. Kling quedó afuera:
  rechaza bikinis y lencería.
  **Tiempos reales.** Wan tardó ~75 s de proceso por cada segundo de video,
  casi igual a 480p (1234 s por 16 s) que a 720p. Un video de 16 s son 20
  minutos; uno de 6 s, unos 8. Grabá corto.
  **Modelos.** Reemplazo: `fal-ai/wan/v2.2-14b/animate/replace` (el último
  Animate que hay en fal). Animación: `fal-ai/wan/v2.2-14b/animate/move`.
  Existe una versión liviana (`fal-ai/wan-motion`, más rápida y barata) que se
  activa con `FAL_ANIMATE_MOVE_MODEL`, pero en la prueba real deformó los
  brazos. Seedance: `bytedance/seedance-2.0/reference-to-video` y
  `…/fast/reference-to-video` (`FAL_SEEDANCE_REF_MODEL`,
  `FAL_SEEDANCE_REF_FAST_MODEL`; precios en `PERSONAJES_PRECIO_SEEDANCE_REF`
  y `…_FAST`).
  **El inspector revisa el video.** Cuando el clip está listo, el mismo
  inspector de prenda de Fotos mira 3 cuadros (principio, medio, final) y te
  da una nota de 1 a 10 con lo que cambió. Adjuntá las fotos reales de la
  prenda en el modal para que compare contra el producto; sin fotos, compara
  contra la foto de referencia. La nota queda en la galería. Lo mismo pasa con
  los clips de "que hable a cámara". (Se apaga con `qc_prenda = no` en
  Ajustes, igual que en Fotos.)
  **Prenda en la mano:** todo lo que queda dentro de tu silueta se redibuja,
  la prenda incluida. Si la querés mostrar sin que la toque el motor, dejala
  colgada en una percha o sobre una mesa AL LADO tuyo, fuera de tu silueta.
- **🎬 Video.** Cualquier foto de la galería se manda a la pestaña Videos con
  un toque y entra como la foto principal del video de vidriera. Mismo flujo
  que con una foto de publicación: cuadros llave y movimiento toma por toma.
- **Galería.** Todo lo que sale (fotos y clips) queda ahí, con su caption
  editable, para rehacer (↻ con una corrección), bajar o borrar. Si Drive está
  conectado, cada foto y clip se sube solo.

### Entrenar un LoRA de ella (Ficha → Entrenamiento)

Le enseña su cara y su cuerpo al modelo de video Wan 2.2 (trainer de fal) con
la hoja, las fotos de la galería y sus videos, todo con su frase gatillo
(`NOMBRE_PJ`). Después los videos salen con ella sin foto de referencia por
pedido, y en foto a video la cara se corre menos.

- **Texto a video** aprende de fotos (y videos si hay). Sirve para el botón
  "Video 5 s" de la ficha: escribís qué hace y sale ella.
- **Foto a video** necesita VIDEOS de ella en la galería (hacé antes un par
  con "Que se mueva" o "Movete vos"). Sirve para "Que se mueva" eligiendo el
  motor "Wan 2.2 con su LoRA".
- Empezá con la **prueba corta de 100 pasos** (~US$0,40): tarda unos 10
  minutos y te dice si vale la pena el completo de 1000 (~US$4, 30 a 60 min).
  Lo ideal son 15 o más fotos; con la hoja y 10 fotos de la galería alcanza
  para probar. Se sigue en Galería → En curso y se retoma solo si el server
  se reinicia. Hasta 6 LoRAs por personaje.
- Variables: `FAL_WAN_TRAINER_T2V`, `FAL_WAN_TRAINER_I2V`, `FAL_WAN_LORA_T2V`,
  `FAL_WAN_LORA_I2V` (rutas en fal), `PERSONAJES_PRECIO_PASO_T2V/I2V` y
  `PERSONAJES_PRECIO_LORA_SEG` (precios).

### Qué cuesta

Los precios salen de los Ajustes (los mismos de Fotos) y todo pasa por el
mismo tope mensual del Presupuesto:

- Retrato: una imagen 4K. Hoja de identidad: una imagen 4K (se cambia con
  `PERSONAJES_CALIDAD_IDENTIDAD`). Los personajes nuevos sacan fotos a la
  calidad de Ajustes (4K por defecto); se baja desde la Ficha.
- Cada foto: una imagen a la calidad de la Ficha (1K, 2K o 4K).
- Charla y diario: centavos por mensaje (no se anotan en el ledger para no
  llenarlo de ruido).
- Audio: ~US$0,02. Clip hablando: 8 segundos del motor elegido (Veo Fast
  ~US$1,20; Veo estándar ~US$3,20). **Veo necesita una key de Google con
  facturación habilitada**, igual que en Videos.
- Que se mueva: los segundos del motor elegido (Seedance ~US$0,18 el clip de
  5 s; Wan ~US$0,25; MiniMax ~US$1,30).
- Movete vos: ~US$0,08 por segundo de tu video (un reel de 15 s, ~US$1,20).
  El precio es el de la documentación de fal: si no coincide con lo que te
  cobran, corregilo con `PERSONAJES_PRECIO_MOVETE`.

### Variables (opcionales)

- `PERSONAJES_PREFIX` — otra ruta que no sea `/personajes`.
- `PERSONAJES_TEXT_MODEL` — el modelo del cerebro (default `gemini-3.6-flash`;
  Google dio de baja `gemini-2.5-flash` para cuentas nuevas el 14/9/2026). Si
  Google jubila otro modelo, la app lee el que sugiere en el error y reintenta
  sola con ese (también en Videos, para el guion y las traducciones).
- `PERSONAJES_TTS_MODEL` — el modelo de voz (default `gemini-2.5-flash-preview-tts`).
- `PERSONAJES_PRECIO_MOVETE` — US$ por segundo de Movete vos (default 0.08).
- `PERSONAJES_MOVETE_MAX_SEG` — tope de segundos por video (default 20).
- `FAL_ANIMATE_REPLACE_MODEL` / `FAL_ANIMATE_MOVE_MODEL` — si fal le cambia
  la ruta a Wan Animate (default `fal-ai/wan/v2.2-14b/animate/replace` y `…/move`).

Dos cosas para tener en cuenta:
- **El personaje es inventado.** No uses la cara de una persona real que no
  sea vos: legalmente y para las plataformas tiene que ser sintético. Instagram
  y TikTok piden etiquetar el contenido generado con IA.
- **La cara en video todavía puede correrse un poco.** Por eso el clip arranca
  de una foto que ya es exactamente ella, y el prompt le prohíbe redibujarla.
  Si en un clip se corre, rehacelo: no cobran distinto por rehacer.

## Reels: "Mirá lo que llevo puesto hoy" y la cámara recorriendo la foto (v2.12.0)

### Plantilla nueva: outfit del día

Se suma a las plantillas de guion **"Mirá lo que llevo puesto hoy"**. El guion
arranca literalmente con esa frase (o la misma idea con sus palabras), y después
va **pieza por pieza, de arriba hacia abajo**: una sola pieza por tramo, cómo se
siente puesta y qué le gusta de ella, con datos reales del producto. Un tramo
cuenta con qué la combinó o para qué la usa, y el último dice dónde conseguirla.

Habla **en primera persona y en presente**, como frente al espejo ("me puse",
"llevo", "esto que tengo acá"), y tiene prohibido el tono de catálogo. Si el
producto es de una sola pieza, en vez de ir pieza por pieza recorre sus partes
(el escote, la espalda, el detalle, el largo).

Viene con tono chetita, ambiente casa, 30 segundos, sin micrófono de mano, y con
precio y talles sobre el video.

### Los tramos que salen de una foto ahora recorren la prenda

En los tramos donde no hay video de ella ni video tuyo, el reel usa las fotos del
producto. Antes eso era un **zoom al centro**, para adelante o para atrás: la
foto se acercaba pero nunca mostraba nada nuevo.

Ahora la cámara **viaja de verdad por la prenda**. Hay quince recorridos que van
rotando, así dos trozos seguidos nunca muestran lo mismo:

- **De la prenda de arriba:** del escote a la cintura · entrando al escote · de la
  cintura al escote · del bretel al escote · del hombro al centro del pecho.
- **De la prenda de abajo:** de la cintura a la cadera · entrando al detalle de
  abajo · de la cadera de un lado al otro · del tiro a la pierna · de la cadera
  al ruedo.
- **De toda la prenda:** entrando al detalle del medio · bajando por el costado ·
  de un costado al otro · saliendo del detalle al conjunto · del bretel a la cadera.

**Están ordenados alternando arriba / abajo / todo**, así que si la prenda es de
dos piezas, la de abajo tiene su turno igual que la de arriba — no se lleva todo
el tiempo el top.

Y si la prenda es de **una sola mitad**, no pasea por la que no existe: un corpiño
o una remera usan sólo los de arriba (más los de toda la prenda), y una bombacha
o una calza sólo los de abajo. Eso sale del título y la descripción del producto;
ante la duda, recorre todo.

**Un límite geométrico que había que respetar:** con zoom *z* la ventana ocupa
1/z del cuadro, así que el centro no puede acercarse al borde más de 1/(2z).
Pedir mirar a la altura y=0,72 con zoom 1,55 era **imposible** y ffmpeg lo
recortaba en silencio a 0,677: el recorrido no llegaba adonde decía. Ahora el
zoom **sube solo** lo justo para que el punto pedido sea alcanzable (hasta un
máximo de 2, donde la foto empieza a verse blanda).

Hay un selector nuevo, **"Los tramos que salen de una foto"**, con las dos
opciones: *Recorriendo los detalles* (lo que viene puesto) o *Zoom simple al
centro* (como antes).

**Un detalle de nitidez:** la foto se agranda al doble del tamaño de salida antes
de recorrerla. Con el lienzo justo, un zoom de 1,7 estaba estirando 1080 px a
1836 y la tela salía blanda.

## Reels (pestaña 🎞️ Reels, en `/reels`) — etapa 1

Un reel vertical de Instagram donde el Personaje habla a cámara desde el
local y, entre medio, aparecen tomas de la prenda sola mientras su voz sigue.
Se entra desde Personajes (botón "🎞️ Reel" en la ficha, o el link del
encabezado). Cinco pasos, y no se gasta en video hasta el último:

1. **Producto.** Pegás el link (Tiendanube o Mercado Libre) y "Leer el link"
   trae título, descripción, precio y fotos; ML por su API pública cuando
   responde (con talles y colores), si no por la página. O cargás los datos a
   mano y subís las fotos. Elegís tono, dónde está ella (local, depósito,
   showroom, casa), duración (25/35/45 s) y cómo está vestida.
2. **Guion y voz.** Gemini escribe el guion en 5 o 7 tramos alternados: ella a
   cámara (gancho y cierre con llamado a la acción) y producto (tela, calce,
   colores, talles, precio con datos reales). Lo corregís, sumás o sacás tramos
   y "Generar voces" graba cada tramo con la voz del Personaje: ahí ves cuánto
   dura cada uno.
3. **Escenas.** Por cada tramo de ella, una foto 9:16 con Nano Banana: ella en
   el lugar elegido, la prenda de las fotos reales apoyada al lado en el
   mostrador, con distinto encuadre en cada una. Rehacés la que no te guste o
   subís la tuya.
4. **Reel.** Cada escena va a OmniHuman 1.5 (fal) con su tramo de audio: ella
   habla con labios, cara y manos sincronizados. Los tramos de producto son
   flashes de las fotos de la prenda con zoom lento (ffmpeg, sin costo). Se
   pegan los tramos, se ponen los subtítulos quemados y sale 1080x1920, con
   copia a Drive si está conectado. Si un tramo no te gusta, "Rehacer tramo"
   rehace sólo ese y vuelve a armar.

**Tus videos reales en los tramos de producto (v1.1.0).** En cada tramo de
producto hay "⬆️ Subir mis videos": hasta 3 videos tuyos (primeros planos de
la prenda, costuras, tela, filmados con el celular). Se recortan al vertical
9:16 centrado, a 1080x1920 y 30 fps, sin su audio; al armar el reel se cortan
al largo de la voz de ese tramo (repartido entre los videos si son varios; si
uno es más corto que su parte, se repite) y reemplazan a los flashes de fotos.
Quedan en el disco del servidor: si un deploy los borra, "▶" avisa y los
subís de nuevo.

**Si el server se reinicia a mitad de un reel (v1.2.0).** El trabajo va tramo
por tramo y guarda cada uno apenas sale. Si Railway reinicia (un deploy, un
corte), al volver a abrir el reel o consultar el trabajo, el vigilante lo
retoma donde quedó: los tramos ya hechos se conservan, y si un tramo de ella
estaba en fal se espera ese mismo resultado sin volver a pagarlo (hasta 3
reintentos; después avisa). Las voces, las escenas y las fotos viven en el KV
y sobreviven siempre; los videos (tramos, tus videos propios y el reel final)
viven en el disco del server, que sin un volumen montado en `/data` se borra
en cada deploy: si un tramo ya hecho desaparece, se rehace (y si es de ella,
se vuelve a pagar); el reel final queda además en tu Drive. Con un volumen en
`/data` no se pierde nada. Durante un armado largo el trabajo manda latidos,
así que nadie lo da por muerto mientras trabaja.

**Voz de influencer, mini mic y look de celular (v1.3.0).** Tres cosas que
salieron mal en el primer reel real y se corrigieron:
- *La voz.* Ya no lee "como un audio de WhatsApp": la consigna es una
  influencer argentina joven grabando a cámara, rioplatense marcado (la "y" y
  la "ll" como "sh"), voseo, sin tono de locutora. El **tono** del paso 1
  manda el estilo (canchera = rápida y con onda; cercana; divertida; seria) y
  hay un select de **voz** que arranca en "Leda · joven" (la del personaje
  suele ser más adulta). Si cambiás voz o tono, volvé a generar las voces.
  El guion también habla como influencer (mirá, posta, re, la verdad).
- *El micrófono.* "Micrófono chiquito en la mano" (sí por defecto): en la
  escena ella sostiene cerca de la boca un mini mic inalámbrico negro de
  solapa, y OmniHuman recibe la orden de no soltarlo.
- *El look.* "Look de la imagen de ella": **Celular** (por defecto) pide la
  escena como cuadro de video de celular (luces quemadas, poca nitidez,
  neblina de lente sucio, grano) y además pasa el video de ella por un filtro
  ffmpeg con ese look (bloom, negros levantados, blandura y grano);
  **Celular fuerte** exagera todo; **Limpia** deja la foto prolija sin filtro.
  Las fotos del producto y tus videos propios no se tocan.

**Cada escena con su encuadre, sus detalles y preguntas de aclaración (v1.4.0).**
En el paso 3, cada escena de ella tiene un select de **encuadre** (automático,
que va rotando entre los cuatro, o uno fijo), un campo de **detalles** (lo que
la foto no puede adivinar: qué hace con la prenda, expresión, pelo, qué hay
alrededor) y el botón **❓ Preguntame**: Gemini mira el texto del tramo, el
producto, el lugar y lo ya decidido, y hace 3 o 4 preguntas cortas con
opciones (la primera es la que recomienda). Elegís una opción o escribís la
tuya y la respuesta se suma a los detalles; después "Generar escena". Los
detalles y el encuadre quedan guardados aunque corrijas el texto del tramo.

### Etapa 2 (v2.0.0): clips IA del producto, música, precio y talles en pantalla, plantillas

- **Clips IA de la prenda.** En cada tramo de producto (paso 2) elegís "Flashes
  de las fotos · sin costo" o "Video IA de la prenda": la foto real del
  producto, recortada a 9:16, va a un motor image-to-video de fal (los mismos
  de Videos: Seedance Lite 720p, Wan 2.6 Flash, LTX 2.3 Fast, Seedance Pro;
  el motor se elige en el paso 4) con un paneo lento que muestra tela y
  detalles. Sale un clip de 5 o 10 s (si el tramo dura más, se repite) y el
  costo aparece en la tarjeta, en el resumen y en la tabla del paso 4
  (Seedance Lite ≈ US$0,18 por clip de 5 s; Wan ≈ US$0,25). Si subiste tus
  videos, mandan ellos. Si el server se reinicia a mitad de un clip, se
  retoma igual que con OmniHuman.
- **Música de fondo.** Biblioteca de pistas de tu cuenta (paso 4: "Subir una
  pista", mp3/m4a/wav de hasta 20 MB, se convierten a mp3 y quedan en el KV
  para todos los reels; hasta 8). Elegís la pista y el volumen; va en loop y
  con fade al final, siempre debajo de la voz. Además:
  - **Cómo suena** (v2.5.0): *Encima del video* es la música del reel, limpia
    (22% de volumen). *Como si sonara en el local* la filtra como un parlante
    chico del negocio (corta graves bajo 180 Hz y agudos sobre 3,8 kHz, y le
    suma una reverb cortita de ambiente) y arranca en 14%: se oye como la
    música que hay puesta en el lugar donde ella está grabando, no como una
    pista pegada encima.
  - **Arranca en el segundo** (v2.5.0): elegís desde qué parte del tema entra,
    así podés empezar en el estribillo en vez de en la intro. El botón "▶
    escuchar desde ahí" reproduce la pista desde ese punto para buscarlo.
    La pista se corta y se repite en archivos aparte antes de mezclar, porque
    combinar el salto con la repetición en un solo paso de ffmpeg deja el
    salto sólo en la primera vuelta.
  - **De dónde sacar una canción**: hay un desplegable en el paso 4 que lo
    explica. Resumen: para Instagram conviene generar el reel sin música y
    ponerla desde Instagram (licenciada, y los temas del momento ayudan al
    alcance); si la querés pegada al video, Pixabay Music y la Biblioteca de
    audio de YouTube dan mp3 gratis; y no conviene bajar un tema conocido de
    YouTube o Spotify, porque Instagram lo reconoce y puede silenciar el reel.
- **Precio, talles y llamado a la acción sobre el video.** El precio y los
  talles del producto (paso 1) aparecen en una caja arriba durante los tramos
  de producto ("$ 24.900", "Talles 85 al 100"; el precio se formatea solo:
  "ARS 24900" → "$ 24.900"), y el llamado a la acción (texto editable,
  "Escribinos por DM" por defecto) en una caja clara durante el último tramo.
  Se apagan por separado en el paso 4. Va todo en el mismo archivo de
  subtítulos (ASS), así que no depende de fuentes extra.
- **Plantillas** (arriba de "Cómo es el reel"): **Necesidad → Solución**,
  Lanzamiento, Oferta/promo, Detalle de producto y Un día con la prenda.
  La de Necesidad → Solución (v2.7.0) es la estructura que más vende y le
  impone al guion un orden fijo: el primer tramo abre con el problema de la
  clienta, como pregunta o queja y sin nombrar el producto; el segundo agranda
  la molestia; recién el tercero presenta el producto como la solución; los
  siguientes dan la prueba (tela, calce, costura, talles) con datos reales; y
  el último cierra con el alivio y el llamado a la acción. Ese orden pisa la
  regla general del gancho. Al elegir una se llenan tono,
  lugar, duración, look, mic, precio/talles y el llamado a la acción (después
  cambiás lo que quieras) y el guion sigue su enfoque (la de Detalle además
  pide clips IA en los tramos de producto).
- **Ropa interior puesta en la escena.** Si en "Cómo está vestida" ponés
  corpiño, conjunto, malla, etc., la escena lleva el mismo marco de catálogo
  de tienda que usa Fotos (el filtro de Gemini leía "selfie de celular en
  corpiño" como sugerente). Si igual bloquea, se reintenta una vez en modo
  catálogo con el look limpio, y si vuelve a bloquear el aviso dice qué
  probar (nombre de catálogo para la prenda, cambiar el modelo en Fotos →
  Ajustes, o subir tu propia escena).

**El lugar también se pregunta, y las escenas se siguen entre sí (v2.1.0).**
- *Cómo es el lugar* (paso 1, debajo de "Dónde está ella"): un campo de texto
  y el botón **❓ Preguntame sobre el lugar**. Gemini pregunta lo que la foto
  no puede adivinar del lugar: dónde va el producto (colgado en un perchero,
  apoyado en el mostrador, en una caja abierta), qué se ve detrás, cómo es el
  mueble, la luz, si hay cartel de la marca. Elegís una opción por pregunta o
  escribís la tuya y se suma al campo; eso va a todas las escenas del reel.
  No pregunta por la ropa ni por la pose: eso se decide en el paso 3.
- *Seguir la primera escena* (paso 3, tildado por defecto): la primera escena
  que generes queda de referencia, y las siguientes la reciben como imagen
  además de los retratos del personaje, con la orden de mantener idénticos el
  lugar, el fondo, la luz, la ropa y el peinado, cambiando sólo el encuadre y
  la pose. Con esto el reel deja de saltar de un local a otro entre tramos.
  Destildalo si querés que cada escena sea libre. Y cuando hay escena de
  referencia, las preguntas del paso 3 dejan de preguntar por el lugar y la
  ropa: sólo por la pose, la expresión y las manos de ese tramo.

**Que se note menos que es IA (v2.2.0).** Tres cosas del primer reel bueno:
- *El registro.* El tono **chetita** (nuevo y por defecto) es una influencer de
  Palermo: el guion se escribe con sus muletillas ("o sea", "tipo", "nada",
  "literal", "obvio"), sus adjetivos ("divino", "amo", "me muero",
  "obsesionada") y algún anglicismo de moda, y la voz lo lee con las vocales
  alargadas y la entonación que sube al final. El tono ahora manda las dos
  cosas: cómo **escribe** el guion y cómo lo **habla**.
- *La voz.* Además del registro, la consigna pide habla real: ritmo
  desparejo, pausas de verdad en las comas, una respiración entre frases,
  alguna sílaba alargada. Y el audio pasa por un **aire de micrófono real**
  (paso 1, se puede apagar): corte de graves, presencia en los 3,4 kHz,
  compresión de lavalier y una reflexión cortita del ambiente. La voz de
  Gemini sale de estudio, y esa limpieza también suena a IA.
- *La cámara.* OmniHuman devuelve el cuadro clavado: el fondo queda congelado
  píxel a píxel y eso es lo que más delata. Con **Cámara en mano** (por
  defecto) el video de ella se agranda un 5% y se recorta con un
  desplazamiento que cambia con el tiempo (dos ondas de períodos distintos por
  eje, así no se repite ni parece un vaivén): unos 10 píxeles de deriva, como
  un teléfono sostenido con la mano. Y el prompt de OmniHuman pide
  micro-movimientos todo el tiempo: inclinaciones de cabeza en las palabras
  fuertes, cejas, parpadeo, el peso cambiando de pierna, la mano libre
  gesticulando.

**Voz de piba de 19 o 20 que habla de corrido (v2.3.0).** La usuaria mandó
un reel de otra marca como referencia y se midieron las dos voces:

| | la referencia | la nuestra (antes) |
|---|---|---|
| tono de voz (F0 mediana) | 242 Hz | 190 Hz |
| huecos de 150 ms o más | 2 en 20 s | 19 en 20 s |
| el hueco más largo | 0,30 s | 0,65 s |

O sea: la voz de Gemini es de mujer adulta y para una frase por segundo. Con
eso se agregaron dos cosas.
- *Ritmo por tono.* Cada tono trae ahora su propio ritmo para la voz. El de
  **chetita** dice: rápido y de corrido, encadenando una frase con la otra sin
  pausa, arrancando la que sigue antes de que se apague la anterior, casi sin
  respirar. (Antes la consigna pedía lo contrario: una respiración entre
  frases. Servía para "cercana" y arruinaba "chetita".)
- *Energía de la voz* (paso 1): **Tal cual sale**, **De corrido** (la que
  viene puesta), **Un toque más joven** (+5% de tono) y **Bastante más joven**
  (+10%). Todas menos la primera le recortan los silencios largos con
  `silenceremove` (el hueco más largo baja de 0,65 a 0,25 s, igual que la
  referencia) y la aceleran un poco. Las de "+ tono" usan `rubberband` con
  `formant=preserved`: sin eso el tono sube pero el timbre se encoge y queda
  el efecto de cinta acelerada (fue exactamente lo que pasó en la v2.3.0, que
  subía 16% sin preservar formantes). El tratamiento va ANTES de OmniHuman,
  así los labios sincronizan con lo que se oye. Y como habla más rápido, el
  guion escribe más palabras por segundo (2,3 / 2,5 / 2,55 / 2,7) y el reel
  sale un poco más barato, porque OmniHuman cobra por segundo.
- *Probar la voz* (botón ▶ al lado del selector de voz): graba una frase de
  muestra con la voz, el tono y la energía elegidos, para escuchar antes de
  grabar el guion entero. Cuesta lo que un mensaje de voz.
- *El micrófono, corregido.* La primera versión del "aire de micrófono" sonaba
  a radio AM, y estaba medido por qué: sacaba cuerpo en 260 Hz, metía 2,5 dB
  en 3,4 kHz y sumaba un eco de 24 ms que peina el espectro y deja timbre
  metálico. Quedó: cuerpo en 1,3 kHz, una pizca en 3 kHz, menos filo en
  7,5 kHz (ahí teníamos 4 dB de más contra la referencia) y compresión suave,
  sin eco.

**Si el paso 4 no aparece (v2.4.1).** Antes el paso "Generar el reel" se
escondía solo cuando faltaba algo, sin decir qué, y no había forma de
destrabarlo. Ahora al final del paso 3 aparece un cartel que dice exactamente
qué falta y de qué tramo: la voz del tramo N, la escena del tramo N, o acortar
un tramo de ella que pasa los 28 segundos. El paso 4 vuelve a aparecer solo en
cuanto se completa, aunque estés parada en otro paso, y si tocás el "4" del
encabezado sin estar listo te avisa qué falta en vez de no hacer nada.

**Manos y gestos en los tramos de ella (v2.5.2).** Dos cosas que aparecieron
en los reels reales:
- *Se le desaparecía lo que tenía en la mano.* En un reel, la percha con el
  corpiño que ella sostenía en la foto se esfumó al segundo de empezar el
  video. OmniHuman anima a partir de una foto y pierde los objetos chicos. Se
  corrigió por los dos lados: el prompt ahora le prohíbe explícitamente que
  algo salga o entre en las manos y, con micrófono puesto, ningún encuadre le
  pide además sostener la prenda en alto (señala la que está en el mostrador).
- *Los gestos no acompañaban lo que decía.* La consigna anterior pedía
  movimiento constante (hombros, peso, mano suelta gesticulando) y eso sale
  descoordinado. Ahora pide lo contrario: que cada movimiento siga a lo que
  dice —asentir en las palabras que remarca, las cejas en esas palabras— y que
  se quede quieta entre frases. Nada de moverse porque sí.

Si igual sale mal, rehacé ese tramo: OmniHuman da un resultado distinto cada
vez y pagás sólo ese tramo.

**Nueve filtros y una tira para elegirlos mirando (v2.6.0).** El filtro dejó
de ser un select a ciegas con tres opciones. Ahora hay nueve y, en el paso 3,
una tira de miniaturas con **tu propia escena** pasada por cada uno: tocás la
que te gusta y queda elegida. No cuesta nada (es ffmpeg sobre la foto que ya
tenés, sin llamar a ningún motor).

Los filtros: Celular (el de antes), **Aro de luz** (cara pareja y brillante,
bordes apagados), Celular fuerte, Cámara frontal (más fría y con contraste),
Luz de tarde (dorada), Con flash (contraste duro y bordes oscuros), Con grano
(desaturada, tipo cámara vieja), Nítida de celular (color de celular sin
ablandar) y Limpia (sin filtro).

Cada filtro además le pide a la escena la luz que le corresponde, no sólo
colorea el video después: el aro de luz, por ejemplo, pide luz frontal pareja
y el reflejo circular del aro en los ojos, que es lo que lo hace creíble.

**Reescribir el guion ya no te cuesta las escenas (v2.7.1).** Volver a tocar
"Escribir el guion" reemplazaba los tramos enteros, y con eso se perdía la
marca de "esta escena ya está hecha": había que generarlas (y pagarlas) de
nuevo, y como el paso 3 necesita una escena para mostrar la tira de filtros,
tampoco aparecían los filtros. Las fotos nunca se borraron, estaban guardadas
en el KV bajo el índice de cada tramo; lo que se perdía era la marca. Ahora,
al reescribir, cada tramo de ella que ya tenía su foto la recupera con sus
detalles y su encuadre. Además cada escena tiene un "⬇️ Bajar" para guardarte
la foto, y cuando todavía no hay ninguna, el paso 3 avisa que ahí van a
aparecer los filtros en vez de no mostrar nada.

**La boca contra la voz (v2.8.0).** Medido sobre los reels reales: el armado
sumaba un desfase que CRECÍA tramo a tramo. OmniHuman devuelve el video unas
décimas más corto que el audio que recibió, y al pegar los archivos tal cual
esa diferencia se acumulaba (medido con destellos y pitidos sincronizados:
-7 ms en el primer tramo, -27 en el segundo, -37 en el tercero). Ahora, antes
de pegar, a cada tramo se le clona el último cuadro si falta y se le agrega
silencio al audio si falta, y los dos se cortan en el mismo instante; recién
ahí se concatenan por filtro (que además empareja tamaño, píxeles y formato
de audio de todos los tramos). Medido de nuevo: el desfase queda fijo y no
crece.

Lo que queda del desfase es de OmniHuman, no del armado. Comparando el
movimiento de la boca contra la energía de la voz: en un video de una persona
real la correlación en el instante cero es positiva (+0,14) y en los reels
generados es negativa, o sea que la boca se mueve un poco cuando no hay voz.
Tres cosas ayudan, en orden: poner **Energía de la voz** en "Tal cual sale"
(acelerar la voz y cortarle los silencios le complica seguir los labios),
pasar la calidad a **1080p**, y sacar el micrófono si le queda cerca de la
boca. El prompt de la escena ahora pide además que el micrófono NO le tape la
boca ni el mentón, justamente por esto.

**El motor de los tramos de ella se puede cambiar (v2.9.0).** En el paso 4 hay
un selector "Motor de los tramos de ella". Cada motor trae en la tabla su
nombre, su precio por segundo, el tope de voz por tramo, su ruta en fal y qué
campos opcionales acepta; el resto del programa no sabe cuál está puesto, así
que el costo que se muestra, el tope que se valida y el aviso de "acortá el
tramo" salen siempre del motor elegido. Sumar un motor nuevo es agregar una
fila. Como el motor se guarda por reel, se pueden comparar dos en el mismo
tramo con "Rehacer tramo" y quedarse con el que mejor sincronice los labios.

**MiniMax H3 Max Lip Sync como segundo motor (v2.10.0).** Está hecho sólo
para sincronizar labios y transcribe la voz para guiarse, que es justo lo que
le falta a OmniHuman. Se elige en el paso 4 y queda guardado en el reel, así
que se puede rehacer el MISMO tramo con los dos y comparar. Lo que hay que
saber antes de usarlo:

| | OmniHuman 1.5 | MiniMax H3 Max Lip Sync |
|---|---|---|
| voz por tramo | hasta 28 s | entre 5 y 14 s |
| precio por segundo | US$0,16 | US$0,26 (estimado, confirmalo en fal) |
| movimiento del cuerpo | bueno | menos |
| sincronía de labios | su punto flojo | su especialidad |

Los tres detalles que resuelve el programa solo: si el tramo dura menos de
5 s le agrega silencio para llegar al mínimo que MiniMax pide (y después el
tramo se corta igual al largo real de la voz), le manda la resolución con la
grafía que ese motor usa (768P y 1080P en vez de 720p y 1080p) y no le manda
prompt, porque no lo acepta. El tope de 14 s se valida antes de gastar: si un
tramo se pasa, el paso 3 avisa cuál hay que acortar.

Costo: la voz y las escenas centavos; OmniHuman US$0,16 por segundo de ella
hablando (unos US$3 para 18 s). El trabajo corre en segundo plano con reloj y
figura también en "En curso" de Personajes; cada tramo de ella tiene tope de
28 s de audio (OmniHuman en 1080p admite 30). Variables: `REELS_PREFIX`
(default `/reels`) y `REELS_OMNI_MODEL`.

## Actualizaciones (igual que ML×TN)
- Cambiás archivos → los subís al repo → Railway redeploya solo → hard refresh.

## Importante
- Esta versión **no tiene login**: cualquiera con el link entra. Ideal para
  probar con tus papás. Antes de promocionar a desconocidos hay que sumar
  login + datos por usuario + medición de consumo + cobro (Mercado Pago).
- Mantené las API keys SOLO en las Variables de Railway, nunca dentro de los
  archivos del repo.
