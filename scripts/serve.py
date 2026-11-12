#!/usr/bin/env python3
"""Simple HTTP server for the viewer."""

import http.server
import os
import socketserver
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIEWER_DIR = os.path.join(PROJECT_DIR, "viewer")
SITES_DIR = os.path.join(PROJECT_DIR, "data", "sites")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=VIEWER_DIR, **kwargs)
    
    def translate_path(self, path):
        if path.startswith('/data/sites/'):
            return os.path.join(SITES_DIR, path[12:])
        return super().translate_path(path)
    
    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()


with socketserver.TCPServer(("", PORT), Handler) as httpd:
    print(f"Serving viewer at http://localhost:{PORT}")
    print(f"  Red Bull Ring: http://localhost:{PORT}/")
    print(f"  Japan sites:   http://localhost:{PORT}/site.html")
    print("Press Ctrl+C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped")
