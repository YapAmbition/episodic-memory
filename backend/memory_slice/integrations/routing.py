"""Persisted host routing and legacy person aliases; no transfer API."""
import hashlib
import json
from ..errors import MemoryError


def alias_id(sid, platform, uid):
    return 'identity_' + hashlib.sha256(json.dumps([sid, platform, uid]).encode()).hexdigest()


class ChatRouting:
    def __init__(self, core):
        self.core, self.store = core, core.store

    def rows(self, con=None):
        return self.store.list('astrbot_scope', con=con)

    def resolve(self, key, con=None):
        row = self.store.get('astrbot_scope', key, con=con)
        return None if row.get('disabled') else row

    def person(self, sid, platform, uid, con=None):
        try:
            alias = self.store.get('astrbot_person_alias', alias_id(sid, platform, uid), sid, con)
        except MemoryError as exc:
            if exc.status_code != 404:
                raise
            return None
        return self.store.get('person', alias['person_id'], sid, con)['id']

