/* The exact shipped page-shell polling loop, with deterministic browser events. */
const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const shell = fs.readFileSync(path.join(__dirname,'page-shell.html'),'utf8');
const source = shell.match(/<script>([\s\S]*?)<\/script>/)[1];
const flush = async () => { for(let i=0;i<20;i++) await Promise.resolve(); };
const snapshot = (state='idle') => ({connected:true,slots:[{state}],managerWatch:{status:'watching'}});

function harness({hidden=false,presentation=false,honorAbort=true}={}) {
  let now=0,sequence=0;
  const timers=new Map(),requests=[],snapshots=[],note={textContent:''};
  const eventTarget=()=>{
    const listeners=new Map();
    return {
      addEventListener(type,fn,options={}) { const list=listeners.get(type)||[];list.push({fn,once:options.once});listeners.set(type,list); },
      emit(type,props={}) { for(const entry of [...(listeners.get(type)||[])]) {entry.fn(props);if(entry.once)listeners.set(type,listeners.get(type).filter(item=>item!==entry));} }
    };
  };
  const document={...eventTarget(),hidden,documentElement:{dataset:{}},getElementById:id=>id==='agent-office'?{agentOffice:{applySnapshot:s=>snapshots.push(s)}}:note};
  const window={...eventTarget(),location:{pathname:presentation?'/presentation':'/'}};
  const schedule=(fn,delay=0)=>{const id=++sequence;timers.set(id,{fn,at:now+delay});return id;};
  const fetch=(url,options)=>new Promise((resolve,reject)=>{
    requests.push({url,options,resolve,reject});
    if(honorAbort) options.signal.addEventListener('abort',()=>reject(Error('Aborted')),{once:true});
  });
  vm.runInNewContext(source,{window,document,fetch,AbortController,setTimeout:schedule,clearTimeout:id=>timers.delete(id)});
  const advance=async ms=>{
    const end=now+ms;await flush();
    for(let steps=0;steps<10000;steps++) {
      const next=[...timers].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];
      if(!next) break;
      now=next[1].at;timers.delete(next[0]);next[1].fn();await flush();
    }
    now=end;await flush();
  };
  return {window,document,requests,snapshots,timers,note,advance,
    last:()=>snapshots.at(-1),
    reply:async(i,data=snapshot())=>{requests[i].resolve({ok:true,json:async()=>data});await flush();},
    fail:async i=>{requests[i].reject(Error('Offline'));await flush();},
    visibility:async hidden=>{document.hidden=hidden;document.emit('visibilitychange');await flush();},
    event:async type=>{window.emit(type,{persisted:true});await flush();}
  };
}

test('starts once, never caches snapshots, then polls once per second',async()=>{
  const h=harness();assert.equal(h.requests.length,1);assert.equal(h.last().connected,false);
  assert.equal(h.requests[0].url,'/api/state');assert.equal(h.requests[0].options.cache,'no-store');
  await h.reply(0);assert.equal(h.last().slots[0].state,'idle');assert.equal(h.timers.size,1);
  await h.advance(999);assert.equal(h.requests.length,1);
  await h.advance(1);assert.equal(h.requests.length,2);
});

test('transient failure clears live status and recovers on the next poll',async()=>{
  const h=harness();await h.reply(0,snapshot('working'));assert.match(h.note.textContent,/Local manager watch/);
  await h.advance(1000);await h.fail(1);
  assert.equal(h.last().connected,false);assert.equal(h.note.textContent,'');
  await h.advance(1000);await h.reply(2);assert.equal(h.last().connected,true);assert.equal(h.timers.size,1);
});

test('a disconnected cached page resumes after every hide/show cycle',async()=>{
  const h=harness();await h.fail(0);
  for(let i=0;i<3;i++) {
    await h.event('pagehide');assert.equal(h.timers.size,0);
    const count=h.requests.length;await h.advance(10000);assert.equal(h.requests.length,count);
    await h.event('pageshow');assert.equal(h.requests.length,count+1);
    await h.reply(count);assert.equal(h.last().connected,true);assert.equal(h.timers.size,1);
  }
});

test('background tabs pause requests and refresh immediately when visible',async()=>{
  const h=harness();await h.reply(0,snapshot('working'));
  await h.visibility(true);assert.equal(h.timers.size,0);
  await h.advance(60000);assert.equal(h.requests.length,1);
  await h.visibility(false);assert.equal(h.requests.length,2);assert.equal(h.last().connected,false);
  await h.reply(1);assert.equal(h.last().connected,true);
});

test('initially hidden pages wait for visibility, including hidden pageshow/online events',async()=>{
  const h=harness({hidden:true});assert.equal(h.requests.length,0);
  await h.event('pageshow');await h.event('online');assert.equal(h.requests.length,0);
  await h.visibility(false);assert.equal(h.requests.length,1);
});

test('pageshow, visible and online events never duplicate in-flight polling',async()=>{
  const h=harness();
  for(let i=0;i<4;i++){await h.event('pageshow');await h.event('online');await h.visibility(false);}
  assert.equal(h.requests.length,1);assert.equal(h.timers.size,1,'only the request deadline');
  await h.reply(0);await h.event('online');assert.equal(h.requests.length,2);
  await h.reply(1);await h.advance(1000);assert.equal(h.requests.length,3);
});

test('hide aborts an in-flight request without letting its rejection clear newer state',async()=>{
  const h=harness();await h.event('pagehide');assert.equal(h.requests[0].options.signal.aborted,true);
  assert.equal(h.timers.size,0);await h.event('pageshow');await h.reply(1,snapshot('working'));
  assert.equal(h.last().slots[0].state,'working');assert.equal(h.timers.size,1);
});

for(const staleResult of ['success','failure']) test(`late ${staleResult} from an aborted request cannot repaint or schedule another loop`,async()=>{
  const h=harness({honorAbort:false});await h.event('pagehide');await h.event('pageshow');
  await h.reply(1,snapshot('working'));const count=h.snapshots.length;
  if(staleResult==='success') await h.reply(0,snapshot('idle'));else await h.fail(0);
  assert.equal(h.snapshots.length,count);assert.equal(h.timers.size,1);
  await h.advance(1000);assert.equal(h.requests.length,3);
});

test('late JSON parsing after a pause cannot repaint the restored page',async()=>{
  const h=harness();let finishJson;
  h.requests[0].resolve({ok:true,json:()=>new Promise(resolve=>{finishJson=resolve;})});await flush();
  await h.event('pagehide');await h.event('pageshow');await h.reply(1,snapshot('working'));
  finishJson(snapshot('idle'));await flush();assert.equal(h.last().slots[0].state,'working');assert.equal(h.timers.size,1);
});

test('four-second request deadline disconnects and retries without AbortSignal.timeout',async()=>{
  const h=harness();await h.advance(3999);assert.equal(h.requests[0].options.signal.aborted,false);
  await h.advance(1);assert.equal(h.requests[0].options.signal.aborted,true);assert.equal(h.last().connected,false);
  await h.advance(1000);assert.equal(h.requests.length,2);await h.reply(1);assert.equal(h.last().connected,true);
});

test('a response arriving after its timeout cannot claim live activity',async()=>{
  const h=harness({honorAbort:false});await h.advance(4000);await h.reply(0,snapshot('working'));
  assert.equal(h.last().connected,false);assert.equal(h.timers.size,1);
});

for(const bad of ['http','json','null','missing-slots','nonboolean-connected']) test(`${bad} response cannot masquerade as idle`,async()=>{
  const h=harness();
  if(bad==='http') h.requests[0].resolve({ok:false});
  else if(bad==='json') h.requests[0].resolve({ok:true,json:async()=>{throw Error('invalid JSON');}});
  else await h.reply(0,bad==='null'?null:bad==='missing-slots'?{connected:true}:{connected:'yes',slots:[]});
  await flush();assert.equal(h.last().connected,false);assert.equal(h.timers.size,1);
});

test('presentation keeps its redacted API route across reconnection',async()=>{
  const h=harness({presentation:true});assert.equal(h.document.documentElement.dataset.presentation,'true');
  assert.equal(h.document.title,'Agent Office — presentation');assert.equal(h.requests[0].url,'/api/state?presentation=1');
  await h.fail(0);await h.event('pagehide');await h.event('pageshow');
  assert.equal(h.requests[1].url,'/api/state?presentation=1');
});

for(const [watch,expected] of [
  [{status:'manual-required'},/Manual coordination.*No automatic prompts/],
  [{status:'state-limit'},/Saved state is full.*Keep the saved receipts/],
  [{status:'idle',continuityStatus:'recovery-needed'},/Recovery needs your review.*No automatic recovery/],
  [{status:'paused-error',continuityStatus:'recovery-needed'},/automatic rounds are paused/],
  [{status:'configuration-changed'},/configuration changed.*stopped/]
]) test(`watch notice explains ${watch.status} ${watch.continuityStatus||''}`,async()=>{
  const h=harness();await h.reply(0,{...snapshot(),managerWatch:watch});
  assert.match(h.note.textContent,expected);
});
