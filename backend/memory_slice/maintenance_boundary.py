"""Conversation-local progress; unchanged waiting material never invokes a model."""
import hashlib
import json
from .errors import MemoryError


def fingerprint(messages, config):
    material = [(m["id"], m["content"], m.get("maintenance_offset", 0)) for m in messages]
    return hashlib.sha256(json.dumps([material, config], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_boundary(plan, messages, covered, force=False):
    ids = [m["id"] for m in messages]
    # Old adapters may omit the cursor; infer it only from a contiguous covered prefix.
    if "processed_through_message_id" in plan.model_fields_set:
        cursor = plan.processed_through_message_id
        if cursor is not None and cursor not in ids:
            raise MemoryError(502, "整理边界引用未知消息，未推进进度")
        count = ids.index(cursor) + 1 if cursor else 0
    else:
        count = 0
        while count < len(ids) and ids[count] in covered:
            count += 1
    if covered != set(ids[:count]):
        raise MemoryError(502, "整理边界必须连续，边界前每条消息须记录或明确跳过，未写入")
    if force and count != len(ids):
        raise MemoryError(502, "达到待整理上限后模型仍未完成指定前缀，已暂停，原文保留")
    return ids[count:], ids[count - 1] if count else None
