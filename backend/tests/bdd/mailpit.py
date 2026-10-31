"""Shared real-SMTP inbox for mail acceptance scenarios."""
import os
import time

import httpx
import pytest


class Mailpit:
    def __init__(self, client: httpx.Client):
        self.client = client

    def request(self, method: str, path: str) -> httpx.Response:
        try:
            response = self.client.request(method, path)
            response.raise_for_status()
            return response
        except httpx.HTTPError as error:
            pytest.fail(
                f"Mailpit API unavailable at {self.client.base_url} ({type(error).__name__}). "
                "Start it with docker compose -f infra/docker-compose.declaration-test.yml up -d mailpit "
                "and source scripts/test-env.sh.",
                pytrace=False,
            )

    def clear(self) -> None:
        self.request("DELETE", "/api/v1/messages")

    def wait_for_message(self, recipient: str) -> dict:
        """Read the matching message, allowing at most three seconds for delivery."""
        deadline = time.monotonic() + 3
        while True:
            messages = self.request("GET", "/api/v1/messages").json()["messages"]
            for message in messages:
                if any(address["Address"] == recipient for address in message["To"]):
                    return self.request("GET", f"/api/v1/message/{message['ID']}").json()
            if time.monotonic() >= deadline:
                pytest.fail(f"Mailpit received no message for {recipient} within three seconds", pytrace=False)
            time.sleep(0.1)


@pytest.fixture
def mailpit():
    api = os.environ.get("VERWALINO_MAILPIT_API")
    if not api:
        pytest.fail("VERWALINO_MAILPIT_API is required; source scripts/test-env.sh", pytrace=False)
    with httpx.Client(base_url=api, timeout=1, trust_env=False) as client:
        inbox = Mailpit(client)
        inbox.clear()
        yield inbox
