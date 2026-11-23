"""A stand-in for the model list of an OpenAI-compatible provider, for the settings page of an acceptance instance.

Started and stopped by ``tests.offline_invoice_processing stub start|stop``; standard library only, so that it starts at once.
It listens on 127.0.0.1 and answers ``GET <base>/models`` with the given model ids, nothing else. Never ``localhost``: the app
connects to the first address a name resolves to, and that can be the IPv6 one, where nothing listens.

It ends by itself when the acceptance instance it was started for is gone (the record of the run in the acceptance lock names
another scratch directory or has been removed), so that no listener outlives ``scripts/acceptance-env.sh stop``.
"""
import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HOST = "127.0.0.1"
CHECK_SECONDS = 0.5


def instance_is_running(lock: Path, scratch: Path) -> bool:
    """Whether the lock still records a run in this scratch directory (``acceptance-env.sh stop`` removes both)."""
    try:
        lines = (lock / "instance").read_text().splitlines()
    except OSError:
        return False
    return f"scratch={scratch}" in lines and scratch.is_dir()


def handler_for(models: list[str]):
    body = json.dumps({"object": "list", "data": [{"id": model, "object": "model", "owned_by": "offline"} for model in models]}).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            found = self.path.split("?", 1)[0].rstrip("/").endswith("/models")
            payload = body if found else b'{"error":"not found"}'
            self.send_response(200 if found else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_arguments):
            pass

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--lock", type=Path, required=True, help="the acceptance lock directory of the run")
    parser.add_argument("--scratch", type=Path, required=True, help="the scratch directory of the run")
    parser.add_argument("--model", action="append", required=True, help="a model id to list (repeatable)")
    arguments = parser.parse_args(argv)
    server = HTTPServer((HOST, arguments.port), handler_for(arguments.model))
    server.timeout = CHECK_SECONDS
    try:
        while instance_is_running(arguments.lock, arguments.scratch):
            server.handle_request()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
