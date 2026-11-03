"""Preserve reinforcement provenance across event merges and migration paths."""
from copy import deepcopy


def portable_event(event, message_ids):
    """Return a detached event; absent chat evidence remains explicit provenance."""
    result=deepcopy(event)
    for exposure in result.get('reinforcements', []):
        mid=exposure.get('message_id')
        if mid and mid not in message_ids:
            exposure['unavailable_message_id']=mid
            exposure['message_id']=None
    return result


def merge_reinforcements(exposures):
    result={}
    for index, exposure in enumerate(exposures):
        identity=exposure.get('message_id') or exposure.get('unavailable_message_id')
        # Malformed legacy entries without identity must not all collapse under None.
        key=('message',identity) if identity else ('unknown',index)
        previous=result.get(key)
        if previous and previous.get('message_id') and not exposure.get('message_id'):
            continue
        result[key]=exposure
    return list(result.values())
