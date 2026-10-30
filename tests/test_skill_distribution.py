import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import hashlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'skills/memory-slice/scripts'))
import client
import hook
spec = importlib.util.spec_from_file_location('service_host', ROOT/'distribution/service_host.py')
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_space_and_session_identity(self):
        config = {'spaces':{}, 'sessions':{}, 'instance_id':'installation'}
        spaces, conversations = [], {}
        def request(config, path, body=None, **kwargs):
            if path.endswith('/spaces'):
                if body is not None:
                    spaces.append(dict(body, id=str(len(spaces))))
                    return spaces[-1]
                return spaces
            if path.endswith('/persons'):
                return [{'id':'person'}]
            if path.endswith('/conversations'):
                rows = conversations.setdefault(path, [])
                if body is not None:
                    rows.append(dict(body, id=str(len(rows))))
                    return rows[-1]
                return rows
            self.fail(path)
        with patch.object(client, 'request', side_effect=request):
            sid1, cid1 = client.binding(self.home, config, 'codex', 'session1')
            sid2, cid2 = client.binding(self.home, config, 'codex', 'session2')
            self.assertEqual(sid1, sid2)
            self.assertNotEqual(cid1, cid2)
            self.assertEqual(client.binding(self.home,config,'codex','session1'),(sid1,cid1))
            other, _ = client.binding(self.home,config,'qoder')
            self.assertNotEqual(sid1,other)
            self.assertEqual(client.binding(self.home,config,'foo')[0],client.binding(self.home,config,'bar')[0])
            # Recover mapping without duplicating spaces after a lost config write.
            config['spaces'] = {}
            self.assertEqual(client.binding(self.home,config,'codex')[0],sid1)

    def test_hook_preserves_other_handlers_and_is_idempotent(self):
        existing = {'hooks':{'Stop':[{'hooks':[{'type':'command','command':'echo other'}]}]}}
        client.write(self.home/'hooks.json',existing)
        with patch.object(client.shutil,'which',return_value='/codex'), patch.object(client.subprocess,'run') as run:
            run.return_value.stdout='codex-cli 0.144.6'
            first=client.install_hooks(self.home,self.home)
            after=(self.home/'hooks.json').read_bytes()
            client.install_hooks(self.home,self.home)
        self.assertEqual(first['status'],'installed_needs_review')
        self.assertEqual(after,(self.home/'hooks.json').read_bytes())
        self.assertIn('echo other',after.decode())
        self.assertEqual(len(list(self.home.glob('hooks.json.backup-*'))),1)

    def test_no_downgrade(self):
        config={'version':'2.0.0','port':8095,'credentials':'session'}
        client.write(self.home/'config.json',config)
        with patch.object(client,'SKILL',self.home), patch.object(client,'stage') as stage, patch.object(client,'start'):
            client.write(self.home/'release.json',{'service_version':'1.0.0'})
            self.assertEqual(client.ensure(self.home)['version'],'2.0.0')
            stage.assert_not_called()

    def test_failed_upgrade_restores_database_and_config(self):
        old={'version':'1.0.0','port':8095,'credentials':'session'}
        client.write(self.home/'config.json',old)
        client.write(self.home/'release.json',{'service_version':'1.1.0'})
        db=self.home/'data/memory.sqlite3'
        db.parent.mkdir()
        with sqlite3.connect(db) as conn:
            conn.execute('create table memory(value text)')
            conn.execute("insert into memory values ('original')")
        def start(home,config):
            if config['version']=='1.1.0':
                with sqlite3.connect(db) as conn:
                    conn.execute("update memory set value='broken'")
                raise RuntimeError('new release failed')
        with patch.object(client,'SKILL',self.home),patch.object(client,'stage'),patch.object(client,'health',return_value={'status':'ok'}),patch.object(client,'stop'),patch.object(client,'start',side_effect=start):
            with self.assertRaisesRegex(RuntimeError,'failed'):
                client.ensure(self.home)
        self.assertEqual(client.read(self.home/'config.json'),old)
        with sqlite3.connect(db) as conn:
            self.assertEqual(conn.execute('select value from memory').fetchone()[0],'original')

    def test_archive_traversal_and_checksum(self):
        for name in ('../escape','C:/escape','/absolute','back\\slash'):
            path=self.home/'bad.zip'
            with zipfile.ZipFile(path,'w') as z:
                z.writestr(name,'data')
            manifest={'service_version':'0.1.0','source':{'path':str(path)},'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
            with self.assertRaisesRegex(ValueError,'路径'):
                client.stage(self.home,manifest)
            self.assertFalse((self.home/'releases/0.1.0').exists())
            manifest['sha256']='wrong'
            with self.assertRaisesRegex(RuntimeError,'SHA256'):
                client.stage(self.home,manifest)

    def test_credentials_survive_clear_but_explicit_delete_works(self):
        class Backend:
            def __init__(self): self.data={}
            def get_password(self,n,k): return self.data.get((n,k))
            def set_password(self,n,k,v): self.data[n,k]=v
            def delete_password(self,n,k): del self.data[n,k]
        backend=Backend()
        first=host.CredentialMap(backend,'ns',[])
        first['space']='secret'
        first.clear()
        second=host.CredentialMap(backend,'ns',['space'])
        self.assertEqual(second.get('space'),'secret')
        self.assertIn('space',second)
        self.assertEqual(second.pop('space',None),'secret')
        self.assertIsNone(first.get('space'))

    def test_hook_chunking_and_retry_ids(self):
        calls=[]
        text='测'*40001
        with patch.object(client,'request',side_effect=lambda c,p,b: calls.append(b)):
            hook.record({},'s','c','turn','user',text)
            hook.record({},'s','c','turn','user',text)
        self.assertEqual(''.join(c['content'] for c in calls[:3]),text)
        self.assertEqual([c['client_id'] for c in calls[:3]],[c['client_id'] for c in calls[3:]])
        self.assertEqual(len({c['client_id'] for c in calls[:3]}),3)

    def test_hook_recalls_without_recording_or_creating_sessions(self):
        client.write(self.home/'config.json',{'version':'1.0.0','spaces':{'codex':'s'}})
        with patch.object(client,'start'),patch.object(client,'binding') as bind,patch.object(client,'request',return_value={'context':'<memory-slice>event</memory-slice>'}),patch.object(hook,'record') as record:
            payload={'hook_event_name':'UserPromptSubmit','session_id':'session','turn_id':'turn','prompt':'question'}
            result=hook.run(self.home,payload)
            self.assertIn('<memory-slice>',result['hookSpecificOutput']['additionalContext'])
            payload.update(hook_event_name='Stop',last_assistant_message='answer')
            self.assertEqual(hook.run(self.home,payload),{})
            record.assert_not_called()
            bind.assert_not_called()

    def test_hook_failure_is_json_and_does_not_leak_payload(self):
        with patch.object(sys,'argv',['hook','--home',str(self.home)]),patch.object(sys,'stdin',io.StringIO('{bad secret')),patch.object(sys,'stdout',io.StringIO()) as out:
            hook.main()
        self.assertEqual(json.loads(out.getvalue()),{})
        self.assertNotIn('secret',(self.home/'logs/hooks.log').read_text())


if __name__=='__main__':
    unittest.main()
