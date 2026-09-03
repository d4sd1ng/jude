const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const pages=$$('section').map(s=>s.dataset.page); const nav=$('#nav');
pages.forEach((p,i)=>{let b=document.createElement('button');b.textContent=p;b.onclick=()=>show(p);if(!i)b.className='active';nav.append(b)});
function show(p){$$('section').forEach(s=>s.classList.toggle('active',s.dataset.page===p));[...nav.children].forEach(b=>b.classList.toggle('active',b.textContent===p));if(p==='Märkte')loadICT();if(p==='Werkzeuge')loadImages();
 if(p==='Schreibtisch'){loadAktiv();loadReviews();loadConfirmations();loadNotifications();loadAuftraege()}
 if(p==='System'){loadSystem();loadMemory();loadRouting();loadAgents();loadDocs()}};show(pages[0]);
function fmt(v){const d=new Date(typeof v==='number'?v*1000:v);return isNaN(d)?String(v??''):d.toLocaleString('de-DE',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})}
function toast(x){let t=$('#toast');t.textContent=x;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),3500)}
async function api(url,opt={}){let r=await fetch(url,{headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});let d=await r.json().catch(()=>({error:r.statusText}));if(!r.ok||d.error)throw Error(d.error||r.statusText);return d}
api('/api/status').then(s=>{$('#health').textContent='lokal bereit';$('#stats').innerHTML=`<div class=stat><b>${s.router.models.length}</b>Modelle</div><div class=stat><b>${s.mail.filter(x=>x.configured).length}/5</b>Mailkonten</div><div class=stat><b>${s.memory.active_memories}</b>Erinnerungen</div><div class=stat><b>${s.ict.scheduler.enabled?'AN':'AUS'}</b>Kill Zones</div>`;$('#mailStatus').innerHTML=s.mail.map(x=>`<div class=source>${esc(x.address)} · ${x.configured?'bereit':'Schlüssel fehlt'}</div>`).join('')}).catch(e=>toast(e.message));
$('#chatForm').onsubmit=async e=>{e.preventDefault();let x=$('#chatInput'),v=x.value;x.value='';
 const chat=$('#chat');chat.innerHTML+=`<div class="message user">${esc(v)}</div>`;
 // Sichtbarer Denk-Zustand: vorher kam 16-60 s lang schlicht nichts.
 const wait=document.createElement('div');wait.className='message thinking';wait.textContent='Jude denkt …';
 chat.append(wait);chat.scrollTop=chat.scrollHeight;
 const btn=e.target.querySelector('button');btn.disabled=true;
 try{let d=await api('/api/chat',{method:'POST',body:JSON.stringify({text:v})});noteModel(d.model);
  wait.outerHTML=`<div class=message>${esc(d.answer)}<small> · ${esc(d.model||'')}</small>${d.route_id?`<div><button onclick="routeFeedback('${d.route_id}',1)">Passt</button> <button onclick="routeFeedback('${d.route_id}',-1)" style="background:#64748b">Unzureichend</button></div>`:''}</div>`}
 catch(err){wait.outerHTML=`<div class="message error">Fehler: ${esc(err.message)}</div>`}
 finally{btn.disabled=false;chat.scrollTop=chat.scrollHeight}};window.routeFeedback=async(id,value)=>{try{await api(`/api/routing/${id}/feedback`,{method:'POST',body:JSON.stringify({value})});toast('Routing-Feedback gespeichert')}catch(e){toast(e.message)}};
$('#loadMarket').onclick=async()=>{try{let m=$('#market').value,i=$('#interval').value,d=await api(`/api/market/${encodeURIComponent(m)}?interval=${i}&limit=300`);drawCandles(d.candles);$('#marketMeta').textContent=`${d.source} · ${d.caveat} · ${new Date(d.updated_at).toLocaleString()}`}catch(e){toast(e.message)}};
function drawCandles(rows){let c=$('#chart'),dpr=devicePixelRatio||1,w=c.clientWidth,h=360;c.width=w*dpr;c.height=h*dpr;let x=c.getContext('2d');x.scale(dpr,dpr);x.clearRect(0,0,w,h);if(!rows.length)return;
 const padL=10,padR=74,padT=12,padB=26;
 let lo=Math.min(...rows.map(r=>r.low)),hi=Math.max(...rows.map(r=>r.high));
 const cw=(w-padL-padR)/rows.length,py=v=>padT+(hi-v)/(hi-lo||1)*(h-padT-padB);
 x.font='11px Inter,ui-sans-serif,system-ui,sans-serif';
 // Preisskala rechts an den Rasterlinien
 for(let j=0;j<5;j++){const v=hi-j*(hi-lo)/4,y=py(v);
  x.strokeStyle='#26302a';x.beginPath();x.moveTo(padL,y);x.lineTo(w-padR,y);x.stroke();
  x.fillStyle='#9aa38f';x.textAlign='left';x.textBaseline='middle';
  x.fillText(v.toLocaleString('de-DE',{maximumFractionDigits:v<100?2:0}),w-padR+8,y)}
 // Zeitskala unten: HH:MM innerhalb von 3 Tagen, sonst Datum (time = Unix-Sekunden)
 const span=(rows.at(-1).time-rows[0].time),step=Math.max(1,Math.ceil(rows.length/6));
 x.fillStyle='#9aa38f';x.textAlign='center';x.textBaseline='top';
 rows.forEach((r,n)=>{if(n%step===0){const t=new Date(r.time*1000);
  const label=span>3*86400?t.toLocaleDateString('de-DE',{day:'2-digit',month:'2-digit'}):t.toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'});
  x.fillText(label,padL+(n+.5)*cw,h-padB+8)}});
 rows.forEach((r,n)=>{let cx=padL+(n+.5)*cw,up=r.close>=r.open;x.strokeStyle=x.fillStyle=up?'#2e6b40':'#d4583b';x.beginPath();x.moveTo(cx,py(r.high));x.lineTo(cx,py(r.low));x.stroke();let y=Math.min(py(r.open),py(r.close)),bh=Math.max(1,Math.abs(py(r.open)-py(r.close)));x.fillRect(cx-Math.max(1,cw*.28),y,Math.max(2,cw*.56),bh)});
 // Letzter Kurs als Markierung an der Preisskala
 const last=rows.at(-1),ly=py(last.close);
 x.fillStyle='#d4a017';x.textAlign='left';x.textBaseline='middle';
 x.fillRect(w-padR,ly-9,4,18);
 x.fillText(last.close.toLocaleString('de-DE',{maximumFractionDigits:last.close<100?2:0}),w-padR+8,ly)}
async function loadICT(){try{let s=await api('/api/ict/status'),c=await api('/api/ict/cards');$('#killzones').innerHTML=`<b>Zeitzone ${esc(s.scheduler.timezone)}</b><p>${s.scheduler.zones.map(z=>`${esc(z.name)} ${z.start}–${z.end}`).join(' · ')}</p><small>${esc(s.scheduler.source_note)}</small>`;$('#cards').innerHTML=c.map(cardHTML).join('')||'<p>Noch keine Trading Cards.</p>'}catch(e){toast(e.message)}}
function cardHTML(c){return `<article class="card ${c.status==='SETUP_FOUND'?'setup':c.status==='TRADE_BLOCKED'?'blocked':''}"><span class=eyebrow>${esc(c.status)}</span><h3>${esc(c.symbol)} · ${esc(c.direction||'WAIT')}</h3><p><b>H4:</b> ${esc(str(c.h4_bias))}<br><b>H1:</b> ${esc(str(c.h1_context))}<br><b>M1:</b> ${esc(str(c.m1_entry))}</p><p>${esc(str(c.confluence))}</p><small>${esc(c.kill_zone||'')} · ${new Date(c.created_at).toLocaleString()}</small></article>`}
$$('.ictRun').forEach(b=>b.onclick=async()=>{b.disabled=true;toast(`${b.dataset.symbol} wird analysiert…`);try{let c=await api('/api/ict/analyse/'+b.dataset.symbol,{method:'POST'});$('#cards').insertAdjacentHTML('afterbegin',cardHTML(c))}catch(e){toast(e.message)}finally{b.disabled=false}});
let _rmap,_rbase,_rlayer,_rframes=[],_rhost='',_rzoom=7,_rmarker,_rsizer,_rmode='rainviewer',_rbounds=null,_rsource='RainViewer';
function radarFrameLabel(f){const d=new Date(f.time*1000),t=d.toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'});
 if(f.kind==='forecast'){const plus=Math.round((f.time*1000-Date.now())/60000);return `<span class=fc>${t} (+${Math.max(plus,0)} min)</span>`}
 return (f.kind==='now'||f===_rframes[_rnowIdx])?`<span class=now>Jetzt ${t}</span>`:t}
let _rnowIdx=0;
function showRadarFrame(i){if(!_rframes.length)return;i=Math.max(0,Math.min(_rframes.length-1,i));const f=_rframes[i];$('#radarFrame').value=i;if(_rlayer)_rmap.removeLayer(_rlayer);
 // DWD liefert fertig georeferenzierte PNG-Frames, RainViewer klassische Kacheln.
 _rlayer=_rmode==='dwd'
  ?L.imageOverlay(`/api/radar/frame/${f.key}.png`,_rbounds,{opacity:0.8,zIndex:5})
  :L.tileLayer(`${_rhost}${f.path}/256/{z}/{x}/{y}/6/1_1.png`,{opacity:0.72,zIndex:5,maxNativeZoom:7,maxZoom:12});
 _rlayer.addTo(_rmap);$('#radarMeta').innerHTML=`${radarFrameLabel(f)} · ${_rsource} · 35039 Marburg`}
async function loadRadar(){try{const d=await api('/api/radar');_rframes=d.frames||[];_rmode=d.mode||'rainviewer';_rhost=d.host;_rzoom=d.zoom||7;_rsource=d.source||'RainViewer';
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
function shopSizeSync(){const c=$('#shopCategory').value,sz=$('#shopSize'),br=$('#shopBrand');
 if(c==='jeans'){sz.innerHTML='<option>33/34</option>';sz.disabled=true;br.value='gstar';br.disabled=true}
 else if(c==='schuhe'){sz.innerHTML='<option>44</option>';sz.disabled=true;br.value='nike';br.disabled=true}
 else{if(sz.disabled){sz.innerHTML='<option>L</option><option>XL</option><option selected>XXL</option>';sz.disabled=false}br.disabled=false}}
$('#shopCategory').onchange=shopSizeSync;shopSizeSync();
$('#shopRun').onclick=async()=>{const b=$('#shopRun');b.disabled=true;$('#shopResult').textContent='Suche läuft…';
 try{let d=await api('/api/shopping',{method:'POST',body:JSON.stringify({category:$('#shopCategory').value,brand:$('#shopBrand').value,size:$('#shopSize').value})});
 $('#shopResult').innerHTML=`<p class="muted small">${esc(d.brand)} · Größe ${esc(d.size)} · ${d.count} Treffer</p>`+
  (d.products.map(p=>`<a class="source shopitem" href="${esc(p.url)}" target=_blank>${p.image?`<img class=thumb loading=lazy src="${esc(p.image)}" alt="">`:''}<span><b>${esc(p.title)}</b><br>${esc(p.prices.join(', '))}</span></a>`).join('')||'Keine Treffer')}
 catch(e){toast(e.message);$('#shopResult').textContent=''}finally{b.disabled=false}};
$('#mealRun').onclick=async()=>{toast('Essensplan wird erstellt…');try{let d=await api('/api/meals',{method:'POST',body:JSON.stringify({days:+$('#mealDays').value,people:+$('#mealPeople').value})});$('#mealResult').textContent=`PDF: ${d.pdf_path}`}catch(e){toast(e.message)}};
$('#newsRun').onclick=async()=>{try{let d=await api('/api/news');$('#newsResult').innerHTML=d.articles.map(x=>`<a class=source href="${esc(x.url)}" target=_blank><span class=eyebrow>${esc(x.topic||'')}</span> <b>${esc(x.title)}</b><br><small>${esc(x.source)} · ${fmt(x.published_at)}</small></a>`).join('')}catch(e){toast(e.message)}};
$('#mailSearch').onclick=async()=>{try{let a=$('#mailAccount').value,d=await api(`/api/mail/${a}/search?q=${encodeURIComponent($('#mailQuery').value)}`);$('#mailResults').innerHTML=d.map(m=>`<article class=source><button class="linkbutton" onclick="readMail('${a}','${esc(m.id)}','${esc(m.folder)}')">${esc(m.subject||'(ohne Betreff)')}</button><br><small>${esc(m.from)} · ${esc(m.date)}</small><div><button onclick="archiveMail('${a}','${esc(m.id)}','${esc(m.folder)}')">Archivieren</button> <button onclick="deleteMail('${a}','${esc(m.id)}','${esc(m.folder)}')">Löschen vormerken</button></div></article>`).join('')||'Keine Treffer'}catch(e){toast(e.message)}};
window.readMail=async(a,id,f)=>{try{let d=await api(`/api/mail/${a}/${encodeURIComponent(id)}?folder=${encodeURIComponent(f)}`);$('#mailResults').insertAdjacentHTML('afterbegin',`<article class=card><h3>${esc(d.subject)}</h3><pre>${esc(d.text)}</pre></article>`)}catch(e){toast(e.message)}};
window.archiveMail=async(a,id,f)=>{try{await api('/api/mail/archive',{method:'POST',body:JSON.stringify({account:a,message_id:id,folder:f})});toast('Archiviert');$('#mailSearch').click()}catch(e){toast(e.message)}};
window.deleteMail=async(a,id,f)=>{try{await requestConfirmation('mail_delete',`E-Mail ${id} aus ${a}/${f} löschen`,{account:a,message_id:id,folder:f});toast('Löschung wartet auf Bestätigung')}catch(e){toast(e.message)}};
$('#mailCompose').onsubmit=async e=>{e.preventDefault();let mode=e.submitter.value,p={account:$('#mailAccount').value,to:$('#mailTo').value,subject:$('#mailSubject').value,body:$('#mailBody').value};try{if(mode==='draft'){await api('/api/mail/draft',{method:'POST',body:JSON.stringify(p)});toast('Entwurf gespeichert')}else{await requestConfirmation('mail_send',`E-Mail über ${p.account} an ${p.to}: ${p.subject}`,p);toast('Versand wartet auf Bestätigung')}}catch(x){toast(x.message)}};
async function loadCalendar(){try{let d=await api('/api/calendar');$('#calendarEvents').innerHTML=d.map(x=>`<div class=source><b>${esc(x.title)}</b><br><small>${fmt(x.starts_at)} · ${esc(x.location)}</small></div>`).join('')||'Keine Termine'}catch(e){toast(e.message)}}
$('#calendarForm').onsubmit=async e=>{e.preventDefault();let p={title:$('#calendarTitle').value,starts_at:$('#calendarStart').value,ends_at:$('#calendarEnd').value,location:$('#calendarLocation').value,description:$('#calendarDescription').value};try{await requestConfirmation('calendar_create',`Termin erstellen: ${p.title} am ${p.starts_at}`,p);toast('Termin wartet auf Bestätigung')}catch(x){toast(x.message)}};loadCalendar();
$('#reposRun').onclick=async()=>{try{let d=await api('/api/coding/repositories');$('#repos').innerHTML=d.map((r,i)=>`<div class=source><b>${esc(r.path)}</b><br><small>${esc(r.branch)} · ${r.dirty?'geändert':'sauber'}</small><br><button onclick="repoTest(${i})">Tests</button></div>`).join('');window.repoData=d}catch(e){toast(e.message)}};window.repoTest=async i=>{toast('Tests laufen…');try{let d=await api('/api/coding/test',{method:'POST',body:JSON.stringify({repo:repoData[i].path})});toast(`Tests: ${d.status}`)}catch(e){toast(e.message)}};
$('#loadConfirmations').onclick=loadConfirmations;
function confirmHtmlBtn(payload){const vals=Object.values(payload||{});const html=vals.find(v=>typeof v==='string'&&(v.trimStart().toLowerCase().startsWith('<!doctype')||v.trimStart().startsWith('<html')||v.includes('<body')));if(!html)return '';const id='htmlprev_'+Math.random().toString(36).slice(2);window._htmlPreviews=window._htmlPreviews||{};window._htmlPreviews[id]=html;return ` <button onclick="openHtmlPreview('${id}')">Im Browser öffnen</button>`}
window.openHtmlPreview=function(id){const html=window._htmlPreviews&&window._htmlPreviews[id];if(!html)return;const blob=new Blob([html],{type:'text/html'});const url=URL.createObjectURL(blob);window.open(url,'_blank','noopener')};
async function loadConfirmations(){try{let d=await api('/api/confirmations');$('#confirmations').innerHTML=d.map(c=>`<article class=card><span class=eyebrow>${esc(c.action_type)}${c.agent?' · '+esc(c.agent):''}${c.runde>1?' · Runde '+c.runde:''}</span><h3>${esc(c.summary)}</h3><pre>${esc(JSON.stringify(c.payload,null,2))}</pre><div class=agentrun><button onclick="decide('${c.id}','approve')">Bestätigen</button> <button onclick="decide('${c.id}','reject')" style="background:#64748b">Ablehnen</button>${c.agent?`<input id="an_conf_${c.id}" placeholder="Anmerkung – nötig für eine Revision"><button class=ghost onclick="confirmRevision('${c.id}')">Zurück damit</button>`:''}${confirmHtmlBtn(c.payload)}</div></article>`).join('')||'<p>Keine offenen Bestätigungen.</p>'}catch(e){toast(e.message)}}window.decide=async(id,d)=>{try{await api(`/api/confirmations/${id}/${d}`,{method:'POST'});loadConfirmations()}catch(e){toast(e.message)}};window.confirmRevision=async id=>{const a=($('#an_conf_'+id)?.value||'').trim();if(!a)return toast('Für eine Revision brauche ich eine Anmerkung – sonst kommt dasselbe zurück.');try{await api(`/api/confirmations/${id}/revision`,{method:'POST',body:JSON.stringify({anmerkung:a})});toast('Zurück an den Mitarbeiter.');loadConfirmations()}catch(e){toast(e.message)}};
$('#loadAktiv')?.addEventListener('click',loadAktiv);async function loadAktiv(){try{let d=await api('/api/agents/aktiv');$('#aktiv').innerHTML=d.map(a=>{
  if(a.status==='aktiv'){let sek=Math.max(0,Math.round((Date.now()-new Date(a.seit))/1000));let dauer=sek<60?sek+'s':Math.round(sek/60)+'min';
    return `<div class=row><span>🟢 <b>${esc(a.agent)}</b> ${esc(a.person||'')} · ${esc((a.task||'').slice(0,70))}</span><small>läuft seit ${dauer}</small></div>`}
  if(a.status==='idle'){return `<div class=row><span>⚪ <b>${esc(a.agent)}</b> ${esc(a.person||'')}</span><small>zuletzt ${esc(a.letzter_status)} · ${fmt(a.letzter_lauf_am)}</small></div>`}
  return `<div class=row><span>⚪ <b>${esc(a.agent)}</b> ${esc(a.person||'')}</span><small class=muted>noch nie gelaufen</small></div>`
}).join('')||'<p class="muted small">Kein Team angelegt.</p>'}catch(e){toast(e.message)}}
setInterval(()=>{if($('[data-page="Schreibtisch"]')?.classList.contains('active'))loadAktiv()},8000);
$('#loadNotifications')?.addEventListener('click',loadNotifications);async function loadNotifications(){try{let d=await api('/api/notifications');$('#notifications').innerHTML=d.slice(0,5).map(n=>`<div class=row><span>${esc(n.message||'')}</span><span><small>${esc((n.created_at||'').slice(0,16).replace('T',' '))}</small> <button onclick="markNotificationRead('${esc(n.id)}')">Gelesen</button></span></div>`).join('')||'<p>Keine ungelesenen Meldungen.</p>'}catch(e){toast(e.message)}}
window.markNotificationRead=async id=>{try{await api(`/api/notifications/${id}/read`,{method:'POST'});loadNotifications()}catch(e){toast(e.message)}};
$('#readAllNotifications')?.addEventListener('click',async()=>{try{let r=await api('/api/notifications/read_all',{method:'POST'});toast(`${r.markiert} als gelesen markiert.`);loadNotifications()}catch(e){toast(e.message)}});
let reviewArt='';
// Die Abnahme-Liste (#reviews) steht auf dem Schreibtisch, nicht im System-Tab.
// Der Klick fuehrte frueher nach 'System' und scrollte zu einem Element, das
// dort gar nicht liegt – die Liste blieb ungeladen und unsichtbar.
function revToggle(art){reviewArt=reviewArt===art?'':art;show('Schreibtisch');
  loadReviews().then(()=>$('#reviews')?.scrollIntoView({behavior:'smooth'}))}
$$('.toggle.rev').forEach(b=>b.onclick=()=>revToggle(b.dataset.art));
$('#loadReviews').onclick=()=>{reviewArt='';loadReviews()};async function loadReviews(){try{let d=await api('/api/reviews'+(reviewArt?'?art='+encodeURIComponent(reviewArt):''));$('#reviews').innerHTML=d.vorlagen.map(v=>`<article class="card agentcard"><span class=eyebrow>${esc(v.art)} · ${esc(v.person||v.agent)}${v.runde>1?' · Runde '+v.runde:''}</span><h3>${esc(v.titel)}</h3><pre class="auszug">${esc(v.auszug)||'<ohne Text>'}</pre><div class=agentrun><button onclick="openReview('${v.id}')">Ansehen</button><input id="an_${v.id}" placeholder="Anmerkung – nötig für eine Revision"><button onclick="reviewDecide('${v.id}','abnehmen')">Abnehmen</button><button class=ghost onclick="reviewDecide('${v.id}','revision')">Zurück damit</button></div></article>`).join('')||`<p>${reviewArt?'Nichts zur Abnahme in dieser Art.':'Nichts zur Abnahme.'}</p>`;tickReview(d.zusammenfassung);ampeln(d.offen_nach_art)}catch(e){toast(e.message)}}
/* Ampeln zeigen nur den aktuellen Zustand: grün wenn etwas anliegt, sonst rot. */
const REV_FEST=['grafik','post','email','newsletter'];
function ampeln(nachArt){if(!nachArt)return;
  // Arten ohne feste Ampel (dokument, recherche, sequenz, sonstiges) bekommen
  // eine, sobald etwas offen ist – sonst faellt genau die Art durch, die keine
  // Lampe hat. Nichts Offenes darf ohne sichtbaren Weg dorthin bleiben.
  const extra=$('#revExtra');
  if(extra)extra.innerHTML=Object.entries(nachArt).filter(([a,n])=>n>0&&!REV_FEST.includes(a))
    .map(([a,n])=>`<button class="toggle rev" data-art="${esc(a)}" title="${n} ${esc(a)} zur Freigabe"><span class="lamp"></span><small>${esc(a[0].toUpperCase()+a.slice(1))}</small></button>`).join('');
  $$('.toggle.rev').forEach(b=>{b.onclick=()=>revToggle(b.dataset.art);
    let n=nachArt[b.dataset.art]||0;b.classList.toggle('hat',n>0);b.classList.toggle('on',b.dataset.art===reviewArt);
    b.title=n?`${n} ${b.dataset.art} zur Freigabe`:`Nichts ${b.dataset.art} zur Freigabe`})}
/* ---- Vorschau-Overlay: Texte formatiert, Bilder inline, Seiten gerendert ---- */
function mdRender(t){const z=esc(t||'');const zeilen=z.split('\n');let html='',inList=false,inTable=false;
 const flush=()=>{if(inList){html+='</ul>';inList=false}if(inTable){html+='</table>';inTable=false}};
 for(const l of zeilen){
  if(/^\|/.test(l)){if(!inTable){html+='<table class="mdtable">';inTable=true}
   if(/^\|[\s:-]+\|/.test(l.replace(/\|/g,'|')) && /^[\s|:-]+$/.test(l))continue;
   html+='<tr>'+l.split('|').slice(1,-1).map(c=>`<td>${c.trim()}</td>`).join('')+'</tr>';continue}
  if(inTable){html+='</table>';inTable=false}
  if(/^######?\s/.test(l)){flush();html+=`<h4>${l.replace(/^#+\s*/,'')}</h4>`}
  else if(/^###\s/.test(l)){flush();html+=`<h4>${l.slice(4)}</h4>`}
  else if(/^##\s/.test(l)){flush();html+=`<h3>${l.slice(3)}</h3>`}
  else if(/^#\s/.test(l)){flush();html+=`<h2>${l.slice(2)}</h2>`}
  else if(/^\s*[-*]\s/.test(l)){if(!inList){html+='<ul>';inList=true}html+=`<li>${l.replace(/^\s*[-*]\s/,'')}</li>`}
  else if(/^---+$/.test(l.trim())){flush();html+='<hr>'}
  else if(l.trim()===''){flush();html+='<br>'}
  else{flush();html+=`<p>${l}</p>`}}
 flush();return html.replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>')}
function overlayShow(){$('#overlay').hidden=false;document.body.style.overflow='hidden'}
function overlayHide(){$('#overlay').hidden=true;document.body.style.overflow='';$('#overlayBody').innerHTML='';$('#overlayTabs').innerHTML='';$('#overlayActions').innerHTML=''}
$('#overlayClose').onclick=overlayHide;$('#overlay').onclick=e=>{if(e.target.id==='overlay')overlayHide()};
function dateiUrl(p){return '/api/austausch/datei?pfad='+encodeURIComponent(p.trim())}
/* Inhalte werden per fetch geholt und in den Rahmen gereicht (srcdoc / Blob):
   Ein sandboxed iframe bekommt vom Browser keine Basic-Auth mit – direktes
   src= lieferte 401 und die Browser-Seite "Erneut versuchen". */
function quelleTab(p){const q=p.trim(),n=q.split('/').pop();
 if(/\.(png|jpe?g|webp|gif|svg)$/i.test(q))return{name:n,lazy:async()=>{
   const r=await fetch(dateiUrl(q));if(!r.ok)throw Error('Bild nicht ladbar ('+r.status+')');
   const url=URL.createObjectURL(await r.blob());
   return `<img class="ovimg" src="${url}" alt="${esc(n)}">`}};
 if(/\.html?$/i.test(q))return{name:n,lazy:async()=>{
   const r=await fetch(dateiUrl(q));if(!r.ok)throw Error('Seite nicht ladbar ('+r.status+')');
   const roh=await r.text();
   const burl=URL.createObjectURL(new Blob([roh],{type:'text/html'}));
   return `<div style="margin-bottom:8px"><button onclick="window.open('${burl}','_blank','noopener')">Im Browser öffnen</button></div>`
        + `<iframe class="oviframe" sandbox="" srcdoc="${esc(roh)}"></iframe>`
        + `<details><summary class=small>Quelltext anzeigen</summary>`
        + `<pre class="auszug" style="max-height:60vh">${esc(roh)}</pre></details>`}};
 return{name:n,html:null,pfad:q}}
window.openReview=async id=>{try{const v=await api('/api/reviews/'+id);
 $('#overlayTitle').textContent=v.titel||'';
 const tabs=[{name:'Inhalt',html:`<div class="ovmd">${mdRender(v.inhalt||'<ohne Text>')}</div>`}];
 (v.quelle||'').split(',').map(s=>s.trim()).filter(s=>s.startsWith('/')||s.startsWith('austausch/')).forEach(p=>{
  const t=quelleTab(p);
  if(t.lazy)tabs.push(t);
  else tabs.push({name:t.name,lazy:async()=>{const r=await fetch(dateiUrl(t.pfad));const txt=await r.text();return `<div class="ovmd">${mdRender(txt)}</div>`}});
 });
 if((v.verlauf||'').trim())tabs.push({name:'Verlauf',html:`<div class="ovmd"><pre style="white-space:pre-wrap">${esc(v.verlauf)}</pre></div>`});
 $('#overlayTabs').innerHTML=tabs.map((t,i)=>`<button class="${i?'ghost':''}" data-tab="${i}">${esc(t.name)}</button>`).join('');
 const zeig=async i=>{[...$('#overlayTabs').children].forEach((b,j)=>b.className=j==i?'':'ghost');
  const t=tabs[i];$('#overlayBody').innerHTML=t.html??await t.lazy().catch(e=>`<p>${esc(String(e))}</p>`)};
 [...$('#overlayTabs').children].forEach((b,i)=>b.onclick=()=>zeig(i));
 $('#overlayActions').innerHTML=`<input id="ovAn" placeholder="Anmerkung – nötig für eine Revision"><button onclick="overlayDecide('${esc(v.id)}','abnehmen')">Abnehmen</button><button class=ghost onclick="overlayDecide('${esc(v.id)}','revision')">Zurück damit</button>`;
 zeig(0);overlayShow()}catch(e){toast(e.message)}};
window.overlayDecide=async(id,was)=>{const a=($('#ovAn')?.value||'').trim();if(was==='revision'&&!a)return toast('Für eine Revision brauche ich eine Anmerkung.');
 try{await api(`/api/reviews/${id}/${was}`,{method:'POST',body:JSON.stringify({anmerkung:a})});toast(was==='abnehmen'?'Abgenommen.':'Zurück an den Verfasser.');overlayHide();loadReviews()}catch(e){toast(e.message)}};
/* ---- Aufträge ---- */
$('#loadAuftraege').onclick=()=>loadAuftraege();
async function loadAuftraege(){try{const d=await api('/api/auftraege');const offen=d.filter(a=>!['abgenommen','abgebrochen'].includes(a.status));
 $('#auftraege').innerHTML=offen.map(a=>`<article class=card><span class=eyebrow>${esc(a.agent)} · ${esc(a.status)}${a.faellig_am?' · fällig '+esc(a.faellig_am.slice(0,10)):''}</span><h3>${esc(a.titel)}</h3><details><summary class=small>Details</summary><p class=small>${esc(a.beschreibung||'')}</p></details></article>`).join('')||'<p>Keine offenen Aufträge.</p>'}catch(e){toast(e.message)}}
window.reviewDecide=async(id,was)=>{let a=($('#an_'+id)?.value||'').trim();if(was==='revision'&&!a)return toast('Für eine Revision brauche ich eine Anmerkung – sonst kommt dasselbe zurück.');try{await api(`/api/reviews/${id}/${was}`,{method:'POST',body:JSON.stringify({anmerkung:a})});toast(was==='abnehmen'?'Abgenommen.':'Zurück an den Verfasser.');loadReviews()}catch(e){toast(e.message)}};
function tickReview(z){z=z||{};let n=z.offen||0,r=z.revision||0,p=z.pruefung||0,e=$('#tickReview');
 if(e)e.textContent=[n?`${n} offen`:'',p?`${p} bei Jude`:'',r?`${r} in Revision`:''].filter(Boolean).join(' · ')||'nichts offen'}
async function pollReviews(){try{let d=await api('/api/reviews?limit=1');tickReview(d.zusammenfassung);ampeln(d.offen_nach_art);
  // Liegt der Schreibtisch offen, auch die Liste nachziehen: sonst zeigt eine
  // stundenlang offene Seite den Stand vom Seitenwechsel.
  if(document.querySelector('section.active')?.dataset.page==='Schreibtisch')loadReviews()}
  catch(e){}finally{setTimeout(pollReviews,60000)}}pollReviews();
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
  // Groq-Anzeige am 02.09.2026 entfernt, zusammen mit dem Provider selbst.
  const gq='';
  $('#hudTokens').innerHTML=`<div class=row><span>Input</span><b>${Number(u.input_tokens).toLocaleString('de-DE')}</b></div><div class=row><span>Output</span><b>${Number(u.output_tokens).toLocaleString('de-DE')}</b></div><div class=row><span>Budget</span><b>$${Number(s.router.budget_used_usd).toFixed(3)} / $${Number(s.router.budget_limit_usd).toFixed(0)}</b></div>`+ctx+gq
}catch(e){}finally{setTimeout(pollTokens,15000)}}
async function pollTicker(){if(toggles.market){try{const x=await api('/api/market/XAU%2FUSD?interval=1h&limit=2&refresh=true');const c=x.candles.at(-1),pr=x.candles.at(-2);if(c)$('#tickXau').innerHTML=`<span class="${!pr||c.close>=pr.close?'up':'down'}">${c.close.toFixed(2)}</span>`}catch(e){}
 try{const b=await api('/api/market/BTC%2FUSD?interval=1h&limit=2&refresh=true');const c=b.candles.at(-1),pr=b.candles.at(-2);if(c)$('#tickBtc').innerHTML=`<span class="${!pr||c.close>=pr.close?'up':'down'}">${Number(c.close).toLocaleString('de-DE',{maximumFractionDigits:0})}</span>`}catch(e){}}
 setTimeout(pollTicker,120000)}
let newsItems=[],newsIdx=0;
async function pollNews(){if(toggles.news){try{const d=await api('/api/briefing');newsItems=Object.entries(d.headlines||{}).flatMap(([topic,items])=>items.map(t=>`${topic}: ${t}`));if(d.markets&&d.markets.length){$('#tickXau').textContent=(d.markets.find(m=>m.label==='Gold')||{}).price?.toFixed(0)??$('#tickXau').textContent;const btc=d.markets.find(m=>m.label==='Bitcoin');if(btc)$('#tickBtc').innerHTML=`<span class="${btc.change_pct>=0?'up':'down'}">${btc.price.toLocaleString('de-DE',{maximumFractionDigits:0})}</span>`}}catch(e){}}setTimeout(pollNews,600000)}
setInterval(()=>{if(newsItems.length){newsIdx=(newsIdx+1)%newsItems.length;const n=newsItems[newsIdx];$('#tickNews').textContent=n}},8000);
window.noteModel=m=>{if(m)$('#tickModel').textContent=m};
// Schalter (Markt/News clientseitig, Stimme serverseitig)
const toggles=JSON.parse(localStorage.getItem('judeToggles')||'{"market":true,"news":true}');
function renderToggles(){$('#tglMarket').classList.toggle('on',toggles.market);$('#tglNews').classList.toggle('on',toggles.news);$('#tglVoice').classList.toggle('on',voiceRunning)}
for(const[id,key]of[['#tglMarket','market'],['#tglNews','news']])$(id).onclick=()=>{toggles[key]=!toggles[key];localStorage.setItem('judeToggles',JSON.stringify(toggles));renderToggles()};
$('#tglVoice').onclick=()=>$('#voiceToggle').click();
const _vb=voiceBadge;voiceBadge=s=>{_vb(s);$('#tickVoice').textContent=s.running?(s.state||'an'):'aus';renderToggles()};
$('#wakeForm')&&($('#wakeForm').onsubmit=async e=>{e.preventDefault();
 try{const d=await api('/api/voice/phrases',{method:'POST',body:JSON.stringify({wake_word:$('#wakeWord').value,sleep_word:$('#sleepWord').value,greeting:$('#wakeGreeting').value,farewell:$('#wakeFarewell').value})});
  $('#wakeState').textContent=`aktiv: „${d.wake_word}" · Schlafwort: „${d.sleep_word}"`;toast('Aktivierungswort übernommen')}
 catch(x){toast(x.message)}});
api('/api/voice').then(d=>{if($('#wakeWord')){$('#wakeWord').value=d.wake_word||'';$('#sleepWord').value=d.sleep_word||'';$('#wakeGreeting').value=d.greeting||'';$('#wakeFarewell').value=d.farewell||'';
 $('#wakeState').textContent=`aktiv: „${d.wake_word}"`}}).catch(()=>{});
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
  $('#agentList').innerHTML=(d.agents||[]).map(a=>`<article class="card agentcard"><details><summary><b>${esc(a.person||a.name)}</b> · ${esc(a.name)}${a.model?` · <span class=small>${esc(a.model)}</span>`:''}</summary><p class=role>${esc(a.role)}</p><p class=skills>${(a.skills||[]).map(esc).join(' · ')||'keine Skills'}</p><div class=agentrun><input placeholder="Aufgabe für ${esc(a.name)}" data-agent="${esc(a.name)}"><button onclick="runAgent('${esc(a.name)}',this)">Beauftragen</button><button onclick="delAgent('${esc(a.name)}')" style="background:#64748b">Entfernen</button></div><div class=agentout></div></details></article>`).join('')||'<p>Noch keine Mitarbeiter. Lege oben einen an.</p>'}catch(e){toast(e.message)}}
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
$('#austauschForm').onsubmit=async e=>{e.preventDefault();const f=e.target,btn=f.querySelector('button');btn.disabled=true;$('#austauschStatus').textContent='Dokument wird übergeben…';try{let r=await fetch('/api/austausch/upload',{method:'POST',body:new FormData(f)}),d=await r.json();if(d.error)throw Error(d.error);let hinweis=f.querySelector('[name=hinweis]').value;let c=await api('/api/chat',{method:'POST',body:JSON.stringify({text:`Neues Dokument von mir im Austausch (an-team): "${d.name}". ${hinweis?('Hinweis: '+hinweis+'. '):''}Stell es mit dokument_zustellen den Mitarbeitern zu, die es betrifft.`})});$('#austauschStatus').textContent='Jude: '+c.answer;f.reset()}catch(err){$('#austauschStatus').textContent='';toast(err.message)}finally{btn.disabled=false}};
