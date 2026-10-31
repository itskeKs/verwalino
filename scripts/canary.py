#!/usr/bin/env python3
"""Exercise the hosted API using only synthetic data and a dedicated canary login."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import socket
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import build_opener, HTTPCookieProcessor, HTTPRedirectHandler, Request
import uuid

DEFAULT_URL = "https://app.verwalino.de"
REQUEST_TIMEOUT = 10
MAX_RESPONSE_BYTES = 1024 * 1024
# An immutable synthetic fixture period; no yearly operator maintenance is needed.
PERIOD = {"period_start": "2026-01-01", "period_end": "2026-01-31"}


class CanaryError(Exception):
    def __init__(self, kind):
        self.kind = kind
        super().__init__(kind)


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, base_url):
        parsed = urlsplit(base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment
                or (parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"})):
            raise CanaryError("Configuration")
        self.base = base_url.rstrip("/")
        self.cookies = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies), NoRedirects())

    def request(self, method, path, body=None, expected=200):
        data = json.dumps(body).encode() if body is not None else None
        request = Request(self.base + path, data=data, method=method,
                          headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with self.opener.open(request, timeout=REQUEST_TIMEOUT) as response:
                if response.status != expected:
                    raise CanaryError(f"HTTP{response.status}")
                if expected == 204:
                    return None
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise CanaryError("InvalidResponse")
                return json.loads(raw)
        except HTTPError as error:
            error.close()
            raise CanaryError(f"HTTP{error.code}") from None
        except (TimeoutError, socket.timeout):
            raise CanaryError("Timeout") from None
        except URLError as error:
            raise CanaryError("Timeout" if isinstance(error.reason, TimeoutError) else "Connection") from None
        except (OSError, ValueError):
            raise CanaryError("InvalidResponse") from None


def identifier(row, key="id"):
    try:
        return str(uuid.UUID(row[key]))
    except (KeyError, ValueError, TypeError, AttributeError):
        raise CanaryError("InvalidResponse") from None


def only(rows):
    if not isinstance(rows, list) or len(rows) != 1:
        raise CanaryError("Fixture")
    return rows[0]


def tenant_ledger(client):
    # Reuse the BDD/API contract, without importing pytest or resetting a database.
    # Exactly one synthetic lease and one readable MIET scope belong to this org.
    lease = only(client.request("GET", "/api/leases"))
    lease_id, unit_id = identifier(lease), identifier(lease, "unit_id")
    unit = client.request("GET", f"/api/units/{unit_id}")
    property_id = identifier(unit, "property_id")
    query = urlencode({"property_id": property_id, "scope_type": "MIET", "capability": "read"})
    page = client.request("GET", f"/api/accounting-scopes/page?{query}")
    if not isinstance(page, dict) or page.get("next_cursor") is not None:
        raise CanaryError("Fixture")
    scope_id = identifier(only(page.get("items")))
    query = urlencode({**PERIOD, "accounting_scope_id": scope_id})
    ledger = client.request("GET", f"/api/payments/mietkonto/lease/{lease_id}?{query}")
    try:
        totals = [Decimal(ledger[key]) for key in ("soll_total", "ist_total", "balance")]
        if not all(value.is_finite() for value in totals) or not isinstance(ledger["open_items"], list):
            raise ValueError
    except (KeyError, TypeError, InvalidOperation, ValueError):
        raise CanaryError("InvalidResponse") from None


def make_report(steps):
    return {
        "run": {"mode": "hosted", "finished": datetime.now(timezone.utc).isoformat(),
                "git_sha": os.environ.get("GITHUB_SHA", "")},
        "scenarios": [{"feature": "canary", "scenario": "Hosted canary", "tags": ["canary"],
                       "outcome": "failed" if any(step["outcome"] == "failed" for step in steps) else "passed",
                       "steps": steps, "artifacts": []}],
    }


def journey(base_url, email, password):
    steps = []
    created_id = None

    def step(name, action):
        try:
            value = action()
        except CanaryError as error:
            steps.append({"name": name, "outcome": "failed", "error": {"type": error.kind}})
            raise
        steps.append({"name": name, "outcome": "passed"})
        return value

    def login():
        if not email or not password:
            raise CanaryError("Configuration")
        client = Client(base_url)
        result = client.request("POST", "/api/auth/login", {"email": email, "password": password})
        if not isinstance(result, dict) or result.get("role") != "OWNER_ADMIN" or not list(client.cookies):
            raise CanaryError("InvalidResponse")
        return client

    try:
        client = step("login", login)
        created_id = step("create-property", lambda: identifier(client.request("POST", "/api/properties", {
            "name": f"Canary Prüfung {uuid.uuid4()}", "management_type": "MIET",
        }, expected=201)))
        step("tenant-ledger", lambda: tenant_ledger(client))
    except CanaryError:
        pass
    finally:
        if created_id:
            try:
                step("cleanup", lambda: client.request("DELETE", f"/api/properties/{created_id}", expected=204))
            except CanaryError:
                pass
    return make_report(steps)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_URL)
    parser.add_argument("--report", type=Path, default=Path("report/canary.json"))
    parser.add_argument("--dry-run", action="store_true", help="Print the journey without HTTP requests or a report")
    args = parser.parse_args(argv)
    if args.dry_run:
        print("login → create unused property → read synthetic tenant ledger → delete created property")
        return 0
    report = journey(args.base_url, os.environ.get("CANARY_EMAIL"), os.environ.get("CANARY_PASSWORD"))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    for step in report["scenarios"][0]["steps"]:
        print(f"{step['name']}: {step['outcome']}" + (f" ({step['error']['type']})" if step.get("error") else ""))
    return int(report["scenarios"][0]["outcome"] == "failed")


if __name__ == "__main__":
    sys.exit(main())
