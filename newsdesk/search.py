"""Small Bloomberg-style query language, compiled to existing safe filters."""
import re
import shlex


def parse(raw: str) -> dict:
    out = {"text": [], "source": None, "lang": None, "topic": None,
           "asset": None, "min_cred": None, "hours": None}
    try:
        parts = shlex.split(raw or "")
    except ValueError:
        parts = (raw or "").split()
    for part in parts:
        key, sep, value = part.partition(":")
        if not sep or key.lower() not in {"source", "lang", "topic", "asset", "cred", "after"}:
            out["text"].append(part)
            continue
        key, value = key.lower(), value.strip()
        if key == "source" and value:
            out["source"] = value
        elif key == "lang":
            if value.lower() in {"zh", "en", "pt", "all"}:
                out["lang"] = value.lower()
            else:
                out["text"].append(part)
        elif key == "topic" and re.fullmatch(r"[a-z_]{2,24}", value.lower()):
            out["topic"] = value.lower()
        elif key == "asset" and re.fullmatch(r"[A-Za-z0-9/^.]{2,20}", value):
            out["asset"] = value
        elif key == "cred":
            try:
                out["min_cred"] = max(0, min(100, float(value.lstrip(">="))))
            except ValueError:
                out["text"].append(part)
        elif key == "after":
            match = re.fullmatch(r"(\d+)(h|d)", value.lower())
            if match:
                out["hours"] = int(match.group(1)) * (24 if match.group(2) == "d" else 1)
            else:
                out["text"].append(part)
    out["text"] = " ".join(out["text"]).strip()
    return out
