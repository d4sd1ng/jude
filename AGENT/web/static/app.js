const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const pages=$$('section').map(s=>s.dataset.page); const nav=$('#nav');
pages.forEach((p,i)=>{let b=document.createElement('button');b.textContent=p;b.onclick=()=>show(p);if(!i)b.className='active';nav.append(b)});
function show(p){$$('section').forEach(s=>s.classList.toggle('active',s.dataset.page===p));[...nav.children].forEach(b=>b.classList.toggle('active',b.textContent===p));if(p==='Märkte')loadICT();if(p==='Werkzeuge')loadImages();
 if(p==='System'){loadSystem();loadConfirmations();loadMemory();loadRouting();loadAgents();loadDocs()}};show(pages[0]);
function toast(x){let t=$('#toast');t.textContent=x;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),3500)}
async function api(url,opt={}){let r=await fetch(url,{headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});let d=await r.json().catch(()=>({error:r.statusText}));if(!r.ok||d.error)throw Error(d.error||r.statusText);return d}
api('/api/status').then(s=>{$('#health').textContent='lokal bereit';$('#health').style.color='#28765b';$('#stats').innerHTML=`<div class=stat><b>${s.router.models.length}</b>Modelle</div><div class=stat><b>${s.mail.filter(x=>x.configured).length}/5</b>Mailkonten</div><div class=stat><b>${s.memory.active_memories}</b>Erinnerungen</div><div class=stat><b>${s.ict.scheduler.enabled?'AN':'AUS'}</b>Kill Zones</div>`;$('#mailStatus').innerHTML=s.mail.map(x=>`<div class=source>${esc(x.address)} · ${x.configured?'bereit':'Schlüssel fehlt'}</div>`).join('');loadLights()}).catch(e=>toast(e.message));
$('#chatForm').onsubmit=async e=>{e.preventDefault();let x=$('#chatInput'),v=x.value;x.value='';$('#chat').innerHTML+=`<div class="message user">${esc(v)}</div>`;try{let d=await api('/api/chat',{method:'POST',body:JSON.stringify({text:v})});noteModel(d.model);$('#chat').innerHTML+=`<div class=message>${esc(d.answer)}<small> · ${esc(d.model||'')}</small>${d.route_id?`<div><button onclick="routeFeedback('${d.route_id}',1)">Passt</button> <button onclick="routeFeedback('${d.route_id}',-1)" style="background:#64748b">Unzureichend</button></div>`:''}</div>`}catch(e){toast(e.message)}};window.routeFeedback=async(id,value)=>{try{await api(`/api/routing/${id}/feedback`,{method:'POST',body:JSON.stringify({value})});toast('Routing-Feedback gespeichert')}catch(e){toast(e.message)}};
$('#loadMarket').onclick=async()=>{try{let m=$('#market').value,i=$('#interval').value,d=await api(`/api/market/${encodeURIComponent(m)}?interval=${i}&limit=300`);drawCandles(d.candles);$('#marketMeta').textContent=`${d.source} · ${d.caveat} · ${new Date(d.updated_at).toLocaleString()}`}catch(e){toast(e.message)}};
function drawCandles(rows){let c=$('#chart'),dpr=devicePixelRatio||1,w=c.clientWidth,h=360;c.width=w*dpr;c.height=h*dpr;let x=c.getContext('2d');x.scale(dpr,dpr);x.clearRect(0,0,w,h);if(!rows.length)return;let lo=Math.min(...rows.map(r=>r.low)),hi=Math.max(...rows.map(r=>r.high)),pad=28,cw=(w-pad*2)/rows.length,py=v=>pad+(hi-v)/(hi-lo||1)*(h-pad*2);x.strokeStyle='#dbe2e8';for(let j=0;j<5;j++){let y=pad+j*(h-pad*2)/4;x.beginPath();x.moveTo(pad,y);x.lineTo(w-pad,y);x.stroke()}rows.forEach((r,n)=>{let cx=pad+(n+.5)*cw,up=r.close>=r.open;x.strokeStyle=x.fillStyle=up?'#28765b':'#d4583b';x.beginPath();x.moveTo(cx,py(r.high));x.lineTo(cx,py(r.low));x.stroke();let y=Math.min(py(r.open),py(r.close)),bh=Math.max(1,Math.abs(py(r.open)-py(r.close)));x.fillRect(cx-Math.max(1,cw*.28),y,Math.max(2,cw*.56),bh)})}
async function loadICT(){try{let s=await api('/api/ict/status'),c=await api('/api/ict/cards');$('#killzones').innerHTML=`<b>Zeitzone ${esc(s.scheduler.timezone)}</b><p>${s.scheduler.zones.map(z=>`${esc(z.name)} ${z.start}–${z.end}`).join(' · ')}</p><small>${esc(s.scheduler.source_note)}</small>`;$('#cards').innerHTML=c.map(cardHTML).join('')||'<p>Noch keine Trading Cards.</p>'}catch(e){toast(e.message)}}
function cardHTML(c){return `<article class="card ${c.status==='SETUP_FOUND'?'setup':c.status==='TRADE_BLOCKED'?'blocked':''}"><span class=eyebrow>${esc(c.status)}</span><h3>${esc(c.symbol)} · ${esc(c.direction||'WAIT')}</h3><p><b>H4:</b> ${esc(str(c.h4_bias))}<br><b>H1:</b> ${esc(str(c.h1_context))}<br><b>M1:</b> ${esc(str(c.m1_entry))}</p><p>${esc(str(c.confluence))}</p><small>${esc(c.kill_zone||'')} · ${new Date(c.created_at).toLocaleString()}</small></article>`}
$$('.ictRun').forEach(b=>b.onclick=async()=>{b.disabled=true;toast(`${b.dataset.symbol} wird analysiert…`);try{let c=await api('/api/ict/analyse/'+b.dataset.symbol,{method:'POST'});$('#cards').insertAdjacentHTML('afterbegin',cardHTML(c))}catch(e){toast(e.message)}finally{b.disabled=false}});
let _rmap,_rbase,_rlayer,_rframes=[],_rhost='',_rzoom=9,_rmarker,_rsizer,_rmode='rainviewer',_rbounds=null,_rsource='RainViewer';
function radarFrameLabel(f){const d=new Date(f.time*1000),t=d.toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'});
 if(f.kind==='forecast'){const plus=Math.round((f.time*1000-Date.now())/60000);return `<span class=fc>${t} (+${Math.max(plus,0)} min)</span>`}
 return (f.kind==='now'||f===_rframes[_rnowIdx])?`<span class=now>Jetzt ${t}</span>`:t}
let _rnowIdx=0;
function showRadarFrame(i){if(!_rframes.length)return;i=Math.max(0,Math.min(_rframes.length-1,i));const f=_rframes[i];$('#radarFrame').value=i;if(_rlayer)_rmap.removeLayer(_rlayer);
 // DWD liefert fertig georeferenzierte PNG-Frames, RainViewer klassische Kacheln.
 _rlayer=_rmode==='dwd'
  ?L.imageOverlay(`/api/radar/frame/${f.key}.png`,_rbounds,{opacity:0.8,zIndex:5})
  :L.tileLayer(`${_rhost}${f.path}/256/{z}/{x}/{y}/6/1_1.png`,{opacity:0.72,zIndex:5,maxNativeZoom:10,maxZoom:12});
 _rlayer.addTo(_rmap);$('#radarMeta').innerHTML=`${radarFrameLabel(f)} · ${_rsource} · 35039 Marburg`}
async function loadRadar(){try{const d=await api('/api/radar');_rframes=d.frames||[];_rmode=d.mode||'rainviewer';_rhost=d.host;_rzoom=d.zoom||9;_rsource=d.source||'RainViewer';
 _rbounds=d.bounds?[[d.bounds.lat_min,d.bounds.lon_min],[d.bounds.lat_max,d.bounds.lon_max]]:null;
 const nowAt=_rframes.findIndex(f=>f.kind==='now');
 _rnowIdx=nowAt>=0?nowAt:(d.past_count||_rframes.length)-1;
 if(!_rframes.length)throw Error('Keine Radardaten');
  if(!_rmap){_rmap=L.map('radarMap',{zoomControl:true,attributionControl:false}).setView([d.latitude,d.longitude],_rzoom);_rbase=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:12,className:'radarbase'}).addTo(_rmap);_rmarker=L.marker([d.latitude,d.longitude],{icon:L.divIcon({className:'',html:'<div class=radarpin>●</div>',iconSize:[16,16],iconAnchor:[8,8]})}).addTo(_rmap);_rmarker.bindPopup(`${esc(d.zip)} ${esc(d.city)}`)}
  $('#radarPlace').textContent=`${d.zip} ${d.city}`.toUpperCase();
  const sl=$('#radarFrame');sl.max=_rframes.length-1;sl.value=_rnowIdx;sl.oninput=()=>showRadarFrame(+sl.value);
  showRadarFrame(_rnowIdx);
  // Die Karte startet, bevor das Flex-Layout der Cockpit-Spalte steht; ohne
  // erneutes invalidateSize rutscht Marburg aus der Mitte. Ein Timeout traf
  // das nur zufaellig – der Observer greift bei jeder Groessenaenderung.
  if(!_rsizer){_rsizer=new ResizeObserver(()=>_rmap.invalidateSize());_rsizer.observe($('#radarMap'))}
  setTimeout(loadRadar,300000)          // Frames altern: alle 5 Minuten nachladen
 }catch(e){
  // Ein einziger Fehlversuch (Server gerade weg, Netz kurz zu) liess den Radar
  // frueher dauerhaft leer zurueck. Jetzt wird still erneut versucht.
  $('#radarMeta').textContent='Radar nicht erreichbar – neuer Versuch…';
  setTimeout(loadRadar,15000)}}
$('#factForm').onsubmit=async e=>{e.preventDefault();$('#factResult').innerHTML='<div class=panel>Quellen werden geprüft…</div>';try{let d=await api('/api/fact-check',{method:'POST',body:JSON.stringify({url:$('#factUrl').value})});$('#factResult').innerHTML=`<div class=panel><span class=eyebrow>${esc(d.verdict)}</span><h3>${esc(d.source.title)}</h3><p>${esc(d.notice)}</p></div>`+d.claims.map(c=>`<article class=card><b>${esc(c.status)}</b><h3>${esc(c.claim)}</h3><p>${esc(c.explanation||'')}</p>${[...(c.supporting_urls||[]),...(c.contradicting_urls||[])].map(u=>`<a class=source href="${esc(u)}" target=_blank rel=noopener>${esc(u)}</a>`).join('')}</article>`).join('')}catch(e){toast(e.message);$('#factResult').innerHTML=''}};
$('#ocrForm').onsubmit=async e=>{e.preventDefault();let r=await fetch('/api/ocr',{method:'POST',body:new FormData(e.target)}),d=await r.json();if(d.error)return toast(d.error);$('#ocrText').textContent=d.text};
$('#shopRun').onclick=async()=>{try{let d=await api('/api/shopping',{method:'POST',body:JSON.stringify({category:$('#shopCategory').value})});$('#shopResult').innerHTML=d.products.map(p=>`<a class=source href="${esc(p.url)}" target=_blank>${esc(p.title)} ${esc(p.prices.join(', '))}</a>`).join('')||'Keine Treffer'}catch(e){toast(e.message)}};
$('#mealRun').onclick=async()=>{toast('Essensplan wird erstellt…');try{let d=await api('/api/meals',{method:'POST',body:JSON.stringify({days:+$('#mealDays').value,people:+$('#mealPeople').value})});$('#mealResult').textContent=`PDF: ${d.pdf_path}`}catch(e){toast(e.message)}};
$('#newsRun').onclick=async()=>{try{let d=await api('/api/news');$('#newsResult').innerHTML=d.articles.map(x=>`<a class=source href="${esc(x.url)}" target=_blank><b>${esc(x.title)}</b><br><small>${esc(x.source)} · ${fmt(x.published_at)}</small></a>`).join('')}catch(e){toast(e.message)}};
$('#mailSearch').onclick=async()=>{try{let a=$('#mailAccount').value,d=await api(`/api/mail/${a}/search?q=${encodeURIComponent($('#mailQuery').value)}`);$('#mailResults').innerHTML=d.map(m=>`<article class=source><button class="linkbutton" onclick="readMail('${a}','${esc(m.id)}','${esc(m.folder)}')">${esc(m.subject||'(ohne Betreff)')}</button><br><small>${esc(m.from)} · ${esc(m.date)}</small><div><button onclick="archiveMail('${a}','${esc(m.id)}','${esc(m.folder)}')">Archivieren</button> <button onclick="deleteMail('${a}','${esc(m.id)}','${esc(m.folder)}')">Löschen vormerken</button></div></article>`).join('')||'Keine Treffer'}catch(e){toast(e.message)}};
window.readMail=async(a,id,f)=>{try{let d=await api(`/api/mail/${a}/${encodeURIComponent(id)}?folder=${encodeURIComponent(f)}`);$('#mailResults').insertAdjacentHTML('afterbegin',`<article class=card><h3>${esc(d.subject)}</h3><pre>${esc(d.text)}</pre></article>`)}catch(e){toast(e.message)}};
window.archiveMail=async(a,id,f)=>{try{await api('/api/mail/archive',{method:'POST',body:JSON.stringify({account:a,message_id:id,folder:f})});toast('Archiviert');$('#mailSearch').click()}catch(e){toast(e.message)}};
window.deleteMail=async(a,id,f)=>{try{await requestConfirmation('mail_delete',`E-Mail ${id} aus ${a}/${f} löschen`,{account:a,message_id:id,folder:f});toast('Löschung wartet auf Bestätigung')}catch(e){toast(e.message)}};
$('#mailCompose').onsubmit=async e=>{e.preventDefault();let mode=e.submitter.value,p={account:$('#mailAccount').value,to:$('#mailTo').value,subject:$('#mailSubject').value,body:$('#mailBody').value};try{if(mode==='draft'){await api('/api/mail/draft',{method:'POST',body:JSON.stringify(p)});toast('Entwurf gespeichert')}else{await requestConfirmation('mail_send',`E-Mail über ${p.account} an ${p.to}: ${p.subject}`,p);toast('Versand wartet auf Bestätigung')}}catch(x){toast(x.message)}};
async function loadCalendar(){try{let d=await api('/api/calendar');$('#calendarEvents').innerHTML=d.map(x=>`<div class=source><b>${esc(x.title)}</b><br><small>${fmt(x.starts_at)} · ${esc(x.location)}</small></div>`).join('')||'Keine Termine'}catch(e){toast(e.message)}}
$('#calendarForm').onsubmit=async e=>{e.preventDefault();let p={title:$('#calendarTitle').value,starts_at:$('#calendarStart').value,ends_at:$('#calendarEnd').value,location:$('#calendarLocation').value,description:$('#calendarDescription').value};try{await requestConfirmation('calendar_create',`Termin erstellen: ${p.title} am ${p.starts_at}`,p);toast('Termin wartet auf Bestätigung')}catch(x){toast(x.message)}};loadCalendar();
async function loadLights(){try{let d=await api('/api/lights');$('#lights').innerHTML=Object.keys(d.entities).map(r=>`<div class=source><b>${esc(r)}</b> <button onclick="light('${r}','on')">An</button> <button onclick="light('${r}','off')" style="background:#64748b">Aus</button></div>`).join('');let a=await api('/api/home-actions'),rows=[];for(let group of ['alexa','grow'])for(let action of a[group])rows.push(`<div class=source><b>${esc(group)}</b> · ${esc(action)} <button onclick="homeAction('${esc(group)}','${esc(action)}')">Ausführen</button></div>`);$('#homeActions').innerHTML=rows.join('')||'Noch keine Aktionen konfiguriert.'}catch(e){$('#lights').textContent=e.message}}window.light=async(r,s)=>{try{await api(`/api/lights/${r}/${s}`,{method:'POST'});toast(`${r} ${s}`)}catch(e){toast(e.message)}};window.homeAction=async(g,a)=>{try{await api(`/api/home-actions/${encodeURIComponent(g)}/${encodeURIComponent(a)}`,{method:'POST'});toast(`${g}: ${a} ausgeführt`)}catch(e){toast(e.message)}};
$('#reposRun').onclick=async()=>{try{let d=await api('/api/coding/repositories');$('#repos').innerHTML=d.map((r,i)=>`<div class=source><b>${esc(r.path)}</b><br><small>${esc(r.branch)} · ${r.dirty?'geändert':'sauber'}</small><br><button onclick="repoTest(${i})">Tests</button></div>`).join('');window.repoData=d}catch(e){toast(e.message)}};window.repoTest=async i=>{toast('Tests laufen…');try{let d=await api('/api/coding/test',{method:'POST',body:JSON.stringify({repo:repoData[i].path})});toast(`Tests: ${d.status}`)}catch(e){toast(e.message)}};
$('#loadConfirmations').onclick=loadConfirmations;async function loadConfirmations(){try{let d=await api('/api/confirmations');$('#confirmations').innerHTML=d.map(c=>`<article class=card><span class=eyebrow>${esc(c.action_type)}</span><h3>${esc(c.summary)}</h3><pre>${esc(JSON.stringify(c.payload,null,2))}</pre><button onclick="decide('${c.id}','approve')">Bestätigen</button> <button onclick="decide('${c.id}','reject')" style="background:#64748b">Ablehnen</button></article>`).join('')||'<p>Keine offenen Bestätigungen.</p>'}catch(e){toast(e.message)}}window.decide=async(id,d)=>{try{await api(`/api/confirmations/${id}/${d}`,{method:'POST'});loadConfirmations()}catch(e){toast(e.message)}};
$('#loadMemory').onclick=loadMemory;async function loadMemory(){try{let d=await api('/api/memory'),s=d.stats;$('#memoryStats').textContent=`${s.active_memories} bestätigt · ${s.memory_candidates} Kandidaten · ${s.training_turns} lokal gespeicherte Trainingsturns · ${s.excluded_turns} ausgeschlossen`;$('#memoryItems').innerHTML=d.items.map(x=>`<article class=card><span class=eyebrow>${esc(x.status)} · ${esc(x.source)}</span><p>${esc(x.content)}</p><small>${x.occurrences}× erkannt · ${fmt(x.updated_at)}</small><div>${x.status==='candidate'?`<button onclick="approveMemory('${x.id}')">Übernehmen</button> `:''}<button onclick="forgetMemory('${x.id}')" style="background:#64748b">Vergessen</button></div></article>`).join('')||'<p>Noch keine Erinnerungen.</p>'}catch(e){toast(e.message)}}
$('#memoryForm').onsubmit=async e=>{e.preventDefault();try{await api('/api/memory',{method:'POST',body:JSON.stringify({content:$('#memoryContent').value})});$('#memoryContent').value='';loadMemory()}catch(x){toast(x.message)}};window.approveMemory=async id=>{try{await api(`/api/memory/${id}/approve`,{method:'POST'});loadMemory()}catch(e){toast(e.message)}};window.forgetMemory=async id=>{try{await api(`/api/memory/${id}`,{method:'DELETE'});loadMemory()}catch(e){toast(e.message)}};
$('#loadRouting').onclick=loadRouting;async function loadRouting(){try{let s=(await api('/api/status')).router,u=s.usage,t=s.routing_training;$('#routingStats').innerHTML=`<div class=stat><b>$${Number(s.budget_used_usd).toFixed(4)} / $${Number(s.budget_limit_usd).toFixed(2)}</b>Monatsbudget</div><div class=stat><b>${Number(u.input_tokens).toLocaleString('de-DE')}</b>Input-Tokens</div><div class=stat><b>${Number(u.output_tokens).toLocaleString('de-DE')}</b>Output-Tokens</div><div class=stat><b>${t.escalated}/${t.total}</b>Eskalationen</div>`;$('#routingUsage').innerHTML=s.usage_by_model.map(x=>`<div class=source><b>${esc(x.model)}</b> · ${x.calls} Aufrufe · $${Number(x.cost_usd).toFixed(5)}<br><small>${Number(x.input_tokens).toLocaleString('de-DE')} in · ${Number(x.output_tokens).toLocaleString('de-DE')} out · ${Number(x.cached_input_tokens).toLocaleString('de-DE')} Cache · Tarif ${esc(x.tariffs||'standard')}</small></div>`).join('')||'Noch kein Verbrauch.';$('#routingHistory').innerHTML=s.recent_routes.map(x=>`<div class=source><b>${esc(x.final_model||x.selected_model)}</b> · ${esc(x.task_type)} · Stufe ${x.complexity}<br><small>${x.escalated?'eskaliert':'lokal/erste Wahl'} · ${x.latency_ms} ms · ${x.user_feedback===1?'passt':x.user_feedback===-1?'unzureichend':'ohne Feedback'}</small></div>`).join('')||'Noch keine Entscheidungen.'}catch(e){toast(e.message)}}
async function requestConfirmation(action_type,summary,payload){return api('/api/confirmations',{method:'POST',body:JSON.stringify({action_type,summary,payload})})}
function str(x){return typeof x==='string'?x:JSON.stringify(x)}function esc(x){return String(x??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]))}window.addEventListener('resize',()=>$('#loadMarket').click());
// --- Sprachsteuerung ---------------------------------------------------------
let voiceSince=0,voiceRunning=false;
function voiceBadge(s){const el=$('#voiceState');const labels={aus:'Sprachsteuerung aus','wartet':`wartet auf „${s.wake_word}“`,'aufnahme':'hört zu…','denkt':'denkt nach…','spricht':'spricht…','aktiv':'aktiv – einfach sprechen','fehler':'Sprachfehler'};el.textContent=labels[s.state]||s.state;el.classList.toggle('on',!!s.running);const b=$('#voiceToggle');b.textContent=s.running?'🎙️ Stoppen':'🎙️ Sprachsteuerung';if(s.error)el.title=s.error}
async function pollVoice(){try{const d=await api(`/api/voice/events?since=${voiceSince}`);voiceSince=d.last_id;voiceRunning=d.running;voiceBadge(d);let added=false;for(const e of d.events){if(e.kind==='heard'){$('#chat').innerHTML+=`<div class="message user">🎙️ ${esc(e.text)}</div>`;added=true}else if(e.kind==='answer'){$('#chat').innerHTML+=`<div class=message>${esc(e.text)}<small> · ${esc(e.model||'Sprachantwort')}</small></div>`;added=true;noteModel(e.model)}else if(e.kind==='error'||e.kind==='warning'){toast(e.text)}}if(added){const c=$('#chat');c.scrollTop=c.scrollHeight}}catch(e){/* Server evtl. nicht erreichbar – nächster Versuch */}finally{setTimeout(pollVoice,voiceRunning?1200:4000)}}
$('#voiceToggle').onclick=async()=>{const b=$('#voiceToggle');b.disabled=true;try{const s=voiceRunning?await api('/api/voice/stop',{method:'POST'}):await api('/api/voice/start',{method:'POST'});voiceRunning=s.running;voiceBadge(s);toast(s.running?'Sprachsteuerung gestartet':'Sprachsteuerung gestoppt')}catch(e){toast(e.message)}finally{b.disabled=false}};
pollVoice();

// --- Cockpit -----------------------------------------------------------------
function tick2(){const d=new Date();$('#clockTime').textContent=d.toLocaleTimeString('de-DE');$('#clockDate').textContent=d.toLocaleDateString('de-DE',{weekday:'long',day:'2-digit',month:'long',year:'numeric'})}
setInterval(tick2,1000);tick2();
function setGauge(id,val,max,text,warn=.7,hot=.88){const g=$(id);if(!g)return;const p=Math.max(0,Math.min(100,(val/max)*100));g.style.setProperty('--p',p);g.classList.toggle('warn',p>=warn*100&&p<hot*100);g.classList.toggle('hot',p>=hot*100);g.querySelector('b').textContent=text}
async function pollSystem(){try{const s=await api('/api/system');
  setGauge('#gRam',s.memory.percent,100,`${s.memory.percent.toFixed(0)}`);
  if(s.disk)setGauge('#gDisk',s.disk.percent,100,`${s.disk.percent.toFixed(0)}`);
  const net=s.network.rx_kbps+s.network.tx_kbps;setGauge('#gNet',Math.min(net,100000),100000,net>=1024?`${(net/1024).toFixed(1)}M`:`${net.toFixed(0)}`);
  const t=s.temperatures;if(t.cpu!=null)setGauge('#gCpuTemp',t.cpu,100,`${t.cpu.toFixed(0)}`);
  if(t.gpu!=null)setGauge('#gGpuTemp',t.gpu,110,`${t.gpu.toFixed(0)}`);
  if(t.nvme!=null)setGauge('#gNvme',t.nvme,90,`${t.nvme.toFixed(0)}`);
  setGauge('#gCpu',s.cpu_percent,100,`${s.cpu_percent.toFixed(0)}`);
}catch(e){}finally{setTimeout(pollSystem,2500)}}
async function pollWeather(){try{const w=await api('/api/weather');
  $('#hudWeather').innerHTML=`<b>${w.temperature!=null?w.temperature.toFixed(1):'–'}°C</b> <span class=cond>${esc(w.condition)}</span><br><span class=small>gefühlt ${w.feels_like??'–'}° · ${w.min??'–'}°/${w.max??'–'}° · Wind ${w.wind_kmh??'–'} km/h · ${esc(w.location.split(',')[1]||'')}</span>`
}catch(e){$('#hudWeather').textContent='Wetter nicht erreichbar'}finally{setTimeout(pollWeather,600000)}}
// Growcontroller: nur die in HA_GROW_SENSORS_JSON freigegebenen Messwerte.
async function pollGrow(){try{const d=await api('/api/grow');const box=$('#hudGrow');
  if(!d.configured){box.innerHTML='<span class="muted small">nicht eingerichtet</span>';return}
  box.innerHTML=(d.sensors||[]).map(s=>`<div class="row"><span>${esc(s.label)}</span><b>${esc(s.value)}${s.unit?' '+esc(s.unit):''}</b></div>`).join('')
    ||'<span class="muted small">keine Werte</span>'}
 catch(e){$('#hudGrow').innerHTML='<span class="muted small">nicht erreichbar</span>'}
 finally{setTimeout(pollGrow,60000)}}

// Rezept des Tages aus der Notion-DB „Küchenmanagement".
async function pollMeals(){const box=$('#hudMeals');try{const d=await api('/api/recipes/today');
  if(!d.configured){box.innerHTML='<span class="muted small">Notion nicht eingerichtet</span>';return}
  const r=d.recipe;if(!r){box.innerHTML='<span class="muted small">kein Rezept</span>';return}
  const meta=[r.category,r.minutes?r.minutes+' Min':'',r.portions?r.portions+' Portionen':'']
    .filter(Boolean).map(esc).join(' · ');
  const block=(title,text)=>text?`<details><summary>${title}</summary><p class="small">${esc(text)}</p></details>`:'';
  box.innerHTML=`<b class="mealname">${esc(r.name)}</b><p class="muted small">${meta}</p>`
    +block('Zutaten',r.ingredients)+block('Zubereitung',r.preparation)
    +block('Airfryer',r.airfryer)+block('Einkaufshinweis',r.note)
    +(r.devices&&r.devices.length?`<p class="muted small">${r.devices.map(esc).join(' · ')}</p>`:'')}
 catch(e){box.innerHTML='<span class="muted small">nicht erreichbar</span>'}
 finally{setTimeout(pollMeals,900000)}}
async function pollTokens(){try{const s=await api('/api/status'),u=s.router.usage,c=s.router.context;
  // Kontextfenster: exakter Stand vom letzten Modellaufruf (prompt_eval_count).
  let ctx='<div class=row><span>Kontext</span><b>–</b></div>';
  if(c&&c.context_length){const p=Math.min(100,c.input_tokens/c.context_length*100);
    const cls=p>=88?'hot':(p>=70?'warn':'');
    ctx=`<div class="row ctx ${cls}" title="${c.model}: ${c.input_tokens.toLocaleString('de-DE')} von ${c.context_length.toLocaleString('de-DE')} Token"><span>Kontext</span><b>${p.toFixed(0)}%</b><i class=ctxbar><i style="width:${p.toFixed(1)}%"></i></i></div>`}
  $('#hudTokens').innerHTML=`<div class=row><span>Input</span><b>${Number(u.input_tokens).toLocaleString('de-DE')}</b></div><div class=row><span>Output</span><b>${Number(u.output_tokens).toLocaleString('de-DE')}</b></div><div class=row><span>Budget</span><b>$${Number(s.router.budget_used_usd).toFixed(3)} / $${Number(s.router.budget_limit_usd).toFixed(0)}</b></div>`+ctx
}catch(e){}finally{setTimeout(pollTokens,15000)}}
async function pollTicker(){if(toggles.market){try{const x=await api('/api/market/XAU%2FUSD?interval=1h&limit=2&refresh=true');const c=x.candles.at(-1),pr=x.candles.at(-2);if(c)$('#tickXau').innerHTML=`<span class="${!pr||c.close>=pr.close?'up':'down'}">${c.close.toFixed(2)}</span>`}catch(e){}
 try{const b=await api('/api/market/BTC%2FUSD?interval=1h&limit=2&refresh=true');const c=b.candles.at(-1),pr=b.candles.at(-2);if(c)$('#tickBtc').innerHTML=`<span class="${!pr||c.close>=pr.close?'up':'down'}">${Number(c.close).toLocaleString('de-DE',{maximumFractionDigits:0})}</span>`}catch(e){}}
 setTimeout(pollTicker,120000)}
let newsItems=[],newsIdx=0;
async function pollNews(){if(toggles.news){try{const d=await api('/api/briefing');newsItems=Object.entries(d.headlines||{}).flatMap(([topic,items])=>items.map(t=>`${topic}: ${t}`));if(d.markets&&d.markets.length){$('#tickXau').textContent=(d.markets.find(m=>m.label==='Gold')||{}).price?.toFixed(0)??$('#tickXau').textContent;const btc=d.markets.find(m=>m.label==='Bitcoin');if(btc)$('#tickBtc').innerHTML=`<span class="${btc.change_pct>=0?'up':'down'}">${btc.price.toLocaleString('de-DE',{maximumFractionDigits:0})}</span>`}}catch(e){}}setTimeout(pollNews,600000)}
setInterval(()=>{if(newsItems.length){newsIdx=(newsIdx+1)%newsItems.length;const n=newsItems[newsIdx];$('#tickNews').textContent=n.title}},8000);
window.noteModel=m=>{if(m)$('#tickModel').textContent=m};
// Schalter (Markt/News/Radar clientseitig, Stimme serverseitig)
const toggles=JSON.parse(localStorage.getItem('judeToggles')||'{"market":true,"news":true,"radar":true}');
function renderToggles(){$('#tglMarket').classList.toggle('on',toggles.market);$('#tglNews').classList.toggle('on',toggles.news);$('#tglRadar').classList.toggle('on',toggles.radar);$('#tglVoice').classList.toggle('on',voiceRunning)}
for(const[id,key]of[['#tglMarket','market'],['#tglNews','news'],['#tglRadar','radar']])$(id).onclick=()=>{toggles[key]=!toggles[key];localStorage.setItem('judeToggles',JSON.stringify(toggles));renderToggles()};
$('#tglVoice').onclick=()=>$('#voiceToggle').click();
const _vb=voiceBadge;voiceBadge=s=>{_vb(s);$('#tickVoice').textContent=s.running?(s.state||'an'):'aus';renderToggles()};
renderToggles();pollSystem();pollWeather();loadRadar();pollGrow();pollMeals();pollTokens();pollTicker();pollNews();

// Thema überspringen
$('#voiceSkip').onclick=async()=>{try{await api('/api/voice/skip',{method:'POST'});toast('Thema übersprungen')}catch(e){toast(e.message)}};

// ICT walk-forward Training (benötigt MT5-Verbindung)
$$('.ictTrain').forEach(b=>b.onclick=async()=>{const sym=b.dataset.symbol;b.disabled=true;toast(`${sym}: Training läuft (kann dauern)…`);try{const r=await api('/api/ict/train/'+sym,{method:'POST'});const m=r.metrics||r;toast(`${sym} trainiert: ROC-AUC ${(m.roc_auc??0).toFixed(2)}, Precision ${(m.precision??0).toFixed(2)}, ${m.test_signals??'?'} Signale`)}catch(e){toast(`${sym} Training: ${e.message}`)}finally{b.disabled=false}});

// --- Bilder (Erzeugen / Bearbeiten / 3D-Render / Galerie) --------------------
function imgCard(m){const cap=m.prompt||m.title||m.kind;return `<article class=card><img src="/images/${encodeURIComponent(m.file)}" alt="${esc(cap)}" loading=lazy style="width:100%;border-radius:8px"><small>${esc(m.kind)} · ${esc(m.source||'')} · ${new Date(m.created_at).toLocaleString('de-DE')}</small><p>${esc(str(cap)).slice(0,140)}</p></article>`}
async function loadImages(){try{const d=await api('/api/images');$('#imageGallery').innerHTML=(d.items||[]).map(imgCard).join('')||'<p>Noch keine Bilder.</p>'}catch(e){toast(e.message)}}
$('#loadImages')&&($('#loadImages').onclick=loadImages);
$('#imgGenForm')&&($('#imgGenForm').onsubmit=async e=>{e.preventDefault();toast('Bild wird erzeugt…');try{const d=await api('/api/images/generate',{method:'POST',body:JSON.stringify({prompt:$('#imgPrompt').value,size:$('#imgSize').value})});$('#imgGenResult').innerHTML=imgCard(d);loadImages()}catch(x){toast(x.message)}});
$('#imgEditForm')&&($('#imgEditForm').onsubmit=async e=>{e.preventDefault();toast('Bild wird bearbeitet…');try{const r=await fetch('/api/images/edit',{method:'POST',body:new FormData(e.target)});const d=await r.json();if(d.error)throw Error(d.error);$('#imgEditResult').innerHTML=imgCard(d);loadImages()}catch(x){toast(x.message)}});
$('#renderRun')&&($('#renderRun').onclick=async()=>{const desc=$('#renderPrompt').value.trim();if(!desc)return toast('Bitte Szene beschreiben');toast('Jude baut die 3D-Szene…');try{const d=await api('/api/chat',{method:'POST',body:JSON.stringify({text:`Rendere lokal mit Blender eine 3D-Szene und nutze das Werkzeug render_3d_scene. Titel: ${$('#renderTitle').value||'szene'}. Szene: ${desc}`})});$('#renderResult').innerHTML=`<p>${esc(d.answer||'')}</p>`;setTimeout(loadImages,1500)}catch(x){toast(x.message)}});

// Bild verstehen (Vision)
$('#visionForm')&&($('#visionForm').onsubmit=async e=>{e.preventDefault();toast('Bild wird analysiert…');try{const r=await fetch('/api/vision',{method:'POST',body:new FormData(e.target)});const d=await r.json();if(d.error)throw Error(d.error);$('#visionResult').innerHTML=`<div class=panel><small>${esc(d.model)} · ${esc(d.source)}</small><p>${esc(d.answer)}</p></div>`}catch(x){toast(x.message)}});

// --- Team / Sub-Agenten ("Mitarbeiter") -------------------------------------
let _skills=[];
async function loadAgents(){try{const d=await api('/api/agents');_skills=d.skills||[];
  $('#agentSkills').innerHTML=_skills.map(s=>`<label><input type=checkbox value="${esc(s)}"> ${esc(s)}</label>`).join('');
  $('#agentList').innerHTML=(d.agents||[]).map(a=>`<article class="card agentcard"><h3>${esc(a.name)}</h3><p class=role>${esc(a.role)}</p><p class=skills>${(a.skills||[]).map(esc).join(' · ')||'keine Skills'}</p><div class=agentrun><input placeholder="Aufgabe für ${esc(a.name)}" data-agent="${esc(a.name)}"><button onclick="runAgent('${esc(a.name)}',this)">Beauftragen</button><button onclick="delAgent('${esc(a.name)}')" style="background:#64748b">Entfernen</button></div><div class=agentout></div></article>`).join('')||'<p>Noch keine Mitarbeiter. Lege oben einen an.</p>'}catch(e){toast(e.message)}}
$('#agentForm')&&($('#agentForm').onsubmit=async e=>{e.preventDefault();const skills=[...document.querySelectorAll('#agentSkills input:checked')].map(c=>c.value);if(!skills.length)return toast('Mindestens einen Skill wählen');try{await api('/api/agents',{method:'POST',body:JSON.stringify({name:$('#agentName').value,role:$('#agentRole').value,skills})});$('#agentName').value='';$('#agentRole').value='';toast('Mitarbeiter angelegt');loadAgents()}catch(x){toast(x.message)}});
window.runAgent=async(name,btn)=>{const inp=btn.previousElementSibling;const task=inp.value.trim();if(!task)return toast('Aufgabe eingeben');btn.disabled=true;const out=btn.closest('.agentcard').querySelector('.agentout');out.innerHTML='<p class=muted>arbeitet…</p>';try{const d=await api(`/api/agents/${encodeURIComponent(name)}/run`,{method:'POST',body:JSON.stringify({task})});out.innerHTML=`<div class=message>${esc(d.answer)}<small> · ${esc(d.model||'')}</small></div>`}catch(x){out.innerHTML='';toast(x.message)}finally{btn.disabled=false}};
window.delAgent=async name=>{try{await api('/api/agents/'+encodeURIComponent(name),{method:'DELETE'});loadAgents()}catch(e){toast(e.message)}};
$('#loadAgents')&&($('#loadAgents').onclick=loadAgents);

// --- System: Health, Aufgaben, Sicherungen ----------------------------------
function healthHTML(h){const badge=h.status==='ok'?'<span class=now>OK</span>':'<span class=fc>DEGRADED</span>';const o=h.ollama||{};const d=h.disk||{};return `<p>Status: ${badge} · Ollama: ${o.reachable?'erreichbar':'weg'} (${h.models_installed} Modelle) · Disk frei: ${d.free_gb} GB (${d.used_percent}% belegt) · DB: ${h.database?.ok?'ok':'Fehler'} · Mikrofon: ${h.microphone?.available?'ja':'nein'} · Wake-Word: ${h.wakeword?.trained_model_present?'trainiert':'—'}</p>`+(h.recent_errors?.length?`<p class=muted>Letzte Fehler: ${h.recent_errors.map(e=>esc(e.what)).slice(0,3).join(' · ')}</p>`:'')}
async function loadSystem(){try{const h=await api('/api/health');$('#healthPanel').innerHTML=healthHTML(h)}catch(e){$('#healthPanel').textContent=e.message}
  try{const d=await api('/api/tasks');$('#taskList').innerHTML=(d.tasks||[]).map(t=>`<div class=source><b>${esc(t.name)}</b> · ${esc(t.action_type)} · ${t.schedule.type==='daily'?('täglich '+t.schedule.at):('alle '+t.schedule.every_minutes+' min')} <button onclick="delTask('${t.id}')" style="background:#64748b">✕</button></div>`).join('')||'<p class=muted>Keine Aufgaben.</p>'}catch(e){}
  try{const b=await api('/api/backups');$('#backupList').innerHTML=(b.backups||[]).map(x=>`<div class=source>${esc(x.archive)} · ${(x.size_bytes/1024).toFixed(0)} KB · ${new Date(x.modified).toLocaleString('de-DE')}</div>`).join('')||'<p class=muted>Noch keine Sicherung.</p>'}catch(e){}}
$('#loadSystem')&&($('#loadSystem').onclick=loadSystem);
$('#taskForm')&&($('#taskForm').onsubmit=async e=>{e.preventDefault();const p={name:$('#taskName').value,action_type:$('#taskAction').value,prompt:$('#taskPrompt').value||null};const at=$('#taskAt').value.trim(),every=$('#taskEvery').value.trim();if(at)p.at=at;else if(every)p.every_minutes=+every;else return toast('HH:MM oder Minuten angeben');try{await api('/api/tasks',{method:'POST',body:JSON.stringify(p)});$('#taskName').value='';toast('Aufgabe angelegt');loadSystem()}catch(x){toast(x.message)}});
window.delTask=async id=>{try{await api('/api/tasks/'+id,{method:'DELETE'});loadSystem()}catch(e){toast(e.message)}};
$('#backupRun')&&($('#backupRun').onclick=async()=>{toast('Sicherung läuft…');try{await api('/api/backups',{method:'POST'});toast('Gesichert');loadSystem()}catch(e){toast(e.message)}});

// --- Wissen: Dokumente (RAG) ------------------------------------------------
async function loadDocs(){try{const d=await api('/api/documents');$('#docList').innerHTML=(d.documents||[]).map(x=>`<div class=source>${esc(x.path.split('/').pop())} · ${x.chunks} Abschnitte <button onclick="forgetDoc('${esc(x.path)}')" style="background:#64748b">✕</button></div>`).join('')||'<p class=muted>Noch keine Dokumente.</p>'}catch(e){toast(e.message)}}
$('#loadDocs')&&($('#loadDocs').onclick=loadDocs);
$('#docIngestForm')&&($('#docIngestForm').onsubmit=async e=>{e.preventDefault();toast('Wird eingelesen…');try{const r=await api('/api/documents/ingest',{method:'POST',body:JSON.stringify({path:$('#docPath').value})});toast(`${r.chunks} Abschnitte eingelesen`);$('#docPath').value='';loadDocs()}catch(x){toast(x.message)}});
$('#docSearchForm')&&($('#docSearchForm').onsubmit=async e=>{e.preventDefault();try{const r=await api('/api/documents/search',{method:'POST',body:JSON.stringify({query:$('#docQuery').value})});$('#docResults').innerHTML=(r.results||[]).map(x=>`<article class=card><small>${esc(x.path.split('/').pop())} · Score ${x.score}</small><p>${esc(x.text).slice(0,400)}</p></article>`).join('')||`<p class=muted>${esc(r.note||'Keine Treffer.')}</p>`}catch(x){toast(x.message)}});
window.forgetDoc=async path=>{try{await api('/api/documents',{method:'DELETE',body:JSON.stringify({path})});loadDocs()}catch(e){toast(e.message)}};
