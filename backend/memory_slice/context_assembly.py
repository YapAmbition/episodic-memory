"""Host-side injection policy, shared by adapters; never performs retrieval."""
from .recall_fragments import assemble
from .store import dt


def assemble_context(result, config, current_context=None):
    items = result["items"]
    selected, omitted, context = assemble(
        items, {i["event_id"]: i["event"] for i in items},
        result.get("candidates", []), dt(result["at"]), config,
        result.get("retrieval_query", result["query"]), current_context,
        lambda event, at, config: dict(event), lambda a, b: 0,
        prepared={i["event_id"]: i["fragments"] for i in items},
    )
    return dict(result, selected=selected, omitted=omitted,
                context=context, context_chars=len(context))
