"""Lossless event mutations, applied inside the caller's SQLite transaction."""
import re

from .errors import MemoryError as HTTPException

from .store import iso, new_id


def unique(items):
    return list(dict.fromkeys(items))


def append_update(previous, raw, change_type, recorded_at):
    """Model edits cannot replace the canonical narrative or discard old sources."""
    delta = raw["body"]
    if delta.strip() != previous["body"].strip():
        label = "更正记录（后述更正优先于前述旧说法）" if change_type == "correction" else "后续进展"
        raw["body"] = previous["body"] + f"\n\n### {label} · {recorded_at}\n" + delta
    else:
        raw["body"] = previous["body"]
    raw["source_ids"] = unique(previous.get("source_ids", []) + raw["source_ids"])
    raw["person_ids"] = unique(previous.get("person_ids", []) + raw["person_ids"])
    raw["anchors"] = {kind: unique(previous.get("anchors", {}).get(kind, []) + values) for kind, values in raw["anchors"].items()}
    links = {link["target_id"]: link for link in previous.get("links", [])}
    links.update({link["target_id"]: link for link in raw["links"]})
    raw["links"] = list(links.values())
    raw["occurred_at"] = raw["occurred_at"] or previous.get("occurred_at")
    raw["salience"] = max(previous["salience"], raw["salience"])
    raw["salience_reason"] = raw.get("salience_reason") or previous.get("salience_reason", "")
    return raw


def save_fragment(store, sid, event, content, kind, source_ids, con, source_ranges=None):
    return store.put("fragment", {
        "id": new_id("fragment"), "space_id": sid, "event_id": event["id"],
        "kind": kind, "content": content, "source_ids": unique(source_ids),
        "recorded_at": iso(), "source_ranges": source_ranges or [],
    }, sid, con=con)


def ensure_baseline(store, sid, event, con):
    found = con.execute("SELECT 1 FROM records WHERE kind='fragment' AND space_id=? AND json_extract(data,'$.event_id')=? LIMIT 1", (sid, event["id"])).fetchone()
    if not found:
        save_fragment(store, sid, event, event["body"], "baseline", event.get("source_ids", []), con)


def audit(store, sid, kind, ids, reason, con):
    return store.put("operation", {"id": new_id("operation"), "space_id": sid,
                     "kind": kind, "event_ids": ids, "reason": reason, "at": iso()}, sid, con=con)


def merge_events(store, sid, body, con):
    ids = body.event_ids
    if len(set(ids)) != len(ids) or body.target_id not in ids:
        raise HTTPException(422, "合并需提供不同事件，且 target_id 必须位于 event_ids 中")
    events = [store.get("event", eid, sid, con) for eid in ids]
    if any(event["status"] == "archived" for event in events):
        raise HTTPException(409, "已归档事件不能再次合并")
    target = next(e for e in events if e["id"] == body.target_id)
    ensure_baseline(store, sid, target, con)
    for source in events:
        if source["id"] == target["id"]:
            continue
        ensure_baseline(store, sid, source, con)
        section = f"\n\n### 合并来源：{source['title']} · {source['id']}\n" + source["body"]
        target["body"] += section
        target["source_ids"] = unique(target["source_ids"] + source["source_ids"])
        target["person_ids"] = unique(target["person_ids"] + source["person_ids"])
        for kind, values in source["anchors"].items():
            target["anchors"][kind] = unique(target["anchors"].get(kind, []) + values)
        target["links"] += source["links"]
        target["salience"] = max(target["salience"], source["salience"])
        target["learned_at"] = min(target["learned_at"], source["learned_at"])
        exposures = {e["message_id"]: e for e in target.get("reinforcements", []) + source.get("reinforcements", [])}
        target["reinforcements"] = list(exposures.values())
        save_fragment(store, sid, target, source["body"], "merge", source["source_ids"], con)
        source.update(status="archived", merged_into=target["id"], updated_at=iso())
        store.put("event", source, sid, con=con)
    redirects = set(ids) - {target["id"]}
    target["links"] = list({link["target_id"]: link for link in target["links"] if link["target_id"] not in ids}.values())
    for source_id in redirects:
        target["body"] = target["body"].replace("[[" + source_id + "]]", "[已合并来源 " + source_id + "]")
    target["updated_at"] = iso()
    target = store.put("event", target, sid, con=con)
    for event in store.list("event", sid, con):
        if event["id"] in ids:
            continue
        changed = False
        for link in event["links"]:
            if link["target_id"] in redirects:
                link["target_id"] = target["id"]
                changed = True
        for source_id in redirects:
            marker = "[[" + source_id + "]]"
            if marker in event["body"]:
                event["body"] = event["body"].replace(marker, "[[" + target["id"] + "]]")
                changed = True
        if changed:
            event["links"] = list({link["target_id"]: link for link in event["links"]}.values())
            event["updated_at"] = iso()
            store.put("event", event, sid, con=con)
    operation = audit(store, sid, "merge", ids, body.reason, con)
    return {"event": target, "archived_ids": sorted(redirects), "operation": operation}


def split_event(store, sid, eid, body, con):
    original = store.get("event", eid, sid, con)
    if original["status"] == "archived":
        raise HTTPException(409, "已归档事件不能再次拆分")
    normalize = lambda text: re.sub(r"\s+", "", text)
    if normalize("".join(part.body for part in body.parts)) != normalize(original["body"]):
        raise HTTPException(422, "拆分必须按顺序完整分配原正文，不能遗漏、重复或改写内容")
    ensure_baseline(store, sid, original, con)
    children = []
    for part in body.parts:
        child = dict(original, id=new_id("event"), title=part.title, body=part.body,
                     split_from=eid, updated_at=iso())
        child.pop("version", None)
        child = store.put("event", child, sid, con=con)
        save_fragment(store, sid, child, part.body, "split", child["source_ids"], con)
        children.append(child)
    original.update(status="archived", split_into=[e["id"] for e in children], updated_at=iso())
    store.put("event", original, sid, con=con)
    for event in store.list("event", sid, con):
        if event["id"] == eid:
            continue
        if any(link["target_id"] == eid for link in event["links"]):
            previous = next(link for link in event["links"] if link["target_id"] == eid)
            event["links"] = [link for link in event["links"] if link["target_id"] != eid]
            event["links"] += [dict(previous, target_id=child["id"], relation="拆分来源 · " + previous["relation"]) for child in children]
            event["body"] = event["body"].replace("[[" + eid + "]]", " ".join("[[" + child["id"] + "]]" for child in children))
            store.put("event", event, sid, con=con)
    operation = audit(store, sid, "split", [eid] + [e["id"] for e in children], body.reason, con)
    return {"events": children, "archived_id": eid, "operation": operation}
