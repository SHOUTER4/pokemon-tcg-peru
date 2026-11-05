"""Buscador de una carta por nombre y número en las tiendas de Perú y en TCGplayer.

Escribes "Dhelmise 091/084" (o "Dhelmise 91") y el idioma, y devuelve cada versión de
TCGplayer que coincide con su precio de mercado y las ofertas en MasterSet y en las
tiendas de "tiendas_cartas" (tiendas.yaml), con la más barata y el veredicto.
La app local lo usa en /api/buscar-carta; la web usa la copia del Worker de Cloudflare
(worker/buscador.js), que hace lo mismo.

Uso:  python buscador.py "Dhelmise 091/084" [en|ja|es]
"""
import json
import re
import sys
import urllib.parse

import requests

import cartas
import subastas
from scanner import abrir_db, log, normalizar, session

MASTERSET_CATALOGO = "https://www.masterset.pe/catalog"
MASTERSET_BUSCAR = "https://www.masterset.pe/api/catalog/paginate"
LINEA = {"ja": "Pokemon Japan"}   # el resto de idiomas se compara con la carta en inglés


def _tcgplayer(q, cuantos=50):
    cuerpo = {
        "algorithm": "sales_synonym_v2", "from": 0, "size": cuantos,
        "filters": {"term": {"productLineName": ["pokemon", "pokemon-japan"]}, "range": {}, "match": {}},
        "listingSearch": {"context": {"cart": {}}, "filters": {
            "term": {"sellerStatus": "Live", "channelId": 0}, "range": {"quantity": {"gte": 1}},
            "exclude": {"channelExclusion": 0}}},
        "context": {"cart": {}, "shippingCountry": "US"}, "settings": {"useFuzzySearch": True}, "sort": {},
    }
    r = session.post(subastas.BUSCAR_API, params={"q": q, "isList": "false"}, json=cuerpo, timeout=30)
    r.raise_for_status()
    return r.json()["results"][0]["results"]


def versiones_tcgplayer(base, numero, total, idioma):
    """Productos de TCGplayer con ese nombre y número (y total, si lo diste), del idioma pedido."""
    linea = LINEA.get(idioma, "Pokemon")
    vistos, out = set(), []
    for q in (f"{base} {numero[1]}", base):
        for x in _tcgplayer(q):
            pid = str(int(x["productId"]))
            num, tot = cartas._numero((x.get("customAttributes") or {}).get("number"))
            if (pid in vistos or x.get("productLineName") != linea or num != numero
                    or (total and tot and tot[1] != total[1])
                    or not cartas._mismo_nombre(base, re.sub(r"\s*-\s*[\w/]+$", "", x.get("productName") or ""))):
                continue
            vistos.add(pid)
            out.append({"tcgplayer_id": pid, "tcg_nombre": x.get("productName"), "tcg_set": x.get("setName"),
                        "tcg_linea": linea, "rareza": x.get("rarityName"),
                        "numero": (x.get("customAttributes") or {}).get("number"),
                        "mercado_usd": x.get("marketPrice"), "minimo_usd": x.get("lowestPrice"),
                        "tcg_listados": int(x.get("totalListings") or 0),
                        "tcg_url": cartas.TCGPLAYER_WEB.format(pid),
                        "imagen": f"https://tcgplayer-cdn.tcgplayer.com/product/{pid}_in_400x400.jpg"})
    # Las dos búsquedas se juntan: con "nombre número" a veces solo sale una versión
    # (p. ej. la de Prize Pack) y la carta normal aparece buscando solo el nombre.
    return sorted(out, key=lambda v: -v["tcg_listados"])[:4]


def masterset(base, numero, total, idioma, tcg_ids):
    """Cartas de MasterSet con ofertas: por id de TCGplayer o, si no hay, por nombre y número."""
    s = requests.Session()
    s.headers["User-Agent"] = session.headers.get("User-Agent", "Mozilla/5.0")
    s.get(MASTERSET_CATALOGO, timeout=30)   # deja las cookies de sesión y XSRF
    token = urllib.parse.unquote(s.cookies.get("XSRF-TOKEN") or "")
    consulta = f"{base} {numero[1]}" if numero else base   # sin número se busca por nombre (y se cruza por id)
    r = s.post(MASTERSET_BUSCAR, json={"searchValue": consulta, "take": 60, "skip": 0},
               headers={"X-Xsrf-Token": token, "Accept": "application/json"}, timeout=30)
    r.raise_for_status()
    out = []
    for c in r.json().get("data") or []:
        lengua = ((c.get("language") or {}).get("code") or "").split("-")[0]
        nombre = c.get("fullname") or c.get("name") or ""
        partes = cartas.partes_carta(nombre)
        mismo = str(c.get("tcgplayer_id") or "") in tcg_ids if tcg_ids else (
            partes and partes[1] == numero and (not total or not partes[2] or partes[2][1] == total[1])
            and cartas._mismo_nombre(base, partes[0]))
        if not mismo or (idioma and lengua != idioma) or not c.get("items_count") or c.get("min_price") is None:
            continue
        out.append({"tienda": "MasterSet", "precio": cartas._num(c.get("min_price")), "stock": True,
                    "url": cartas.MASTERSET.format(c.get("id")), "masterset_id": c.get("id"),
                    "ofertas": c.get("items_count") or 1, "tcgplayer_id": str(c.get("tcgplayer_id") or ""),
                    "detalle": f"{nombre} · {(c.get('expansion') or {}).get('name') or ''}".strip(" ·")})
    return out


def buscar(texto, idioma="en"):
    """Resultado para la app/web: {consulta, versiones: [...], aviso?}."""
    texto = " ".join(str(texto or "").split())[:80]
    idioma = idioma if idioma in ("en", "ja", "es") else "en"
    partes = cartas.partes_carta(texto)
    if not partes:
        raise ValueError("Escribe el nombre y el número de la carta, por ejemplo: Dhelmise 091/084")
    base, numero, total = partes
    tc = cartas.tipo_cambio(abrir_db())
    versiones = []
    try:
        versiones = versiones_tcgplayer(base, numero, total, idioma)
    except Exception as ex:
        log.warning("Buscador: TCGplayer no respondió (%s)", ex)
    try:
        ms = masterset(base, numero, total, idioma, {v["tcgplayer_id"] for v in versiones})
    except Exception as ex:
        log.warning("Buscador: MasterSet no respondió (%s)", ex)
        ms = []
    if not versiones:   # sin TCGplayer igual se muestran las ofertas de Perú
        versiones = [{"tcgplayer_id": None, "tcg_nombre": None, "tcg_set": None, "mercado_usd": None,
                      "numero": f"{numero[0]}{numero[1]}" + (f"/{total[0]}{total[1]}" if total else "")}]
    for i, v in enumerate(versiones):
        tot = cartas._numero(v.get("numero"))[1] or total
        nombre = f"{base} {numero[0]}{numero[1]:03d}" + (f"/{tot[0]}{tot[1]:03d}" if tot else "")
        f = {"id": f"buscar:{normalizar(nombre)}:{idioma}", "nombre": nombre, "idioma": idioma,
             "tcg_nombre": v.get("tcg_nombre")}
        peru = [o for o in ms if not v["tcgplayer_id"] or o["tcgplayer_id"] == v["tcgplayer_id"]]
        peru = sorted(peru, key=lambda o: o["precio"])[:1] + cartas.en_otras_tiendas(f)
        v["nombre"], v["idioma"] = nombre, idioma
        v["mercado_pen"] = round(v["mercado_usd"] * tc, 2) if v.get("mercado_usd") else None
        v["peru"] = sorted(peru, key=lambda o: (not o["stock"], o["precio"] or 0))
        disp = [o for o in v["peru"] if o["stock"] and o["precio"]]
        mejor = min(disp, key=lambda o: o["precio"]) if disp else None
        v["mejor_tienda"], v["mejor_precio"] = (mejor["tienda"], mejor["precio"]) if mejor else (None, None)
        v.update(cartas.veredicto(v["mejor_precio"], v["mercado_pen"]))
        v["masterset_id"] = next((o["masterset_id"] for o in v["peru"] if o.get("masterset_id")), None)
    return {"consulta": texto, "idioma": idioma, "usd_pen": tc, "versiones": versiones}


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    print(json.dumps(buscar(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "en"), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
