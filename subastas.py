"""Subastas de cartas (Facebook, grupos, etc.): seguimiento y avisos antes del cierre.

Facebook no deja leer publicaciones sin iniciar sesión, así que el link, la hora de
cierre y la puja actual los anotas tú en la app. La app guarda el valor de la carta
en TCGplayer para decirte hasta cuánto conviene pujar, y programa avisos en Windows
(tareas "TCG Subasta …" que muestran recordatorio.ps1 con sonido) aunque la app esté cerrada.
"""
import datetime as dt
import os
import re
import subprocess
import unicodedata
import uuid

import cartas
from scanner import BASE, guardar_prefs, log, session

BUSCAR_API = "https://mp-search-api.tcgplayer.com/v1/search/request"
RE_TCG = re.compile(r"tcgplayer\.com/(?:[a-z-]+/)?product/(\d+)", re.I)
AVISOS = (60, 30, 10, 3)        # minutos antes del cierre que se pueden elegir
TOPE = 0.8                      # pujar hasta 80% del mercado sigue siendo buena compra
PREFIJO_TAREA = "TCG Subasta"


def buscar_tcgplayer(q, cuantos=12):
    """Cartas de Pokémon (inglés y japonés) en TCGplayer que coinciden con el texto."""
    cuerpo = {
        "algorithm": "sales_synonym_v2", "from": 0, "size": cuantos,
        "filters": {"term": {"productLineName": ["pokemon", "pokemon-japan"]}, "range": {}, "match": {}},
        "listingSearch": {"context": {"cart": {}}, "filters": {
            "term": {"sellerStatus": "Live", "channelId": 0}, "range": {"quantity": {"gte": 1}},
            "exclude": {"channelExclusion": 0}}},
        "context": {"cart": {}, "shippingCountry": "US"}, "settings": {"useFuzzySearch": True}, "sort": {},
    }
    r = session.post(BUSCAR_API, params={"q": q, "isList": "false"}, json=cuerpo, timeout=30)
    r.raise_for_status()
    out = []
    for x in r.json()["results"][0]["results"]:
        pid = str(int(x["productId"]))
        out.append({"id": pid, "nombre": x.get("productName"), "set": x.get("setName"),
                    "linea": x.get("productLineName"), "rareza": x.get("rarityName"),
                    "mercado_usd": x.get("marketPrice"),
                    "imagen": f"https://tcgplayer-cdn.tcgplayer.com/product/{pid}_in_200x200.jpg"})
    return out


def tcg_id_de(texto):
    """Id de TCGplayer desde un id, un link de TCGplayer o un link de MasterSet."""
    texto = str(texto or "").strip()
    if texto.isdigit():
        return texto
    m = RE_TCG.search(texto)
    if m:
        return m.group(1)
    card_id = cartas.id_de(texto)
    return cartas.ficha_masterset(card_id)["tcgplayer_id"] if card_id else None


def referencia(db, tcg_id):
    """Valor de la carta en TCGplayer, en dólares y soles."""
    t = cartas.precio_tcgplayer(tcg_id)
    tc = cartas.tipo_cambio(db)
    return {**t, "tcgplayer_id": str(tcg_id), "usd_pen": tc,
            "mercado_pen": round(t["mercado_usd"] * tc, 2) if t["mercado_usd"] else None,
            "imagen": f"https://tcgplayer-cdn.tcgplayer.com/product/{tcg_id}_in_400x400.jpg",
            "ref_ts": dt.datetime.now().isoformat(timespec="seconds")}


def _num(x):
    try:
        v = float(x)
        return round(v, 2) if v > 0 else None
    except (TypeError, ValueError):
        return None


def guardar(db, prefs, datos):
    """Crea o edita una subasta y reprograma sus avisos. Devuelve la subasta."""
    url = str(datos.get("url", "")).strip()[:500]
    if not re.match(r"https?://", url):
        raise ValueError("Pega el link de la subasta (https://…)")
    try:
        cierre = dt.datetime.fromisoformat(str(datos.get("cierre", ""))).replace(tzinfo=None, microsecond=0)
    except ValueError:
        raise ValueError("Indica la fecha y hora de cierre") from None
    subastas = prefs.setdefault("subastas", {})
    sid = str(datos.get("id") or "")
    previa = subastas.get(sid, {})
    if not previa:
        if len(subastas) >= 100:
            raise ValueError("Máximo 100 subastas: quita alguna cerrada")
        sid = uuid.uuid4().hex[:8]
    s = {**previa, "id": sid, "url": url, "cierre": cierre.isoformat(timespec="seconds"),
         "titulo": str(datos.get("titulo", "")).strip()[:120] or previa.get("titulo") or "Subasta",
         "puja_actual": _num(datos.get("puja_actual")), "mi_maximo": _num(datos.get("mi_maximo")),
         "avisos": sorted({int(m) for m in datos.get("avisos", AVISOS[1:]) if int(m) in AVISOS}, reverse=True),
         "resultado": previa.get("resultado")}
    s.setdefault("creada", dt.datetime.now().isoformat(timespec="seconds"))

    tcg = datos.get("tcg")
    if tcg is not None:  # "" quita la referencia; un id o link la (re)calcula
        tcg_id = tcg_id_de(tcg) if str(tcg).strip() else None
        if str(tcg).strip() and not tcg_id:
            raise ValueError("No reconozco esa carta: elige un resultado de TCGplayer o pega su link")
        for k in [k for k in s if k.startswith("tcg_") or k in ("tcgplayer_id", "mercado_usd", "minimo_usd",
                                                                 "mercado_pen", "usd_pen", "imagen", "ref_ts")]:
            del s[k]
        if tcg_id:
            s.update(referencia(db, tcg_id))
    subastas[sid] = s
    guardar_prefs(prefs)
    s["avisos_programados"] = programar_avisos(s)
    return s


def marcar(prefs, sid, **cambios):
    """Cambios rápidos: puja actual o resultado ("ganada"/"perdida"/None)."""
    s = prefs.get("subastas", {}).get(sid)
    if not s:
        raise ValueError("Esa subasta ya no existe")
    if "puja_actual" in cambios:
        s["puja_actual"] = _num(cambios["puja_actual"])
    if "resultado" in cambios:
        s["resultado"] = cambios["resultado"] if cambios["resultado"] in ("ganada", "perdida") else None
    guardar_prefs(prefs)
    programar_avisos(s)  # el texto del aviso lleva la puja actual
    return s


def quitar(prefs, sid):
    prefs.get("subastas", {}).pop(sid, None)
    guardar_prefs(prefs)
    _powershell(f"Get-ScheduledTask -TaskName '{PREFIJO_TAREA} {sid} *' -ErrorAction SilentlyContinue | "
                "Unregister-ScheduledTask -Confirm:$false")


# --------------------------------------------------------------------------
# Avisos en Windows (Programador de tareas)
# --------------------------------------------------------------------------

def _ascii(s):
    """Texto sin tildes ni comillas para pasarlo como argumento de la tarea."""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[\"'`$]", "", s)


def _powershell(comando):
    if os.name != "nt":
        return None
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", comando],
                       capture_output=True, text=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        log.warning("PowerShell falló: %s", r.stderr.strip()[:300])
    return r


def programar_avisos(s):
    """Borra los avisos de la subasta y crea uno por cada minuto elegido que siga en el futuro.
    Devuelve las horas programadas (ISO)."""
    cierre = dt.datetime.fromisoformat(s["cierre"])
    ahora = dt.datetime.now()
    horas = [] if s.get("resultado") else [
        (m, cierre - dt.timedelta(minutes=m)) for m in s.get("avisos", []) if cierre - dt.timedelta(minutes=m) > ahora]
    script = BASE / "recordatorio.ps1"
    lineas = [f"Get-ScheduledTask -TaskName '{PREFIJO_TAREA} {s['id']} *' -ErrorAction SilentlyContinue | "
              "Unregister-ScheduledTask -Confirm:$false"]
    for m, cuando in horas:
        mensaje = f"{_ascii(s['titulo'])}: cierra a las {cierre:%H:%M}, faltan {m} minutos."
        if s.get("puja_actual"):
            mensaje += f" Ultima puja anotada: S/ {s['puja_actual']:g}."
        if s.get("mi_maximo"):
            mensaje += f" Tu maximo: S/ {s['mi_maximo']:g}."
        args = (f'-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{script}" '
                f'-Titulo "Subasta Pokemon - faltan {m} min" -Mensaje "{mensaje}" -Url "{_ascii(s["url"])}"')
        lineas.append(
            f"$t = New-ScheduledTaskTrigger -Once -At ([datetime]'{cuando:%Y-%m-%dT%H:%M:%S}'); "
            f"$t.EndBoundary = '{cierre + dt.timedelta(minutes=5):%Y-%m-%dT%H:%M:%S}'; "
            f"Register-ScheduledTask -TaskName '{PREFIJO_TAREA} {s['id']} {m:02d}m' -Force "
            f"-Description 'Aviso de subasta: {_ascii(s['titulo'])}' "
            f"-Action (New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '{args}') -Trigger $t "
            "-Settings (New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
            "-WakeToRun -DeleteExpiredTaskAfter (New-TimeSpan -Hours 1)) | Out-Null")
    r = _powershell("; ".join(lineas))
    if r is None or r.returncode != 0:
        return []
    return [c.isoformat(timespec="seconds") for _, c in horas]


def datos_subastas(prefs):
    """Subastas para la app, cada una con los avisos que siguen pendientes."""
    ahora = dt.datetime.now()
    out = []
    for s in prefs.get("subastas", {}).values():
        cierre = dt.datetime.fromisoformat(s["cierre"])
        pendientes = [] if s.get("resultado") else [
            (cierre - dt.timedelta(minutes=m)).isoformat(timespec="seconds")
            for m in s.get("avisos", []) if cierre - dt.timedelta(minutes=m) > ahora]
        tope = round(s["mercado_pen"] * TOPE) if s.get("mercado_pen") else None
        out.append({**s, "avisos_pendientes": pendientes, "tope": tope})
    return sorted(out, key=lambda s: (bool(s.get("resultado")) or s["cierre"] < ahora.isoformat(), s["cierre"]))
