import asyncio
from datetime import datetime, timezone
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'distribution')]
from memory_slice.service import MemoryService
from memory_slice.schemas import SpaceInput, ConversationInput, MessageInput
from codex_sync import HistorySync, eligible


def entry(i, at, role='userMessage', text='新消息', phase=None):
    item={'id':i,'type':role,'content':[{'type':'text','text':text}],'text':text,'phase':phase}
    return {'item':item,'startedAtMs':int(at*1000),'completedAtMs':int(at*1000),'turnId':'turn'}

class Reader:
    def __init__(self):
        self.executable='fake'
        self.calls=[]
        self.threads=[{'id':'thread','source':'vscode','parentThreadId':None,'ephemeral':False,'updatedAt':200,'preview':'正常聊天','cwd':'/project'}]
        self.turns=[{'id':'turn','status':'completed','startedAt':100,'completedAt':102}]
        self.items={'turn':[entry('m1',101)]}
        self.failure=False
    async def start(self):pass
    async def close(self):pass
    async def call(self,method,params):
        self.calls.append((method,params))
        if self.failure:raise RuntimeError('offline')
        if method=='thread/read':return {'thread':self.threads[0]}
        if method=='thread/list':return {'data':self.threads,'nextCursor':None}
        rows=self.turns if method=='thread/turns/list' else self.items[params['turnId']]
        index=int(params.get('cursor') or 0);limit=params['limit']
        return {'data':rows[index:index+limit],'nextCursor':str(index+limit) if index+limit<len(rows) else None}

class SyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.core=MemoryService(self.temp.name)
        self.sid=self.core.add_space(SpaceInput(name='同步测试'))['id']
        self.reader=Reader();self.sync=HistorySync(self.core,self.reader)
        with patch('codex_sync.time.time',return_value=100.5):
            await self.sync.configure({'space_id':self.sid})
    async def asyncTearDown(self):
        await self.sync.close();await self.core.close();self.temp.cleanup()
    def messages(self):return self.core.store.list('message',self.sid)
    async def test_boundary_old_thread_continuation_and_types(self):
        self.reader.items['turn']=[entry('old',99),entry('same-second',100.6),entry('think',101,'reasoning'),entry('progress',101,'agentMessage',phase='commentary'),entry('answer',102,'agentMessage',phase='final_answer')]
        await self.sync.cycle()
        self.assertEqual(self.sync.state['status'],'idle',self.sync.state['last_error'])
        self.assertEqual([m['role'] for m in self.messages()],['user','assistant'])
        self.assertEqual({m['at'] for m in self.messages()},{'1970-01-01T00:01:40.600000+00:00','1970-01-01T00:01:42+00:00'})
    async def test_first_second_not_lost(self):
        self.reader.turns[0].update(startedAt=100,completedAt=100)
        self.reader.items['turn']=[entry('same-second',100.8)]
        await self.sync.cycle();self.assertEqual(len(self.messages()),1)
    async def test_replay_restart_and_pause_preserve_boundary(self):
        await self.sync.cycle();await self.sync.cycle()
        self.sync=HistorySync(self.core,self.reader)
        await self.sync.cycle();self.assertEqual(len(self.messages()),1)
        await self.sync.configure({'enabled':False})
        await self.sync.configure({'space_id':self.sid})
        self.assertFalse(self.sync.state['enabled'])
        self.assertEqual(self.sync.state['since'],100.5)
    async def test_internal_tasks_and_exclusions(self):
        template=self.reader.threads[0]
        self.reader.threads=[dict(template,id='child',source={'subAgent':{'other':'guardian'}}),dict(template,id='temporary',ephemeral=True),dict(template,id='excluded'),dict(template,id='project',cwd='/excluded')]
        await self.sync.configure({'excluded_threads':['excluded'],'excluded_projects':['/excluded']})
        await self.sync.cycle()
        self.assertEqual(len(self.messages()),0)
        self.assertEqual(len(self.sync.state['threads']),0)
    async def test_pending_turn_waits_until_finished(self):
        self.reader.turns[0]['status']='inProgress'
        await self.sync.cycle();self.assertEqual(len(self.messages()),0)
        self.reader.turns[0]['status']='interrupted'
        self.reader.threads[0]['updatedAt']=201
        await self.sync.cycle();self.assertEqual(len(self.messages()),1)
    async def test_provisional_interrupted_turn_is_not_marked_done(self):
        self.reader.turns[0].update(status='interrupted',completedAt=None)
        await self.sync.cycle()
        self.assertEqual(len(self.messages()),0)
        self.assertTrue(self.sync.state['threads']['thread']['has_active'])
        self.reader.turns[0].update(status='completed',completedAt=103)
        self.reader.items['turn'].append(entry('answer',103,'agentMessage',phase='final_answer'))
        await self.sync.cycle()
        self.assertEqual(sorted(m['role'] for m in self.messages()),['assistant','user'])
        await self.sync.cycle()
        self.assertEqual(len(self.messages()),2)

    async def test_upgrade_replays_missing_answer_without_duplicate_user(self):
        await self.sync.cycle()
        cutoff=self.sync.state['since']
        self.sync.state.pop('completion_revision')
        self.sync.save()
        self.reader.items['turn'].append(entry('answer',102,'agentMessage',phase='final_answer'))
        self.sync=HistorySync(self.core,self.reader)
        await self.sync.cycle()
        self.assertEqual(self.sync.state['since'],cutoff)
        self.assertEqual(sorted(m['role'] for m in self.messages()),['assistant','user'])

    async def test_many_turns_paginate_without_watermark_loss(self):
        self.reader.turns=[{'id':str(i),'status':'completed','startedAt':i,'completedAt':i+1} for i in range(150,100,-1)]
        self.reader.items={str(i):[entry(str(i),i+.5)] for i in range(101,151)}
        for _ in range(14):await self.sync.cycle()
        self.assertEqual(len(self.messages()),50,self.sync.state['last_error'])
    async def test_large_turn_chunk_budget_and_resume(self):
        self.reader.items['turn']=[entry('long',101,text='x'*65000)]
        await self.sync.turns('thread',self.sync.state['threads'].setdefault('thread',{}))
        st=self.sync.state['threads']['thread'];job=st['pending'][0]
        import time
        await self.sync.ingest_turn('thread',st,job,[1],time.monotonic()+20)
        self.assertEqual(len(self.messages()),1)
        await self.sync.cycle()
        self.assertEqual(sum(len(m['content']) for m in self.messages()),65000)
    async def test_failure_does_not_consume_progress(self):
        self.reader.failure=True;await self.sync.cycle();self.assertEqual(self.sync.state['status'],'error')
        self.reader.failure=False;await self.sync.cycle();self.assertEqual(len(self.messages()),1)
    async def test_explicit_backfill_is_range_limited(self):
        self.reader.turns=[{'id':'old','status':'completed','startedAt':80,'completedAt':90}]
        self.reader.items={'old':[entry('outside',81),entry('inside',86)]}
        await self.sync.cycle();self.assertEqual(len(self.messages()),0)
        await self.sync.backfill('thread',85,88)
        await self.sync.cycle();self.assertEqual(len(self.messages()),1)
    async def test_conversation_display_keeps_stable_integration_identity(self):
        raw='agent-session:codex:stable-id'
        c=self.core.add_conversation(self.sid,ConversationInput(title=raw))
        self.core.add_message(self.sid,c['id'],MessageInput(role='user',content='可辨认的聊天内容'))
        row=self.core.conversations(self.sid)[0]
        self.assertEqual(row['title'],raw)
        self.assertIn('可辨认的聊天内容',row['display_title'])

    async def test_missing_timestamp_does_not_mark_turn_done(self):
        row=entry('bad',101);row['startedAtMs']=None;self.reader.items['turn']=[row]
        await self.sync.cycle()
        self.assertEqual(self.sync.state['status'],'error')
        self.assertTrue(self.sync.state['threads']['thread']['pending'])

if __name__=='__main__':unittest.main()
