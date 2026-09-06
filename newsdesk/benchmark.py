"""Repeatable local query performance benchmark; no network calls."""
import statistics
import time

from . import store


def _percentile(values, q):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * q))]


def run(conn, iterations=30) -> dict:
    cases = {
        "ranked_feed": lambda: store.feed(conn, limit=80, since_ts=0),
        "latin_fulltext": lambda: store.feed(conn, q="market", limit=80, since_ts=0),
        "cjk_text": lambda: store.feed(conn, q="市场", limit=80, since_ts=0),
        "entity_filter": lambda: store.feed(conn, asset="MSFT", limit=80, since_ts=0),
    }
    results = {}
    for name, operation in cases.items():
        operation()  # warm SQLite page cache and prepared structures
        samples = []
        for _ in range(max(1, iterations)):
            started = time.perf_counter()
            rows = operation()
            samples.append((time.perf_counter() - started) * 1000)
        results[name] = {
            "p50_ms": round(statistics.median(samples), 3),
            "p95_ms": round(_percentile(samples, .95), 3),
            "max_ms": round(max(samples), 3), "rows": len(rows),
        }
    worst = max((x["p95_ms"] for x in results.values()), default=0)
    return {"iterations": max(1, iterations), "items": conn.execute(
        "SELECT COUNT(*) FROM items").fetchone()[0], "cases": results,
        "worst_p95_ms": worst, "target_p95_ms": 250, "pass": worst <= 250}
