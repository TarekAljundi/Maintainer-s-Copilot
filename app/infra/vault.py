"""VaultClient — boot-cache secrets, fail-closed on runtime outage.

Interface:
    load(path) -> dict       # read + cache, raises InfraError if path missing
    cached(path) -> dict     # cache-only, raises if not pre-loaded
    health() -> bool         # used by boot check + runtime monitor
"""
