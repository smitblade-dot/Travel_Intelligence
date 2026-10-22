"""Evidence collection for the country builder: permitted sources only, no model, no guessing.

Every page is fetched read-only over HTTPS from a host named in config/country_builder.json, after a
robots.txt check, with bounded size, time and retries. Text is reduced to numbered passages (verbatim source
text, bounded length) so a draft can later be checked claim by claim against exactly what was retrieved.
Publication dates are recorded only when the source states them.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

from .feed_health import HttpResult
from .sanitise import clean_text
from .store import fingerprint

USER_AGENT = "TravelIntelligenceCountryBuilder/1.0 (+https://travelintelligence22.com; read-only; honours robots.txt)"
ROBOTS_TOKEN = "TravelIntelligenceCountryBuilder"
STATUSES = ("RETRIEVED", "STALE", "UNAVAILABLE", "BLOCKED_BY_ROBOTS", "PARSE_FAILED")


def http_get(url, *, timeout=20, attempts=2, max_bytes=1_500_000):
    """Bounded read-only GET with the builder's own identifying User-Agent. Transient failures retry once."""
    last = None
    for i in range(max(1, attempts)):
        t0 = time.monotonic()
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/json;q=0.9,*/*;q=0.5",
                                        "Accept-Language": "en"})
            with urlopen(req, timeout=timeout) as r:
                return HttpResult(r.status, r.read(max_bytes), None, time.monotonic() - t0, i + 1)
        except HTTPError as exc:
            last = HttpResult(exc.code, b"", "http_error", time.monotonic() - t0, i + 1)
            if exc.code not in (429, 500, 502, 503, 504):
                return last
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            last = HttpResult(None, b"", "network_error:" + type(reason).__name__, time.monotonic() - t0, i + 1)
        if i < attempts - 1:
            time.sleep(2)
    return last


# ---- HTML reduction ------------------------------------------------------------------------------------------
class _Extract(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "form", "nav", "header", "footer", "button", "select", "iframe",
            "template", "option"}
    BLOCK = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "dd", "dt", "blockquote", "caption", "figcaption"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.title = {}, ""
        self.blocks_all, self.blocks_main = [], []
        self._skip, self._main, self._tag, self._buf = 0, 0, None, []
        self._in_title, self._row, self._cell = False, None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            key = a.get("name") or a.get("property")
            if key and a.get("content") is not None:
                self.meta[key] = a["content"]
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self._skip += 1
            return
        if tag == "main":
            self._main += 1
        if self._skip:
            return
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag in self.BLOCK:
            self._flush()
            self._tag = tag
        elif tag == "br":
            self._buf.append(" ")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "main":
            self._main = max(0, self._main - 1)
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            cells = [c for c in self._row if c]
            if cells:
                self._emit("tr", " | ".join(cells))
            self._row = None
        elif tag in self.BLOCK:
            self._flush()

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        if self._cell is not None:
            self._cell.append(data)
        else:
            self._buf.append(data)

    def _emit(self, tag, text):
        text = " ".join(text.split())
        if text:
            (self.blocks_main if self._main else self.blocks_all).append((tag, text))
            if self._main:
                self.blocks_all.append((tag, text))

    def _flush(self):
        text = "".join(self._buf)
        self._buf = []
        if self._tag:
            self._emit(self._tag, text)
        elif text.strip():
            self._emit("p", text)
        self._tag = None


def html_blocks(html):
    p = _Extract()
    p.feed(html)
    p._flush()
    blocks = p.blocks_main or p.blocks_all
    return blocks, p.meta, " ".join(p.title.split())


def _chunks(text, limit):
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        while len(sentence) > limit:
            cut = sentence.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            out.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if cur and len(cur) + 1 + len(sentence) > limit:
            out.append(cur)
            cur = sentence
        else:
            cur = (cur + " " + sentence).strip()
    if cur:
        out.append(cur)
    return [c for c in out if c]


def passages_from_blocks(blocks, code, limit=600, max_passages=120):
    """Verbatim passages with their heading path. Consecutive small blocks merge up to ``limit`` characters;
    duplicates (pages often repeat a table as prose) are dropped."""
    path, passages, seen = {}, [], set()
    pending, pending_loc = "", ""

    def push(text, loc):
        key = re.sub(r"\W+", " ", text.lower()).strip()
        if len(key) < 25 or key in seen:
            return
        seen.add(key)
        passages.append({"id": f"{code}.{len(passages) + 1:02d}", "locator": loc, "text": text})

    def flush():
        nonlocal pending
        if pending:
            push(pending, pending_loc)
        pending = ""

    for tag, text in blocks:
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            flush()
            level = int(tag[1])
            path = {k: v for k, v in path.items() if k < level}
            path[level] = text
            continue
        loc = " > ".join(path[k] for k in sorted(path))
        piece = ("- " if tag == "li" else "") + text
        for chunk in _chunks(piece, limit):
            if pending and (loc != pending_loc or len(pending) + 1 + len(chunk) > limit):
                flush()
            pending_loc = loc
            pending = (pending + " " + chunk).strip()
    flush()
    return passages[:max_passages]


MONTH = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August",
                                     "September", "October", "November", "December"], 1)}


def stated_date(parser, meta, text):
    """(ISO date or None, how). Only a date the source itself states is recorded."""
    if parser == "govuk":
        for key in ("govuk:public-updated-at", "govuk:updated-at"):
            m = re.match(r"(\d{4}-\d{2}-\d{2})", meta.get(key, ""))
            if m:
                return m.group(1), key
    if parser == "cdc":
        m = re.search(r"Page last reviewed:\s*([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", text)
        if m and m.group(1) in MONTH:
            return f"{int(m.group(3)):04d}-{MONTH[m.group(1)]:02d}-{int(m.group(2)):02d}", "Page last reviewed"
    return None, None


# ---- NASA POWER ----------------------------------------------------------------------------------------------
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def parse_nasa(body, expect_lat, expect_lon):
    doc = json.loads(body.decode("utf-8"))
    par = doc["properties"]["parameter"]
    t2m, pre = par["T2M"], par["PRECTOTCORR"]
    fill = doc.get("header", {}).get("fill_value", -999.0)
    for series in (t2m, pre):
        for m in MONTHS:
            if not isinstance(series.get(m), (int, float)) or series[m] == fill:
                raise ValueError("missing or fill value in " + m)
    lon, lat = doc["geometry"]["coordinates"][:2]
    if abs(lat - expect_lat) > 0.01 or abs(lon - expect_lon) > 0.01:
        raise ValueError("coordinates differ from the request")
    period = doc.get("header", {}).get("range", "")
    structured = {"temperature_c": {m: t2m[m] for m in MONTHS}, "precipitation": {m: pre[m] for m in MONTHS}, "precip_unit": "mm/day",
                  "period": period, "model": ",".join(doc.get("header", {}).get("sources", [])),
                  "lat": lat, "lon": lon, "area": f"grid point {lat}, {lon}", "api_version": doc.get("header", {}).get("api", {}).get("version")}
    return structured, climate_passages(structured, "NP")


def climate_passages(st, code):
    T, P, unit = st["temperature_c"], st["precipitation"], st["precip_unit"]
    out = []
    for i in range(0, 12, 3):
        ms = MONTHS[i:i + 3]
        text = "; ".join(f"{m}: mean 2 m temperature {T[m]} C, precipitation {P[m]} {unit}" for m in ms)
        out.append({"id": f"{code}.{len(out) + 1:02d}", "locator": "Monthly climatology", "text": text})
    return out


def parse_cckp(body, iso3):
    """World Bank Climate Change Knowledge Portal: CRU TS 4.07 monthly climatology 1991-2020, national mean."""
    doc = json.loads(body.decode("utf-8"))
    if doc.get("metadata", {}).get("status") != "success":
        raise ValueError("API did not report success")
    tas, pr = doc["data"]["tas"][iso3], doc["data"]["pr"][iso3]
    T, P = {}, {}
    for key, name in ((tas, T), (pr, P)):
        for label, v in key.items():
            m = MONTHS[int(label[-2:]) - 1]
            if not isinstance(v, (int, float)):
                raise ValueError("non-numeric value")
            name[m] = v
    if set(T) != set(MONTHS) or set(P) != set(MONTHS):
        raise ValueError("not all twelve months present")
    structured = {"temperature_c": T, "precipitation": P, "precip_unit": "mm/month", "lat": None, "lon": None,
                  "period": "monthly climatology 1991-2020 (CRU TS 4.07), national mean", "model": "CRU TS 4.07",
                  "area": f"{iso3} national mean"}
    return structured, climate_passages(structured, "WB")


# ---- robots.txt ----------------------------------------------------------------------------------------------
class Robots:
    """robots.txt per host. 404/410 = no restrictions. Any other failure = not permitted (conservative)."""
    def __init__(self, http, timeout=15):
        self.http, self.timeout, self.cache = http, timeout, {}

    def allowed(self, url):
        parts = urlsplit(url)
        host = parts.netloc
        if host not in self.cache:
            res = self.http(f"{parts.scheme}://{host}/robots.txt", timeout=self.timeout, attempts=1, max_bytes=300_000)
            if res is not None and res.status == 200:
                rp = RobotFileParser()
                rp.parse(res.body.decode("utf-8", "replace").splitlines())
                self.cache[host] = rp
            elif res is not None and res.status in (404, 410):
                self.cache[host] = True
            else:
                self.cache[host] = f"robots.txt could not be read (HTTP {getattr(res, 'status', None) or getattr(res, 'error_kind', 'no response')}); treated as not permitted"
        entry = self.cache[host]
        if entry is True:
            return True, "no robots.txt"
        if isinstance(entry, str):
            return False, entry
        target = parts.path + ("?" + parts.query if parts.query else "")
        ok = entry.can_fetch(ROBOTS_TOKEN, target)
        return ok, ("robots.txt allows" if ok else "robots.txt disallows this path for generic agents")


# ---- collection ----------------------------------------------------------------------------------------------
def resolve_url(src, params, cc):
    fields = {"cc": cc, "iso3": params.get("iso3", ""), "fcdo": params.get("fcdo", ""), "travelhealthpro": params.get("travelhealthpro", ""),
              "cdc": params.get("cdc", ""), "lat": params.get("location", {}).get("lat", ""),
              "lon": params.get("location", {}).get("lon", "")}
    return src["url"].format(**fields)


def source_id_for(src, source_key, cc, data_sources):
    """Reuse the TI source already in data.json when there is one; otherwise propose a new id."""
    existing = (src.get("existing_source_id") or "").format(cc=cc)
    if existing and any(s.get("id") == existing for s in data_sources):
        return existing, True
    return f"cb-{cc}-{source_key.replace('_', '-')}", False


def collect_source(source_key, src, params, cc, http, now, limits, robots, data_sources):
    """One evidence document. Never raises: every failure is recorded as a status, not hidden."""
    url = resolve_url(src, params, cc)
    sid, reused = source_id_for(src, source_key, cc, data_sources)
    doc = {"sourceKey": source_key, "sourceId": sid, "sourceIdIsExisting": reused, "code": src["code"],
           "publisher": src["publisher"], "jurisdiction": src["jurisdiction"], "tier": src["tier"], "title": src["title"],
           "url": url, "retrievedAt": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
           "originalLanguage": src["original_language"], "contentRights": src["content_rights"],
           "accessMethod": src["access_method"], "automationPermission": src["automation_permission"],
           "independenceGroupId": src["independence_group"], "currentCapable": bool(src.get("current_capable")),
           "staleAfterDays": src["stale_after_days"], "publicationDate": None, "publicationDateStatus": "NOT_STATED",
           "status": "UNAVAILABLE", "error": None, "httpStatus": None, "passages": [], "contentSha256": None}
    allowed, why = robots.allowed(url)
    exc = src.get("robots_exception") or {}
    if not allowed and exc.get("approved_by") == "Marc" and exc.get("approved_on"):
        doc["robotsException"] = {"approvedBy": exc["approved_by"], "approvedOn": exc["approved_on"], "scope": exc.get("scope")}
        allowed = True
    if not allowed:
        doc.update(status="BLOCKED_BY_ROBOTS", error=why)
        return doc
    res = http(url, timeout=limits["http_timeout_seconds"], attempts=limits["http_attempts"],
               max_bytes=limits["max_bytes_per_page"])
    doc["httpStatus"] = getattr(res, "status", None)
    if res is None or res.status != 200 or not res.body:
        doc["error"] = (getattr(res, "error_kind", None) or "empty") + (f":{res.status}" if res and res.status else "")
        return doc
    try:
        if src["parser"] in ("nasa_power", "worldbank_cckp"):
            if src["parser"] == "nasa_power":
                loc = params["location"]
                structured, passages = parse_nasa(res.body, loc["lat"], loc["lon"])
            else:
                structured, passages = parse_cckp(res.body, params["iso3"])
            doc["structured"] = structured
            doc["dataPeriod"] = structured["period"]
            text = json.dumps(structured, sort_keys=True)
        else:
            blocks, meta, title = html_blocks(res.body.decode("utf-8", "replace"))
            text = "\n".join(t for _, t in blocks)
            passages = passages_from_blocks(blocks, src["code"], limits["max_passage_chars"])
            pub, how = stated_date(src["parser"], meta, text)
            if pub:
                doc.update(publicationDate=pub, publicationDateStatus="STATED", publicationDateSource=how)
            doc["pageTitle"] = clean_text(title, 160)
            if meta.get("govuk:updated-at"):
                doc["pageUpdatedAt"] = meta["govuk:updated-at"][:25]
        if not passages:
            raise ValueError("no passages extracted")
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        doc.update(status="PARSE_FAILED", error=type(exc).__name__ + ":" + clean_text(exc, 80))
        return doc
    doc["passages"] = passages
    doc["contentSha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    doc["bytes"] = len(res.body)
    doc["status"] = "RETRIEVED"
    if doc["publicationDate"]:
        age = (now.date() - datetime.fromisoformat(doc["publicationDate"]).date()).days
        doc["publicationAgeDays"] = age
        if age > src["stale_after_days"]:
            doc["status"] = "STALE"
            doc["error"] = f"published {age} days ago; limit {src['stale_after_days']}"
    return doc


def collect_category(cfg, cc, category, http, now, data_sources, params=None):
    params = params or cfg.raw["country_params"][cc]
    robots = Robots(http, cfg.limits_cfg["http_timeout_seconds"])
    return [collect_source(k, cfg.sources[k], params, cc, http, now, cfg.limits_cfg, robots, data_sources)
            for k in cfg.category_defs[category]["sources"]]


# ---- passage selection ---------------------------------------------------------------------------------------
def select_passages(evidence, topic_defs, limits):
    """Ordinary-code relevance filter: which retrieved passages each model topic may use. Stale, blocked and
    unavailable sources contribute nothing."""
    usable = {e["sourceKey"]: e for e in evidence if e["status"] == "RETRIEVED"}
    chosen, per_topic = {}, {}
    for tname, t in topic_defs.items():
        if t["kind"] != "model":
            continue
        rx = [re.compile(r"\b" + re.escape(k), re.I) for k in t["keywords"]]
        by_source = {}
        for sk in t["sources"]:
            scored = []
            for p in (usable.get(sk) or {}).get("passages", []):
                score = sum(1 for r in rx if r.search(p["text"]))
                if score:
                    scored.append((-score, p["id"], p))
            by_source[sk] = [(sc, p) for sc, _, p in sorted(scored, key=lambda x: (x[0], x[1]))]
        cap = limits["max_passages_per_topic"]
        picked = [lst[0][1] for lst in by_source.values() if lst][:cap]     # the best passage of every source first
        rest = [p for _, p in sorted((sp for lst in by_source.values() for sp in lst[1:]), key=lambda sp: (sp[0], sp[1]["id"]))]
        for p in rest:
            if len(picked) >= cap:
                break
            picked.append(p)
        per_topic[tname] = [p["id"] for p in picked]
        for p in picked:
            chosen[p["id"]] = p
    ordered = sorted(chosen.values(), key=lambda p: p["id"])[:limits["max_passages_per_task"]]
    keep = {p["id"] for p in ordered}
    return ordered, {t: [i for i in ids if i in keep] for t, ids in per_topic.items()}


def evidence_fingerprint(passages, evidence, topics, reference):
    """What the draft depends on: the exact passage texts the model sees, the deterministic inputs and the
    source statuses. Page chrome that changes on every fetch (live weather widgets, tokens) does not count."""
    return fingerprint({
        "passages": [[p["id"], p["text"]] for p in passages],
        "structured": {e["sourceKey"]: e.get("structured") for e in evidence if e.get("structured")},
        "status": {e["sourceKey"]: e["status"] for e in evidence},
        "topics": sorted(topics), "reference": reference})
