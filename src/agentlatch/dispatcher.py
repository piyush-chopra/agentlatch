"""Effects run outside database transactions; receiver must deduplicate Idempotency-Key."""

import asyncio
import json
import uuid
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .models import DeliveryAck


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Dispatcher:
    def __init__(self, coordinator, handlers):
        self.coordinator = coordinator
        self.handlers = handlers
        self.owner = "dispatcher-" + uuid.uuid4().hex

    async def once(self):
        count = 0
        for destination, handler in self.handlers.items():
            item = self.coordinator.claim_delivery("effect", destination, self.owner, ttl=30)
            if not item:
                continue
            ack = DeliveryAck(id=item["id"], owner=self.owner, token=item["token"])
            try:
                async with asyncio.timeout(20):
                    await handler(item)
                self.coordinator.acknowledge(ack)
            except asyncio.CancelledError:
                # Visibility timeout makes interrupted delivery recoverable.
                raise
            except Exception:
                self.coordinator.reject_delivery(ack, retry_after=min(60, 2 ** item["attempts"]))
            count += 1
        return count

    async def run(self):
        while True:
            await self.once()
            await asyncio.sleep(0.5)


def http_handlers(configuration):
    """URLs come only from operator configuration, never from agent payloads."""
    handlers = {}
    for destination, url in configuration.items():
        parsed = urlparse(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Effect endpoints require HTTP(S) URLs without embedded credentials")

        async def send(item, endpoint=url):
            def request():
                payload = json.dumps(
                    {
                        "id": item["id"],
                        "workflow_id": item["workflow_id"],
                        "schema_version": item["schema_version"],
                        "payload": item["payload"],
                    }
                ).encode()
                req = Request(
                    endpoint,
                    data=payload,
                    method="POST",
                    headers={"Content-Type": "application/json", "Idempotency-Key": item["id"]},
                )
                with build_opener(NoRedirect).open(req, timeout=10) as response:
                    if not 200 <= response.status < 300:
                        raise RuntimeError("Effect endpoint rejected delivery")

            await asyncio.to_thread(request)

        handlers[destination] = send
    return handlers
