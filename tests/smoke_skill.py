"""Local real-process smoke test. Pass an isolated --home already set up by the client.
Never points to a user's default data directory; no model or embedding requests.
"""
import argparse
import json
import hashlib
import zipfile
from pathlib import Path
import socket
import subprocess
import sys
from urllib.request import urlopen

p=argparse.ArgumentParser()
p.add_argument('--home',type=Path,required=True)
p.add_argument('--skill',type=Path,required=True)
p.add_argument('--test-upgrade', action='store_true')
a=p.parse_args()
sys.path.insert(0,str(a.skill.resolve()/'scripts'))
import client
home=a.home.resolve()
config=client.read(home/'config.json')
assert home != client.default_home().resolve(), 'Only an isolated home may be tested'

def payload(event, **kwargs):
    row={'hook_event_name':event,'session_id':'smoke-session','turn_id':'smoke-turn',**kwargs}
    result=subprocess.run([sys.executable,str(a.skill/'scripts/hook.py'),'--home',str(home)],input=json.dumps(row),capture_output=True,text=True,check=True,timeout=30)
    return json.loads(result.stdout)

try:
    with client.lock(home/'service.lock'):
        sid,cid=client.binding(home,config,'codex','smoke-session')
        other,_=client.binding(home,config,'codex','another-session')
        assert sid==other
        unknown,_=client.binding(home,config,'unlisted-agent-one')
        same_unknown,_=client.binding(home,config,'unlisted-agent-two')
        assert unknown==same_unknown and unknown!=sid
    client.request(config,f'/api/v1/spaces/{sid}/events',{
        'title':'紫色烤箱的面包失败','body':'昨天用紫色烤箱烤面包，忘记预热导致面包没发起来。',
        'anchors':{'details':['紫色烤箱','面包','忘记预热']},'salience':1})
    result=payload('UserPromptSubmit',prompt='我用紫色烤箱烤面包失败那次是怎么回事？')
    assert '紫色烤箱' in result['hookSpecificOutput']['additionalContext'],result
    payload('UserPromptSubmit',prompt='我用紫色烤箱烤面包失败那次是怎么回事？')
    payload('Stop',last_assistant_message='那次忘记预热了。')
    messages=client.request(config,f'/api/v1/spaces/{sid}/conversations/{cid}/messages')
    assert messages==[], 'Hooks must not ingest raw prompts or replies'
    for role,text in [('user','已同步的用户消息'),('assistant','已同步的最终回复')]:
        client.request(config,f'/api/v1/spaces/{sid}/conversations/{cid}/messages',{'role':role,'content':text,'client_id':'smoke-'+role})
    with urlopen(f"http://127.0.0.1:{config['port']}/") as r:
        assert b'<html' in r.read()
    # Own service occupied port must be reported, never terminated.
    try:
        client.free_port(config['port'])
        raise AssertionError('port check missed listener')
    except RuntimeError:
        pass
    assert client.health(config)
    client.stop(config)
    assert not client.health(config)
    assert payload('SessionStart')=={}
    assert client.health(config), 'hook should restart stopped service'
    assert len(client.request(config,f'/api/v1/spaces/{sid}/conversations/{cid}/messages'))==2
    if a.test_upgrade:
        original_skill = client.SKILL
        manifest = client.read(original_skill/'release.json')
        source = (original_skill/manifest['source']['path']).resolve()
        upgrade_skill = home/'upgrade-smoke-skill'
        upgrade_skill.mkdir(exist_ok=True)
        def release(version, broken=False):
            target = home/f'upgrade-test-{version}.zip'
            with zipfile.ZipFile(source) as src, zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as dst:
                for name in src.namelist():
                    data = src.read(name)
                    if name == 'service-package.json':
                        data = json.dumps({'version':version,'api_version':1}).encode()
                    if broken and name == 'service_host.py':
                        data = b'raise RuntimeError("intentional isolated upgrade smoke failure")\n'
                    dst.writestr(name,data)
            client.write(upgrade_skill/'release.json',{'service_version':version,'source':{'path':str(target)},'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
        try:
            client.SKILL = upgrade_skill
            release('0.2.1')
            with client.lock(home/'service.lock'):
                upgraded = client.ensure(home)
            assert upgraded['version']=='0.2.1'
            assert len(client.request(upgraded,f'/api/v1/spaces/{sid}/conversations/{cid}/messages'))==2
            release('0.2.2', broken=True)
            try:
                with client.lock(home/'service.lock'):
                    client.ensure(home)
                raise AssertionError('bad release unexpectedly started')
            except RuntimeError:
                pass
            restored = client.read(home/'config.json')
            assert restored['version']=='0.2.1'
            assert client.health(restored)['version']=='0.2.1'
            assert len(client.request(restored,f'/api/v1/spaces/{sid}/conversations/{cid}/messages'))==2
            print('Real service upgrade and failed-upgrade rollback: passed')
        finally:
            client.SKILL = original_skill
    print(json.dumps({'passed':['real cp + venv installation','isolated hook configuration','agent/session spaces','unknown shared space','event recall into hook output','hooks do not write messages','user/assistant history persistence','React static UI','port conflict protection','hook restart','data survives restart']},ensure_ascii=False))
finally:
    client.stop(config)
