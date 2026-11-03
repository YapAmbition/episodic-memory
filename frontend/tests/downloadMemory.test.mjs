import test from 'node:test';
import assert from 'node:assert/strict';

globalThis.window={location:{hash:''},dispatchEvent:()=>{}};
globalThis.localStorage={getItem:()=>null};
const {downloadMemory}=await import('../.test-build/api.js');

test('export displays backend validation reason and HTTP status',async()=>{
  const original=globalThis.fetch;
  try {
    globalThis.fetch=async()=>new Response(JSON.stringify({detail:'关联指向缺失记录：example'}),{status:422});
    await assert.rejects(downloadMemory('space'),/HTTP 422.*关联指向缺失记录：example/);
  } finally {globalThis.fetch=original;}
});
test('export authentication failure requests login',async()=>{
  const original=globalThis.fetch;const dispatch=window.dispatchEvent;let called=false;
  try {
    window.dispatchEvent=event=>{called=event.type==='memory-slice-auth'};
    globalThis.fetch=async()=>new Response(JSON.stringify({detail:'令牌无效'}),{status:401});
    await assert.rejects(downloadMemory('space'),/HTTP 401.*令牌无效/);
    assert.equal(called,true);
  } finally {globalThis.fetch=original;window.dispatchEvent=dispatch;}
});
test('export non-JSON error keeps status and hides HTML',async()=>{
  const original=globalThis.fetch;
  try {
    globalThis.fetch=async()=>new Response('<html>proxy error</html>',{status:502});
    await assert.rejects(downloadMemory('space'),error=>error.message.includes('HTTP 502')&&!error.message.includes('<html>'));
  } finally {globalThis.fetch=original;}
});
