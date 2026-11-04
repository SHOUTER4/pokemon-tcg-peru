"""Escaneo diario de tiendas peruanas de Pokémon TCG.

Lee tiendas.yaml, descarga el catálogo Pokémon de cada tienda, lo guarda en
data/tcg.db y genera un reporte HTML + CSV con los cambios desde el escaneo
anterior (productos nuevos, cambios de precio, reposiciones y agotados).

La app interactiva (app.py) usa este mismo módulo para escanear y armar los datos.

Uso:
    python scanner.py                  # escanea todas las tiendas activas
    python scanner.py --tienda Phantom # solo una tienda
    python scanner.py --tienda cartas  # solo las cartas sueltas que sigues (ver cartas.py)
    python scanner.py --sin-alertas    # no envía Telegram
    python scanner.py --web sitio      # además escribe sitio/index.html sin datos personales
"""
import argparse
import csv
import datetime as dt
import html
import json
import logging
import os
import re
import sqlite3
import sys
import time
import unicodedata
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "data" / "tcg.db"
PREFS_PATH = BASE / "data" / "preferencias.json"
REPORTES = BASE / "reportes"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

log = logging.getLogger("tcg")
session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept-Language": "es-PE,es;q=0.9"})


def get(url, params=None, intentos=3):
    """GET con reintentos ante 429/5xx (Shopify limita peticiones seguidas) y ante
    tiempos de espera o cortes de conexión (algunas tiendas a veces tardan)."""
    for i in range(intentos):
        try:
            r = session.get(url, params=params, timeout=30)
        except (requests.Timeout, requests.ConnectionError) as ex:
            if i == intentos - 1:
                raise
            log.info("%s en %s, reintento en %ss", type(ex).__name__, url, 10 * (i + 1))
            time.sleep(10 * (i + 1))
            continue
        if (r.status_code == 429 or r.status_code >= 500) and i < intentos - 1:
            espera = int(r.headers.get("Retry-After", 0) or 0) or 10 * (i + 1)
            log.info("HTTP %s en %s, reintento en %ss", r.status_code, url, espera)
            time.sleep(min(espera, 60))
            continue
        break
    r.raise_for_status()
    return r


# --------------------------------------------------------------------------
# Adaptadores por plataforma. Cada uno produce dicts con:
#   pid, nombre, url, precio, precio_normal, stock (bool), imagen
# --------------------------------------------------------------------------

def scan_woocommerce(t):
    api = t["url"].rstrip("/") + "/wp-json/wc/store/v1"
    cats, page = [], 1
    while True:
        r = get(f"{api}/products/categories", {"per_page": 100, "page": page})
        cats += r.json()
        if page >= int(r.headers.get("X-WP-TotalPages", 1)):
            break
        page += 1
    por_slug = {c["slug"]: c["id"] for c in cats}
    faltan = [s for s in t["categorias"] if s not in por_slug]
    if faltan:
        log.warning("%s: categorías no encontradas: %s", t["nombre"], faltan)
    ids = ",".join(str(por_slug[s]) for s in t["categorias"] if s in por_slug)
    if not ids:
        raise RuntimeError("ninguna categoría válida")

    page = 1
    while True:
        r = get(f"{api}/products", {"category": ids, "per_page": 100, "page": page})
        for p in r.json():
            pr = p["prices"]
            div = 10 ** int(pr.get("currency_minor_unit", 2))
            precio = int(pr["price"]) / div if pr.get("price") else None
            normal = int(pr["regular_price"]) / div if pr.get("regular_price") else None
            yield {
                "pid": str(p["id"]),
                "nombre": html.unescape(p["name"]),
                "url": p["permalink"],
                "precio": precio,
                "precio_normal": normal if normal and normal != precio else None,
                "stock": bool(p.get("is_in_stock")),
                "imagen": p["images"][0]["src"] if p.get("images") else "",
            }
        if page >= int(r.headers.get("X-WP-TotalPages", 1)):
            break
        page += 1


def _producto_shopify(base, pid, handle, titulo, variantes, imagen, centavos):
    """Normaliza un producto Shopify. El JSON público da precios como "59.90";
    el JSON incrustado en el HTML los da en céntimos (5990)."""
    conv = (lambda x: int(x) / 100) if centavos else float
    precios = [conv(v["price"]) for v in variantes if v.get("price") is not None]
    comp = [conv(v["compare_at_price"]) for v in variantes if v.get("compare_at_price")]
    precio = min(precios) if precios else None
    normal = max(comp) if comp else None
    return {
        "pid": str(pid),
        "nombre": titulo,
        "url": f"{base}/products/{handle}",
        "precio": precio,
        "precio_normal": normal if normal and normal > (precio or 0) else None,
        "stock": any(v.get("available") for v in variantes),
        "imagen": imagen,
    }


def _shopify_json(base, col, pausa):
    page = 1
    while True:
        # Sin reintentos: si Shopify limita (429) se pasa directo al HTML.
        r = get(f"{base}/collections/{col}/products.json", {"limit": 250, "page": page}, intentos=1)
        prods = r.json().get("products", [])
        for p in prods:
            img = p["images"][0]["src"] if p.get("images") else ""
            yield _producto_shopify(base, p["id"], p["handle"], p["title"], p["variants"], img, False)
        if len(prods) < 250:
            return
        page += 1
        time.sleep(pausa)


def _shopify_html(base, col, pausa):
    """Lee la página HTML de la colección. Cada tarjeta trae un atributo
    data-json-product con variantes, precio y disponibilidad."""
    page = 1
    while True:
        # Un solo reintento: si Shopify sigue limitando, insistir solo alarga el bloqueo.
        r = get(f"{base}/collections/{col}", {"page": page}, intentos=2)
        soup = BeautifulSoup(r.content, "html.parser")
        for card in soup.select("li.product"):
            nodo = card.select_one("[data-json-product]")
            if not nodo:
                continue
            j = json.loads(nodo["data-json-product"])
            t = card.select_one("a.card-title")
            titulo = t.get_text(strip=True) if t else j["variants"][0].get("name", j["handle"])
            titulo = html.unescape(titulo)  # el tema escapa dos veces: "&amp;amp;"
            img = ""
            im = card.select_one("img[data-srcset], img[srcset]")
            if im:
                fuentes = [s.split() for s in (im.get("data-srcset") or im.get("srcset")).split(",") if s.strip()]
                grandes = [u for u, *w in fuentes if w and w[0].rstrip("w").isdigit() and int(w[0].rstrip("w")) >= 300]
                img = (grandes or [fuentes[-1][0]])[0]
            elif card.select_one("img"):
                im = card.select_one("img")
                img = im.get("data-src") or im.get("src") or ""
            img = "https:" + img if img.startswith("//") else img
            yield _producto_shopify(base, j["id"], j["handle"], titulo, j["variants"], img, True)
        if not soup.select_one("link[rel=next]"):
            return
        page += 1
        time.sleep(pausa)


def scan_shopify(t):
    # Shopify limita por IP las peticiones seguidas a toda la tienda (HTTP 429
    # "local_rate_limited", Retry-After 60), así que se espacian con "pausa".
    base = t["url"].rstrip("/")
    pausa = t.get("pausa", 8)
    usar_html = False
    for col in t["colecciones"]:
        n = 0
        if not usar_html:
            try:
                for p in _shopify_json(base, col, pausa):
                    n += 1
                    yield p
            except requests.HTTPError as ex:
                if ex.response is None or ex.response.status_code != 429:
                    raise
                log.info("%s: Shopify limitó el JSON (429); se lee el HTML", t["nombre"])
                usar_html = True
        if usar_html and n == 0:
            for p in _shopify_html(base, col, pausa):
                n += 1
                yield p
        if n == 0:
            log.warning("%s: la colección %r está vacía; revisa tiendas.yaml", t["nombre"], col)
        time.sleep(pausa)


def scan_magento(t):
    # Phantom bloquea (403) el parámetro de paginación "p", así que se lee una
    # sola página con el máximo de productos que acepta el listado.
    limite = t.get("por_pagina", 36)
    for url in t["urls"]:
        r = get(url, {"product_list_limit": limite})
        soup = BeautifulSoup(r.content, "html.parser")
        items = soup.select("li.product-item")
        if len(items) >= limite:
            log.warning("%s: %s tiene %d+ productos; puede faltar alguno", t["nombre"], url, limite)
        for li in items:
            a = li.select_one("a.product-item-link")
            if a:
                final = li.select_one("[data-price-type=finalPrice]")
                old = li.select_one("[data-price-type=oldPrice]")
                box = li.select_one("[data-product-id]")
                img = li.select_one("img.product-image-photo")
                yield {
                    "pid": box["data-product-id"] if box else a["href"],
                    "nombre": a.get_text(strip=True),
                    "url": a["href"],
                    "precio": float(final["data-price-amount"]) if final else None,
                    "precio_normal": float(old["data-price-amount"]) if old else None,
                    "stock": (li.select_one(".action.tocart") is not None
                              and li.select_one(".stock.unavailable") is None),
                    "imagen": (img.get("data-src") or img.get("src") or "") if img else "",
                }
        time.sleep(2)


ADAPTADORES = {"woocommerce": scan_woocommerce, "shopify": scan_shopify, "magento": scan_magento}


def escanear_tienda(t):
    filtro = [f.lower() for f in t.get("filtro", [])]
    excluir = [f.lower() for f in t.get("excluir", [])]
    productos = {}
    for p in ADAPTADORES[t["tipo"]](t):
        nombre = p["nombre"].lower()
        if filtro and not any(f in nombre for f in filtro):
            continue
        if any(f in nombre for f in excluir):
            continue
        productos[p["pid"]] = p  # deduplica entre categorías/colecciones
    return list(productos.values())


# --------------------------------------------------------------------------
# Base de datos y detección de cambios
# --------------------------------------------------------------------------

ESQUEMA = """
CREATE TABLE IF NOT EXISTS productos(
    tienda TEXT, pid TEXT, nombre TEXT, url TEXT, imagen TEXT,
    precio REAL, precio_normal REAL, stock INTEGER,
    primera_vez TEXT, ultima_vez TEXT,
    PRIMARY KEY (tienda, pid));
CREATE TABLE IF NOT EXISTS historial(
    fecha TEXT, tienda TEXT, pid TEXT, precio REAL, stock INTEGER,
    PRIMARY KEY (fecha, tienda, pid));
CREATE TABLE IF NOT EXISTS escaneos(
    ts TEXT, tienda TEXT, ok INTEGER, productos INTEGER, error TEXT);
CREATE TABLE IF NOT EXISTS cambios(
    ts TEXT, tienda TEXT, pid TEXT, tipo TEXT, nombre TEXT, url TEXT,
    imagen TEXT, precio REAL, antes REAL);
CREATE TABLE IF NOT EXISTS cartas(
    id TEXT PRIMARY KEY, datos TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS cartas_historial(
    fecha TEXT, id TEXT, precio REAL, mercado_usd REAL, usd_pen REAL,
    PRIMARY KEY (fecha, id));
CREATE TABLE IF NOT EXISTS tipo_cambio(
    fecha TEXT PRIMARY KEY, usd_pen REAL);
"""


def abrir_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript(ESQUEMA)
    return db


def guardar_y_comparar(db, tienda, productos, ts):
    """Actualiza la DB y devuelve la lista de cambios respecto al escaneo previo."""
    prev = db.execute(
        "SELECT MAX(ts) FROM escaneos WHERE tienda=? AND ok=1", (tienda,)).fetchone()[0]
    anteriores = {r["pid"]: r for r in db.execute(
        "SELECT * FROM productos WHERE tienda=?", (tienda,))}
    cambios = []
    hoy = ts[:10]

    for p in productos:
        a = anteriores.get(p["pid"])
        base = {"tienda": tienda, "pid": p["pid"], "nombre": p["nombre"], "url": p["url"],
                "precio": p["precio"], "imagen": p["imagen"]}
        if a is None:
            if prev:  # en el primer escaneo de una tienda todo sería "nuevo"
                cambios.append({**base, "tipo": "nuevo", "antes": None})
        else:
            if a["ultima_vez"] != prev and prev:
                cambios.append({**base, "tipo": "volvió", "antes": None})
            if p["precio"] is not None and a["precio"] is not None and abs(p["precio"] - a["precio"]) > 0.009:
                tipo = "bajó" if p["precio"] < a["precio"] else "subió"
                cambios.append({**base, "tipo": tipo, "antes": a["precio"]})
            if p["stock"] and not a["stock"]:
                cambios.append({**base, "tipo": "repuesto", "antes": None})
            elif not p["stock"] and a["stock"]:
                cambios.append({**base, "tipo": "agotado", "antes": None})

        db.execute(
            """INSERT INTO productos VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(tienda, pid) DO UPDATE SET
                 nombre=excluded.nombre, url=excluded.url, imagen=excluded.imagen,
                 precio=excluded.precio, precio_normal=excluded.precio_normal,
                 stock=excluded.stock, ultima_vez=excluded.ultima_vez""",
            (tienda, p["pid"], p["nombre"], p["url"], p["imagen"], p["precio"],
             p["precio_normal"], int(p["stock"]), ts, ts))
        db.execute("INSERT OR REPLACE INTO historial VALUES (?,?,?,?,?)",
                   (hoy, tienda, p["pid"], p["precio"], int(p["stock"])))

    vistos = {p["pid"] for p in productos}
    for pid, a in anteriores.items():
        if pid not in vistos and prev and a["ultima_vez"] == prev:
            cambios.append({"tienda": tienda, "pid": pid, "nombre": a["nombre"], "url": a["url"],
                            "precio": a["precio"], "imagen": a["imagen"],
                            "tipo": "retirado", "antes": None})

    db.execute("INSERT INTO escaneos VALUES (?,?,?,?,?)", (ts, tienda, 1, len(productos), None))
    db.executemany("INSERT INTO cambios VALUES (?,?,?,?,?,?,?,?,?)",
                   [(ts, tienda, c["pid"], c["tipo"], c["nombre"], c["url"], c["imagen"],
                     c["precio"], c["antes"]) for c in cambios])
    db.commit()
    return cambios


# --------------------------------------------------------------------------
# Preferencias del usuario (las edita la app: lista de vigilancia, favoritos,
# tiendas pausadas). Si no existen, se usa "vigilar" de tiendas.yaml.
# --------------------------------------------------------------------------

def cargar_config():
    return yaml.safe_load((BASE / "tiendas.yaml").read_text(encoding="utf-8"))


def cargar_prefs(cfg=None):
    cfg = cfg or cargar_config()
    # "cartas" de tiendas.yaml son las cartas por defecto (las que sigue la versión web).
    cartas_cfg = {m.group(0).lower(): {"nombre": "", "objetivo": None} for m in
                  (re.search(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", str(c), re.I) for c in cfg.get("cartas") or [])
                  if m}
    prefs = {"vigilar": [str(v) for v in cfg.get("vigilar", [])], "favoritos": {}, "pausadas": [],
             "cartas": cartas_cfg, "subastas": {}}
    try:
        prefs.update(json.loads(PREFS_PATH.read_text(encoding="utf-8")))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return prefs


def guardar_prefs(prefs):
    PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PREFS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PREFS_PATH)


# --------------------------------------------------------------------------
# Equivalencias: el mismo producto en distintas tiendas (para "Dónde comprar").
# Clave = set + tipo de producto + Pokémon destacado + familia de idioma.
# --------------------------------------------------------------------------

TIPOS = [  # el orden importa: gana el primer patrón que coincide
    ("mini_tin_display", "Display de Mini Tins", r"mini tins? display|display de mini"),
    ("etb", "Elite Trainer Box", r"elite trainer|\betb\b|entrenador elite"),
    ("bundle", "Booster Bundle", r"\bbundle\b|paquete de sobres"),
    ("display", "Booster Box / Display", r"booster box|booster display|display box|\bdisplay\b|caja de sobres|caja de refuerzos"),
    ("build_battle", "Build & Battle", r"build (and |y )?battle"),
    ("super_premium", "Super Premium Collection", r"super premium"),
    ("premium", "Premium Collection", r"premium collection|coleccion premium|premiun"),
    ("poster", "Poster Collection", r"\bposter\b"),
    ("binder", "Binder Collection", r"binder collection|coleccion de carpetas"),
    ("surprise", "Surprise Box", r"surprise box|caja sorpresa"),
    ("pin", "Pin Collection", r"pin collection"),
    ("sticker", "Tech Sticker Collection", r"sticker"),
    ("illustration", "Illustration Collection", r"illustration collection|coleccion de ilustraci"),
    ("mini_tin", "Mini Tin", r"mini tins?|mini lata"),
    ("tin", "Tin / Lata", r"\btins?\b|\blatas?\b"),
    ("blister", "Blister", r"blister|\b3 pack\b|checklane"),
    ("deck", "Mazo / Battle Deck", r"battle deck|\bdeck\b|\bmazo\b"),
    ("sleeved", "Sleeved Booster", r"sleeved"),
    ("box", "Caja / Colección", r"\bbox\b|\bcoleccion\b|\bcollection\b"),
    ("sobre", "Sobre / Booster", r"\bsobres?\b|\bbooster\b|\bpack\b"),
]
TIPOS_DE_SET = {"etb", "bundle", "display", "blister", "sleeved", "sobre", "mini_tin_display"}
IDIOMAS = [
    ("es", r"\b(espanol|esp|castellano)\b"),
    ("en", r"\b(ingles|english|eng)\b"),
    ("jp", r"\b(japones|japanese|japon|jp)\b"),
    ("zh", r"\b(chino|chinese|china)\b"),
    ("ko", r"\b(coreano|korean|corea)\b"),
]
RE_CAJA = re.compile(r"caja completa|\b\d{2,} unidades|\bcaja en\b|booster caja|\bdisplay\b|\b36 ct\b")
RE_POKEMON = re.compile(r"\b((?:mega )?[a-z]+) (?:ex|v|vmax|vstar|gx)\b")
NO_POKEMON = {"pokemon", "tcg", "box", "and", "de", "del", "the", "deck", "collection"}


def normalizar(s):
    s = unicodedata.normalize("NFD", str(s).lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def cargar_sets():
    try:
        datos = yaml.safe_load((BASE / "equivalencias.yaml").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    return [(s["nombre"], [normalizar(a) for a in s["alias"]]) for s in datos.get("sets", [])]


def idioma_de(nombre):
    n = normalizar(nombre)
    for codigo, patron in IDIOMAS:
        if re.search(patron, n):
            return codigo
    return "?"


def grupo_de(nombre, sets):
    """Devuelve (clave, título legible) o (None, None) si no se puede agrupar con confianza."""
    n = f" {normalizar(nombre)} "
    set_nombre = next((nom for nom, alias in sets if any(f" {a} " in n for a in alias)), None)
    tipo = next(((k, t) for k, t, patron in TIPOS if re.search(patron, n)), None)
    if not tipo:
        return None, None
    # Una caja completa (10 mini tins, 36 sobres) no se compara con la unidad suelta.
    if RE_CAJA.search(n):
        if tipo[0] == "sobre":
            tipo = next((k, t) for k, t, _ in TIPOS if k == "display")
        elif tipo[0] == "mini_tin":
            tipo = next((k, t) for k, t, _ in TIPOS if k == "mini_tin_display")
        elif tipo[0] in ("tin", "sleeved", "blister"):
            return None, None
    m = RE_POKEMON.search(n)
    pokemon = m.group(1) if m and m.group(1).split()[-1] not in NO_POKEMON else ""
    if tipo[0] in TIPOS_DE_SET and not set_nombre:
        return None, None
    if not set_nombre and not pokemon:
        return None, None
    if tipo[0] == "box" and not pokemon:  # "caja" sola es demasiado genérico
        return None, None
    asia = idioma_de(nombre) in ("jp", "zh", "ko")
    clave = "|".join([normalizar(set_nombre or ""), tipo[0], pokemon, "asia" if asia else "occ"])
    partes = [set_nombre, tipo[1], f"{pokemon.title()} ex" if pokemon else None,
              "versión asiática" if asia else None]
    return clave, " · ".join(p for p in partes if p)


# --------------------------------------------------------------------------
# Datos para la interfaz (los usan el reporte estático y la app)
# --------------------------------------------------------------------------

def armar_datos(db, prefs=None, cfg=None):
    cfg = cfg or cargar_config()
    prefs = prefs or cargar_prefs(cfg)
    ultimos = "(SELECT tienda, MAX(ts) ts FROM escaneos WHERE ok=1 GROUP BY tienda)"
    filas = db.execute(
        f"SELECT p.* FROM productos p JOIN {ultimos} u "
        "ON p.tienda = u.tienda AND p.ultima_vez = u.ts ORDER BY p.stock DESC, p.nombre").fetchall()
    cambios = db.execute(
        f"SELECT c.* FROM cambios c JOIN {ultimos} u ON c.tienda = u.tienda AND c.ts = u.ts").fetchall()
    estado = {r["tienda"]: dict(r) for r in db.execute(
        "SELECT e.* FROM escaneos e JOIN (SELECT tienda, MAX(ts) ts FROM escaneos GROUP BY tienda) m "
        "ON e.tienda = m.tienda AND e.ts = m.ts")}
    tiendas = []
    for t in cfg["tiendas"]:
        e = estado.get(t["nombre"], {})
        tiendas.append({
            "tienda": t["nombre"], "url": t["url"], "plataforma": t["tipo"],
            "activa": t.get("activa", True), "pausada": t["nombre"] in prefs.get("pausadas", []),
            "ts": e.get("ts"), "ok": e.get("ok"), "productos": e.get("productos"), "error": e.get("error"),
        })

    sets, grupos, productos = cargar_sets(), {}, []
    for r in filas:
        clave, titulo = grupo_de(r["nombre"], sets)
        if clave:
            grupos[clave] = titulo
        productos.append({
            "tienda": r["tienda"], "pid": r["pid"], "nombre": r["nombre"], "url": r["url"],
            # WooCommerce devuelve 0 cuando el producto no tiene precio publicado.
            "imagen": r["imagen"], "precio": r["precio"] or None, "precio_normal": r["precio_normal"],
            "stock": bool(r["stock"]), "desde": r["primera_vez"][:10],
            "idioma": idioma_de(r["nombre"]), "g": clave,
        })
    ts = db.execute("SELECT MAX(ts) FROM escaneos").fetchone()[0]
    import cartas, subastas  # aquí y no arriba: ambos importan este módulo
    return {
        "ts": ts, "prefs": prefs, "tiendas": tiendas, "grupos": grupos, "productos": productos,
        "cambios": [dict(c) for c in cambios], "cartas": cartas.datos_cartas(db, prefs),
        "subastas": subastas.datos_subastas(prefs),
        "telegram": bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID")),
    }


def historial(db, tienda, pid):
    return [dict(r) for r in db.execute(
        "SELECT fecha, precio, stock FROM historial WHERE tienda=? AND pid=? ORDER BY fecha",
        (tienda, pid))]


# --------------------------------------------------------------------------
# Reportes y alertas
# --------------------------------------------------------------------------

ORDEN = ["nuevo", "repuesto", "bajó", "volvió", "subió", "agotado", "retirado"]
ETIQUETA = {"nuevo": "Nuevos", "repuesto": "Repuestos", "bajó": "Bajaron de precio",
            "volvió": "Volvieron al catálogo", "subió": "Subieron de precio",
            "agotado": "Se agotaron", "retirado": "Retirados del catálogo"}


def soles(x):
    return f"S/ {x:,.2f}" if x is not None else "—"


def vigilado(nombre, vigilar):
    n = nombre.lower()
    return any(str(v).lower() in n for v in vigilar)


def pagina_ui(datos, modo):
    """Rellena ui.html. modo="app" carga los datos desde la API; "estatico" los lleva incrustados."""
    # "</" escapado para que ningún nombre de producto pueda cerrar el <script>.
    json_datos = json.dumps(datos, ensure_ascii=False).replace("</", "<\\/") if datos else "null"
    return ((BASE / "ui.html").read_text(encoding="utf-8")
            .replace("__MODO__", modo).replace("__DATOS__", json_datos))


def datos_publicos(datos):
    """Copia de los datos sin lo personal (favoritos, subastas, precios objetivo) para la web."""
    prefs = datos["prefs"]
    return {**datos, "telegram": False, "subastas": [],
            "cartas": [{**c, "objetivo": None} for c in datos["cartas"]],
            "prefs": {"vigilar": prefs.get("vigilar", []), "favoritos": {}, "pausadas": [],
                      "cartas": {}, "subastas": {}}}


def generar_reporte(db, ts, web=None):
    """Escribe el reporte diario. Con web=<carpeta> también escribe <carpeta>/index.html
    sin datos personales (lo publica GitHub Pages)."""
    REPORTES.mkdir(exist_ok=True)
    fecha = ts[:10]
    datos = armar_datos(db)
    if web:
        Path(web).mkdir(parents=True, exist_ok=True)
        (Path(web) / "index.html").write_text(pagina_ui(datos_publicos(datos), "web"), encoding="utf-8")
    with open(REPORTES / f"productos_{fecha}.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["tienda", "nombre", "precio", "precio_normal", "stock", "idioma", "url", "visto_desde"])
        for p in datos["productos"]:
            w.writerow([p["tienda"], p["nombre"], p["precio"], p["precio_normal"],
                        "sí" if p["stock"] else "no", p["idioma"], p["url"], p["desde"]])
    pagina = pagina_ui(datos, "estatico")
    ruta = REPORTES / f"reporte_{fecha}.html"
    ruta.write_text(pagina, encoding="utf-8")
    (REPORTES / "ultimo.html").write_text(pagina, encoding="utf-8")
    return ruta


def favoritos_en_objetivo(db, prefs):
    """Favoritos con stock cuyo precio actual es igual o menor al objetivo."""
    hits = []
    for clave, fav in prefs.get("favoritos", {}).items():
        objetivo = fav.get("objetivo")
        tienda, _, pid = clave.partition("|")
        r = db.execute("SELECT * FROM productos WHERE tienda=? AND pid=?", (tienda, pid)).fetchone()
        if objetivo and r and r["stock"] and r["precio"] is not None and r["precio"] <= objetivo:
            hits.append({"tienda": tienda, "nombre": r["nombre"], "url": r["url"],
                         "precio": r["precio"], "objetivo": objetivo})
    return hits


def enviar_telegram(cambios, vigilar, resumen, objetivos=()):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return
    errores = [t for t, _, err in resumen if err]
    if not cambios and not errores and not objetivos:
        return
    lineas = ["Pokémon TCG Perú — cambios de hoy"]
    if objetivos:
        lineas.append(f"\n🎯 Favoritos en tu precio objetivo ({len(objetivos)}):")
        for f in objetivos:
            lineas.append(f"{f['nombre']} [{f['tienda']}] {soles(f['precio'])} "
                          f"(objetivo {soles(f['objetivo'])})\n{f['url']}")
    for tipo in ORDEN:
        items = sorted((c for c in cambios if c["tipo"] == tipo),
                       key=lambda c: not vigilado(c["nombre"], vigilar))
        if not items:
            continue
        lineas.append(f"\n{ETIQUETA[tipo]} ({len(items)}):")
        for c in items[:15]:
            antes = f"{soles(c['antes'])} → " if c["antes"] is not None else ""
            star = "⭐ " if vigilado(c["nombre"], vigilar) else ""
            lineas.append(f"{star}{c['nombre']} [{c['tienda']}] {antes}{soles(c['precio'])}\n{c['url']}")
        if len(items) > 15:
            lineas.append(f"…y {len(items) - 15} más (ver reporte)")
    if errores:
        lineas.append("\nFallaron: " + ", ".join(errores))
    texto = "\n".join(lineas)
    for i in range(0, len(texto), 4000):
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": texto[i:i + 4000],
                                "disable_web_page_preview": "true"}, timeout=30)
        except requests.RequestException as ex:
            log.warning("Telegram falló: %s", ex)


# --------------------------------------------------------------------------

def configurar_log(*handlers):
    (BASE / "data").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[*handlers, logging.FileHandler(BASE / "data" / "escaneo.log", encoding="utf-8")])


def ejecutar_escaneo(solo=None, alertas=True, web=None):
    """Escanea las tiendas activas (o solo una) y las cartas seguidas, y devuelve un
    resumen. solo="cartas" actualiza solo las cartas. Lo usan la línea de comandos
    y el botón "Escanear" de la app."""
    import cartas
    cfg = cargar_config()
    prefs = cargar_prefs(cfg)
    tiendas = [t for t in cfg["tiendas"] if t.get("activa", True)]
    solo_cartas = bool(solo) and solo.lower() == "cartas"
    if solo_cartas:
        tiendas = []
    elif solo:
        tiendas = [t for t in tiendas if t["nombre"].lower() == solo.lower()]
        if not tiendas:
            raise ValueError(f"No hay tienda activa llamada {solo!r}")
    else:
        tiendas = [t for t in tiendas if t["nombre"] not in prefs.get("pausadas", [])]

    db = abrir_db()
    ts = dt.datetime.now().isoformat(timespec="seconds")
    cambios, resumen = [], []
    for t in tiendas:
        log.info("%s: escaneando…", t["nombre"])
        try:
            prods = escanear_tienda(t)
            if not prods:
                raise RuntimeError("0 productos (¿cambió la web?)")
            cambios += guardar_y_comparar(db, t["nombre"], prods, ts)
            resumen.append((t["nombre"], len(prods), None))
            log.info("%s: %d productos", t["nombre"], len(prods))
        except Exception as ex:  # una tienda caída no debe frenar las demás
            db.execute("INSERT INTO escaneos VALUES (?,?,?,?,?)", (ts, t["nombre"], 0, 0, str(ex)))
            db.commit()
            resumen.append((t["nombre"], 0, str(ex)[:200]))
            log.error("%s: %s", t["nombre"], ex)

    fichas, errores_cartas = [], []
    if solo_cartas or not solo:
        if prefs.get("cartas"):
            log.info("Cartas: revisando %d en MasterSet y TCGplayer…", len(prefs["cartas"]))
        fichas, errores_cartas = cartas.actualizar(db, prefs, ts)
        if errores_cartas:
            resumen.append(("Cartas", len(fichas), "; ".join(f"{n}: {e}" for n, e in errores_cartas)[:200]))

    ruta = generar_reporte(db, ts, web)
    objetivos = favoritos_en_objetivo(db, prefs) + cartas.en_objetivo(db, prefs)
    log.info("%d cambios. Reporte: %s", len(cambios), ruta)
    if alertas:
        enviar_telegram(cambios, prefs["vigilar"], resumen, objetivos)
    db.close()
    return {"ts": ts, "cambios": len(cambios), "objetivos": len(objetivos), "cartas": len(fichas),
            "resumen": [{"tienda": t, "productos": n, "error": e} for t, n, e in resumen]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tienda", help='escanear solo esta tienda (nombre en tiendas.yaml), o "cartas"')
    ap.add_argument("--sin-alertas", action="store_true", help="no enviar Telegram")
    ap.add_argument("--web", metavar="CARPETA", help="escribir también la versión pública en CARPETA/index.html")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    configurar_log(logging.StreamHandler(sys.stdout))
    try:
        ejecutar_escaneo(args.tienda, alertas=not args.sin_alertas, web=args.web)
    except ValueError as ex:
        sys.exit(str(ex))


if __name__ == "__main__":
    main()
