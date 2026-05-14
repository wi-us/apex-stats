#!/usr/bin/env python3
"""Раздаёт весь репозиторий. Удобно, если нужны и designs/, и output/ и т.д.

Запуск из корня проекта: python serve.py

Страницы (порт по умолчанию 8765):
  http://127.0.0.1:8765/designs/design-18-paper-cards.html
  http://127.0.0.1:8765/designs/design-18-paper-cards-admin.html
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


def main() -> None:
    ap = argparse.ArgumentParser(description="HTTP static server for repo root")
    ap.add_argument(
        "-p",
        "--port",
        type=int,
        default=None,
        help="порт (по умолчанию %s или APEX_HTTP_PORT)" % DEFAULT_PORT,
    )
    args = ap.parse_args()
    port = args.port if args.port is not None else pick_port()

    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    handler = http.server.SimpleHTTPRequestHandler
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", port), handler) as httpd:
        print("Каталог:", os.getcwd(), "(корень сайта)")
        print("Старт:   http://127.0.0.1:%s/  → index.html со ссылками" % port)
        print("Карта:   http://127.0.0.1:%s/designs/design-18-paper-cards.html" % port)
        print("Админка: http://127.0.0.1:%s/designs/design-18-paper-cards-admin.html" % port)
        print("Неверно из этого режима: .../design-18-... без папки designs/ в пути.")
        print("(WinError 10013: смените порт, напр. py serve.py -p 9000)")
        print("Ctrl+C — выход")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
