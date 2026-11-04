import test from 'node:test';
import assert from 'node:assert/strict';
import {prepareMemories} from '../.test-build/memoryGraph.js';
const event=(id,date,links=[],title=id)=>({id,title,body:'',learned_at:date,occurred_at:null,links:links.map(target_id=>({target_id,relation:'后续',strength:.8}))});
test('empty, isolated and dangling links do not invent edges or mutate records',()=>{
 assert.equal(prepareMemories([]).nodes.length,0);
 const input=[event('a','2026-11-01',['a','missing'])],copy=structuredClone(input);
 assert.equal(prepareMemories(input).edges.length,0);assert.deepEqual(input,copy);
});
test('graph preparation is deterministic regardless of API ordering',()=>{
 const rows=[event('b','2026-11-02',['a']),event('a','2026-11-01'),event('c','2026-11-03')];
 const first=prepareMemories(rows),second=prepareMemories([...rows].reverse());
 assert.deepEqual(first.nodes,second.nodes);assert.equal(first.edges.length,1);
 assert.equal(first.edges[0].source,'b');assert.equal(first.edges[0].target,'a');
});
test('time coordinates grow instead of squeezing dates and crowded days',()=>{
 const rows=Array.from({length:40},(_,i)=>event(String(i),'2026-11-01'));
 const result=prepareMemories([...rows,event('later','2026-11-03')]);
 const today=result.nodes.filter(n=>n.event.id!=='later');
 assert.equal(new Set(today.map(n=>n.timeY)).size,40);
 assert.equal(Math.max(...today.map(n=>n.timeY)),39*122);
 assert.equal(result.nodes.find(n=>n.event.id==='later').timeX,290);
});
test('search includes incoming and outgoing neighbours but not unrelated events',()=>{
 const result=prepareMemories([event('a','2026-11-01',['b'],'面包'),event('b','2026-11-02'),event('c','2026-11-03',['a']),event('d','2026-11-04')],'面包');
 assert.deepEqual(new Set(result.nodes.map(n=>n.event.id)),new Set(['a','b','c']));
 assert.equal(result.matches,1);assert.equal(result.edges.length,2);
 assert.equal(prepareMemories(result.nodes.map(n=>n.event),'不存在').nodes.length,0);
});
test('cap keeps search matches first, reports omitted records, no dangling edges',()=>{
 const rows=Array.from({length:500},(_,i)=>event(String(i),'2026-11-01',i?[String(i-1)]:[]));
 const result=prepareMemories(rows);assert.equal(result.nodes.length,400);assert.equal(result.omitted,100);
 const ids=new Set(result.nodes.map(n=>n.event.id));assert.ok(result.edges.every(e=>ids.has(e.source)&&ids.has(e.target)));
 const search=prepareMemories([event('a','2026-11-01',['b'],'目标'),event('b','2026-11-02')],'目标',1);
 assert.equal(search.nodes[0].event.id,'a');assert.equal(search.omitted,1);
});

test('same-day cards retain event time order instead of ID order',()=>{
 const result=prepareMemories([event('a','2026-11-01T18:00:00Z'),event('z','2026-11-01T09:00:00Z')]);
 assert.deepEqual(result.nodes.map(n=>n.event.id),['z','a']);
});
