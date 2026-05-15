#!/usr/bin/env python3
"""Раздаёт текущую папку (designs/) по HTTP. Запуск: python serve.py

Важно: адреса БЕЗ префикса /designs — файл лежит в корне этого сервера.
"""
from __future__ import annotations

import argparse
import http.server
import os
import socketserver

DEFAULT_PORT = 8765


def pick_port() -> int:
    raw = os.environ.get("APEX_HTTP_PORT") or os.environ.get("SERVE_PORT") or ""
    if raw.strip():
        try:
            return int(raw.strip(), 10)
        except ValueError:
            pass
    return DEFAULT_PORT


class DesignsRootHandler(http.server.SimpleHTTPRequestHandler):
    """Корень сервера = папка designs/. Запросы /designs/... тоже обслуживаем (как после ссылки с корня репо)."""

    def translate_path(self, path: str) -> str:
        path = path.split("?", 1)[0].split("#", 1)[0]
        if path.startswith("/designs/"):
            path = path[len("/designs") :] or "/"
        elif path.rstrip("/") == "/designs":
            path = "/"
        return super().translate_path(path)


def main() -> None:
    ap = argparse.ArgumentParser(description="HTTP static server for designs/")
    ap.add_argument(
        "-p",
        "--port",
        type=int,
        default=None,
        help="порт (по умолчанию %s или переменная APEX_HTTP_PORT)" % DEFAULT_PORT,
    )
    args = ap.parse_args()
    port = args.port if args.port is not None else pick_port()

    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", port), DesignsRootHandler) as httpd:
        print("Каталог:", os.getcwd(), "(корень сайта)")
        print("Старт:   http://127.0.0.1:%s/  → index.html со ссылками" % port)
        print("Карта:   http://127.0.0.1:%s/design-18-paper-cards.html" % port)
        print("         (и /designs/design-18-paper-cards.html — то же самое)")
        print("Админка: http://127.0.0.1:%s/design-18-paper-cards-admin.html" % port)
        print("(WinError 10013 на 8080: другой порт, напр. -p 9000)")
        print("Ctrl+C — выход")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
