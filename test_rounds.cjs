/* Deterministic behavior checks for the exact inline controller shipped to browsers. */
const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, 'office.html'), 'utf8');
const source = html.split('// ROUNDS_CONTROLLER_START')[1].split('// ROUNDS_CONTROLLER_END')[0];
const create = vm.runInNewContext(`(() => { ${source.slice(source.indexOf('\n'))}; return createOfficeRounds; })()`);
const flush = async () => { for(let i=0;i<15;i++) await Promise.resolve(); };
const deferred = () => {let resolve; const promise=new Promise(r=>resolve=r); return {promise,resolve};};
const event = (id, to='worker', at=1000, kind='check') => ({id,from:'manager',to,at,kind});
test('office desks mirror the Creator Micro agent-key layout',()=>{
  assert.match(html,/grid-template-areas:'whiteboard key1 key2 coffee' 'key3 key4 key5 key6'/);
  assert.match(html,/row-gap:64px/);
  assert.match(html,/station\.style\.gridArea=`key\$\{i\+1\}`/);
  assert.match(html,/Office whiteboard/);
  assert.match(html,/No coffee, no workee/);
  assert.match(html,/\['corridor','Heading to the whiteboard…',0\],[\s\S]*\['board-corridor','Heading to the whiteboard…',0\],[\s\S]*\['board','Thinking at the whiteboard…',1600\],[\s\S]*\['board-corridor','Coffee break…',0\],[\s\S]*\['coffee-corridor','Coffee break…',0\],[\s\S]*\['coffee','Coffee fuels the next step…',1200\],[\s\S]*\['coffee-corridor','Back to the desk…',0\],[\s\S]*\['corridor','Back to the desk…',0\]/);
});
function harness() {
  const h={now:1000,connected:true,ids:new Set(['manager','worker','other']),log:[],pending:[],fail:false,reduced:false};
  const gate=(kind,e)=>{const d=deferred();h.log.push([kind,e?.id]);h.pending.push(d);return d.promise;};
  h.queue=create({
    now:()=>h.now,
    resolve:e=>h.ids.has(e.from)&&(e.kind==='overview'||h.ids.has(e.to))?{from:e.from,to:e.to}:null,
    valid:e=>h.connected&&h.ids.has(e.from)&&(e.kind==='overview'||h.ids.has(e.to)),
    visit:async(e)=>{h.log.push(['metadata',JSON.parse(JSON.stringify(e))]);if(h.fail)throw Error('No route');if(!h.reduced&&e.kind!=='overview')await gate('visit',e);},
    stopVisit:()=>h.log.push(['fallback']),
    speak:e=>{if(h.speechFail){h.speechFail=false;throw Error('Scene removed');}return gate('speak',e);},release:()=>h.log.push(['release']),
    home:()=>gate('home'),cancel:()=>{h.log.push(['cancel']);h.pending.splice(0).forEach(d=>d.resolve());}
  });
  h.next=async()=>{assert.ok(h.pending.length,'expected a pending animation');h.pending.shift().resolve();await flush();};
  h.kinds=()=>h.log.filter(x=>x[0]!=='metadata'&&x[0]!=='release');
  return h;
}

test('serial visits wait for arrival and speech, then return home once',async()=>{
  const h=harness();h.queue.receive([event('a'),event('b','other')]);await flush();
  assert.deepEqual(h.kinds(),[['visit','a']]);
  await h.next();assert.deepEqual(h.kinds().at(-1),['speak','a']);
  await h.next();assert.deepEqual(h.kinds().at(-1),['visit','b']);
  await h.next();await h.next();assert.deepEqual(h.kinds().at(-1),['home',undefined]);await h.next();
});
test('new check arriving during the return waits without overlapping routes',async()=>{
  const h=harness();h.queue.receive([event('a')]);await h.next();await h.next();
  h.queue.receive([event('b','other')]);await flush();assert.equal(h.kinds().at(-1)[0],'home');
  await h.next();assert.deepEqual(h.kinds().at(-1),['visit','b']);h.queue.clear();await flush();
});
test('repeated snapshots, rapid identical pairs, invalid and stale events do not replay',async()=>{
  const h=harness(),a=event('a');h.queue.receive([a]);
  h.queue.receive([a,event('duplicate-pair'),event('old','other',900),event('future','other',1010),event('self','manager'),null]);
  await h.next();await h.next();await h.next();
  assert.deepEqual(h.kinds().filter(x=>x[0]==='visit'),[['visit','a']]);
});
test('checks that expire in the queue or while walking never speak',async()=>{
  const h=harness();h.queue.receive([event('a'),event('b','other')]);h.now=1046;
  await h.next();assert.equal(h.kinds().filter(x=>x[0]==='speak').length,0);
  assert.equal(h.kinds().filter(x=>x[0]==='visit').length,1);await h.next();
});
test('disconnect or changed participant cancels the visit and retains replay protection',async()=>{
  const h=harness(),a=event('a');h.queue.receive([a,event('b','other')]);
  h.ids.delete('worker');h.queue.sync();await flush();
  assert.equal(h.kinds().filter(x=>x[0]==='speak').length,0);
  h.ids.add('worker');h.queue.receive([a]);await flush();
  assert.equal(h.kinds().filter(x=>x[0]==='visit').length,1);h.queue.clear();await flush();
});
test('overview invents no desk visit and route failure still shows safe bubbles',async()=>{
  const h=harness();h.queue.receive([event('overview',null,1000,'overview')]);await flush();
  assert.deepEqual(h.kinds(),[['speak','overview']]);await h.next();await h.next();
  h.fail=true;h.queue.receive([event('failed')]);await flush();
  assert.deepEqual(h.kinds().slice(-2),[['fallback'],['speak','failed']]);h.queue.clear();await flush();
});
test('animation receives only whitelisted metadata',async()=>{
  const h=harness();h.queue.receive([{...event('private'),prompt:'PRIVATE PROMPT',result:'PRIVATE RESULT',content:{secret:true}}]);await flush();
  const metadata=h.log.find(x=>x[0]==='metadata')[1];
  assert.deepEqual(Object.keys(metadata).sort(),['at','from','id','kind','to']);
  assert.ok(!JSON.stringify(h.log).includes('PRIVATE'));h.queue.clear();await flush();
});
test('an overloaded queue keeps at most twelve waiting events',async()=>{
  const h=harness();h.queue.receive([event('first')]);
  const batch=Array.from({length:20},(_,i)=>event(`queued-${i}`,`worker-${i}`));
  batch.forEach(e=>h.ids.add(e.to));h.queue.receive(batch);await flush();
  await h.next();await h.next();
  assert.deepEqual(h.kinds().at(-1),['visit','queued-8']);h.queue.clear();await flush();
});
test('an interrupted scene releases animation ownership and permits the next visit',async()=>{
  const h=harness();h.speechFail=true;h.queue.receive([event('broken'),event('next','other')]);
  await h.next();assert.ok(h.kinds().some(x=>x[0]==='cancel'));
  assert.deepEqual(h.kinds().at(-1),['visit','next']);h.queue.clear();await flush();
});

// A minimal DOM and virtual clock exercise the whole office, including snapshot/motion ownership.
function office({reduced=false,board=false}={}) {
  let now=1000000,seq=0;const timers=new Map(),elements=[];
  const schedule=(fn,delay=0)=>{const id=++seq;timers.set(id,{fn,at:now+delay});return id;};
  class Element {
    constructor(tag='div',cls='') {this.tag=tag;this.className=cls;this.children=[];this.dataset={};this.attributes={};this.isConnected=true;this.value='Example';this.textContent='';this.offsetWidth=120;this.offsetHeight=40;this.style={setProperty(k,v){this[k]=v;}};this.classList={add:c=>{this.className+=' '+c;}};elements.push(this);}
    append(...els){for(const el of els){if(el.parent)el.parent.children=el.parent.children.filter(x=>x!==el);el.parent=this;this.children.push(el);}}
    replaceChildren(...els){this.children=[];this.append(...els);}
    setAttribute(k,v){this.attributes[k]=v;}
    addEventListener(){}
    querySelector(selector){const match=e=>selector.startsWith('.')?e.className.split(' ').includes(selector.slice(1)):e.tag===selector;for(const child of this.children){if(match(child))return child;const nested=child.querySelector(selector);if(nested)return nested;}return null;}
    querySelectorAll(selector){const match=e=>selector.startsWith('.')?e.className.split(' ').includes(selector.slice(1)):e.tag===selector;return this.children.flatMap(child=>[...(match(child)?[child]:[]),...child.querySelectorAll(selector)]);}
    set innerHTML(value){this.children=[];for(const match of value.matchAll(/<(\w+)[^>]*class="([^"]+)"/g))this.append(new Element(match[1],match[2]));}
    getBoundingClientRect(){let left=0,top=0,width=840,height=560;
      if(this.className==='station'){const i=stations().indexOf(this),slot=[[1,0],[2,0],[0,1],[1,1],[2,1],[3,1]][i];left=20+slot[0]*200;top=84+slot[1]*286;width=190;height=222;}
      else if(this.className.split(' ').includes('office-whiteboard')){left=20;top=84;width=190;height=222;}
      else if(this.className.split(' ').includes('coffee-space')){left=620;top=84;width=190;height=222;}
      else if(this.className.split(' ').includes('robot')){const m=(this.style.transform||'').match(/translate\(([-\d]+)px,([-\d]+)px\)/);if(m){left=+m[1];top=+m[2];}width=38;height=48;}
      return {left,top,width,height,right:left+width,bottom:top+height};}
  }
  const root=new Element(),windowEl=new Element('div','office-window'),room=new Element('div','office-room');root.append(windowEl);windowEl.append(room);
  room.append(new Element('div','office-desks'));
  const sign=new Element('div','open-sign');sign.append(new Element('small'));room.append(sign);
  for(const cls of ['keypad','task-input','compose-title','send-task','selection-detail','office-tag'])windowEl.append(new Element('div',cls));windowEl.append(new Element('form'));
  if(board) {
    const hub=new Element('section','job-hub');windowEl.append(hub);
    for(const cls of ['job-list','board-health','needs-list','needs-count','folders','results-count','result-detail'])hub.append(new Element('div',cls));
  }
  const stations=()=>elements.filter(e=>e.className==='station');
  const robots=()=>stations().map((_,i)=>elements.find(e=>e.className===`robot robot-${i+1}`));
  const motion={matches:reduced,addEventListener:(_,fn)=>motion.change=fn};
  const context={document:{getElementById:()=>root,createElement:tag=>new Element(tag)},window:{matchMedia:()=>motion,addEventListener(){}},Date:{now:()=>now,parse:Date.parse},setTimeout:schedule,clearTimeout:id=>timers.delete(id),requestAnimationFrame:fn=>schedule(fn,16),cancelAnimationFrame:id=>timers.delete(id),ResizeObserver:class{observe(){}}};
  vm.runInNewContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],context);
  const snapshot=(communications=[],states=['working','working','idle','idle','idle','idle'])=>root.agentOffice.applySnapshot({connected:true,slots:states.map((state,i)=>({avatar:i,key:i+1,id:i===0?'manager':i===1?'worker':`other-${i}`,title:`Agent ${i}`,state})),communications});
  const advance=async ms=>{const end=now+ms;await flush();for(let steps=0;steps<10000;steps++){const next=[...timers].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;now=next[1].at;timers.delete(next[0]);next[1].fn();await flush();}now=end;await flush();};
  const bubbles=()=>root.querySelector('.comms-layer').children;
  return {root,robots,stations,snapshot,advance,bubbles,motion};
}
test('whole office keeps a busy manager on the route across status polls and resumes both jobs',async()=>{
  const h=office();h.snapshot([event('round')]);await h.advance(500);
  const [manager,worker]=h.robots();assert.equal(manager.dataset.rounds,'true');assert.equal(h.bubbles().length,0);
  const route=manager.style.transform;h.snapshot([event('round')],['done','working','idle','idle','idle','idle']);
  assert.equal(manager.style.transform,route,'completed snapshot must not pull Scout home during a round');
  await h.advance(6000);assert.equal(h.bubbles().length,2);assert.equal(manager.dataset.walking,'false');assert.equal(worker.dataset.walking,'false');
  assert.equal(manager.dataset.state,'done','real status remains authoritative while walking');
  await h.advance(12000);assert.equal(h.bubbles().length,0);assert.equal(manager.dataset.rounds,'false');assert.equal(worker.dataset.state,'working');
});
test('reduced motion uses stationary bubbles and no clipboard walking',async()=>{
  const h=office({reduced:true});h.snapshot([event('round')],['idle','idle','idle','idle','idle','idle']);await h.advance(1);
  assert.equal(h.bubbles().length,2);assert.ok(h.robots().every(r=>r.dataset.walking==='false'));
  assert.ok(h.robots().every(r=>r.style['--walk-duration']==='0ms'));
  await h.advance(4300);assert.equal(h.robots()[0].dataset.rounds,'false');
  assert.match(html,/@media\(prefers-reduced-motion:reduce\)[^}]*animation:none!important/);
});
test('disconnect cancels in-flight movement and clears clipboard and speech',async()=>{
  const h=office();h.snapshot([event('round')]);await h.advance(500);
  h.root.agentOffice.applySnapshot({connected:false,slots:[]});await h.advance(10000);
  assert.equal(h.bubbles().length,0);assert.equal(h.robots()[0].dataset.rounds,'false');
  assert.ok(h.robots().every(r=>r.dataset.walking==='false'));
});
test('switching reduced motion on mid-round cancels movement without replay',async()=>{
  const h=office();h.snapshot([event('round')],['idle','idle','idle','idle','idle','idle']);await h.advance(300);
  h.motion.matches=true;h.motion.change();await h.advance(7000);h.snapshot([event('round')]);await h.advance(1);
  assert.equal(h.bubbles().length,0);assert.equal(h.robots()[0].dataset.rounds,'false');
});
test('manager needing input retains amber state and stationary speech fallback',async()=>{
  const h=office();h.snapshot([event('round')],['waiting','idle','idle','idle','idle','idle']);await h.advance(1);
  assert.equal(h.bubbles().length,2);assert.equal(h.robots()[0].dataset.state,'waiting');
  assert.equal(h.robots()[0].dataset.walking,'false');
  await h.advance(4500);assert.equal(h.robots()[0].dataset.rounds,'false');
});

function boardFixture() {
  return {source:{available:true,stale:false},agents:[{id:'safe-manager',label:'Scout',key:1,assignment:null}],needsYou:[],results:[]};
}
test('neon OPEN sign follows live work, not unread results or waiting approvals',()=>{
  const h=office({reduced:true}),sign=h.root.querySelector('.open-sign');
  h.root.agentOffice.applySnapshot({connected:false,slots:[]});
  assert.equal(sign.querySelector('small').textContent,'Reconnecting…','initial disconnect is not an empty office');
  h.snapshot([],['idle','working','idle','idle','idle','idle']);
  assert.equal(sign.dataset.open,'true');assert.equal(sign.querySelector('small').textContent,'Agents at work');
  h.snapshot([],['idle','done','waiting','idle','idle','idle']);
  assert.equal(sign.dataset.open,'false');assert.equal(sign.querySelector('small').textContent,'Off duty');
  h.snapshot([],['working','idle','idle','idle','idle','idle']);
  assert.equal(sign.dataset.open,'true','manager direct work also lights the sign');
  h.root.agentOffice.applySnapshot({connected:false,slots:[]});
  assert.equal(sign.dataset.open,'false');assert.equal(sign.querySelector('small').textContent,'Reconnecting…');
});
function applyBoard(h,state,board=boardFixture(),connected=true,key=1) {
  h.root.agentOffice.applySnapshot({connected,slots:[{avatar:0,key,id:'manager',title:'Scout',state}],jobBoard:board});
  const row=h.root.querySelector('.job-list').children[0];
  return {stage:row.dataset.stage,title:row.querySelector('.job-title').textContent,chip:row.querySelector('.stage-chip').textContent};
}
test('whiteboard without an assignment follows live activity through each transition',()=>{
  const h=office({reduced:true,board:true});
  for(const [state,stage,title,chip] of [
    ['working','working','Active · no project assignment listed','Working'],
    ['waiting','waiting','Needs your input or approval','Needs you'],
    ['done','done','Response complete · unread','Complete'],
    ['idle','ready','On a break ☕ · ready for the next assignment','On a break'],
    ['error','error','Task reported an error','Error'],
    ['unknown','unknown','Activity status unavailable','Unavailable'],
    ['working','working','Active · no project assignment listed','Working']
  ]) {
    assert.deepEqual(applyBoard(h,state),{stage,title,chip});
    assert.equal(h.root.querySelector('.room-board-count').textContent,state==='working'?'1 active':'0 active');
    assert.equal(h.robots()[0].dataset.state,state);
    assert.ok(h.root.querySelector('.job-list').children[0].attributes['aria-label'].includes(title));
  }
});
test('whiteboard keeps explicitly tracked assignments and stages',()=>{
  const h=office({reduced:true,board:true}),board=boardFixture();
  board.agents[0].assignment={title:'Office fixes',stage:'testing'};
  assert.deepEqual(applyBoard(h,'working',board),{stage:'testing',title:'Office fixes',chip:'Testing'});
  assert.equal(h.root.querySelector('.room-board-count').textContent,'1 active');
  assert.equal(applyBoard(h,'idle',board).title,'Office fixes');
  assert.equal(h.root.querySelector('.room-board-count').textContent,'0 active');
});
test('disconnect, missing slots, unknown states and unpinned agents never imply readiness',()=>{
  const h=office({reduced:true,board:true});
  applyBoard(h,'working');
  assert.equal(applyBoard(h,'idle',boardFixture(),false).chip,'Reconnecting…');
  assert.equal(h.root.querySelector('.room-board-count').textContent,'Reconnecting…');
  assert.equal(applyBoard(h,undefined).chip,'Unavailable');
  assert.equal(applyBoard(h,'unrecognized').chip,'Unavailable');
  assert.equal(applyBoard(h,'idle',boardFixture(),true,2).chip,'Unavailable');
});
test('stale or unavailable assignment data cannot declare an idle agent ready',()=>{
  const h=office({reduced:true,board:true});
  for(const source of [{available:true,stale:true},{available:false,stale:false}]) {
    const board=boardFixture();board.source=source;
    assert.equal(applyBoard(h,'idle',board).title,'Current assignment unavailable');
    assert.equal(applyBoard(h,'working',board).chip,'Working');
  }
});
test('presentation hides board details and absent board data stays unavailable',()=>{
  const h=office({reduced:true,board:true});
  h.root.agentOffice.applySnapshot({connected:true,slots:[],jobBoard:{presentation:true}});
  assert.equal(h.root.querySelector('.job-hub').hidden,true);
  h.root.agentOffice.applySnapshot({connected:false,slots:[]});
  assert.equal(h.root.querySelector('.board-health').textContent,'Unavailable');
});

function watchSnapshot(h,{state='idle',watch={},communications=[],board=boardFixture(),key=1}={}) {
  board.agents[0].role='manager';board.agents[0].key=key;
  board.source.updatedAt='1970-01-01T00:15:00Z';
  h.root.agentOffice.applySnapshot({connected:true,managerWatch:{enabled:true,status:'watching',lastLocalCheckAt:1000,...watch},
    slots:[{avatar:0,key,id:'manager',title:'Scout',state},{avatar:1,key:key===2?1:2,id:'worker',title:'Bolt',state:'working'}],jobBoard:board,communications});
  return {label:h.stations()[0].querySelector('.station-state').textContent,note:h.stations()[0].querySelector('.manager-check').textContent};
}
test('healthy local watch labels only the configured manager and keeps real colors',()=>{
  const h=office({reduced:true,board:true});
  const view=watchSnapshot(h,{key:2});
  assert.equal(view.label,'Watching team');assert.match(view.note,/^Last checked /);
  assert.equal(h.root.querySelector('.job-list').children[0].querySelector('.job-title').textContent,'Watching team');
  assert.equal(h.robots()[0].dataset.state,'idle');
  assert.equal(h.stations()[1].querySelector('.station-state').textContent,'Working');
  assert.equal(h.stations()[1].querySelector('.manager-check').hidden,true);
  assert.match(h.stations()[0].attributes['aria-label'],/Watching team, Last checked/);
});
test('direct manager work, unread results and input needs remain truthful',()=>{
  const h=office({reduced:true,board:true});
  for(const [state,label] of [['working','Working'],['done','Complete · unread'],['waiting','Needs your input'],['error','Error'],['unknown','Status unavailable']]) {
    const view=watchSnapshot(h,{state,watch:{status:'manager-busy'}});
    assert.equal(view.label,label);assert.match(view.note,/Watching team · Last checked/);
    assert.equal(h.robots()[0].dataset.state,state);
  }
  const board=boardFixture();board.agents[0].assignment={title:'User-approved project',stage:'testing'};
  watchSnapshot(h,{state:'working',board});
  assert.equal(h.root.querySelector('.job-list').children[0].querySelector('.job-title').textContent,'User-approved project');
});
test('Checking in follows actual round events and returns to Watching team',async()=>{
  const h=office({reduced:true,board:true});
  assert.equal(watchSnapshot(h,{communications:[event('real-check')]}).label,'Checking in');
  assert.equal(h.robots()[0].dataset.state,'idle');
  await h.advance(5000);
  assert.equal(h.stations()[0].querySelector('.station-state').textContent,'Watching team');
  watchSnapshot(h,{communications:[event('stale-check','worker',900)]});
  assert.equal(h.stations()[0].querySelector('.station-state').textContent,'Watching team');
});
test('paused, disabled, stale and unsupported watch states never claim monitoring is healthy',()=>{
  const h=office({reduced:true,board:true});
  for(const [watch,label] of [
    [{status:'paused-error'},'Watch paused'],[{enabled:false},'Watch disabled'],
    [{status:'configuration-changed'},'Watch disabled'],[{status:'disabled'},'Watch disabled'],
    [{status:'starting'},'Watch starting'],[{status:'unavailable'},'Watch unavailable'],
    [{status:'new-unknown-status'},'Watch unavailable'],[{lastLocalCheckAt:950},'Watch unavailable'],
    [{lastLocalCheckAt:1007},'Watch unavailable'],[{lastLocalCheckAt:null},'Watch unavailable']
  ]) assert.equal(watchSnapshot(h,{watch}).label,label);
  const board=boardFixture();board.source.available=false;
  assert.equal(watchSnapshot(h,{board}).note,'Last check unavailable');
});
test('recovery and continuation holds have explicit private-detail-free labels',()=>{
  const h=office({reduced:true,board:true});
  assert.equal(watchSnapshot(h,{watch:{continuityStatus:'recovery-needed'}}).label,'Recovery needed');
  assert.equal(watchSnapshot(h,{watch:{continuityStatus:'continuation-limit'}}).label,'Resume needs attention');
  assert.equal(watchSnapshot(h,{watch:{continuityStatus:null}}).label,'Watching team');
});
test('disconnect, absent watch and private presentation clear manager details',()=>{
  const h=office({reduced:true,board:true});watchSnapshot(h);
  h.root.agentOffice.applySnapshot({connected:false,slots:[]});
  assert.equal(h.stations()[0].querySelector('.manager-check').hidden,true);
  assert.equal(h.stations()[0].querySelector('.station-state').textContent,'Reconnecting…');
  applyBoard(h,'idle');
  assert.equal(h.stations()[0].querySelector('.station-state').textContent,'On a break ☕');
  watchSnapshot(h);
  h.root.agentOffice.applySnapshot({connected:true,slots:[{avatar:0,key:1,id:'presentation-slot-1',title:'Agent 1',state:'idle'}],jobBoard:{presentation:true}});
  assert.equal(h.stations()[0].querySelector('.manager-check').hidden,true);
  assert.equal(h.stations()[0].querySelector('.station-state').textContent,'On a break ☕');
});

test('break labels require confirmed idle; reconnection never disguises unknown activity',()=>{
  const h=office({reduced:true,board:true}),sign=h.root.querySelector('.open-sign');
  applyBoard(h,'idle');
  assert.equal(h.stations()[0].querySelector('.station-state').textContent,'On a break ☕');
  assert.match(h.stations()[0].attributes['aria-label'],/On a break ☕/);
  applyBoard(h,'unknown');
  assert.equal(h.stations()[0].querySelector('.station-state').textContent,'Status unavailable');
  assert.equal(sign.querySelector('small').textContent,'Status unavailable');
  applyBoard(h,'idle',boardFixture(),false);
  assert.equal(h.stations()[0].dataset.state,'unknown');
  assert.equal(h.stations()[0].querySelector('.station-state').textContent,'Reconnecting…');
  assert.equal(sign.attributes['aria-label'],'Office reconnecting');
  assert.equal(h.root.querySelector('.office-tag').textContent,'Reconnecting… · live feed interrupted');
  assert.equal(h.root.querySelector('.job-list').children[0].querySelector('.job-title').textContent,'Live connection interrupted');
  applyBoard(h,'working');
  assert.equal(h.stations()[0].querySelector('.station-state').textContent,'Working');
  assert.equal(sign.dataset.open,'true');
});
