"""Read-only Codex history synchronization. No prompts, turns or models are started."""
import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import time

from memory_slice.schemas import ConversationInput, MessageInput


class RpcError(RuntimeError):
    pass


class CodexReader:
    def __init__(self, executable=None):
        desktop = Path('/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')
        self.executable = executable or (str(desktop) if desktop.exists() else shutil.which('codex'))
        self.proc = None
        self.sequence = 0

    async def start(self):
        if self.proc and self.proc.returncode is None:
            return
        if not self.executable:
            raise RpcError('找不到 Codex 程序，请设置 codex_executable')
        self.proc = await asyncio.create_subprocess_exec(self.executable, 'app-server', stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, limit=32*1024*1024)
        await self.call('initialize', {'clientInfo':{'name':'memory_slice_history','version':'0.2.0'}})
        self.proc.stdin.write(b'{"method":"initialized","params":{}}\n')
        await self.proc.stdin.drain()

    async def call(self, method, params):
        self.sequence += 1
        request_id = self.sequence
        self.proc.stdin.write((json.dumps({'id':request_id,'method':method,'params':params})+'\n').encode())
        await self.proc.stdin.drain()
        async def read():
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    raise RpcError('Codex 历史接口已断开')
                result = json.loads(line)
                if result.get('id') != request_id:
                    continue
                if 'error' in result:
                    # Do not relay provider/host error strings or history contents.
                    raise RpcError(f"Codex {method} 不可用（{result['error'].get('code')}）；请使用与桌面版匹配的版本")
                return result['result']
        return await asyncio.wait_for(read(), 12)

    async def close(self):
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 3)
            except asyncio.TimeoutError:
                self.proc.kill()
                await self.proc.wait()
        self.proc = None


def timestamp(value):
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
    raise ValueError('missing timestamp')


def eligible(thread, excluded_threads, excluded_projects):
    # These are host identities, not classification of natural-language content.
    return (thread.get('source') in ('vscode','cli') and not thread.get('parentThreadId')
            and not thread.get('ephemeral') and thread['id'] not in excluded_threads
            and str(thread.get('cwd','')) not in excluded_projects)


class HistorySync:
    KEY = 'codex-history-sync'

    def __init__(self, service, reader=None):
        self.service = service
        self.store = service.store
        self.reader = reader or CodexReader()
        self.lock = asyncio.Lock()
        self.task = None
        self.wakeup = asyncio.Event()
        saved = self.store.list('integration')
        self.state = next((r for r in saved if r['id']==self.KEY), {
            'id':self.KEY, 'enabled':False, 'since':None, 'space_id':None,
            'excluded_threads':[], 'excluded_projects':[], 'threads':{},
            'list_cursor':None, 'last_success':None, 'last_error':None,
            'imported_messages':0, 'skipped_items':0, 'status':'disabled'})
        # Older readers finalized provisional interrupted turns before the answer existed.
        # Replay only the already-authorized window; stable item IDs deduplicate imports.
        if self.state.get('completion_revision', 0) < 1:
            for thread in self.state['threads'].values():
                for key in ('watermark', 'turn_cursor', 'scan_boundary'):
                    thread.pop(key, None)
                thread.update(done=[], pending=[], dirty=True)
            self.state['completion_revision'] = 1
            self.save()


    def save(self):
        self.store.put('integration', self.state, '')

    def status(self):
        return {k:v for k,v in self.state.items() if k not in ('threads','list_cursor','version')} | {
            'running':self.lock.locked(), 'pending_turns':sum(len(t.get('pending',[])) for t in self.state['threads'].values()),
            'tracked_threads':len(self.state['threads']), 'interval_seconds':15,
            'max_messages_per_cycle':100, 'max_seconds_per_cycle':15,
            'threads':[{'id':tid,'title':t.get('title',tid),'conversation_id':t.get('cid'),
                        'excluded':tid in self.state['excluded_threads']} for tid,t in self.state['threads'].items()]}

    async def configure(self, body):
        async with self.lock:
            sid = body.get('space_id', self.state['space_id'])
            self.service.config_for(sid)
            if self.state['space_id'] and sid != self.state['space_id']:
                raise ValueError('已绑定的同步空间不能改绑')
            for field in ('excluded_threads','excluded_projects'):
                if field in body:
                    values = body[field]
                    if not isinstance(values,list) or len(values)>1000 or any(not isinstance(x,str) for x in values):
                        raise ValueError('排除范围必须是字符串列表')
                    self.state[field] = values
            if body.get('codex_executable'):
                path = Path(body['codex_executable']).expanduser()
                if not path.is_absolute() or not path.is_file():
                    raise ValueError('Codex 程序需要有效的绝对路径')
                await self.reader.close()
                self.reader.executable = str(path)
                self.state['codex_executable'] = str(path)
            first_enable = self.state['since'] is None
            if first_enable:
                self.state['since'] = time.time()  # Immutable default boundary, never reset by restart or update.
            self.state.update(space_id=sid, enabled=body.get('enabled',True if first_enable else self.state['enabled']))
            self.state['status'] = 'waiting' if self.state['enabled'] else 'disabled'
            self.save()
            self.wakeup.set()
        return self.status()

    async def backfill(self, tid, start, end):
        start, end = timestamp(start), timestamp(end)
        if start >= end or end > time.time()+1:
            raise ValueError('补录范围必须是有效的过去时间段')
        async with self.lock:
            await self.reader.start()
            thread = (await self.reader.call('thread/read',{'threadId':tid}))['thread']
            if not eligible(thread,self.state['excluded_threads'],self.state['excluded_projects']):
                raise ValueError('补录仅支持未排除的正常用户会话')
            state = self.state['threads'].setdefault(tid, {})
            state.update(title=(thread.get('name') or thread.get('preview') or tid)[:100], cwd=thread.get('cwd',''))
            state['backfill'] = {'since':start,'until':end,'cursor':None}
            self.save()
            self.wakeup.set()
        return self.status()

    def conversation(self, tid, state):
        sid = self.state['space_id']
        if state.get('cid'):
            return state['cid']
        legacy = 'agent-session:codex:'+hashlib.sha256(tid.encode()).hexdigest()
        found = next((c for c in self.store.list('conversation',sid)
                      if c.get('external_thread_id')==tid or c.get('title')==legacy),None)
        conv = found or self.service.add_conversation(sid, ConversationInput(title=state.get('title',tid)[:100]))
        conv.update(title=state.get('title',tid)[:100], external_thread_id=tid, integration='codex')
        self.store.put('conversation',conv,sid)
        state['cid'] = conv['id']
        return conv['id']

    async def discover(self):
        params={'limit':30,'sortKey':'updated_at','sortDirection':'desc','sourceKinds':['cli','vscode'],'useStateDbOnly':True}
        if self.state['list_cursor']:
            params['cursor']=self.state['list_cursor']
        result=await self.reader.call('thread/list',params)
        stop=False
        for thread in result['data']:
            if thread.get('updatedAt',time.time()) < self.state['since']-2:
                stop=True
                break
            if not eligible(thread,self.state['excluded_threads'],self.state['excluded_projects']):
                continue
            state=self.state['threads'].setdefault(thread['id'],{})
            observed=thread.get('updatedAt',0)
            if state.get('observed_updated') != observed or time.time()-observed < 2:
                state['dirty']=True
            state.update(title=(thread.get('name') or thread.get('preview') or thread['id'])[:100],cwd=thread.get('cwd',''),observed_updated=observed)
        self.state['list_cursor']=None if stop else result.get('nextCursor')
        self.save()

    async def turns(self, tid, state, backfill=False):
        scan = state.get('backfill') if backfill else state
        boundary = scan['since'] if backfill else max(self.state['since'],state.get('watermark',0)-1)
        params={'threadId':tid,'limit':20,'itemsView':'notLoaded','sortDirection':'desc'}
        cursor=scan.get('cursor' if backfill else 'turn_cursor')
        if cursor:params['cursor']=cursor
        if not backfill:
            if cursor:
                boundary=state.get('scan_boundary',boundary)
            else:
                state['scan_boundary']=boundary
        if not backfill and not cursor:
            state['has_active']=False
        result=await self.reader.call('thread/turns/list',params)
        stop=False
        for turn in result['data']:
            started=turn.get('startedAt')
            if started is None:
                raise RpcError('会话回合缺少时间戳，未导入；请检查 Codex 版本')
            # A pre-boundary turn may contain messages arriving after activation.
            completed=turn.get('completedAt')
            if completed is not None and completed + 1 < boundary:
                stop=True
                break
            if completed is None or turn['status'] not in ('completed','interrupted','failed'):
                if not backfill:state['has_active']=True
                continue  # Pick up again next scan, after final messages become stable.
            upper = scan['until'] if backfill else None
            if upper and started > upper:continue
            done=state.setdefault('done',[])
            queue=state.setdefault('pending',[])
            key=turn['id']+(':'+str(boundary)+':'+str(upper) if backfill else '')
            if key in done or any(q['key']==key for q in queue):continue
            queue.append({'key':key,'id':turn['id'],'started':started,'since':boundary if backfill else self.state['since'],
                          'until':upper,'cursor':None})
        scan['cursor' if backfill else 'turn_cursor']=None if stop else result.get('nextCursor')
        if backfill and scan['cursor'] is None:
            state.pop('backfill',None)
        state.setdefault('pending',[]).sort(key=lambda x:x['started'])
        self.save()

    async def ingest_turn(self, tid, state, job, budget, deadline):
        params={'threadId':tid,'turnId':job['id'],'limit':10,'sortDirection':'asc'}
        if job.get('cursor'):params['cursor']=job['cursor']
        page=await self.reader.call('thread/items/list',params)
        sid=self.state['space_id']
        for entry in page['data']:
            item=entry['item']
            role = 'user' if item['type']=='userMessage' else 'assistant' if item['type']=='agentMessage' and item.get('phase')=='final_answer' else None
            if role is None:
                continue
            ms = entry.get('startedAtMs') if role=='user' else entry.get('completedAtMs')
            if ms is None:
                raise RpcError('消息缺少可靠时间戳，未推进同步位置')
            at=ms/1000
            if at < job['since'] or (job['until'] and at > job['until']):continue
            text = ''.join(c.get('text','') for c in item.get('content',[]) if c.get('type')=='text') if role=='user' else item.get('text','')
            if not text.strip():continue
            cid=self.conversation(tid,state)
            for offset in range(0,len(text),20000):
                chunk=text[offset:offset+20000]
                key='cx:'+hashlib.sha256(f'{tid}:{item["id"]}:{offset}'.encode()).hexdigest()
                existing=next((m for m in self.store.list('message',sid) if m.get('conversation_id')==cid and m.get('client_id')==key),None)
                if existing:continue
                if budget[0]<=0 or time.monotonic()>=deadline:
                    self.save()
                    return False  # Replay this page; committed chunks are idempotent.
                # Compatibility with messages already written by the old Hook.
                oldkey=hashlib.sha256(f'{job["id"]}:{role}:{offset}:{chunk}'.encode()).hexdigest()
                if any(m.get('client_id')==oldkey and m.get('conversation_id')==cid for m in self.store.list('message',sid)):
                    continue
                self.service.add_message(sid,cid,MessageInput(role=role,content=chunk,client_id=key,at=datetime.fromtimestamp(at,timezone.utc)))
                budget[0]-=1
                self.state['imported_messages']+=1
        job['cursor']=page.get('nextCursor')
        if not job['cursor']:
            state.setdefault('done',[]).append(job['key'])
            state['done']=state['done'][-200:]
            # Do not pass older queued turns; watermark only prunes completed history scans.
            if not job['until']:
                state['watermark']=max(state.get('watermark',0),job['started'])
            state['pending'].remove(job)
        self.save()
        return True

    async def cycle(self):
        if self.lock.locked() or not self.state['enabled']:
            return
        async with self.lock:
            self.state['status']='syncing'
            deadline=time.monotonic()+15
            budget=[100]
            try:
                await self.reader.start()
                await self.discover()
                # Least recently visited first prevents a noisy conversation starving others.
                ordered=sorted(self.state['threads'].items(),key=lambda kv:kv[1].get('visited',0))
                for tid,state in ordered:
                    if time.monotonic()>=deadline or budget[0]<=0:break
                    if tid in self.state['excluded_threads'] or state.get('cwd') in self.state['excluded_projects']:continue
                    if not (state.get('dirty',True) or state.get('pending') or state.get('backfill') or state.get('has_active')):
                        continue
                    state['visited']=time.time()
                    await self.turns(tid,state)
                    if state.get('backfill'):await self.turns(tid,state,True)
                    pages=0
                    while state.get('pending') and budget[0]>0 and time.monotonic()<deadline and pages<5:
                        await self.ingest_turn(tid,state,state['pending'][0],budget,deadline)
                        pages+=1
                    if not state.get('turn_cursor') and not state.get('pending') and not state.get('has_active') and time.time()-state.get('observed_updated',0)>2:
                        state['dirty']=False
                self.state.update(status='idle',last_success=time.time(),last_error=None)
            except Exception as exc:
                self.state.update(status='error',last_error=str(exc) if isinstance(exc,RpcError) else type(exc).__name__)
                await self.reader.close()
            finally:
                self.save()

    async def loop(self):
        while True:
            self.wakeup.clear()
            await self.cycle()
            try:await asyncio.wait_for(self.wakeup.wait(),15)
            except asyncio.TimeoutError:pass

    def start(self):
        if self.state.get('codex_executable'):self.reader.executable=self.state['codex_executable']
        self.task=asyncio.create_task(self.loop())

    async def close(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):await self.task
            self.task=None
        await self.reader.close()
