import copy
import json
import unittest
from unittest.mock import patch
import test_transfer
from memory_slice.migration_compensation import compensate, RECOVERY, summary
from memory_slice.space_copy import SpaceCopier, CopyInput
from memory_slice.schemas import SpaceInput
from memory_slice.transfer import digest

class CompensationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_transfer.TransferTests();self.fixture.setUp()
        self.c=self.fixture
    def tearDown(self):self.fixture.tearDown()
    def imported(self, package=None):
        c=self.c;c.pkg=package or c.exporter.export(c.sid)
        body,_=c.preview(c.body());return c.importer.commit(body)
    def evidence(self,core,sid):return core.store.list(RECOVERY,sid)
    def snapshot(self):
        from memory_slice.transfer import rows
        with self.c.source.store.connect() as con:return rows(con,self.c.sid)

    def test_all_missing_reference_categories_keep_originals_and_source(self):
        c=self.c
        event=dict(c.event,person_ids=['person_absent'],source_ids=['msg_absent'],split_from='old_split',split_into=['old_child'],merged_into='old_merge',archive_conversation_id='old_conv',body='正文原样 [[event_absent]]',links=[dict(target_id='event_absent',relation='后续',strength=.8)])
        c.source.store.put('event',event,c.sid)
        msg=dict(c.message,person_id='old_person',sender_id='old_sender',mention_ids=['old_mention'],in_reply_to='old_parent',conversation_id='old_conv')
        c.source.store.put('message',msg,c.sid)
        before=self.snapshot();result=self.imported()
        self.assertEqual(before,self.snapshot())
        out=c.target.store.list('event',c.tid)[0]
        self.assertEqual(out['body'],event['body'])
        self.assertEqual(out['source_ids'],[])
        self.assertIn('old_split',json.dumps(out['migration']))
        evidence=json.dumps(self.evidence(c.target,c.tid),ensure_ascii=False)
        for value in ('old_split','old_child','old_merge','old_conv','old_parent','old_sender','old_mention','msg_absent'):
            self.assertIn(value,evidence)
        self.assertGreater(result['compensation']['compensated'],0)
        imported_msg=c.target.store.list('message',c.tid)[0]
        self.assertEqual(imported_msg['content'],msg['content'])
        self.assertEqual(imported_msg['unavailable_in_reply_to'],'old_parent')
        conv=c.target.store.get('conversation',imported_msg['conversation_id'],c.tid)
        self.assertEqual(conv['title'],'恢复的会话')

    def test_pending_records_never_enter_runtime_and_survive_second_transfer(self):
        c=self.c
        c.source.store.put('message',dict(c.message,role='unknown',content='这段正文不能丢'),c.sid)
        fragment=c.source.store.list('fragment',c.sid)[0]
        c.source.store.put('fragment',dict(fragment,event_id='lost_event',content='独有的片段全文'),c.sid)
        self.imported()
        self.assertEqual(c.target.store.list('message',c.tid),[])
        pending=[r for r in self.evidence(c.target,c.tid) if r['status']=='pending']
        self.assertEqual(len(pending),2)
        from memory_slice.transfer import Transfers, ImportInput
        exported=Transfers(c.target).export(c.tid)
        target=c.source.add_space(SpaceInput(name='再次恢复'))['id']
        transfer=Transfers(c.source)
        b=ImportInput(package=exported,target_space_id=target,operation_id='second-transfer-123')
        p=transfer.preview(b);transfer.commit(b.model_copy(update={'preview_token':p['preview_token']}))
        again=[r for r in self.evidence(c.source,target) if r['status']=='pending']
        self.assertEqual({r['origin_id']:r['payload'] for r in pending},{r['origin_id']:r['payload'] for r in again})
        self.assertEqual(c.source.store.list('message',target),[])

    def test_compensation_is_idempotent_and_does_not_mutate_input(self):
        c=self.c
        records=copy.deepcopy(c.pkg['payload']['records'])
        next(r['value'] for r in records if r['kind']=='message').update(in_reply_to='absent',conversation_id='lost')
        before=copy.deepcopy(records)
        one=compensate(records,[],c.sid)
        two=compensate(*one,c.sid)
        self.assertEqual(one,two)
        self.assertEqual(records,before)

    def test_duplicate_ids_quarantine_both_and_keep_their_distinct_content(self):
        c=self.c;package=copy.deepcopy(c.pkg)
        message=next(r for r in package['payload']['records'] if r['kind']=='message')
        other=copy.deepcopy(message);other['value']['content']='重复 ID 的另一段正文'
        package['payload']['records'].append(other);package['sha256']=digest(package['payload'])
        self.imported(package)
        self.assertEqual(c.target.store.list('message',c.tid),[])
        values=[r['payload']['content'] for r in self.evidence(c.target,c.tid) if r['status']=='pending' and r['record_kind']=='message']
        self.assertCountEqual(values,[message['value']['content'],other['value']['content']])

    def test_missing_id_is_stable_and_unknown_fields_preserved(self):
        c=self.c;records=copy.deepcopy(c.pkg['payload']['records'])
        person=next(r['value'] for r in records if r['kind']=='person');person.pop('id');person['future_field']={'nested':'历史信息'}
        first=compensate(records,[],c.sid);second=compensate(records,[],c.sid)
        self.assertEqual(first,second)
        out=next(r['value'] for r in first[0] if r['kind']=='person')
        self.assertEqual(out['migration_extra']['future_field'],person['future_field'])
        self.assertTrue(out['id'].startswith('recovered_'))

    def test_unknown_consumption_and_unknown_time_are_pending_without_guessed_today(self):
        c=self.c;records=copy.deepcopy(c.pkg['payload']['records'])
        message=next(r['value'] for r in records if r['kind']=='message');message.pop('processed')
        event=next(r['value'] for r in records if r['kind']=='event')
        for k in ('learned_at','occurred_at','updated_at','created_at'):event.pop(k,None)
        normalized,_=compensate(records,[],c.sid)
        self.assertFalse(any(r['kind'] in ('message','event') for r in normalized))
        self.assertTrue(any(r['kind']==RECOVERY and r['value']['record_kind']=='event' and r['value']['status']=='pending' for r in normalized))

    def test_fragment_invalid_ranges_keep_text_and_original_offsets(self):
        c=self.c;frag=c.source.store.list('fragment',c.sid)[0]
        frag.update(source_ranges=[dict(message_id=c.message['id'],start=-8,end='bad')])
        c.source.store.put('fragment',frag,c.sid);self.imported()
        result=c.target.store.list('fragment',c.tid)[0]
        self.assertEqual(result['content'],frag['content']);self.assertEqual(result['source_ranges'],[])
        self.assertTrue(any(r['record_kind']=='fragment' and r['payload'].get('source_ranges')==frag['source_ranges'] for r in self.evidence(c.target,c.tid)))

    def test_direct_copy_and_file_transfer_agree_and_repeat_copy_does_not_duplicate_evidence(self):
        c=self.c;c.source.store.put('message',dict(c.message,conversation_id='lost',in_reply_to='gone'),c.sid)
        frag=c.source.store.list('fragment',c.sid)[0];c.source.store.put('fragment',dict(frag,event_id='absent'),c.sid)
        before=self.snapshot();self.imported()
        target=c.source.add_space(SpaceInput(name='本机副本'))['id']
        copier=SpaceCopier(c.source);body=CopyInput(target_space_id=target)
        for i in range(2):
            p=copier.preview(c.sid,body);copier.copy(c.sid,body.model_copy(update={'preview_token':p['preview_token']}))
            evidence=self.evidence(c.source,target)
            if i==0:first=copy.deepcopy(evidence)
            else:self.assertEqual(first,evidence)
        self.assertEqual(self.snapshot(),before)
        imported_evidence={r['origin_id']:r['payload'] for r in self.evidence(c.target,c.tid)}
        direct_evidence={r['origin_id']:r['payload'] for r in evidence}
        self.assertEqual(imported_evidence,direct_evidence)

    def test_history_keeps_missing_links_and_exact_original_snapshot(self):
        c=self.c
        event=dict(c.event,body='旧历史 [[gone_event]]',source_ids=['gone_message'],split_from='gone_parent')
        with c.source.store.connect() as con:
            con.execute('INSERT INTO versions VALUES(?,?,?,?,?)',('rev_audit',c.event['id'],c.sid,json.dumps(event),c.event['learned_at']))
        self.imported()
        with c.target.store.connect() as con:
            histories=[json.loads(r[0]) for r in con.execute('SELECT data FROM versions WHERE space_id=?',(c.tid,))]
        self.assertTrue(any(h['body']==event['body'] and h['source_ids']==event['source_ids'] for h in histories))
        self.assertTrue(any(r['record_kind']=='revision' and r['payload']['data']==event for r in self.evidence(c.target,c.tid)))

    def test_direct_copy_backup_failure_preserves_both_spaces(self):
        c=self.c;target=c.source.add_space(SpaceInput(name='目标'))['id']
        copier=SpaceCopier(c.source);b=CopyInput(target_space_id=target);p=copier.preview(c.sid,b)
        before=self.snapshot()
        with patch('memory_slice.transfer.verified_backup',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):copier.copy(c.sid,b.model_copy(update={'preview_token':p['preview_token']}))
        self.assertEqual(before,self.snapshot());self.assertEqual(c.source.store.list('event',target),[])

    def test_every_export_field_missing_or_null_is_compensated_or_retained(self):
        from memory_slice.migration_compensation import FIELDS, COMMON
        c=self.c
        original=c.pkg['payload']['records']
        for kind in FIELDS:
            if kind==RECOVERY:continue
            templates=[r for r in original if r['kind']==kind]
            if not templates:
                templates=[dict(kind=kind,value=dict(id='alias_test',space_id=c.sid,platform='qq',uid='42',person_id=c.source.persons(c.sid)[0]['id']))]
            for field in sorted(COMMON|FIELDS[kind]):
                if field in ('migration','migration_extra'):continue
                for action in ('remove','null'):
                    with self.subTest(kind=kind,field=field,action=action):
                        rows=copy.deepcopy(original)
                        target=next((r for r in rows if r['kind']==kind),None)
                        if target is None:target=copy.deepcopy(templates[0]);rows.append(target)
                        if action=='remove':target['value'].pop(field,None)
                        else:target['value'][field]=None
                        before=copy.deepcopy(rows)
                        normalized,history=compensate(rows,[],c.sid)
                        self.assertEqual(rows,before)
                        # Every input record has a live representation or its exact
                        # input in evidence; no malformed field silently drops content.
                        for row in rows:
                            raw=row['value']
                            unchanged=any(r['kind']==row['kind'] and r['value']==raw for r in normalized)
                            archived=any(r['kind']==RECOVERY and r['value']['record_kind']==row['kind'] and r['value']['payload']==raw for r in normalized)
                            self.assertTrue(unchanged or archived,(kind,field,action,row['kind']))

    def test_write_failure_after_recovery_insert_rolls_back(self):
        c=self.c;c.source.store.put('message',dict(c.message,role='unknown'),c.sid)
        destination=c.source.add_space(SpaceInput(name='rollback'))['id']
        copier=SpaceCopier(c.source);b=CopyInput(target_space_id=destination);p=copier.preview(c.sid,b)
        before=self.snapshot();put=c.source.store.put
        def fail(kind,*args,**kwargs):
            result=put(kind,*args,**kwargs)
            if kind==RECOVERY:raise RuntimeError('after recovery insert')
            return result
        with patch.object(c.source.store,'put',side_effect=fail):
            with self.assertRaises(RuntimeError):copier.copy(c.sid,b.model_copy(update={'preview_token':p['preview_token']}))
        self.assertEqual(self.evidence(c.source,destination),[])
        self.assertEqual(c.source.store.list('message',destination),[])
        self.assertEqual(c.source.store.list('event',destination),[])
        self.assertEqual(before,self.snapshot())

    def test_recovery_identity_collision_preserves_both_payloads(self):
        c=self.c;target=c.source.add_space(SpaceInput(name='恢复冲突'))['id']
        for sid,content in ((c.sid,'源的历史正文'),(target,'目标原有历史正文')):
            c.source.store.put(RECOVERY,dict(id='rec_'+sid,origin_id='shared_origin',status='pending',record_kind='message',payload={'content':content},issues=['角色未知']),sid)
        copier=SpaceCopier(c.source);b=CopyInput(target_space_id=target)
        for _ in range(2):
            p=copier.preview(c.sid,b);copier.copy(c.sid,b.model_copy(update={'preview_token':p['preview_token']}))
        evidence=self.evidence(c.source,target)
        bodies=[r['payload'].get('content') for r in evidence if r.get('record_kind')=='message']
        self.assertEqual(bodies.count('源的历史正文'),1)
        self.assertEqual(bodies.count('目标原有历史正文'),1)

    def test_pending_evidence_survives_message_and_group_cleanup(self):
        from memory_slice.message_retention import cleanup_messages
        from memory_slice.focus import cleanup_buffer
        c=self.c;c.source.store.put('message',dict(c.message,processed=None),c.sid)
        self.imported();before=self.evidence(c.target,c.tid)
        cleanup_messages(c.target.store,c.tid,c.target.config_for(c.tid),now='2099-01-01T00:00:00Z')
        cleanup_buffer(c.target.store,c.tid,c.target.config_for(c.tid))
        self.assertEqual(self.evidence(c.target,c.tid),before)

    def test_invalid_buffer_expiry_does_not_delete_the_message(self):
        from memory_slice.focus import cleanup_buffer
        c=self.c;c.source.store.put('message',dict(c.message,retention='buffer',expires_at=None),c.sid)
        self.imported();before=c.target.store.list('message',c.tid)
        cleanup_buffer(c.target.store,c.tid,c.target.config_for(c.tid))
        self.assertEqual(before,c.target.store.list('message',c.tid))
        self.assertEqual(before[0]['retention'],'permanent')

    def test_overwritten_historical_fragments_and_dangling_provenance_export(self):
        c=self.c
        snapshot={'id':'old_fragment','event_id':'old_event','content':'覆盖前的独有片段','source_ranges':[{'message_id':'gone','start':1,'end':3}]}
        c.source.store.put('space_copy',dict(id='copy_old',previous_fragments=[snapshot]),c.sid)
        with c.source.store.connect() as con:
            con.execute('INSERT INTO copy_identity VALUES(?,?,?,?)',(c.sid,'message','origin_gone','msg_gone'))
        self.imported()
        evidence=self.evidence(c.target,c.tid)
        self.assertTrue(any(r['payload']==snapshot for r in evidence))
        self.assertTrue(any(r['record_kind']=='origin' and r['payload']['record_id']=='msg_gone' for r in evidence))

    def test_history_normalization_is_idempotent_with_evidence(self):
        c=self.c
        rev=dict(id='rev_idempotent',event_id=c.event['id'],space_id=c.sid,data=copy.deepcopy(c.event),saved_at=c.event['learned_at'])
        first=compensate(c.pkg['payload']['records'],[rev],c.sid)
        second=compensate(*first,c.sid)
        self.assertEqual(first,second)

    def test_copy_long_agent_title_preserves_original_and_database_restores(self):
        from memory_slice.backup_validation import validate_database
        c=self.c;conversation=c.source.store.list('conversation',c.sid)[0]
        title='agent-session:'+'x'*(100-len('agent-session:'))
        c.source.store.put('conversation',dict(conversation,title=title),c.sid)
        self.imported()
        copied=c.target.store.list('conversation',c.tid)[0]
        self.assertEqual(len(copied['title']),100)
        self.assertEqual(copied['migration']['original_conversation_title'],title)
        with c.target.store.connect() as con:validate_database(con)

    def test_duplicate_recovery_ids_keep_both_records_through_repeated_import(self):
        c=self.c;package=copy.deepcopy(c.pkg)
        for i in range(2):
            package['payload']['records'].append({'kind':RECOVERY,'value':dict(id='same_recovery',space_id=c.sid,origin_id='origin_'+str(i),status='pending',record_kind='message',payload={'content':'distinct_'+str(i)},issues=['unknown'])})
        package['sha256']=digest(package['payload'])
        for _ in range(2):self.imported(package)
        evidence=self.evidence(c.target,c.tid)
        for i in range(2):
            found=[r for r in evidence if r.get('origin_id')=='origin_'+str(i)]
            self.assertEqual(len(found),1)
            self.assertEqual(found[0]['payload'],{'content':'distinct_'+str(i)})
            self.assertEqual(found[0]['migration']['original_recovery_id'],'same_recovery')
        normalized=c.importer.validate(package)
        again=compensate(normalized['records'],normalized['revisions'],c.sid)
        self.assertEqual(normalized['records'],again[0])

    def test_repeat_copy_progress_only_advances_with_matching_results(self):
        c=self.c;target=c.source.add_space(SpaceInput(name='progress'))['id']
        copier=SpaceCopier(c.source)
        def run(policy='skip'):
            b=CopyInput(target_space_id=target,conflict_policy=policy);p=copier.preview(c.sid,b)
            return copier.copy(c.sid,b.model_copy(update={'preview_token':p['preview_token']}))
        run()
        source=c.source.store.get('message',c.message['id'])
        c.source.store.put('message',dict(source,processed=True,maintenance_offset=len(source['content']),processed_at=source['at']),c.sid)
        c.source.store.put('event',dict(c.source.store.get('event',c.event['id']),body='新的整理结果'),c.sid)
        run('skip')
        self.assertFalse(c.source.store.list('message',target)[0]['processed'])
        result=run('overwrite')
        message=c.source.store.list('message',target)[0]
        self.assertTrue(message['processed']);self.assertEqual(message['maintenance_offset'],len(source['content']))
        self.assertEqual(result['counts']['advanced_messages'],1)
        c.source.store.put('message',dict(source,processed=False,maintenance_offset=0),c.sid)
        run('overwrite')
        self.assertEqual(c.source.store.list('message',target)[0],message)

    def test_file_reimport_advances_partial_progress_without_replacing_text(self):
        c=self.c;self.imported()
        c.source.store.put('message',dict(c.message,maintenance_offset=3),c.sid)
        self.imported()
        copied=c.target.store.list('message',c.tid)[0]
        self.assertEqual(copied['maintenance_offset'],3);self.assertFalse(copied['processed'])
        c.source.store.put('message',dict(c.message,processed=True,maintenance_offset=len(c.message['content'])),c.sid)
        self.imported()
        self.assertTrue(c.target.store.list('message',c.tid)[0]['processed'])
        self.assertEqual(c.target.store.list('message',c.tid)[0]['content'],c.message['content'])

    def test_different_target_text_prevents_progress_sync(self):
        c=self.c;self.imported()
        previous=c.target.store.list('message',c.tid)[0]
        c.target.store.put('message',dict(previous,content='目标空间自己的不同正文'),c.tid)
        c.source.store.put('message',dict(c.message,processed=True),c.sid)
        self.imported()
        result=c.target.store.list('message',c.tid)[0]
        self.assertFalse(result['processed']);self.assertEqual(result['content'],'目标空间自己的不同正文')

    def test_progress_write_rolls_back_if_event_copy_fails(self):
        c=self.c;self.imported()
        old_message=c.target.store.list('message',c.tid)[0]
        old_event=c.target.store.list('event',c.tid)[0]
        c.source.store.put('message',dict(c.message,processed=True),c.sid)
        c.pkg=c.exporter.export(c.sid)
        body,_=c.preview(c.body(conflict_policy='overwrite'))
        # Import writes into a temporary DB first. A failure there must leave the
        # real target's progress and event unchanged, as must a final SQL failure.
        from memory_slice.store import Store
        original=Store.put
        def fail(store,kind,*args,**kwargs):
            if kind=='event':raise RuntimeError('event copy failed after progress')
            return original(store,kind,*args,**kwargs)
        with patch.object(Store,'put',new=fail):
            with self.assertRaises(RuntimeError):c.importer.commit(body)
        self.assertEqual(c.target.store.list('message',c.tid)[0],old_message)
        self.assertEqual(c.target.store.list('event',c.tid)[0],old_event)

    def test_pending_event_prevents_marking_its_message_consumed(self):
        c=self.c;self.imported()
        c.source.store.put('message',dict(c.message,processed=True),c.sid)
        c.source.store.put('event',dict(c.event,body=''),c.sid)
        self.imported()
        self.assertFalse(c.target.store.list('message',c.tid)[0]['processed'])
        self.assertTrue(any(r['record_kind']=='event' and r['status']=='pending' for r in self.evidence(c.target,c.tid)))
