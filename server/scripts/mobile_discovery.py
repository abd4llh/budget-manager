#!/usr/bin/env python3
"""Budget Manager LAN discovery responder.

Protocol v1 (UDP):
  client -> broadcast UDP/7358:  BUDGET_MANAGER_DISCOVER\n
  server -> client unicast JSON: {product,name,address,version,protocol}\n
No credentials, account data, or household data are exposed. The endpoint only
advertises that a Budget Manager server exists and where its web UI is located.
"""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path

DISCOVERY_PORT = int(os.getenv("BUDGET_MANAGER_DISCOVERY_PORT", "7358"))
WEB_PORT = int(os.getenv("BUDGET_MANAGER_WEB_PORT", os.getenv("BUDGET_PORT", "8015")))
SERVER_NAME = os.getenv("BUDGET_MANAGER_DISCOVERY_NAME", "Budget Manager")
EXPLICIT_URL = os.getenv("BUDGET_MANAGER_DISCOVERY_URL", "").strip().rstrip("/")
MAGIC = {b"BUDGET_MANAGER_DISCOVER", b"Who is BudgetManagerServer?"}


def version() -> str:
    try:
        return Path("/app/VERSION").read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


def address_for(peer_ip: str) -> str:
    if EXPLICIT_URL:
        return EXPLICIT_URL
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No packet is sent; connect() only asks the kernel which local interface
        # would route traffic to this peer.
        probe.connect((peer_ip, 9))
        local_ip = probe.getsockname()[0]
    except OSError:
        local_ip = socket.gethostbyname(socket.gethostname())
    finally:
        probe.close()
    return f"http://{local_ip}:{WEB_PORT}"


def main() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("0.0.0.0", DISCOVERY_PORT))
    print(f"Budget Manager discovery listening on UDP {DISCOVERY_PORT}", flush=True)

    while True:
        try:
            data, peer = sock.recvfrom(2048)
            message = data.strip()
            if message not in MAGIC:
                continue
            payload = {
                "product": "budget-manager",
                "name": SERVER_NAME,
                "address": address_for(peer[0]),
                "version": version(),
                "protocol": 1,
            }
            sock.sendto(json.dumps(payload, separators=(",", ":")).encode("utf-8"), peer)
        except KeyboardInterrupt:
            break
        except Exception as exc:  # keep discovery alive even after malformed traffic
            print(f"Discovery error: {exc}", flush=True)


if __name__ == "__main__":
    main()
