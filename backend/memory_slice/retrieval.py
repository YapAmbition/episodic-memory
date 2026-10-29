"""Multi-route candidate retrieval. The index is disposable; events remain canonical."""
import hashlib
import time
from collections import defaultdict

from .engine import local_cues, memory_strength
from .store import dt
from .focus import person_score


def shortlist(store, sid, catalog, query, person_id, at, config, expanded=None, diagnostics=None, explore=True):
    eligible = {e["id"]: e for e in catalog if e["status"] != "archived" and dt(e["learned_at"]) <= at}
    lanes = []
    switches = config.get("retrieval_routes", {})
    timings = {}
    started = time.perf_counter()
    if switches.get("lexical", True):
        lanes.append(("full_text", store.search_events(sid, query[-6000:], 30)))
        lanes.append(("anchors", store.search_events(sid, query[-6000:], 20, "anchors")))
    timings["lexical"] = time.perf_counter() - started
    expanded = expanded or {}
    for field in ("structure", "scene", "details", "time"):
        text = " ".join(expanded.get(field, []))
        if text:
            lanes.append(("expanded_" + field, store.search_events(sid, text, 20, "structure" if field == "structure" else None)))
    started = time.perf_counter()
    cue_scores, remembered, dates = [], [], []
    for eid, event in eligible.items():
        cue, _ = local_cues(dict(event, body=""), query, at, config)
        if cue and switches.get("lexical", True):
            cue_scores.append((cue, eid))
        if person_score(event, config, person_id) and switches.get("person_salience", True):
            remembered.append((person_score(event, config, person_id) * memory_strength(event, at, config)["strength"], eid))
        if event.get("occurred_at") and event["status"] == "planned":
            gap = abs((dt(event["occurred_at"]) - at).total_seconds()) / 86400
            if gap <= 1:
                dates.append((1 - gap, eid))
        if switches.get("temporal", True):
            temporal_event = dict(event, title="", body="", anchors={"time": event.get("anchors", {}).get("time", [])})
            date_score, matched = local_cues(temporal_event, "", at, config)
            if matched:
                dates.append((date_score, eid))
    lanes.extend([
        ("context_anchors", [eid for _, eid in sorted(cue_scores, reverse=True)[:20]]),
        ("person_salience", [eid for _, eid in sorted(remembered, reverse=True)[:5]]),
        ("temporal", list(dict.fromkeys(eid for _, eid in sorted(dates, reverse=True)))[:10] if switches.get("temporal", True) else []),
    ])
    if not config.get("focus_person_id"):
        lanes = [(name, ids) for name, ids in lanes if name != "person_salience"]
    timings["person_salience"] = timings["temporal"] = time.perf_counter() - started
    # A small rotating lane gives unrelated older events a chance to reach the LLM.
    if explore:
        exploration = sorted(eligible, key=lambda eid: hashlib.sha256((query + eid).encode()).digest())[:4]
        lanes.append(("exploration", exploration))
    scores, routes = defaultdict(float), defaultdict(list)
    for name, ids in lanes:
        for rank, eid in enumerate(ids):
            if eid in eligible:
                scores[eid] += (0.3 if name == "exploration" else 1) / (20 + rank)
                routes[eid].append(name)
    limit = config.get("retrieval_limit", 40)
    # Reserve slots per route; a keyword-heavy lane cannot eliminate all structure hits.
    selected = []
    for _, ids in lanes:
        for eid in [value for value in ids if value in eligible][:2]:
            if eid not in selected and len(selected) < limit:
                selected.append(eid)
    for eid in sorted(scores, key=lambda key: (-scores[key], key)):
        if eid not in selected and len(selected) < limit:
            selected.append(eid)
    if diagnostics is not None:
        diagnostics["lanes"] = {name: [eid for eid in ids if eid in eligible] for name, ids in lanes}
        diagnostics["fusion_scores"] = {eid: round(scores[eid], 8) for eid in selected}
        diagnostics["timings"] = timings
    return selected, {eid: routes[eid] for eid in selected}


def graph_pool(catalog, seeds, config):
    known = {e["id"]: e for e in catalog if e["status"] != "archived"}
    reverse = defaultdict(list)
    for e in known.values():
        for link in e["links"]:
            reverse[link["target_id"]].append(e["id"])
    seen, frontier = set(seeds), list(seeds)
    limit = config.get("graph_candidate_limit", 200)
    omitted = 0
    for _ in range(config["max_depth"]):
        following = []
        for eid in frontier:
            neighbors = [link["target_id"] for link in known.get(eid, {}).get("links", [])]
            if config["backlinks"]:
                neighbors += reverse[eid]
            for target in neighbors:
                if target not in known or target in seen:
                    continue
                if len(seen) >= limit:
                    omitted += 1
                    continue
                seen.add(target)
                following.append(target)
        frontier = following
    return list(seen), omitted
