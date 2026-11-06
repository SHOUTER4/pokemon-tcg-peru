"""Aviso emergente con sonido, SIN abrir consola (se corre con pythonw.exe).

Lo usan las tareas "TCG Subasta …" (avisos antes del cierre) y notificar.py. Si hay link,
"Sí" abre la subasta en el navegador predeterminado.

Uso: pythonw recordatorio.py --titulo "…" --mensaje "…" [--url "https://…"]
"""
import argparse
import ctypes
import webbrowser
import winsound

MB_OK, MB_YESNO, MB_ICONWARNING = 0x0, 0x4, 0x30
MB_TOPMOST, MB_SETFOREGROUND, IDYES = 0x40000, 0x10000, 6


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--titulo", default="Recordatorio")
    ap.add_argument("--mensaje", default="")
    ap.add_argument("--url", default="")
    a = ap.parse_args()
    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    texto = a.mensaje + ("\n\n¿Abrir la subasta ahora?" if a.url else "")
    estilo = (MB_YESNO if a.url else MB_OK) | MB_ICONWARNING | MB_TOPMOST | MB_SETFOREGROUND
    if ctypes.windll.user32.MessageBoxW(None, texto, a.titulo, estilo) == IDYES and a.url:
        webbrowser.open(a.url)


if __name__ == "__main__":
    main()
