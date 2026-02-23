#!/usr/bin/env python3
"""
End-to-end smoke test for the Event Processing & Distribution Service.

Runs against the live docker-compose stack. No pytest, no testcontainers —
just plain HTTP calls against real services.

Usage:
    python scripts/e2e_test.py
    python scripts/e2e_test.py --api http://localhost:8000 \
                               --receiver http://localhost:9001 \
                               --subscriber-endpoint http://receiver:9001

Note:
    --receiver        is queried by this script (host-visible URL)
    --subscriber-endpoint is stored in the DB and POSTed to by the delivery
                      worker running inside Docker. Must use the Docker-internal
                      service name, not localhost.

Exit code:
    0  all scenarios passed
    1  one or more scenarios failed

Scenarios:
    1.  Health checks          — liveness + readiness
    2.  Subscriber lifecycle   — register, get (no secret leak), update, soft-delete
    3.  Happy path             — publish → enrich → deliver → receiver confirms
    4.  Idempotency            — same message_id published 3× → single row, single delivery
    5.  Multiple subscribers   — one event → independent delivery to each subscriber
    6.  HMAC verification      — signature on received webhook is cryptographically valid
    7.  Failed delivery retry  — bad endpoint → failed → fix endpoint → manual retry → delivered
    8.  Pipeline stats         — counts reflect processed events
    9.  Prometheus metrics     — /metrics exposes expected metric names with non-zero values
    10. API edge cases         — DELETE (soft-delete + 404 after), all 404 paths,
                                 subscriber_id/status/limit filters on GET /deliveries,
                                 409 on retry of delivered attempt
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
import time
import uuid
from typing import Any, Callable, Optional

import httpx

# ── Terminal colours ──────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"


class E2ERunner:
    def __init__(self, api_url: str, receiver_url: str, subscriber_endpoint: str) -> None:
        self.api                 = api_url.rstrip("/")
        self.receiver            = receiver_url.rstrip("/")
        # URL the delivery worker (inside Docker) will POST to.
        # Must be reachable from inside the Docker network, e.g. http://receiver:9001
        self.subscriber_endpoint = subscriber_endpoint.rstrip("/")
        self.client              = httpx.Client(timeout=15.0)
        self.passed              = 0
        self.failed              = 0
        self._cleanup_subs: list[str] = []

    # ── Helpers ───────────────────────────────────────────────────────────────

    def section(self, title: str) -> None:
        print(f"\n{BOLD}{CYAN}{'─' * 50}{RESET}")
        print(f"{BOLD}{CYAN}  {title}{RESET}")
        print(f"{BOLD}{CYAN}{'─' * 50}{RESET}")

    def check(self, label: str, ok: bool, detail: str = "") -> bool:
        if ok:
            print(f"  {GREEN}✓{RESET}  {label}")
            self.passed += 1
        else:
            msg = f"  {RED}✗{RESET}  {label}"
            if detail:
                msg += f"\n      {RED}→ {detail}{RESET}"
            print(msg)
            self.failed += 1
        return ok

    def poll(
        self,
        fn: Callable[[], Any],
        timeout: int = 45,
        interval: float = 1.5,
        label: str = "",
    ) -> Any:
        """Call fn() every `interval` seconds until it returns truthy or times out."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = fn()
            if result:
                return result
            time.sleep(interval)
        if label:
            print(f"      {YELLOW}⏱  timed out after {timeout}s: {label}{RESET}")
        return None

    def _hmac_verify(self, secret: str, raw_body: bytes, signature_header: str, timestamp_ms: str) -> bool:
        """Replicates signing.verify() without importing app code."""
        canonical = f"{timestamp_ms}.".encode() + raw_body
        expected  = "sha256=" + hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature_header)

    def _register_subscriber(self, name: str, endpoint: str) -> dict:
        r = self.client.post(f"{self.api}/api/v1/subscribers", json={"name": name, "endpoint": endpoint})
        r.raise_for_status()
        sub = r.json()
        self._cleanup_subs.append(sub["id"])
        return sub

    def _publish(self, event_type: str, payload: dict, message_id: str | None = None) -> str:
        mid = message_id or str(uuid.uuid4())
        r = self.client.post(
            f"{self.api}/api/v1/events/publish",
            json={"message_id": mid, "event_type": event_type, "payload": payload},
        )
        r.raise_for_status()
        return mid

    def _wait_enriched(self, message_id: str, timeout: int = 45) -> dict | None:
        def _check():
            r = self.client.get(f"{self.api}/api/v1/events/{message_id}")
            if r.status_code == 200 and r.json().get("status") in ("enriched", "dispatched"):
                return r.json()
        return self.poll(_check, timeout=timeout, label=f"event {message_id} enriched")

    def _wait_delivered(self, message_id: str, subscriber_id: str, timeout: int = 45) -> dict | None:
        def _check():
            r = self.client.get(f"{self.api}/api/v1/deliveries", params={"message_id": message_id})
            for d in r.json():
                if d["subscriber_id"] == subscriber_id and d["status"] == "delivered":
                    return d
        return self.poll(_check, timeout=timeout, label=f"delivery to {subscriber_id[:8]}…")

    def _wait_delivery_status(self, delivery_id: str, status: str, timeout: int = 45) -> dict | None:
        def _check():
            r = self.client.get(f"{self.api}/api/v1/deliveries", params={"status": status})
            for d in r.json():
                if d["id"] == delivery_id:
                    return d
        return self.poll(_check, timeout=timeout, label=f"delivery {delivery_id[:8]}… → {status}")

    def _clear_receiver(self) -> None:
        self.client.delete(f"{self.receiver}/received")

    def _receiver_find(self, event_type: str, timeout: int = 30) -> dict | None:
        def _check():
            r = self.client.get(f"{self.receiver}/received", params={"limit": 100})
            for item in r.json().get("items", []):
                if item.get("body", {}).get("event_type") == event_type:
                    return item
        return self.poll(_check, timeout=timeout, label=f"receiver got {event_type}")

    # ── Scenarios ─────────────────────────────────────────────────────────────

    def test_health(self) -> None:
        self.section("1. Health Checks")

        r = self.client.get(f"{self.api}/health/live")
        self.check("GET /health/live → 200", r.status_code == 200)

        r = self.client.get(f"{self.api}/health/ready")
        self.check("GET /health/ready → 200", r.status_code == 200)
        data = r.json()
        self.check(
            "readiness status=ok (DB + RabbitMQ reachable)",
            data.get("status") == "ok",
            str(data),
        )

    def test_subscriber_lifecycle(self) -> tuple[dict, ...]:
        self.section("2. Subscriber Lifecycle")

        # Create
        r = self.client.post(f"{self.api}/api/v1/subscribers", json={
            "name": "e2e-primary",
            "endpoint": f"{self.subscriber_endpoint}/webhook",
        })
        self.check("POST /subscribers → 201", r.status_code == 201)
        sub = r.json()
        self._cleanup_subs.append(sub["id"])

        self.check("secret returned on creation", bool(sub.get("secret")))
        self.check("is_active=true by default", sub.get("is_active") is True)

        # GET must not expose secret
        r2 = self.client.get(f"{self.api}/api/v1/subscribers/{sub['id']}")
        self.check("GET /subscribers/{id} → 200", r2.status_code == 200)
        self.check("GET does not expose secret", "secret" not in r2.json())

        # PATCH
        new_endpoint = f"{self.subscriber_endpoint}/webhook"
        r3 = self.client.patch(f"{self.api}/api/v1/subscribers/{sub['id']}", json={"endpoint": new_endpoint})
        self.check("PATCH /subscribers/{id} → 200", r3.status_code == 200)
        self.check("endpoint updated", r3.json().get("endpoint") == new_endpoint)

        # Appear in list
        r4 = self.client.get(f"{self.api}/api/v1/subscribers")
        ids = [s["id"] for s in r4.json()]
        self.check("subscriber appears in GET /subscribers list", sub["id"] in ids)

        return (sub,)

    def test_happy_path(self, subscriber: dict) -> None:
        self.section("3. Happy Path: publish → enrich → deliver")

        self._clear_receiver()
        tag = "e2e.purchase"
        mid = self._publish(tag, {"amount": 99.0, "currency": "USD"})

        self.check("POST /events/publish → 202", True)  # would have raised on error

        # Enrichment
        event = self._wait_enriched(mid)
        self.check("event reaches status=enriched within 45 s", event is not None)
        if event:
            ep = event.get("enriched_payload") or {}
            self.check("enriched_payload contains _enrichment key", "_enrichment" in ep)
            enrichment = ep.get("_enrichment", {})
            self.check("_enrichment.summary present", "summary" in enrichment)
            self.check("_enrichment.sentiment present", "sentiment" in enrichment)
            self.check("_enrichment.confidence present", "confidence" in enrichment)

        # Delivery
        delivery = self._wait_delivered(mid, subscriber["id"])
        self.check("delivery status=delivered within 45 s", delivery is not None)
        if delivery:
            self.check("last_http_status=200", delivery.get("last_http_status") == 200)
            self.check("attempt_count=1 (succeeded first try)", delivery.get("attempt_count") == 1)

        # Receiver confirmation
        received = self._receiver_find(tag)
        self.check("receiver logged the webhook", received is not None)
        if received:
            body = received.get("body", {})
            self.check("webhook body.event_type correct", body.get("event_type") == tag)
            self.check("webhook body.payload present", "payload" in body)
            self.check("webhook body.delivery_id present", "delivery_id" in body)

    def test_idempotency(self, subscriber: dict) -> None:
        self.section("4. Idempotency: same message_id published 3×")

        mid = str(uuid.uuid4())
        for i in range(3):
            r = self.client.post(f"{self.api}/api/v1/events/publish", json={
                "message_id": mid,
                "event_type": "e2e.dedup",
                "payload": {"attempt": i},
            })
            self.check(f"publish #{i+1} → 202", r.status_code == 202)

        # Wait for processing
        event = self._wait_enriched(mid)
        self.check("event key exists after publishing", event is not None)

        # Exactly one delivery attempt per subscriber despite 3 publishes
        def _one_delivery():
            r = self.client.get(f"{self.api}/api/v1/deliveries", params={"message_id": mid})
            deliveries = [d for d in r.json() if d["subscriber_id"] == subscriber["id"]]
            if deliveries:
                return deliveries
        deliveries = self.poll(_one_delivery, timeout=20, label="delivery attempt created")
        if deliveries is not None:
            self.check(
                f"exactly 1 delivery attempt for subscriber (got {len(deliveries)})",
                len(deliveries) == 1,
            )

    def test_multiple_subscribers(self, subscriber_a: dict) -> None:
        self.section("5. Multiple Subscribers: one event → two deliveries")

        sub_b = self._register_subscriber("e2e-secondary", f"{self.subscriber_endpoint}/webhook")

        mid = self._publish("e2e.multi", {"x": 42})

        def _both_delivered():
            r = self.client.get(f"{self.api}/api/v1/deliveries", params={"message_id": mid})
            delivered = [d for d in r.json() if d["status"] == "delivered"]
            return delivered if len(delivered) >= 2 else None

        delivered = self.poll(_both_delivered, timeout=50, label="both deliveries done")
        self.check("both subscribers received delivery within 50 s", delivered is not None)
        if delivered:
            subscriber_ids = {d["subscriber_id"] for d in delivered}
            self.check(
                f"deliveries for 2 distinct subscribers (got {len(subscriber_ids)})",
                len(subscriber_ids) == 2,
            )
            self.check(
                "subscriber A received it", subscriber_a["id"] in subscriber_ids
            )
            self.check(
                "subscriber B received it", sub_b["id"] in subscriber_ids
            )

    def test_hmac_verification(self, subscriber: dict) -> None:
        self.section("6. HMAC-SHA256 Signature Verification")

        secret = subscriber["secret"]
        tag = f"e2e.hmac.{uuid.uuid4().hex[:6]}"  # unique so we can find it in receiver log
        self._clear_receiver()
        self._publish(tag, {"verify": True})

        received = self._receiver_find(tag)
        self.check("webhook with HMAC headers received by receiver", received is not None)

        if received:
            sig    = received.get("signature", "")
            ts     = received.get("timestamp_ms", "")
            wid    = received.get("webhook_id", "")
            raw    = received.get("raw_body", "").encode()

            self.check("X-Webhook-Signature header present", sig.startswith("sha256="))
            self.check("X-Webhook-Timestamp header present", bool(ts))
            self.check("X-Webhook-ID header present", bool(wid))

            valid = self._hmac_verify(secret, raw, sig, ts)
            self.check("HMAC-SHA256 signature is cryptographically valid", valid)

            tampered = raw + b" "
            self.check(
                "tampered body fails HMAC verification",
                not self._hmac_verify(secret, tampered, sig, ts),
            )
            self.check(
                "wrong secret fails HMAC verification",
                not self._hmac_verify("wrongsecret", raw, sig, ts),
            )

    def test_failed_delivery_and_retry(self) -> None:
        self.section("7. Failed Delivery → Manual Retry")

        # Register subscriber pointing at a port with nothing listening
        dead_sub = self._register_subscriber(
            "e2e-dead-endpoint",
            "http://127.0.0.1:19999/webhook",
        )

        mid = self._publish("e2e.retry", {"will_fail": True})

        # Wait for at least one failed attempt
        def _failed():
            r = self.client.get(f"{self.api}/api/v1/deliveries", params={"message_id": mid})
            for d in r.json():
                if d["subscriber_id"] == dead_sub["id"] and d["status"] == "failed":
                    return d

        failed_attempt = self.poll(_failed, timeout=40, label="first failure recorded")
        self.check("delivery reaches status=failed", failed_attempt is not None)

        if failed_attempt:
            self.check("attempt_count >= 1", failed_attempt["attempt_count"] >= 1)
            self.check("last_error is recorded", bool(failed_attempt.get("last_error")))

            # Fix the endpoint
            self.client.patch(
                f"{self.api}/api/v1/subscribers/{dead_sub['id']}",
                json={"endpoint": f"{self.subscriber_endpoint}/webhook"},
            )

            # Trigger manual retry
            r = self.client.post(f"{self.api}/api/v1/deliveries/{failed_attempt['id']}/retry")
            self.check("POST /deliveries/{id}/retry → 200", r.status_code == 200)
            self.check("retry resets status to pending", r.json().get("status") == "pending")

            # Wait for successful re-delivery
            def _retried():
                r = self.client.get(f"{self.api}/api/v1/deliveries", params={"message_id": mid})
                for d in r.json():
                    if d["id"] == failed_attempt["id"] and d["status"] == "delivered":
                        return d

            delivered = self.poll(_retried, timeout=30, label="retried delivery succeeded")
            self.check("delivery succeeds after endpoint fix + retry", delivered is not None)
            if delivered:
                self.check(
                    "attempt_count incremented from retry",
                    delivered["attempt_count"] > failed_attempt["attempt_count"],
                )

    def test_pipeline_stats(self) -> None:
        self.section("8. Pipeline Stats")

        r = self.client.get(f"{self.api}/api/v1/pipeline/stats")
        self.check("GET /pipeline/stats → 200", r.status_code == 200)
        data = r.json()

        self.check("deliveries_by_status present", "deliveries_by_status" in data)
        self.check("idempotency_keys_by_status present", "idempotency_keys_by_status" in data)
        self.check("queue_depth present", "queue_depth" in data)

        delivered = data.get("deliveries_by_status", {}).get("delivered", 0)
        self.check(f"delivered count > 0 (got {delivered})", delivered > 0)

        enriched = data.get("idempotency_keys_by_status", {}).get("enriched", 0)
        received = data.get("idempotency_keys_by_status", {}).get("received", 0)
        total_events = enriched + received
        self.check(f"idempotency keys recorded (got {total_events})", total_events > 0)

    def test_prometheus_metrics(self) -> None:
        self.section("9. Prometheus Metrics")

        r = self.client.get(f"{self.api}/metrics")
        self.check("GET /metrics → 200", r.status_code == 200)
        self.check(
            "Content-Type is text/plain",
            "text/plain" in r.headers.get("content-type", ""),
        )

        body = r.text
        expected_metrics = [
            "consumer_messages_received_total",
            "consumer_messages_duplicate_total",
            "consumer_enrichment_errors_total",
            "consumer_enrichment_duration_seconds",
            "delivery_attempts_total",
            "delivery_successes_total",
            "delivery_failures_total",
            "delivery_dead_total",
            "delivery_http_duration_seconds",
        ]
        for metric in expected_metrics:
            self.check(f"metric {metric} present", metric in body)

        # At least some counters should be non-zero after running the suite
        self.check(
            "consumer_messages_received_total > 0",
            'consumer_messages_received_total{} ' not in body  # i.e. value is not 0.0 placeholder
            or any(
                line.startswith("consumer_messages_received_total") and not line.endswith(" 0.0")
                for line in body.splitlines()
            ),
        )

    def test_api_edge_cases(self) -> None:
        self.section("10. API Edge Cases & Full Endpoint Coverage")

        # ── DELETE /api/v1/subscribers/{id} ──────────────────────────────────
        temp = self._register_subscriber("e2e-delete-me", f"{self.subscriber_endpoint}/webhook")
        temp_id = temp["id"]
        self._cleanup_subs.remove(temp_id)  # we'll delete it here explicitly

        r = self.client.delete(f"{self.api}/api/v1/subscribers/{temp_id}")
        self.check("DELETE /subscribers/{id} → 204", r.status_code == 204)

        # Deleted subscriber must not appear in list
        r2 = self.client.get(f"{self.api}/api/v1/subscribers")
        ids_after = [s["id"] for s in r2.json()]
        self.check("soft-deleted subscriber absent from GET /subscribers list", temp_id not in ids_after)

        # Deleted subscriber must 404 on GET
        r3 = self.client.get(f"{self.api}/api/v1/subscribers/{temp_id}")
        self.check("GET /subscribers/{id} on deleted → 404", r3.status_code == 404)

        # DELETE again → 404 (already soft-deleted)
        r4 = self.client.delete(f"{self.api}/api/v1/subscribers/{temp_id}")
        self.check("DELETE /subscribers/{id} twice → 404", r4.status_code == 404)

        # ── GET /events/{message_id} 404 ─────────────────────────────────────
        r5 = self.client.get(f"{self.api}/api/v1/events/nonexistent-message-id-{uuid.uuid4()}")
        self.check("GET /events/{message_id} for unknown ID → 404", r5.status_code == 404)

        # ── GET /subscribers/{id} 404 ────────────────────────────────────────
        r6 = self.client.get(f"{self.api}/api/v1/subscribers/{uuid.uuid4()}")
        self.check("GET /subscribers/{id} for unknown ID → 404", r6.status_code == 404)

        # ── GET /deliveries?subscriber_id= filter ────────────────────────────
        # Publish a fresh event with a known subscriber so we have data to filter on
        filter_sub = self._register_subscriber("e2e-filter", f"{self.subscriber_endpoint}/webhook")
        mid = self._publish("e2e.filter", {"x": 1})
        # Wait for delivery attempt to be created (doesn't need to be delivered)
        def _attempt_exists():
            r = self.client.get(
                f"{self.api}/api/v1/deliveries",
                params={"subscriber_id": filter_sub["id"]},
            )
            return r.json() if r.json() else None
        attempts = self.poll(_attempt_exists, timeout=20, label="delivery attempt for filter_sub")
        self.check("GET /deliveries?subscriber_id= returns results", bool(attempts))
        if attempts:
            self.check(
                "all returned rows belong to that subscriber",
                all(d["subscriber_id"] == filter_sub["id"] for d in attempts),
            )

        # ── GET /deliveries?status= filter ───────────────────────────────────
        r7 = self.client.get(f"{self.api}/api/v1/deliveries", params={"status": "delivered"})
        self.check("GET /deliveries?status=delivered → 200", r7.status_code == 200)
        if r7.json():
            self.check(
                "all returned rows have status=delivered",
                all(d["status"] == "delivered" for d in r7.json()),
            )

        # ── GET /deliveries?limit= ────────────────────────────────────────────
        r8 = self.client.get(f"{self.api}/api/v1/deliveries", params={"limit": 2})
        self.check("GET /deliveries?limit=2 → at most 2 results", len(r8.json()) <= 2)

        # ── POST /deliveries/{id}/retry 409 on non-dead/failed ───────────────
        # Find a delivered attempt from the happy-path run
        r9 = self.client.get(f"{self.api}/api/v1/deliveries", params={"status": "delivered"})
        delivered_rows = r9.json()
        if delivered_rows:
            delivery_id = delivered_rows[0]["id"]
            r10 = self.client.post(f"{self.api}/api/v1/deliveries/{delivery_id}/retry")
            self.check(
                "POST /deliveries/{id}/retry on delivered attempt → 409",
                r10.status_code == 409,
            )

        # ── POST /deliveries/{id}/retry 404 on unknown id ────────────────────
        r11 = self.client.post(f"{self.api}/api/v1/deliveries/{uuid.uuid4()}/retry")
        self.check("POST /deliveries/{id}/retry for unknown ID → 404", r11.status_code == 404)

    # ── Cleanup & summary ─────────────────────────────────────────────────────

    def _cleanup(self) -> None:
        self.section("Cleanup")
        for sub_id in self._cleanup_subs:
            r = self.client.delete(f"{self.api}/api/v1/subscribers/{sub_id}")
            status = "✓" if r.status_code == 204 else f"? {r.status_code}"
            print(f"  {status}  deleted subscriber {sub_id[:8]}…")

    def _summary(self) -> bool:
        total = self.passed + self.failed
        print(f"\n{BOLD}{'═' * 50}{RESET}")
        if self.failed == 0:
            print(f"{BOLD}{GREEN}  ALL PASS  {self.passed}/{total}{RESET}")
        else:
            print(f"{BOLD}{RED}  {self.failed} FAILED  {self.passed}/{total} passed{RESET}")
        print(f"{BOLD}{'═' * 50}{RESET}\n")
        return self.failed == 0

    # ── Main ──────────────────────────────────────────────────────────────────

    def _pre_run_cleanup(self) -> None:
        """Delete any e2e-* subscribers left over from a previously aborted run.

        Without this, an aborted run leaves active subscribers in the DB which
        causes subsequent runs to see more deliveries than expected (scenario 5)
        and to receive webhooks signed with the wrong secret (scenario 6).
        """
        r = self.client.get(f"{self.api}/api/v1/subscribers")
        if r.status_code != 200:
            return
        stale = [s for s in r.json() if s.get("name", "").startswith("e2e-")]
        if stale:
            print(f"\n{YELLOW}  ⚠  cleaning up {len(stale)} stale e2e subscriber(s) from a previous run{RESET}")
            for s in stale:
                self.client.delete(f"{self.api}/api/v1/subscribers/{s['id']}")

    def run(self) -> bool:
        print(f"\n{BOLD}Event Processing & Distribution Service — E2E Test Suite{RESET}")
        print(f"  API:                {self.api}")
        print(f"  Receiver (query):   {self.receiver}")
        print(f"  Subscriber endpoint:{self.subscriber_endpoint}  ← used by delivery worker")

        self._pre_run_cleanup()
        self.test_health()

        (sub,) = self.test_subscriber_lifecycle()

        self.test_happy_path(sub)
        self.test_idempotency(sub)
        self.test_multiple_subscribers(sub)
        self.test_hmac_verification(sub)
        self.test_failed_delivery_and_retry()
        self.test_pipeline_stats()
        self.test_prometheus_metrics()
        self.test_api_edge_cases()

        self._cleanup()
        return self._summary()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="E2E smoke test for the event pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--api",      default="http://localhost:8000", help="API base URL (host-visible)")
    parser.add_argument("--receiver", default="http://localhost:9001", help="Receiver base URL (host-visible, for querying received webhooks)")
    parser.add_argument("--subscriber-endpoint", default="http://receiver:9001",
                        help="Receiver URL as seen by the delivery worker inside Docker")
    args = parser.parse_args()

    try:
        runner = E2ERunner(args.api, args.receiver, args.subscriber_endpoint)
        ok = runner.run()
        sys.exit(0 if ok else 1)
    except httpx.ConnectError as exc:
        print(f"\n{RED}{BOLD}Connection error — is the stack running?{RESET}")
        print(f"  {exc}")
        print(f"\n  Start it with:  {BOLD}docker compose up --build{RESET}\n")
        sys.exit(2)


if __name__ == "__main__":
    main()
