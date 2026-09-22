const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
let current='overview', tableData=[], selectedReservation=null, modalTrigger=null, modalBusy=0;
const pendingAlertAcks=new Set();
function toast(t){const el=$('#toast');el.textContent=t;el.classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.classList.remove('show'),4000)}
function pretty(v){if(v===null||v===undefined||v==='')return '—';if(typeof v==='boolean')return v?'Yes':'No';return String(v)}
function esc(v){return pretty(v).replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]))}
function fmtTime(s){if(!s)return '—';try{return new Date(s).toLocaleString()}catch{return s}}
let adminCsrf=null,tableGeneration=0,overviewGeneration=0,modalGeneration=0;
async function timedFetch(url,opts={}){const c=new AbortController(),t=setTimeout(()=>c.abort(),10000);try{return await fetch(url,{...opts,signal:c.signal});}finally{clearTimeout(t);}}
async function json(url,opts={}){if(!adminCsrf){const r=await timedFetch('/auth/admin/session',{cache:'no-store'});if(!r.ok){if(r.status!==401&&r.status!==403)throw Error('Session service unavailable; refresh to retry');document.body.replaceChildren();location.replace('/static/login.html');throw Error('Admin authentication required');}adminCsrf=(await r.json()).csrf;}
const r=await timedFetch(url,{...opts,cache:'no-store',headers:{...opts.headers,'X-CSRF-Token':adminCsrf}});let d={};try{d=await r.json()}catch{throw new Error('Invalid service response; refresh to retry')}if(r.status===401||r.status===403){document.body.replaceChildren();location.replace('/static/login.html');throw Error('Admin authentication required');}if(!r.ok)throw new Error(d.error||`Request failed (${r.status})`);return d;}
$('#adminLogout').onclick=async()=>{try{await json('/auth/admin/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});document.body.replaceChildren();location.replace('/static/login.html');}catch(e){toast(e.message)}};

function statusBadge(s){s=String(s||'offline').toLowerCase();return s.includes('active')||s.includes('occup')?'red':s.includes('reserv')||s.includes('confirm')||s.includes('pending')?'yellow':s.includes('avail')||s==='online'||s.includes('complet')?'green':'gray'}
async function loadOverview(){const generation=++overviewGeneration;const d=await json('/api/overview');if(generation!==overviewGeneration||current!=='overview')return;const st=d.state||{},l=st.locker||{};$('#kUsers').textContent=d.kpi.users;$('#kBookings').textContent=d.kpi.activeReservations;$('#kAlerts').textContent=d.kpi.alerts;$('#kRevenue').textContent=`${Number(d.kpi.demoBookingValue||0).toFixed(2)} EGP`;$('#kRevenue').title=`Demo only, including final late-fee assessments. Excludes ${d.kpi.operatorReviewBookings||0} operator-review and ${d.kpi.legacyReviewBookings||0} legacy-review bookings. Actual Collected Revenue: N/A`;$('#brokerDot').classList.toggle('on',!!st.brokerConnected);$('#brokerText').textContent=st.brokerConnected?'Broker Online':'Broker Offline';$('#liveStatus').textContent=pretty(l.status);$('#liveDoor').textContent=pretty(l.door);$('#liveBattery').textContent=l.battery==null?'—':`${l.battery}%`;$('#liveLast').textContent=fmtTime(l.lastSeen);const b=$('#liveBadge');b.className=`badge ${statusBadge(l.online?l.status:'offline')}`;b.textContent=l.online?String(l.status||'online').toUpperCase():'OFFLINE';const p=$('#pinA');p.className=`pin pA ${statusBadge(l.online?l.status:'offline')}`;renderMini('#activeRes',d.activeReservations,r=>[`Locker ${r.Locker||'A'} • ${r['User Name']||r['User Mobile']||'User'}`,`${r.Date||''} ${r['Start Time']||''}–${r['End Time']||''} UTC • ${r.Status||''}`]);renderMini('#latestAlerts',d.alerts,a=>[a['Alert Type']||'Alert',`${a.Severity||''} • ${a.Message||''}`]);}
function renderMini(sel,data,map){const h=$(sel);h.innerHTML='';if(!data?.length){h.innerHTML='<div class="mini"><div><b>No records yet</b><small>Live data will appear here.</small></div></div>';return}data.slice(0,6).forEach(x=>{const [a,b]=map(x);h.insertAdjacentHTML('beforeend',`<div class="mini"><div><b>${esc(a)}</b><small>${esc(b)}</small></div></div>`)});}
const titles={lockers:['LOCKERS','Locker Fleet'],reservations:['RESERVATIONS','Reservations'],users:['CUSTOMERS','Users'],access:['SECURITY','Access Logs'],alerts:['OPERATIONS','Alerts'],payments:['FINANCE','Demo Values & Fee Assessments'],feedback:['EXPERIENCE','Feedback'],audit:['ADMIN CONTROL','Admin Audit Log']};
async function openTable(name){
 const generation=++tableGeneration;if(current!==name)$('#searchBox').value='';current=name;
 $$('.view').forEach(v=>v.classList.remove('active'));$('#tableView').classList.add('active');$$('.nav').forEach(n=>n.classList.toggle('active',n.dataset.view===name));
 $('#pageTitle').textContent=titles[name][1];$('#tableEyebrow').textContent=titles[name][0];$('#tableTitle').textContent=titles[name][1];
 tableData=[];$('#thead').replaceChildren();$('#tbody').innerHTML='<tr><td>Loading records…</td></tr>';
 try{const rows=await json('/api/'+name);if(current!==name||generation!==tableGeneration)return;if(!Array.isArray(rows))throw Error('Invalid reporting response');tableData=rows;filterTable();}
 catch(error){if(current===name&&generation===tableGeneration)$('#tbody').innerHTML='<tr><td>Records unavailable. Use Refresh to retry.</td></tr>';throw error;}
}

function columnLabel(key){return ['Date','Start Time','End Time','Created At','Updated At','Completed At','Cancelled At','Timestamp'].includes(key)?key+' (UTC)':key;}
function filterTable(){const q=$('#searchBox').value.toLowerCase();renderTable(tableData.filter(r=>Object.entries(r).filter(([k])=>!k.startsWith('_')).some(([,v])=>String(v??'').toLowerCase().includes(q))));}
function renderTable(data){
 const thead=$('#thead'),tbody=$('#tbody');thead.replaceChildren();tbody.replaceChildren();
 if(!data?.length){tbody.innerHTML='<tr><td>No matching records.</td></tr>';return;}
 const keys=Object.keys(data[0]).filter(k=>!k.startsWith('_')),reservation=current==='reservations',alerts=current==='alerts';
 thead.innerHTML='<tr>'+keys.map(k=>`<th scope="col">${esc(columnLabel(k))}</th>`).join('')+((reservation||alerts)?'<th scope="col">Actions</th>':'')+'</tr>';
 for(const r of data){
  const row=document.createElement('tr');row.innerHTML=keys.map(k=>`<td>${esc(r[k])}</td>`).join('');
  if(reservation||alerts){
   const cell=document.createElement('td'),button=document.createElement('button');button.className='tableAction';button.type='button';
   if(reservation){button.textContent='View / Control';button.dataset.booking=r['Booking ID'];button.onclick=()=>openReservation(r['Booking ID']);}
   else {
    const ref=r._ack,key=ref?JSON.stringify(ref):'';
    const ack=r.Acknowledged===true;
    if(ack)pendingAlertAcks.delete(key);
    button.textContent=ack?'Acknowledged':pendingAlertAcks.has(key)?'Acknowledgement pending':'Acknowledge';
    button.disabled=ack||!ref||pendingAlertAcks.has(key);
    button.onclick=async()=>{
     if(button.disabled)return;button.disabled=true;pendingAlertAcks.add(key);button.textContent='Saving…';
     try{const d=await json('/api/alerts/ack',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({row:ref.row,expectedIdentity:ref.identity})});
      if(d.excelSynchronized)pendingAlertAcks.delete(key);
      toast(d.excelSynchronized?'Alert acknowledged.':'Acknowledgement saved; reporting synchronization pending.');
      if(current==='alerts')await openTable('alerts');
     }catch(error){pendingAlertAcks.delete(key);button.disabled=false;button.textContent='Acknowledge';toast(error.message);}
    };
   }
   cell.append(button);row.append(cell);
  }
  tbody.append(row);
 }
}
function showOverview(){current='overview';$$('.view').forEach(v=>v.classList.remove('active'));$('#overview').classList.add('active');$$('.nav').forEach(n=>n.classList.toggle('active',n.dataset.view==='overview'));$('#pageTitle').textContent='Overview';loadOverview().catch(e=>toast('Displayed data may be stale: '+e.message))}
async function openReservation(id){const generation=++modalGeneration;try{const r=await json('/api/reservations/'+encodeURIComponent(id));if(generation!==modalGeneration)return;const wasOpen=$('#reservationModal').classList.contains('open');if(!wasOpen)modalTrigger=document.activeElement;selectedReservation=r;$('#modalTitle').textContent=`Reservation ${selectedReservation['Booking ID']||''}`;const fields=['Booking ID','Locker','User Name','User Mobile','Date','Start Time','End Time','Status','Payment Status','Base Amount','Late Fee','Final Total','Financial Outcome','Created At','Completed At','Cancelled At'];$('#reservationDetails').innerHTML=fields.map(k=>`<div><span>${esc(columnLabel(k))}</span><b class="${k==='Status'?'statusText '+statusBadge(selectedReservation[k]):''}">${esc(selectedReservation[k])}</b></div>`).join('');$('#revealedPin').textContent='••••';const terminal=['cancelled','completed'].includes(String(selectedReservation.Status||'').toLowerCase());$('#reservationCancelBtn').disabled=terminal;$('#reservationModal').classList.add('open');$('#reservationModal').setAttribute('aria-hidden','false');$('.shell').inert=true;if(!wasOpen)$('[data-close-modal].iconBtn').focus()}catch(e){toast(e.message)}}
function closeModal(force=false){if(modalBusy&&!force)return;modalGeneration++;$('#revealedPin').textContent='••••';$('#reservationModal').classList.remove('open');$('#reservationModal').setAttribute('aria-hidden','true');selectedReservation=null;$('.shell').inert=false;(modalTrigger?.isConnected?modalTrigger:$('#refreshBtn')).focus();modalTrigger=null}
$$('[data-close-modal]').forEach(x=>x.onclick=()=>closeModal());
document.addEventListener('keydown',e=>{if(!$('#reservationModal').classList.contains('open'))return;if(e.key==='Escape'){e.preventDefault();closeModal();}else if(e.key==='Tab'){const items=[...$('#reservationModal').querySelectorAll('button:not(:disabled)')].filter(b=>b.getClientRects().length),first=items[0],last=items.at(-1);if(e.shiftKey&&(document.activeElement===first||!items.includes(document.activeElement))){e.preventDefault();last?.focus();}else if(!e.shiftKey&&(document.activeElement===last||!items.includes(document.activeElement))){e.preventDefault();first?.focus();}}});
$('#revealPinBtn').onclick=async()=>{if(!selectedReservation)return;if(!confirm('Reveal this reservation PIN? This action will be audited.'))return;try{const id=selectedReservation['Booking ID'];const d=await json(`/api/reservations/${encodeURIComponent(id)}/reveal-pin`,{method:'POST'});if(selectedReservation?.['Booking ID']!==id||!$('#reservationModal').classList.contains('open'))return;$('#revealedPin').textContent=d.pin||'—';toast('Reservation PIN revealed and audited')}catch(e){toast(e.message)}};
$('#reservationUnlockBtn').onclick=async()=>{if(!selectedReservation)return;if(!confirm(`Emergency unlock Locker ${selectedReservation.Locker||'A'}? This action will be audited.`))return;try{const d=await json('/api/cmd/unlock',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({bookingId:selectedReservation['Booking ID'],locker:selectedReservation.Locker||'A'})});toast(d.mqttPublished?'Command published; physical result unconfirmed':'Command outcome unknown');watchDeviceCommand(d)}catch(e){toast(e.message)}};
$('#reservationCancelBtn').onclick=async()=>{if(!selectedReservation)return;if(!confirm(`Cancel reservation ${selectedReservation['Booking ID']}? This updates the central database and publishes a cancellation event.`))return;try{const d=await json(`/api/reservations/${encodeURIComponent(selectedReservation['Booking ID'])}/cancel`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expectedRevision:selectedReservation.Revision})});toast(d.excelSynchronized?'Reservation cancelled; user apps refresh from the backend':'Reservation cancelled; Excel synchronization pending');closeModal(true);await openTable('reservations');loadOverview().catch(()=>{})}catch(e){toast(e.message)}};
$$('.nav').forEach(n=>n.onclick=()=>n.dataset.view==='overview'?showOverview():openTable(n.dataset.view).catch(e=>toast(e.message)));$$('[data-goto]').forEach(b=>b.onclick=()=>openTable(b.dataset.goto).catch(e=>toast(e.message)));$('#refreshBtn').onclick=()=>(current==='overview'?loadOverview():openTable(current)).catch(e=>toast('Displayed data may be stale: '+e.message));$('#searchBox').oninput=filterTable;$('#adminUnlock').onclick=async()=>{if(!confirm('Send owner emergency remote unlock command to Locker A? This action will be audited.'))return;try{const d=await json('/api/cmd/unlock',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});toast(d.mqttPublished?'Command published; physical result unconfirmed':'Command outcome unknown');watchDeviceCommand(d)}catch(e){toast(e.message)}};
loadOverview().catch(e=>toast('Overview unavailable: '+e.message));setInterval(async()=>{
  try{
    if(current==='overview'){await loadOverview();return;}
    if(!['reservations','lockers'].includes(current))return;
    const generation=++tableGeneration,view=current,rows=await json('/api/'+view);
    if(current!==view||generation!==tableGeneration)return;
    if(!Array.isArray(rows))throw Error('Invalid reporting response');
    tableData=rows;filterTable();
    if(selectedReservation){
      const fresh=await json('/api/reservations/'+encodeURIComponent(selectedReservation['Booking ID']));
      if(selectedReservation&&fresh['Booking ID']===selectedReservation['Booking ID']&&fresh.Revision!==selectedReservation.Revision)await openReservation(fresh['Booking ID']);
    }
  }catch(e){toast('Reservation synchronization unavailable; displayed records may be stale')}
},4000);

async function watchDeviceCommand(d){
 if(!d.requestId)return;
 for(let attempt=0;attempt<9;attempt++){
  await new Promise(resolve=>setTimeout(resolve,1000));
  try{const result=await json('/api/device/commands/'+encodeURIComponent(d.requestId));
   if(!['pending','published'].includes(result.outcome)){toast(result.outcome==='acknowledged'?'Command acknowledged; door state unconfirmed':result.outcome==='rejected'?'Locker rejected the command':'Command outcome unknown');return;}
  }catch{return;}
 }
 toast('Command outcome unknown');
}

for(const selector of ['#reservationCancelBtn','#reservationUnlockBtn','#adminUnlock','#revealPinBtn']){
 const button=$(selector),handler=button.onclick;
 button.onclick=async function(event){if(button.disabled)return;button.disabled=true;modalBusy++;try{await handler.call(this,event);}finally{modalBusy--;button.disabled=selector==='#reservationCancelBtn'&&['cancelled','completed'].includes(String(selectedReservation?.Status||'').toLowerCase());}};
}
