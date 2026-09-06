"""Saved-monitor evaluation and optional generic webhook delivery."""
import json
import ipaddress
import socket
import time
import urllib.parse
import urllib.error
import urllib.request

from . import config, search, store


def matching_clusters(conn, alert, since_ts: int | None = None):
    parsed = search.parse(alert["q"] or "")
    if since_ts is None:
        since_ts = int(time.time()) - (parsed["hours"] or 24) * 3600
    wanted_lang = parsed["lang"] or alert["lang"]
    rows = store.feed(conn, limit=300, min_cred=alert["min_cred"],
                      topic=parsed["topic"] or alert["topic"], q=parsed["text"],
                      source=parsed["source"], asset=parsed["asset"], lang=wanted_lang,
                      since_ts=since_ts)
    return rows


def _validate_webhook_url(url: str):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username:
        raise ValueError("webhook must be an https URL without userinfo")
    for info in socket.getaddrinfo(parsed.hostname, parsed.port or 443,
                                   type=socket.SOCK_STREAM):
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise ValueError("webhook resolves to a non-public address")
    return parsed


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


def _post_webhook(payload: dict):
    _validate_webhook_url(config.ALERT_WEBHOOK)
    headers = {"Content-Type": "application/json", "User-Agent": "newsdesk/1.0"}
    if config.ALERT_WEBHOOK_TOKEN:
        headers["Authorization"] = "Bearer " + config.ALERT_WEBHOOK_TOKEN
    request = urllib.request.Request(config.ALERT_WEBHOOK,
                                     json.dumps(payload, ensure_ascii=False).encode(),
                                     headers, method="POST")
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(request, timeout=12) as response:
        response.read(64_000)


def evaluate(conn, notify=True) -> list[dict]:
    results, now = [], int(time.time())
    for row in store.alerts(conn):
        alert = dict(row)
        matches = matching_clusters(conn, alert)
        count, new_matches = len(matches), []
        for match in matches:
            cur = conn.execute(
                "INSERT OR IGNORE INTO alert_events"
                "(alert_id,cluster_id,headline,cred,event_ts,detected_ts) VALUES(?,?,?,?,?,?)",
                (alert["id"], match["id"], match["headline"], match["cred"],
                 match["last_ts"], now))
            if cur.rowcount:
                new_matches.append(match)
        new_count = len(new_matches)
        delivered, error = False, None
        if notify and new_count and config.ALERT_WEBHOOK:
            payload = {"type": "newsdesk.alert", "alert": alert["name"],
                       "new_count": new_count, "match_count_24h": count,
                       "events": [{"id": r["id"], "headline": r["headline"],
                                   "cred": r["cred"], "url": r["url"]}
                                  for r in new_matches[:10]], "ts": now}
            store.enqueue_alert_delivery(conn, alert["id"], payload, now)
        conn.execute("UPDATE alerts SET last_count=?,last_notified_ts=CASE WHEN ? "
                     "THEN ? ELSE last_notified_ts END WHERE id=?",
                     (count, int(delivered), now, alert["id"]))
        results.append({"id": alert["id"], "count": count, "new_count": new_count,
                        "delivered": delivered, "error": error})
    conn.commit()
    if notify and config.ALERT_WEBHOOK:
        outcomes = deliver_pending(conn, now=now)
        by_alert = {x["alert_id"]: x for x in outcomes}
        for result in results:
            if result["id"] in by_alert:
                outcome = by_alert[result["id"]]
                result.update(delivered=outcome["delivered"], error=outcome["error"])
    return results


def deliver_pending(conn, *, now: int | None = None) -> list[dict]:
    now = now or int(time.time())
    outcomes = []
    for row in store.due_alert_deliveries(conn, now):
        delivered, error = False, None
        try:
            _post_webhook(json.loads(row["payload"]))
            store.finish_alert_delivery(conn, row["id"], now)
            conn.execute("UPDATE alerts SET last_notified_ts=? WHERE id=?",
                         (now, row["alert_id"]))
            conn.commit()
            delivered = True
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:120]}"
            store.fail_alert_delivery(conn, row["id"], int(row["attempts"]) + 1,
                                      error, now)
        outcomes.append({"outbox_id": row["id"], "alert_id": row["alert_id"],
                         "delivered": delivered, "error": error})
    return outcomes
