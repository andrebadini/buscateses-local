"""Offline tests for harvest helpers (no network calls)."""

from __future__ import annotations

import socket

import harvest


def test_force_capes_ip_rewrites_dns_lookup() -> None:
    with harvest.force_capes_ip("127.0.0.1"):
        addresses = {
            info[4][0]
            for info in socket.getaddrinfo(harvest.CAPES_HOST, 443, type=socket.SOCK_STREAM)
        }
    assert addresses == {"127.0.0.1"}
    # The original resolver is restored afterwards (no-op context).
    with harvest.force_capes_ip(None):
        pass


def test_csv_resources_filters_other_formats() -> None:
    package = {
        "resources": [
            {"format": "CSV", "url": "https://example.org/a.csv"},
            {"format": "XLSX", "url": "https://example.org/b.xlsx"},
            {"format": None, "url": "https://example.org/c.CSV"},
            {"format": "PDF", "url": "https://example.org/d.pdf"},
        ]
    }
    resources = harvest.csv_resources(package)
    assert [resource["url"] for resource in resources] == [
        "https://example.org/a.csv",
        "https://example.org/c.CSV",
    ]
