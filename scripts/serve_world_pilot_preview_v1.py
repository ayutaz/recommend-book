"""Loopback preview of two explicit public HTML artifacts; no file browsing."""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--delivery', type=Path, required=True)
    parser.add_argument('--withdrawn', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    # Read just these explicit public HTML files. Never expose their directories,
    # local provenance packets, private profiles, API endpoints or arbitrary URLs.
    pages = {'/': args.delivery.resolve(strict=True).read_bytes(),
             '/withdrawn': args.withdrawn.resolve(strict=True).read_bytes()}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in pages:
                self.send_error(404)
                return
            content = pages[self.path]
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_):
            pass

    server = HTTPServer(('127.0.0.1', args.port), Handler)
    print('Public HTML preview: http://127.0.0.1:' + str(server.server_port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
