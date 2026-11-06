"""Inspectable engineering model; activation values are not calibrated probabilities."""
import heapq
import math
import re
import time
from collections import Counter
from datetime import timedelta
from zoneinfo import ZoneInfo

from .store import dt, iso, new_id


def memory_strength(event, at, config):
    if dt(event["learned_at"]) > at:
        return {"strength": 0.0, "age_days": 0.0, "half_life_days": 0.0, "future": True}
    salience = event["salience"]
    half_life = config["half_life_days"] * (1 + config["salience_multiplier"] * salience ** 2)
    age = max(0.0, (at - dt(event["learned_at"])).total_seconds() / 86400)
    strength = (0.18 + 0.82 * salience) * 2 ** (-age / half_life)
    for exposure in event.get("reinforcements", []):
        when = dt(exposure["at"])
        if when <= at:
            elapsed = max(0, (at - when).total_seconds() / 86400)
            contribution = config["reinforcement_boost"] * 2 ** (-elapsed / half_life)
            strength = 1 - (1 - strength) * (1 - contribution)
    return {"strength": round(min(1, strength), 6), "age_days": round(age, 2), "half_life_days": round(half_life, 2), "future": False}


def grams(text):
    text = re.sub(r"[\s\W_]+", "", text.lower())
    if not text:
        return Counter()
    return Counter(text[i:i + 2] for i in range(max(1, len(text) - 1)))


def cosine(left, right):
    a, b = grams(left), grams(right)
    if not a or not b:
        return 0.0
    return sum(n * b.get(k, 0) for k, n in a.items()) / math.sqrt(sum(n * n for n in a.values()) * sum(n * n for n in b.values()))


def local_cues(event, query, at, config):
    matched = []
    views = [event["title"]] + event["body"].splitlines()
    for kind, values in event.get("anchors", {}).items():
        for value in values:
            views.append(value)
            if len(value) >= 2 and value.lower() in query.lower():
                matched.append({"kind": kind, "text": value})
    score = max([cosine(query, view) * 0.72 for view in views] or [0])
    if matched:
        score = max(score, min(1, 0.65 + 0.1 * len(matched)))
    landmark = any(re.search(r"生日|纪念|周年|节日|第一次", anchor) for anchor in event.get("anchors", {}).get("time", []))
    if event.get("occurred_at") and landmark:
        zone = ZoneInfo(config["timezone"])
        original = dt(event["occurred_at"]).astimezone(zone)
        current = at.astimezone(zone)
        if (original.month, original.day) == (current.month, current.day) and original.year < current.year:
            score = max(score, 0.70)
            matched.append({"kind": "time", "text": "与事件发生日具有相同月日"})
    if event.get("status") == "planned" and event.get("occurred_at"):
        zone = ZoneInfo(config["timezone"])
        if dt(event["occurred_at"]).astimezone(zone).date() == at.astimezone(zone).date():
            score = max(score, .70)
            matched.append({"kind": "time", "text": "今天是计划日期，是否发生尚待确认"})
    return min(1, score), matched


def event_view(event, at, config):
    state = memory_strength(event, at, config)
    return dict(event, memory=state, memory_status="future" if state["future"] else "active" if state["strength"] >= .45 else "quiet" if state["strength"] >= .20 else "dormant")


def build_context(events, person_id, query, at, config, mode="associative", semantic_matches=None, entry_ids=None, rank_scores=None, search_only=False, current_context=None, result_limit=None):
    started = time.perf_counter()
    semantic_matches = semantic_matches or {}
    from .focus import person_score
    if not config.get("focus_person_id"):
        config = dict(config, person_weight=0)
    rank_scores = rank_scores or {}
    all_events = {e["id"]: e for e in events if e.get("status") != "archived" and dt(e["learned_at"]) <= at}
    states, candidates = {}, []
    for event_id, event in all_events.items():
        state = memory_strength(event, at, config)
        states[event_id] = state
        cue, matches = local_cues(event, query, at, config)
        semantic_match = semantic_matches.get(event_id)
        if semantic_match:
            cue = max(cue, semantic_match["score"])
            matches.append({"kind": "embedding", "text": semantic_match["reason"]})
        person = person_score(event, config, person_id)
        total_weight = config["cue_weight"] + config["retention_weight"] + config["person_weight"]
        score = (config["cue_weight"] * cue + config["retention_weight"] * state["strength"] + config["person_weight"] * person) / total_weight if total_weight else 0
        # A constant interlocutor alone must not activate the whole archive.
        eligible = cue >= .06 or (state["strength"] >= .68 and person > 0)
        if mode == "deliberate":
            eligible = cue >= .025
        threshold = config["activation_threshold"] * (.60 if mode == "deliberate" else 1)
        accepted = eligible and score >= threshold and (entry_ids is None or event_id in entry_ids)
        reasons = [m["text"] for m in matches]
        if person:
            reasons.append("与当前交流对象的共同经历" if person > .25 else "涉及关注对象")
        if not matches and state["strength"] >= .68:
            reasons.append("近期仍然鲜明的记忆")
        candidates.append({"event_id": event_id, "title": event["title"], "score": round(score, 6), "cue_score": round(cue, 6), "strength": state["strength"], "matches": matches, "reasons": reasons, "eligible": accepted})
    candidates.sort(key=lambda c: (c["score"], rank_scores.get(c["event_id"], 0), c["event_id"]), reverse=True)
    seeds = [c for c in candidates if c["eligible"]][:config["max_seeds"]]
    ranked_at = time.perf_counter()
    if search_only:
        return {"id": new_id("recall"), "at": iso(at), "person_id": person_id, "query": query, "mode": mode,
                "strategy": "local_anchors", "seeds": [s["event_id"] for s in seeds], "candidates": candidates,
                "selected": [], "omitted": [], "context": "", "context_chars": 0,
                "elapsed_ms": round((ranked_at - started) * 1000, 2),
                "timings_ms": {"fusion": round((ranked_at - started) * 1000, 3), "graph_expansion": 0, "context_build": 0}}
    heap = []
    for seed in seeds:
        heapq.heappush(heap, (-seed["score"], seed["event_id"], 0, [seed["event_id"]], "入口事件"))
    best = {}
    reverse_links = {}
    if config["backlinks"]:
        for neighbor in all_events.values():
            for link in neighbor.get("links", []):
                reverse_links.setdefault(link["target_id"], []).append((neighbor["id"], link.get("strength", .8), "反向关联 · " + link.get("relation", "关联")))
    while heap:
        negative_score, event_id, depth, path, relation = heapq.heappop(heap)
        score = -negative_score
        if event_id in best and best[event_id]["score"] >= score:
            continue
        event = all_events[event_id]
        best[event_id] = {"event_id": event_id, "score": round(score, 6), "depth": depth, "path": path, "relation": relation}
        if depth >= config["max_depth"]:
            continue
        links = [(link["target_id"], link.get("strength", .8), link.get("relation", "关联")) for link in event.get("links", [])]
        if config["backlinks"]:
            links.extend(reverse_links.get(event_id, []))
        for target, edge_strength, edge_relation in links:
            if target not in all_events or target in path:
                continue
            propagated = score * config["propagation_decay"] * edge_strength * (.30 + .70 * states[target]["strength"])
            if propagated >= config["activation_threshold"]:
                heapq.heappush(heap, (-propagated, target, depth + 1, path + [target], edge_relation))
    graph_at = time.perf_counter()
    if result_limit is not None:
        from .event_fragments import split_body, semantic_fragment_score
        ranked = sorted(best.values(), key=lambda a: (-a['score'], a['event_id']))
        items = []
        for rank, activation in enumerate(ranked[:result_limit], 1):
            event = all_events[activation['event_id']]
            parts = split_body(event['body'], config.get('fragment_target_chars', 1500), config.get('fragment_max_chars', 3000))
            corrections = set()
            for p in parts:
                if re.search(r'更正|纠正|修正|撤回|作废|不再|correction|supersed', p['text'], re.I):
                    corrections.update(i for i, q in enumerate(parts) if q['section_first'] == p['section_first'])
            for i, p in enumerate(parts):
                dependencies = {0, p['section_first']} | corrections
                if i > p['section_first']:
                    dependencies.add(i - 1)
                dependencies.discard(i)
                p['dependencies'] = [parts[j]['id'] for j in sorted(dependencies)]
                p['score'] = activation['score'] * (.25 + .75 * max(max([cosine(query, line) for line in p['text'].splitlines()] or [0]), semantic_fragment_score(p, semantic_matches.get(event['id']))))
            items.append(dict(activation, rank=rank, version=event.get('version', 1),
                              event=event_view(event, at, config), fragments=parts))
        return dict(id=new_id('recall'), at=iso(at), person_id=person_id, query=query, mode=mode,
                    strategy='hybrid_embedding' if semantic_matches else 'local_anchors',
                    seeds=[s['event_id'] for s in seeds], candidates=candidates,
                    items=items, limit=result_limit, returned_count=len(items),
                    truncated=len(ranked) > result_limit,
                    elapsed_ms=round((time.perf_counter()-started)*1000, 2),
                    timings_ms=dict(fusion=round((ranked_at-started)*1000, 3),
                                    graph_expansion=round((graph_at-ranked_at)*1000, 3), context_build=0))
    from .recall_fragments import assemble
    selected, omitted, context = assemble(best.values(), all_events, candidates, at, config, query,
                                         current_context, event_view, cosine, semantic_matches=semantic_matches)
    return {"id": new_id("recall"), "at": iso(at), "person_id": person_id, "query": query,
            "strategy": "hybrid_embedding" if semantic_matches else "local_anchors", "mode": mode,
            "seeds": [s["event_id"] for s in seeds], "candidates": candidates, "selected": selected,
            "omitted": omitted, "context": context, "context_chars": len(context),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "timings_ms": {"fusion": round((ranked_at - started) * 1000, 3),
                           "graph_expansion": round((graph_at - ranked_at) * 1000, 3),
                           "context_build": round((time.perf_counter() - graph_at) * 1000, 3)}}


def strength_curve(event, at, config):
    return [{"days": day, "strength": memory_strength(event, at + timedelta(days=day), config)["strength"]} for day in (0, 7, 14, 30, 60, 90, 180, 365)]


def retrieve_events(events, person_id, query, at, config, mode="associative", semantic_matches=None,
                    entry_ids=None, rank_scores=None, limit=None):
    """Pure ranked retrieval. No history parsing, injection budget, or XML rendering."""
    limit = config.get('recall_default_limit', 8) if limit is None else limit
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 120:
        raise ValueError('limit must be an integer between 1 and 120')
    return build_context(events, person_id, query, at, dict(config, max_seeds=len(events)),
                         mode, semantic_matches, entry_ids, rank_scores, result_limit=limit)
