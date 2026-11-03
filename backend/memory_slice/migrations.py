"""Forward-only schema migrations. Existing record IDs and contents stay intact."""
import sqlite3
from datetime import datetime, timezone

SCHEMA_VERSION = 1


def prepare(con, path):
    version = con.execute('PRAGMA user_version').fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(f'数据库版本 {version} 高于本程序支持的 {SCHEMA_VERSION}；请升级程序，不要降级打开此数据库')
    if version == 0 and con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='records'").fetchone():
        directory = path.parent / 'backups'
        directory.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup = directory / f'memory-before-schema-1-{stamp}.sqlite3'
        with sqlite3.connect(str(backup)) as destination:
            con.backup(destination)
        backup.chmod(0o600)


def migrate(con):
    con.execute('BEGIN IMMEDIATE')
    try:
        if con.execute('PRAGMA user_version').fetchone()[0] < 1:
            con.execute('''CREATE TABLE copy_identity (
                space_id TEXT NOT NULL, kind TEXT NOT NULL, origin_id TEXT NOT NULL,
                record_id TEXT NOT NULL, PRIMARY KEY(space_id,kind,origin_id))''')
            con.execute('CREATE INDEX copy_identity_record ON copy_identity(space_id,kind,record_id)')
            con.execute('PRAGMA user_version=1')
        con.commit()
    except BaseException:
        con.rollback()
        raise
