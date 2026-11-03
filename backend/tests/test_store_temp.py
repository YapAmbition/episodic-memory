"""SQLite sorting must not depend on a host's short-lived temp directory."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from memory_slice.store import Store


class StoreTempTests(unittest.TestCase):
    def test_each_new_connection_uses_memory_temp_store(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(directory)
            with store.connect() as con:
                self.assertEqual(con.execute('PRAGMA temp_store').fetchone()[0],2)
                con.execute('PRAGMA temp_store=FILE')
            with store.connect() as con:
                self.assertEqual(con.execute('PRAGMA temp_store').fetchone()[0],2)

    def test_order_by_rowid_with_deleted_host_temp_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            dead=Path(directory)/'deleted-host-temp';dead.mkdir();dead.rmdir()
            env=dict(os.environ,TEMP=str(dead),TMP=str(dead),TMPDIR=str(dead),SQLITE_TMPDIR=str(dead))
            # Separate process prevents SQLite/tempfile caches from masking inherited settings.
            code='''
import json, sys
from memory_slice.store import Store
store=Store(sys.argv[1])
with store.connect() as con:
    con.executemany('INSERT INTO records VALUES(?,?,?,?,?)',
        [('job',f'job_{i:05}', 'space', json.dumps({'id':f'job_{i:05}'}),1) for i in range(4000,-1,-1)])
    # Force the kind/id index so this query really requires a temporary sort.
    plan=con.execute("EXPLAIN QUERY PLAN SELECT data FROM records INDEXED BY sqlite_autoindex_records_1 WHERE kind='job' ORDER BY rowid").fetchall()
    assert any('TEMP B-TREE' in r[3] for r in plan),plan
    rows=con.execute("SELECT data FROM records INDEXED BY sqlite_autoindex_records_1 WHERE kind='job' ORDER BY rowid").fetchall()
    assert json.loads(rows[0][0])['id']=='job_04000'
assert len(store.list('job'))==4001
assert store.list('job')[0]['id']=='job_04000'
'''
            subprocess.run([sys.executable,'-c',code,str(Path(directory)/'database')],env=env,check=True,capture_output=True,text=True,timeout=30)
