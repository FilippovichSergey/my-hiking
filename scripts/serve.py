"""Лакальны прагляд сайта: hike serve [порт]  →  http://localhost:8000"""
import functools
import http.server

from common import DOCS


def main(argv=None) -> None:
    port = int(argv[0]) if argv else 8000
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(DOCS))
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        print(f"Сайт: http://localhost:{port}  (Ctrl+C — спыніць)")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
