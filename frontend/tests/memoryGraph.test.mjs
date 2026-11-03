import test from 'node:test';
import assert from 'node:assert/strict';
import {layoutMemories} from '../.test-build/memoryGraph.js';
const event=(id,date,links=[])=>({id,learned_at:date,occurred_at:null,links:links.map(target_id=>({target_id,relation:'后续',strength:.8}))});
const positions=result=>result.nodes.map(n=>[n.event.id,n.x,n.y]);
test('graph handles empty, isolated and dangling links without invented edges',()=>{
 assert.equal(layoutMemories([]).nodes.length,0);
 const one=layoutMemories([event('a','2026-11-01',['a','missing'])]);
 assert.equal(one.edges.length,0);assert.deepEqual(positions(one),[['a',450,275]]);
});
test('graph layout is deterministic regardless of API ordering and preserves real edges',()=>{
 const rows=[event('b','2026-11-02',['a']),event('a','2026-11-01'),event('c','2026-11-03')];
 const first=layoutMemories(rows),second=layoutMemories([...rows].reverse());
 assert.deepEqual(positions(first),positions(second));assert.equal(first.edges.length,1);
 assert.equal(first.edges[0].source.event.id,'b');assert.equal(first.edges[0].target.event.id,'a');
 for(const n of first.nodes){assert.ok(n.x>=100 && n.x<=800);assert.ok(n.y>=80 && n.y<=470);}
});
test('time layout orders date lanes and separates events on the same date',()=>{
 const result=layoutMemories([event('c','2026-11-03'),event('b','2026-11-01'),event('a','2026-11-01')],true);
 const [a,b,c]=result.nodes;assert.equal(a.x,b.x);assert.notEqual(a.y,b.y);assert.ok(c.x>a.x);
 assert.equal(result.lanes.length,2);
});
test('dense graph has finite bounded positions',()=>{
 const rows=Array.from({length:250},(_,i)=>event(String(i),'2026-11-01',i?[String(i-1)]:[]));
 const result=layoutMemories(rows);assert.equal(result.edges.length,249);
 for(const n of result.nodes){assert.ok(Number.isFinite(n.x)&&Number.isFinite(n.y));assert.ok(n.x>=100 && n.x<=800);}
});
