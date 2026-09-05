const jobs=[
 {id:'scout',name:'Scout',key:'1',status:'working',stage:'Researching',objective:'Compare CMS options',actionItems:[],summary:'',links:[]},
 {id:'aimee',name:'Aimee',key:'4',status:'waiting',stage:'Waiting',objective:'Family guide publish scope',actionItems:['Choose private or shared'],summary:'',links:[]},
 {id:'forge',name:'Forge',key:'2',status:'working',stage:'Building',objective:'Prototype office panels',actionItems:[],summary:'',links:[]},
 {id:'verify',name:'Verify',key:'3',status:'working',stage:'Testing',objective:'Check keyboard flow',actionItems:[],summary:'',links:[]},
 {id:'atlas',name:'Atlas',key:'5',status:'done',stage:'Results',objective:'Information architecture',actionItems:[],summary:'Navigation map and naming decisions are ready.',links:[['Open safe preview','#']]},
 {id:'pixel',name:'Pixel',key:'6',status:'done',stage:'Results',objective:'Visual direction',actionItems:[],summary:'Pixel-art system, contrast, and responsive notes are ready.',links:[['Open safe preview','#']]}
];
const lanes=document.querySelector('#lanes'), folders=document.querySelector('#folders'), detail=document.querySelector('#result-detail');
const stages=['Researching','Building','Testing','Waiting'];
lanes.innerHTML=stages.map(stage=>`<div class="lane"><h3>${stage.toUpperCase()}</h3>${jobs.filter(j=>j.stage===stage).map(j=>`<button class="job" data-status="${j.status}" data-id="${j.id}"><strong>${j.key} · ${j.name}</strong><small>${j.objective}</small></button>`).join('')||'<small>Clear</small>'}</div>`).join('');
const results=jobs.filter(j=>j.status==='done'); folders.innerHTML=results.map((j,i)=>`<button class="folder" role="listitem" aria-selected="${i===0}" data-id="${j.id}"><b>${j.name}</b><small>${j.objective}</small></button>`).join('');
function showResult(id){const j=jobs.find(x=>x.id===id);document.querySelectorAll('.folder').forEach(x=>x.setAttribute('aria-selected',x.dataset.id===id));detail.innerHTML=`<strong>${j.name}: ${j.objective}</strong><p>${j.summary}</p>${j.links.map(([t,u])=>`<a class="safe-link" href="${u}" aria-label="${t} (${j.name} result)">${t} ↗</a>`).join('')}`}
showResult(results[0].id);folders.addEventListener('click',e=>{const b=e.target.closest('.folder');if(b)showResult(b.dataset.id)});
const dialog=document.querySelector('#decision');document.querySelector('#resolve').onclick=()=>dialog.showModal();dialog.addEventListener('close',()=>{if(dialog.returnValue==='resolved'){document.querySelector('.needs').innerHTML='<p class="eyebrow">RESOLVED</p><h2>Aimee can continue.</h2><p>Key 4 decision recorded in this prototype.</p>'}});
document.addEventListener('keydown',e=>{if(e.key==='4'&&!dialog.open)dialog.showModal()});document.querySelector('#presentation').onclick=e=>{const on=document.querySelector('.office').dataset.presentation==='true';document.querySelector('.office').dataset.presentation=String(!on);e.currentTarget.setAttribute('aria-pressed',String(!on));e.currentTarget.textContent=on?'Present safely':'Exit presentation'};
