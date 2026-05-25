#!/usr/bin/env python3
"""
Webhook Receiver — mini-sdv-platform  Milestone 9
==================================================
Receives Grafana alert notifications and logs them.
Simulates a Slack / PagerDuty webhook endpoint for local development.

SDV Concept:
  In production, vehicle anomaly alerts are routed to an on-call system
  (PagerDuty, OpsGenie) or a fleet management dashboard. This service
  is the local stand-in that proves the end-to-end alert pipeline works
  without requiring external service credentials.
"""

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("webhook-receiver")

PORT = int(os.environ.get("PORT", "9000"))


class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")

        try:
            data = json.loads(body)
            title   = data.get("title", "?")
            state   = data.get("state", "?")
            alerts  = data.get("alerts", [])
            log.info(f"{'='*60}")
            log.info(f"[ALERT] {title}  state={state}  count={len(alerts)}")
            for a in alerts:
                status  = a.get("status", "?")
                labels  = a.get("labels", {})
                values  = a.get("values", {})
                ann     = a.get("annotations", {})
                log.info(f"  status={status}  labels={labels}")
                log.info(f"  values={values}")
                if ann.get("summary"):
                    log.info(f"  summary={ann['summary']}")
            log.info(f"{'='*60}")
        except Exception:
            log.info(f"[WEBHOOK RAW] {body[:500]}")

        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *args):
        pass  # suppress default access log


def run() -> None:
    log.info(f"Webhook Receiver listening on port {PORT}...")
    HTTPServer(("0.0.0.0", PORT), WebhookHandler).serve_forever()


if __name__ == "__main__":
    run()
