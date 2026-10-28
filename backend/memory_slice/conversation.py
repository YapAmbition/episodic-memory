"""Conversation order preserves explicit reply causality when simulated clocks are tied."""
from collections import defaultdict
from datetime import datetime, timezone


def ordered_messages(messages):
    def instant(message):
        at = datetime.fromisoformat(message["at"].replace("Z", "+00:00"))
        return at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at.astimezone(timezone.utc)

    chronological = sorted(enumerate(messages), key=lambda item: (instant(item[1]), item[0]))
    by_id = {message["id"]: message for message in messages}
    replies, attached = defaultdict(list), set()
    for _, message in chronological:
        parent = by_id.get(message.get("in_reply_to"))
        if (message["role"] == "assistant" and parent and parent["role"] == "user"
                and message["conversation_id"] == parent["conversation_id"]):
            replies[parent["id"]].append(message)
            attached.add(message["id"])
    result = []
    for _, message in chronological:
        if message["id"] not in attached:
            result.append(message)
            result.extend(replies[message["id"]])
    return result
