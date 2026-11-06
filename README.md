# Escáner diario de Pokémon TCG en tiendas de Perú

Revisa cada día el catálogo Pokémon TCG de tiendas peruanas, guarda el historial
de precios y stock, y genera un reporte con lo que cambió.

## Tiendas

| Tienda | Plataforma | Cómo se lee |
|---|---|---|
| OkamiDex | WooCommerce | API pública `wp-json/wc/store/v1` |
| Ludus Magnus | WooCommerce | API pública `wp-json/wc/store/v1` |
| La Mazmorra TCG | WooCommerce | API pública `wp-json/wc/store/v1` |
| Replay Perú | Shopify | `collections/<handle>/products.json`; si Shopify limita (429), el HTML de la colección |
| Phantom | Magento | HTML del listado de categoría |
| Falabella (incluye Saga, Sodimac, Tottus y su marketplace) | Next.js | `__NEXT_DATA__` del buscador |
| Ripley (y su marketplace) | Next.js | `__NEXT_DATA__` del buscador |
| Plaza Vea, Oechsle, Promart, Wong, Metro, Shopstar | VTEX | API pública `api/catalog_system/pub/products/search` |
| Mercado Libre | API oficial | Desactivada: con token de app (`ML_CLIENT_ID` / `ML_CLIENT_SECRET`) la búsqueda igual responde 403 |
| Raymi Store | WooCommerce | API pública; sellados (`tcg`) y cartas sueltas y gradeadas (`cards`) |
| Dranzer Store | — | Desactivada: bloquea accesos automáticos (Cloudflare) |

Las tiendas grandes venden de todo, así que usan `filtro_tcg` / `excluir_tcg` de `tiendas.yaml`
(Pokémon o "TCG" + una palabra de producto TCG, sin juguetes, ropa, accesorios ni otros juegos).
Los marketplaces (Falabella, Ripley, Shopstar) incluyen vendedores externos: revisa quién vende
antes de comprar, sobre todo en lotes de "cartas al azar".

Agregar o quitar tiendas: editar `tiendas.yaml`. Cualquier otra tienda WooCommerce o
Shopify funciona solo agregando su URL y categorías/colecciones.

## App interactiva

Doble clic en **Pokemon TCG Peru.bat** del Escritorio (o `Abrir app.bat` en esta carpeta).
Abre `http://127.0.0.1:8777` en el navegador; la ventana negra debe quedar abierta
mientras la usas. Solo es accesible desde tu PC.

- **Resumen**: indicadores, cambios del último escaneo, mejores oportunidades de ahorro
  y lo más barato de tu lista de vigilancia.
- **Dónde comprar**: el mismo producto en distintas tiendas (agrupado por set, tipo y
  Pokémon según `equivalencias.yaml`), la tienda recomendada, cuánto ahorras y qué tienda
  sale más barata en general. Filtra por tipo e idioma.
- **Cartas**: pega el link de una carta de MasterSet (`https://www.masterset.pe/card/…`) y la app
  la busca también en MercadoTCG, Dungeon Collector, Cartón Fino, OkamiDex y TCG House
  (`tiendas_cartas` en `tiendas.yaml`; se reconoce por nombre, número, total del set e idioma).
  El precio más bajo en Perú se compara con el de mercado de TCGplayer (en soles, al tipo de cambio
  del día) y se marca la tienda con el *Mejor precio*; las agotadas salen aparte. Veredicto: *Buen precio* (hasta 85% del mercado), *Precio justo* (hasta +10%),
  *Caro* (hasta +50%) o *Muy caro*. Avisa si MasterSet enlaza otra versión de la carta en
  TCGplayer, guarda el historial y acepta un precio objetivo. "Actualizar precios" revisa solo
  las cartas; el escaneo completo también las incluye.
- **Buscar una carta**: escribe nombre y número (`Dhelmise 091/084`) y el idioma, y ves su precio
  en MasterSet y en esas tiendas frente a TCGplayer, con la más barata y el veredicto. En la app
  responde al instante (`buscador.py`).
- **Desde la web (buzón de GitHub)**: la web no puede escribir en el repositorio, así que al buscar,
  seguir una carta (link de MasterSet) o dejar de seguirla abre un issue ya escrito
  (`Buscar: …`, `Seguir: …`, `Dejar: …`). Le das *Create* y el workflow `buzon.yml` lo procesa con
  `buzon.py`: responde en el issue, lo cierra y deja la búsqueda en la rama `busquedas` (la web la
  muestra en 1–2 min) o cambia la lista `cartas:` de `tiendas.yaml` y lanza el escaneo. Solo se
  atienden issues del dueño del repositorio; los issues son públicos, como el repositorio.
- **Subastas**: anota el link de una subasta (Facebook, grupos…), la hora de cierre, la puja
  actual y tu máximo, y elige la carta en TCGplayer con el buscador. La app muestra la cuenta
  regresiva, hasta cuánto conviene pujar (80% del valor de mercado) y si ya pasaron tu máximo.
  Antes del cierre (1 h, 30, 10 o 3 min, a elegir) Windows muestra una ventana con sonido para
  abrir la subasta, aunque la app esté cerrada: son tareas "TCG Subasta …" del Programador de
  tareas que ejecutan `recordatorio.py` con `pythonw` (sin abrir consola; la PC debe estar prendida
  y con tu sesión abierta). La puja de Facebook la lee `fb_subastas.py` (ver abajo).
- **Catálogo**: todo con buscador y filtros ("Solo gradeadas" muestra las cartas PSA, CGC, AGS…, con su nota). Al tocar un producto se ve su historial de
  precio, el precio en otras tiendas y se puede guardar en favoritos.
- **Favoritos**: precio objetivo por producto; la app (y Telegram) avisan cuando un
  escaneo lo encuentra a ese precio o menos.
- **Ajustes**: editar la lista de vigilancia, pausar tiendas y escanear una sola tienda.
- Botón **Escanear ahora** con el progreso en vivo.

Preferencias guardadas en `data/preferencias.json`.

## Escaneo por consola

Doble clic en **Escanear Pokemon TCG.bat** del Escritorio (o `Escanear.bat`), o:

```bash
pip install -r requirements.txt
python scanner.py
```

Opciones: `--tienda OkamiDex` (solo una), `--tienda cartas` (solo las cartas sueltas),
`--sin-alertas` (no envía Telegram).

Analizar cartas sin guardarlas: `python cartas.py <link de MasterSet> [<link> …]`.

Salida:

- `reportes/ultimo.html`: la misma interfaz, guardada sin servidor (sin botón de escaneo
  ni historial; los favoritos quedan solo en ese navegador). La interfaz está en `ui.html`.
- `reportes/reporte_AAAA-MM-DD.html` y `reportes/productos_AAAA-MM-DD.csv`: copia diaria.
- `data/tcg.db`: SQLite con `productos`, `historial` (precio/stock por día), `escaneos`, `cambios`,
  y para las cartas sueltas `cartas`, `cartas_historial` y `tipo_cambio`.
- `data/escaneo.log`: log de cada ejecución.

Si "Dónde comprar" junta mal un producto o no reconoce un set nuevo, agrega o reordena
sus alias en `equivalencias.yaml`.

El primer escaneo de cada tienda solo crea la base; los cambios aparecen desde el segundo.

## Versión web (GitHub Pages, gratis)

`.github/workflows/escaneo.yml` escanea cada día a las 8:00 a. m. (hora de Perú) en GitHub y
publica la página en GitHub Pages. Para escanear al momento: pestaña **Actions** del repositorio →
*Escaneo diario* → **Run workflow**.

- La página es pública y **no lleva datos personales**: sin ajustes, precios objetivo ni tu máximo
  en subastas (`scanner.py --web` usa `datos_publicos`). Los favoritos que marques ahí quedan solo en tu navegador.
- **Subastas en la web**: cada vez que guardas una subasta o anotas una puja en la app, se publica
  `subastas.json` (sin tu máximo ni avisos) en la rama `subastas` usando tu sesión del `gh` CLI. La página
  lo lee al abrirse y cada 2 minutos, con la cuenta regresiva y el historial de pujas. Las pujas de
  Facebook se leen solas desde la PC (ver *Subastas de Facebook sin Claude*).
- **Instalable en el celular (PWA)**: ábrela en Chrome (Android) → menú → *Instalar app*, o en
  Safari (iPhone) → Compartir → *Agregar a inicio*. No es un APK. Los archivos están en `web/`
  (manifest, `sw.js`, íconos) y `scanner.py --web` los copia junto a `index.html`.

## Subastas de Facebook sin Claude

`fb_subastas.py` **solo lee** los comentarios de tus subastas (nunca comenta, puja ni reacciona) con
Brave con un perfil aparte (`data/fb-perfil-brave`; nunca Chrome; tu sesión de Facebook nunca sale de la PC):

1. `pip install -r requirements-pc.txt` (usa tu Brave instalado; no descarga otro navegador).
2. Doble clic en **`Facebook - iniciar sesion.bat`**: inicias sesión tú en la ventana de Brave que se abre, la
   cierras y crea la tarea de Windows *TCG Subastas Facebook* (cada 5 min; quitarla:
   `python fb_subastas.py --desinstalar`).

La tarea abre Facebook solo cuando toca: cada ~40 min por subasta, cada ~10 min en su última hora,
~10 min antes del cierre y 2 min después (lectura final: marca ganada/perdida). Anota la puja más
alta (sin nombres de otros pujadores) y te avisa si te superan, si sube la puja
de alguna carta (varias subidas de la misma pasada llegan en un solo aviso), faltando ~10 min y al cerrar.
Nada de esto abre ventanas de consola: todo corre con `pythonw` y `CREATE_NO_WINDOW`.
Si la sesión vence, avisa para volver a correr el `.bat`. Log: `data/escaneo.log`.

**Avisos al celular** (`notificar.py`, Web Push): en la web instalada, sección Subastas →
*Activar avisos* → *Confirmar en GitHub*. El celular manda su suscripción **cifrada** (RSA-OAEP +
AES-GCM; solo la PC la descifra) en un issue `Avisos: …`; la tarea la guarda en `data/push.json`,
cierra el issue y manda un aviso de prueba. Las llaves privadas (`data/vapid_privada.pem`,
`data/buzon_privada.pem`) no salen de la PC; las públicas están en `web/push.json`
(`python notificar.py --claves` las crea). Sin celular suscrito, los avisos salen en Windows.
Los avisos los manda la PC: tiene que estar prendida.
- Sigue las cartas listadas en `cartas:` de `tiendas.yaml`.
- El historial de precios vive en la rama `datos` (un solo commit con `tcg.db`).
- `data/` y `reportes/` no se suben (ver `.gitignore`).
- GitHub pausa los workflows programados tras 60 días sin actividad en el repositorio; si pasa,
  reactívalo desde la pestaña Actions.

## Programar cada día (opcional, Windows)

```powershell
powershell -ExecutionPolicy Bypass -File programar_tarea.ps1 -Hora 09:00
```

Quitar la tarea: `programar_tarea.ps1 -Quitar`.

## Alertas por Telegram (opcional)

1. Crear un bot con @BotFather y copiar el token.
2. Escribirle al bot y obtener tu chat id (por ejemplo con @userinfobot).
3. Definir las variables de entorno de usuario y volver a programar la tarea:

```powershell
setx TELEGRAM_BOT_TOKEN "123456:ABC..."
setx TELEGRAM_CHAT_ID "123456789"
```

Solo se envía mensaje cuando hay cambios, un favorito llega a su precio objetivo o alguna
tienda falla. Los productos de tu lista de vigilancia salen primero, marcados con ⭐.
