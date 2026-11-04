"""Cartas sueltas: precio en MasterSet (marketplace peruano) frente a TCGplayer.

Cada carta se sigue por su link de MasterSet (https://www.masterset.pe/card/<id>).
La ficha de MasterSet trae el id de TCGplayer de la carta, así que la comparación
es con la misma versión (idioma, set y número) en TCGplayer.

Uso:
    python cartas.py                    # analiza las cartas que sigues en la app
    python cartas.py <link> [<link>…]   # analiza estos links sin guardarlos
"""
import datetime as dt
import html
import json
import re
import sys

from scanner import abrir_db, get, guardar_prefs, cargar_prefs, log, soles

MASTERSET = "https://www.masterset.pe/card/{}"
TCGPLAYER_API = "https://mp-search-api.tcgplayer.com/v1/product/{}/details"
TCGPLAYER_WEB = "https://www.tcgplayer.com/product/{}"
TIPO_CAMBIO_API = "https://open.er-api.com/v6/latest/USD"
RE_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
IDIOMA = {"ja": "Japonés", "en": "Inglés", "es": "Español", "zh": "Chino", "ko": "Coreano"}

# Precio en Perú dividido entre el precio de mercado de TCGplayer (ya en soles).
VEREDICTOS = [
    (0.85, "buen_precio", "Buen precio"),
    (1.10, "justo", "Precio justo"),
    (1.50, "caro", "Caro"),
    (float("inf"), "muy_caro", "Muy caro"),
]


def id_de(link):
    m = RE_ID.search(str(link))
    return m.group(0).lower() if m else None


def _num(x):
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def ficha_masterset(card_id):
    """Datos de la carta y su oferta más barata en MasterSet (vienen en el HTML, atributo data-page)."""
    r = get(MASTERSET.format(card_id), intentos=2)
    m = re.search(r'data-page="([^"]*)"', r.text)
    if not m:
        raise RuntimeError("MasterSet cambió su página (no se encontró data-page)")
    props = json.loads(html.unescape(m.group(1)))["props"]
    c = props.get("card") or {}
    if not c.get("tcgplayer_id"):
        raise RuntimeError("la carta no tiene id de TCGplayer en MasterSet")
    ventas = props.get("saleHistory") or []
    return {
        "id": card_id, "url": MASTERSET.format(card_id),
        "nombre": c.get("fullname") or c.get("name"), "set": (c.get("expansion") or {}).get("name"),
        "idioma": (c.get("language") or {}).get("code"), "rareza": c.get("rarity") or None,
        "tcgplayer_id": str(c["tcgplayer_id"]),
        "imagen": f"https://tcgplayer-cdn.tcgplayer.com/product/{c['tcgplayer_id']}_in_400x400.jpg",
        "precio": _num(c.get("min_price")) if c.get("has_active_offers") else None,
        "promedio": _num(c.get("avg_price")), "ofertas": c.get("items_count") or 0,
        "condiciones": [x.get("value") for x in props.get("conditions") or []],
        "ultima_venta": _num(c.get("last_sale_price")), "ultima_venta_fecha": (c.get("last_sale_at") or "")[:10] or None,
        "ventas": len(ventas),
    }


def precio_tcgplayer(tcg_id):
    d = get(TCGPLAYER_API.format(tcg_id), intentos=2).json()
    return {
        "tcg_nombre": d.get("productName"), "tcg_set": d.get("setName"), "tcg_linea": d.get("productLineName"),
        "mercado_usd": _num(d.get("marketPrice")), "minimo_usd": _num(d.get("lowestPrice")),
        "tcg_listados": int(d.get("listings") or 0), "tcg_url": TCGPLAYER_WEB.format(tcg_id),
    }


def tipo_cambio(db):
    """Soles por dólar del día. Si la consulta falla se usa el último guardado."""
    hoy = dt.date.today().isoformat()
    fila = db.execute("SELECT usd_pen FROM tipo_cambio WHERE fecha=?", (hoy,)).fetchone()
    if fila:
        return fila[0]
    try:
        tc = float(get(TIPO_CAMBIO_API, intentos=2).json()["rates"]["PEN"])
        db.execute("INSERT OR REPLACE INTO tipo_cambio VALUES (?,?)", (hoy, tc))
        db.commit()
        return tc
    except Exception as ex:
        log.warning("Tipo de cambio no disponible (%s); uso el último guardado", ex)
        fila = db.execute("SELECT usd_pen FROM tipo_cambio ORDER BY fecha DESC LIMIT 1").fetchone()
        return fila[0] if fila else 3.75


def veredicto(precio, mercado_pen):
    if precio is None:
        return {"veredicto": "sin_oferta", "veredicto_txt": "Sin ofertas en MasterSet", "ratio": None, "dif": None}
    if not mercado_pen:
        return {"veredicto": "sin_ref", "veredicto_txt": "Sin precio en TCGplayer", "ratio": None, "dif": None}
    ratio = precio / mercado_pen
    clave, txt = next((c, t) for limite, c, t in VEREDICTOS if ratio <= limite)
    return {"veredicto": clave, "veredicto_txt": txt, "ratio": round(ratio, 3), "dif": round(precio - mercado_pen, 2)}


def analizar(card_id, tc):
    """Ficha completa de una carta: MasterSet + TCGplayer + veredicto."""
    f = ficha_masterset(card_id)
    f.update(precio_tcgplayer(f["tcgplayer_id"]))
    f["usd_pen"] = tc
    f["mercado_pen"] = round(f["mercado_usd"] * tc, 2) if f["mercado_usd"] else None
    f["minimo_pen"] = round(f["minimo_usd"] * tc, 2) if f["minimo_usd"] else None
    f.update(veredicto(f["precio"], f["mercado_pen"]))
    # MasterSet a veces enlaza otra versión en TCGplayer (otro idioma o número).
    numero = (f["nombre"] or "").rsplit(" ", 1)[-1]
    otro_idioma = (f["idioma"] == "ja") != (f["tcg_linea"] == "Pokemon Japan")
    if otro_idioma or (numero and numero not in (f["tcg_nombre"] or "")):
        f["aviso"] = (f"MasterSet la publica como {IDIOMA.get(f['idioma'], f['idioma'])} {numero}, pero en TCGplayer "
                      f"enlaza {f['tcg_nombre']} ({f['tcg_linea']}). Confirma la versión antes de comparar.")
    return f


def guardar(db, f, ts):
    db.execute("INSERT OR REPLACE INTO cartas VALUES (?,?,?)", (f["id"], json.dumps(f, ensure_ascii=False), ts))
    db.execute("INSERT OR REPLACE INTO cartas_historial VALUES (?,?,?,?,?)",
               (ts[:10], f["id"], f["precio"], f["mercado_usd"], f["usd_pen"]))
    db.commit()


def agregar(db, prefs, link, ts=None):
    """Empieza a seguir una carta por su link de MasterSet y devuelve su ficha."""
    card_id = id_de(link)
    if not card_id:
        raise ValueError("Pega un link de MasterSet, por ejemplo https://www.masterset.pe/card/…")
    f = analizar(card_id, tipo_cambio(db))
    guardar(db, f, ts or dt.datetime.now().isoformat(timespec="seconds"))
    previo = prefs.setdefault("cartas", {}).get(card_id, {})
    prefs["cartas"][card_id] = {"nombre": f["nombre"], "objetivo": previo.get("objetivo")}
    guardar_prefs(prefs)
    return f


def actualizar(db, prefs, ts):
    """Vuelve a leer todas las cartas seguidas. Devuelve (fichas, errores)."""
    seguidas = prefs.get("cartas", {})
    if not seguidas:
        return [], []
    tc = tipo_cambio(db)
    fichas, errores = [], []
    for card_id, info in seguidas.items():
        try:
            f = analizar(card_id, tc)
            guardar(db, f, ts)
            fichas.append(f)
            log.info("Carta %s: %s en MasterSet, %s en TCGplayer → %s", f["nombre"], soles(f["precio"]),
                     soles(f["mercado_pen"]), f["veredicto_txt"])
        except Exception as ex:  # una carta caída no frena las demás
            errores.append((info.get("nombre") or card_id, str(ex)[:200]))
            log.error("Carta %s: %s", info.get("nombre") or card_id, ex)
    return fichas, errores


def datos_cartas(db, prefs):
    """Cartas seguidas con su última ficha guardada y su historial (para la app)."""
    out = []
    for card_id, info in prefs.get("cartas", {}).items():
        fila = db.execute("SELECT datos, ts FROM cartas WHERE id=?", (card_id,)).fetchone()
        f = json.loads(fila["datos"]) if fila else {"id": card_id, "url": MASTERSET.format(card_id),
                                                     "nombre": info.get("nombre") or card_id}
        f["ts"] = fila["ts"] if fila else None
        f["objetivo"] = info.get("objetivo")
        f["historial"] = [
            {"fecha": h["fecha"], "precio": h["precio"],
             "mercado_pen": round(h["mercado_usd"] * h["usd_pen"], 2) if h["mercado_usd"] else None}
            for h in db.execute("SELECT * FROM cartas_historial WHERE id=? ORDER BY fecha", (card_id,))]
        out.append(f)
    return out


def en_objetivo(db, prefs):
    """Cartas cuya oferta más barata en MasterSet ya está en el precio objetivo (para Telegram)."""
    return [{"tienda": "MasterSet", "nombre": f["nombre"], "url": f["url"], "precio": f["precio"],
             "objetivo": f["objetivo"]}
            for f in datos_cartas(db, prefs)
            if f.get("objetivo") and f.get("precio") is not None and f["precio"] <= f["objetivo"]]


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    db = abrir_db()
    links = sys.argv[1:] or list(cargar_prefs().get("cartas", {}))
    if not links:
        sys.exit("No sigues ninguna carta. Uso: python cartas.py <link de MasterSet> …")
    tc = tipo_cambio(db)
    print(f"Tipo de cambio: S/ {tc:.3f} por dólar\n")
    for link in links:
        card_id = id_de(link)
        if not card_id:
            print(f"{link}: no parece un link de MasterSet\n")
            continue
        try:
            f = analizar(card_id, tc)
        except Exception as ex:
            print(f"{link}: {ex}\n")
            continue
        dif = f"{'+' if f['dif'] > 0 else '−'}{soles(abs(f['dif']))} ({f['ratio'] - 1:+.0%})" if f["dif"] is not None else ""
        print(f"{f['nombre']} · {f['set']} · {IDIOMA.get(f['idioma'], f['idioma'])}")
        print(f"  MasterSet: {soles(f['precio'])} ({f['ofertas']} oferta(s))"
              + (f" · última venta {soles(f['ultima_venta'])} el {f['ultima_venta_fecha']}" if f["ultima_venta"] else ""))
        print(f"  TCGplayer: ${f['mercado_usd']} mercado = {soles(f['mercado_pen'])}  ·  {f['tcg_nombre']} ({f['tcg_set']})")
        print(f"  → {f['veredicto_txt']} {dif}")
        print(f"  ⚠ {f['aviso']}\n" if f.get("aviso") else "")


if __name__ == "__main__":
    main()
