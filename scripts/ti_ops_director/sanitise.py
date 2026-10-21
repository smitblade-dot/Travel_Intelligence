"""External content is untrusted data. It is cleaned, bounded and never interpreted as instructions."""
from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SECRET_PARAMS = {"key", "api_key", "apikey", "token", "map_key", "appname", "access_token", "secret"}
_SECRET_TEXT = re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*\S+")


def clean_text(value, limit=2000):
    text = _CTRL.sub("", str(value or ""))
    return text[:limit]


def redact_url(url):
    """Remove credential-bearing query values and path segments after an embedded key."""
    parts = urlsplit(str(url))
    q = [(k, "REDACTED" if k.lower() in _SECRET_PARAMS else v) for k, v in parse_qsl(parts.query)]
    path = parts.path
    if "/api/area/csv/" in path:      # NASA FIRMS embeds the key as a path segment
        head, _, rest = path.partition("/api/area/csv/")
        segs = rest.split("/")
        path = head + "/api/area/csv/" + "/".join(["REDACTED"] + segs[1:])
    return urlunsplit((parts.scheme, parts.netloc, path, urlencode(q), ""))


def redact_text(text):
    return _SECRET_TEXT.sub(lambda m: m.group(1) + "=REDACTED", str(text))
