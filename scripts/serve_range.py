#!/usr/bin/env python3
"""Serve one firmware file over HTTP with byte-range support."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re


RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    if not header:
        return None
    match = RANGE_RE.fullmatch(header.strip())
    if not match:
        raise ValueError("unsupported Range header")
    first, last = match.groups()
    if not first and not last:
        raise ValueError("empty Range header")
    if first:
        start = int(first)
        end = int(last) if last else size - 1
    else:
        suffix = int(last)
        if suffix <= 0:
            raise ValueError("invalid suffix range")
        start = max(0, size - suffix)
        end = size - 1
    if start >= size or start > end:
        raise ValueError("range outside file")
    return start, min(end, size - 1)


def make_handler(path: Path, range_status: int):
    payload = path.read_bytes()

    class Handler(BaseHTTPRequestHandler):
        server_version = "MINIR3FactoryOTA/1.0"
        protocol_version = "HTTP/1.1"

        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Connection", "Keep-Alive")
            self.end_headers()

        def do_GET(self):
            if self.path.split("?", 1)[0] not in (f"/{path.name}", "/firmware.bin"):
                self.send_error(404)
                return
            try:
                byte_range = parse_range(self.headers.get("Range"), len(payload))
            except ValueError:
                self.send_error(416, "Invalid Range")
                return
            if byte_range is None:
                start, end = 0, len(payload) - 1
                self.send_response(200)
            else:
                start, end = byte_range
                self.send_response(range_status)
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(payload)}")
            data = payload[start : end + 1]
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Connection", "Keep-Alive")
            self.end_headers()
            self.wfile.write(data)
            self.log_message("%s bytes=%d-%d", self.client_address[0], start, end)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=18767)
    parser.add_argument("--range-status", type=int, choices=(200, 206), default=206)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.bind, args.port), make_handler(args.file, args.range_status))
    print(f"Serving {args.file} ({args.file.stat().st_size} bytes) on {args.bind}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
