import {installStatistics} from './statistics.js';
import {installManagement,quotedUnits} from './management.js';
const $ = id => document.getElementById(id);
const app = {state: null, csrf: '', connected: false, busy: false, polling: false, needPoll: false,
  generation: 0, loaded: false, page: 'mochi', logJob: '', focusJob: '', selected: {}, recipients: new Set(),
  broadcastPending: false, eligible: [], toastTimer: 0, reportFiles: []};
const terminal = new Set(['Hoàn thành', 'Thất bại', 'Đã hủy', 'Mã không hợp lệ', 'Mã mời lỗi', 'Đạt giới hạn 24h', 'completed', 'failed', 'cancelled']);
const labels = {completed:'Hoàn thành', failed:'Thất bại', cancelled:'Đã hủy', running:'Đang chạy',
  queued:'Đang chờ', scheduled:'Hẹn giờ', invalid_code:'Mã không hợp lệ', invite_limit_reached:'Đạt giới hạn 24h'};
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const n = value => new Intl.NumberFormat('vi-VN').format(Number(value || 0));
const field = (form, name) => $(form).elements.namedItem(name);
const idValue = value => {const text=String(value).trim(); if(!/^-?\d{1,19}$/.test(text)) throw Error('Nhập Chat ID là số nguyên.'); const v=Number(text); return Number.isSafeInteger(v)?v:text;};
const time = value => {if(!value) return ''; const d=new Date(value); return Number.isNaN(+d)?String(value):new Intl.DateTimeFormat('vi-VN',{timeZone:'Asia/Ho_Chi_Minh',dateStyle:'short',timeStyle:'short'}).format(d);};
function money(value) {try {const v=BigInt(String(value || 0)), sign=v<0n?'-':'', a=v<0n?-v:v; return sign+(a/100n).toLocaleString('vi-VN')+(a%100n?','+String(a%100n).padStart(2,'0'):'');} catch {return '—';}}
function toast(message, error=false) {clearTimeout(app.toastTimer); $('toast').textContent=message; $('toast').classList.toggle('error',error); $('toast').hidden=false; app.toastTimer=setTimeout(()=>{$('toast').hidden=true;},6500);}
function status(message) {$('connection-status').textContent=message;}
function page(name) {app.page=name; document.querySelectorAll('[data-view]').forEach(e=>e.hidden=e.dataset.view!==name); document.querySelectorAll('nav [data-page]').forEach(e=>e.classList.toggle('active',e.dataset.page===name)); window.scrollTo(0,0);if(name==='statistics')statistics.load();}
function tab(group, name) {document.querySelectorAll(`[data-tabs="${group}"] [data-tab]`).forEach(e=>e.classList.toggle('active',e.dataset.tab===name)); document.querySelectorAll('[data-panel]').forEach(e=>{if(e.dataset.panel.startsWith(group+':')) e.hidden=e.dataset.panel!==group+':'+name;});}
function newJob() {page('mochi'); tab('mochi-workspace','tasks'); tab('jobs','setup');}
function button(text, action, id='', extra='') {return `<button type="button" ${action==='copy-code'?'':'data-mutation'} data-action="${action}" data-id="${esc(id)}" ${extra}>${esc(text)}</button>`;}
function badge(value) {const text=labels[value] || value || '—'; const type=/Hoàn thành|completed|READY/.test(text)?'good':/Thất bại|FAILED|lỗi|hủy|failed/.test(text)?'bad':/chờ|Hẹn|combo|giới hạn/.test(text)?'wait':''; return `<span class="badge ${type}">${esc(text)}</span>`;}
function table(container, rows, columns, {key='', select='', empty='Chưa có dữ liệu.'}={}) {
  if(!rows.length) {$(container).innerHTML=`<div class="empty">${esc(empty)}</div>`; return;}
  $(container).innerHTML=`<div class="table-wrap"><table><thead><tr>${columns.map(c=>`<th>${esc(c[0])}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr ${key?`data-key="${esc(row[key])}" data-table="${select}" class="${String(app.selected[select])===String(row[key])?'selected':''}"`:''}>${columns.map(c=>`<td>${typeof c[1]==='function'?c[1](row):esc(row[c[1]])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}
function metrics(container, values) {$(container).innerHTML=values.map(([label,value,note=''])=>`<div class="metric"><div class="metric-label">${esc(label)}</div><div class="metric-value">${esc(value)}</div><div class="metric-note">${esc(note)}</div></div>`).join('');}

async function api(path, payload, headers={}) {
  const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),40000);
  try {const response=await fetch(path,{method:payload===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',redirect:'error',signal:controller.signal,
    headers:{...(payload===undefined?{}:{'Content-Type':'application/json','X-Web-CSRF':app.csrf}),...headers},body:payload===undefined?undefined:JSON.stringify(payload)});
    const data=await response.json(); if(!response.ok) throw Error(data.error || 'Chưa xử lý được yêu cầu.'); return data;
  } catch(error) {if(error.name==='AbortError' || error instanceof TypeError) throw Error('Mất kết nối local hoặc VPS. Kiểm tra trạng thái trước khi gửi lệnh mới.'); throw error;} finally {clearTimeout(timer);}
}
async function poll() {
  if(app.busy || app.polling) {app.needPoll=true; return;}
  app.polling=true; app.needPoll=false; const generation=app.generation;
  try {const response=await api('/api/state'); if(generation!==app.generation) return; app.state=response.state; app.connected=true; render(); status('Đã kết nối VPS · Đồng bộ '+new Date().toLocaleTimeString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh'}));}
  catch(error) {if(generation===app.generation) {app.connected=false; status(error.message); $('job-sync').textContent='Chưa đồng bộ được VPS · Đang giữ kết quả lần trước.';}}
  finally {app.polling=false; controls(); if(app.needPoll&&!app.busy) {app.needPoll=false; setTimeout(poll,0);}}
}
async function command(action, payload={}, callback) {
  if(app.busy) return;
  if(!app.connected || app.state?.closing) {toast('Kết nối VPS tại Cài đặt chung trước khi thao tác.',true); return;}
  app.busy=true; app.generation++; controls();
  try {const response=await api('/api/command',{action,payload,request_id:crypto.randomUUID()}); const result=response.result || {}; if(callback) callback(result); if(result.message) toast(result.message); return result;}
  catch(error) {app.connected=false; toast(error.message,true); status(error.message);}
  finally {app.busy=false; await poll(); controls();}
}
function controls() {
  const locked=!app.connected || app.busy || app.state?.closing;
  document.querySelectorAll('[data-mutation]').forEach(e=>e.disabled=!!locked || e.dataset.allowed==='false');
  $('connect-button').disabled=app.busy;
  const telegram=app.state?.telegram || {}, ids=recipientIds(), noticeBusy=!!telegram.broadcast_running || app.broadcastPending;
  $('broadcast-send').disabled=!!locked || !telegram.running || !telegram.ready || telegram.broadcast_recipient_selection!==true || noticeBusy || !ids.length || ids.length>2000 || !$('broadcast-text').value.trim() || $('broadcast-text').value.length>4000;
  $('broadcast-cancel').disabled=!!locked || !noticeBusy;
  $('broadcast-mode').disabled=noticeBusy;
  document.querySelectorAll('#broadcast-people input,[data-action="recipient-all"],[data-action="recipient-none"]').forEach(e=>e.disabled=noticeBusy);
  $('lookup-start').disabled=!!locked || !!app.state?.lookup?.busy;
  $('sidebar-connection').textContent=app.connected?'● Đã kết nối VPS':'○ Chưa kết nối VPS';
  $('sidebar-connection').classList.toggle('online',app.connected);
  quote();
}
function orderedJobs() {return [...(app.state?.jobs || [])].sort((a,b)=>(!terminal.has(b.status))-(!terminal.has(a.status)) || (Date.parse(b.created_at)||0)-(Date.parse(a.created_at)||0));}
function progress(row) {const done=Number(row.success_count||0), total=Number(row.target_count||0), percent=total?Math.min(100,done/total*100):0; return `<div class="progress">${n(done)} / ${n(total)}<div class="progress-bar"><span data-progress="${percent}"></span></div></div>`;}
function renderJobs() {
  const s=app.state, rows=orderedJobs();
  $('job-queue').textContent=`Đang xử lý: ${s.active_jobs} / ${s.job_limit} job · Đang chờ: ${s.queued_jobs} · Hẹn giờ / Combo: ${s.scheduled_jobs}`;
  $('job-sync').textContent=`Đã đồng bộ ${rows.length} job · Job đang xử lý và job mới hiển thị trước.`;
  table('jobs-table',rows,[['Tên khách / Job',r=>esc(r.customer_name || r.job_id)+(r.combo?' <span class="badge">Combo</span>':'')],['Mã mời','invite_code'],['Tiến độ',progress],['Trạng thái',r=>badge(r.status)],['Bean',r=>n(r.success_count*100)+' / '+n(r.target_count*100)],['Người tạo','creator_name'],['Thời gian',r=>esc((r.combo&&r.status==='Chờ lượt combo'?'Tiếp: ':'')+time(r.scheduled_at || r.created_at))],['Thao tác',r=>button('Log','job-logs',r.job_id)+button('Dừng','job-stop',r.job_id,terminal.has(r.status)?'data-allowed="false"':'')+button('Tiếp tục','job-continue',r.job_id,r.can_continue?'':'data-allowed="false"')]],{key:'job_id',select:'jobs'});
  // Use a local stylesheet rule instead of inline styles under the strict CSP.
  document.querySelectorAll('[data-progress]').forEach(e=>{const p=Math.round(Number(e.dataset.progress)/5)*5; e.className='p'+p;});
  if(app.focusJob) {const row=[...$('jobs-table').querySelectorAll('tr[data-key]')].find(e=>e.dataset.key===app.focusJob); if(row) {row.classList.add('selected'); row.scrollIntoView({block:'nearest'}); app.focusJob='';}}
}
function renderDashboard() {
  const s=app.state, rows=orderedJobs();
  metrics('dashboard-metrics',[['Tổng job',n(s.jobs.length)],['Đang xử lý / chờ',n(s.active_jobs+s.queued_jobs+s.scheduled_jobs)],['Hoàn thành',n(s.jobs.filter(r=>r.status==='Hoàn thành').length)],['Thất bại',n(s.jobs.filter(r=>['Thất bại','Mã không hợp lệ','Đạt giới hạn 24h'].includes(r.status)).length)],['Bean đã chạy',n(s.jobs.reduce((sum,r)=>sum+Number(r.success_count||0)*100,0))],['Proxy sẵn sàng',n(s.proxies.filter(r=>r.status!=='FAILED').length)]]);
  table('dashboard-jobs',rows.slice(0,10),[['Khách / Job',r=>esc(r.customer_name || r.job_id)],['Mã mời','invite_code'],['Tiến độ',progress],['Trạng thái',r=>badge(r.status)],['Thời gian',r=>esc(time(r.scheduled_at || r.created_at))],['',r=>button('Log','job-logs',r.job_id)]]);
  $('dashboard-telegram').textContent=`${s.users.filter(r=>r.active&&String(r.chat_id)!=='-1').length} người dùng được cấp quyền · ${s.telegram.ready?'Bot đang nhận yêu cầu':'Bot chưa nhận yêu cầu'}`;
  $('proxy-pill').textContent=`● ${s.proxies.filter(r=>r.status!=='FAILED').length} proxy trong pool`;
}
function makeProxy(pool) {
  const id=pool==='account'?'proxy-account':'proxy-mail';
  $(id).innerHTML=`<div class="proxy-page"><div class="proxy-title"><div class="page-heading"><h2>${pool==='account'?'Proxy Pool':'Proxy mua mail'}</h2><p>${pool==='account'?'Quản lý proxy xoay, theo dõi trạng thái và phân bổ cho các job trên VPS.':'Mỗi lần mua xoay IP; ưu tiên proxy sẵn sàng, chờ khi tất cả đang xoay.'}</p></div><input id="proxy-search-${pool}" placeholder="Tìm kiếm proxy…" aria-label="Tìm proxy"></div><div class="metrics four" id="proxy-metrics-${pool}"></div><div class="card"><div class="card-heading"><h3>Danh sách proxy</h3><button data-mutation data-action="proxy-check-all" data-pool="${pool}">Check all</button></div><div class="actions"><select id="proxy-provider-${pool}" aria-label="Nhà cung cấp proxy"><option value="proxyvn">Proxy.vn</option><option value="muaproxyxoay">muaproxyxoay.net</option></select><button data-action="proxy-file" data-pool="${pool}" data-mutation>↑ Nhập TXT</button><button data-action="proxy-paste" data-pool="${pool}" data-mutation>Dán proxy</button><button data-action="proxy-export" data-pool="${pool}" data-mutation>↓ Lưu / Xuất TXT</button></div><input type="file" id="proxy-file-${pool}" accept=".txt,text/plain" hidden><div id="proxy-table-${pool}"></div></div><div class="card"><h3>Proxy đã lỗi</h3><div id="proxy-errors-${pool}"></div></div></div>`;
  $('proxy-search-'+pool).addEventListener('input',()=>renderProxy(pool));
  $('proxy-file-'+pool).addEventListener('change',async e=>{const file=e.target.files[0]; e.target.value=''; if(!file) return; try {if(file.size>500000) throw Error('File proxy tối đa 500 KB.'); const bytes=new Uint8Array(await file.arrayBuffer()); const encoding=bytes[0]===255&&bytes[1]===254?'utf-16le':bytes[0]===254&&bytes[1]===255?'utf-16be':'utf-8'; await command('proxy.import',{pool,provider:$('proxy-provider-'+pool).value,text:new TextDecoder(encoding).decode(bytes)});} catch(error) {toast(error.message,true);}});
}
function renderProxy(pool) {
  if(!app.state) return;
  const all=pool==='account'?app.state.proxies:app.state.mail_proxies, query=$('proxy-search-'+pool).value.toLowerCase();
  metrics('proxy-metrics-'+pool,[['Hoạt động',n(all.filter(r=>r.status!=='FAILED').length),'proxy trong danh sách'],['Đang dùng',n(all.filter(r=>/CHECK|USE|ASSIGN/i.test(r.status)).length)],['Đang nghỉ / xoay',n(all.filter(r=>/COOL|ROTAT|nghỉ|xoay/i.test(r.status)).length)],['Đã lỗi',n(all.filter(r=>r.status==='FAILED').length)]]);
  const rows=all.filter(r=>[r.endpoint,r.provider,r.status,r.error].some(v=>String(v||'').toLowerCase().includes(query)));
  const columns=[['Proxy','endpoint'],['Nhà cung cấp','provider'],['Trạng thái',r=>badge(r.status)],['Tốc độ','speed'],['Chi tiết','error'],['Thao tác',r=>`<div class="row-actions">${button('Check','proxy-check',r.id,`data-pool="${pool}"`)}${button('Xoay','proxy-rotate',r.id,`data-pool="${pool}"`)}${button('Xóa','proxy-delete',r.id,`data-pool="${pool}"`)}</div>`]];
  table('proxy-table-'+pool,rows.filter(r=>r.status!=='FAILED'),columns);
  table('proxy-errors-'+pool,rows.filter(r=>r.status==='FAILED'),columns,{empty:'Chưa có proxy lỗi.'});
  controls();
}
function recipientIds() {const mode=$('broadcast-mode').value; return app.eligible.filter(r=>mode==='all'||(mode==='custom'?app.recipients.has(String(r.chat_id)):r.role===mode)).map(r=>r.chat_id);}
function renderRecipients() {
  const all=(app.state?.users || []).filter(r=>r.active&&String(r.chat_id)!=='-1'&&['admin','partner'].includes(r.role));
  const eligible=new Set(all.map(r=>String(r.chat_id))); app.recipients.forEach(id=>{if(!eligible.has(id)) app.recipients.delete(id);});
  app.eligible=all;
  const mode=$('broadcast-mode').value; $('broadcast-custom').hidden=mode!=='custom';
  $('broadcast-people').innerHTML=all.length?[...all].sort((a,b)=>a.name.localeCompare(b.name,'vi')).map(r=>`<label class="check"><input type="checkbox" data-recipient="${esc(r.chat_id)}" ${app.recipients.has(String(r.chat_id))?'checked':''}><span>${esc(r.name)} · ${esc(r.role)}</span><small>${esc(r.chat_id)}</small></label>`).join(''):'<p class="muted">Chưa có admin/partner đang được cấp quyền.</p>';
  broadcastCount();
}
function broadcastCount() {
  const ids=recipientIds(); $('broadcast-length').textContent=$('broadcast-text').value.length+' / 4000';
  $('broadcast-audience').textContent=app.state?.telegram?.broadcast_recipient_selection===true?`Đã chọn ${ids.length} / ${app.eligible.length} admin/partner đang được cấp quyền.`:'VPS cần hỗ trợ chọn người nhận trước khi gửi từ web.';
  controls();
}
function userFilter(rows, input, key) {const query=$(input).value.toLowerCase(), filtered=rows.filter(r=>[r.chat_id,r.name,r.role].some(v=>String(v||'').toLowerCase().includes(query))); if(app.selected[key]&&!filtered.some(r=>String(r.chat_id)===app.selected[key])) app.selected[key]=null; return filtered;}
function renderUsers() {
  const rows=app.state.users;
  metrics('operator-metrics',[['Người dùng được cấp quyền',n(rows.filter(r=>r.active&&String(r.chat_id)!=='-1').length)],['Job hoàn thành',n(rows.reduce((a,r)=>a+r.completed_jobs,0))],['Job thất bại',n(rows.reduce((a,r)=>a+r.failed_jobs,0))],['Bean đã bán',n(rows.reduce((a,r)=>a+r.sold_beans,0))]]);
  const sum=key=>rows.reduce((a,r)=>a+BigInt(String(r[key]||0)),0n);
  metrics('operator-finance',[['Doanh thu (VND)',money(sum('revenue_units'))],['Chi phí mail (VND)',money(sum('cost_units'))],['Lợi nhuận (VND)',money(sum('revenue_units')-sum('cost_units'))]]);
  table('users-table',userFilter(rows,'users-search','users'),[['Chat ID','chat_id'],['Tên','name'],['Vai trò',r=>esc(String(r.chat_id)==='-1'?'Local':r.role)],['Quyền',r=>badge(r.active?'Được phép':'Đã thu hồi')],['Job hoàn thành',r=>n(r.completed_jobs)],['Job thất bại',r=>n(r.failed_jobs)],['Bean đã bán',r=>n(r.sold_beans)],['Doanh thu',r=>esc(money(r.revenue_units))],['Chi phí',r=>esc(money(r.cost_units))],['Lợi nhuận',r=>esc(money(BigInt(String(r.revenue_units))-BigInt(String(r.cost_units))))]],{key:'chat_id',select:'users'});
}
function renderCustomers() {
  table('customers-table',userFilter(app.state.customers,'customers-search','customers'),[['Chat ID','chat_id'],['Tên','name'],['Số dư VND',r=>esc(money(r.balance_units))],['Tổng đơn',r=>n(r.total_orders)],['Hoàn thành',r=>n(r.completed_orders)],['Thất bại',r=>n(r.failed_orders)]],{key:'chat_id',select:'customers'});
}
function render() {
  const s=app.state;
  if(!app.busy) app.broadcastPending=false;
  renderDashboard(); renderJobs(); renderProxy('account'); renderProxy('mail'); renderUsers(); renderCustomers(); renderRecipients();
  $('bot-status').textContent=s.telegram.status; $('broadcast-status').textContent=s.telegram.broadcast; $('sepay-status').textContent=s.sepay.status; $('lookup-status').textContent=s.lookup.status;
  table('profiles-table',s.lookup.profiles,[['Email','email'],['ID tài khoản','user_id'],['Tên','display_name'],['Mã mời','invite_code'],['Beans',r=>n(r.beans)],['Cập nhật',r=>esc(time(r.updated_at))],['',r=>button('Sao chép','copy-code',r.invite_code)]]);
  if(app.selected.transactions&&!s.sepay.transactions.some(r=>String(r.transaction_id)===app.selected.transactions)) app.selected.transactions=null;
  table('transactions-table',s.sepay.transactions,[['ID giao dịch','transaction_id'],['Chat ID','chat_id'],['Số tiền',r=>esc(money(r.amount_units))],['Nội dung','content'],['Trạng thái','state'],['Lý do','reason']],{key:'transaction_id',select:'transactions'});
  $('settings-status').textContent=`API GmailVip: ${s.settings.has_gmailvip_key?'đã lưu':'chưa có'} · API 1TrieuMail: ${s.settings.has_one_trieu_key?'đã lưu':'chưa có'}`;
  if(!app.loaded) {loadSettings(); app.loaded=true;}
  controls();
  management.refreshPrices();
}
function loadSettings() {
  if(!app.state) return;
  for(const [key,value] of Object.entries(app.state.settings)) {const e=field('settings-form',key); if(e) {if(e.type==='checkbox') e.checked=!!value; else e.value=value ?? '';}}
  field('bot-form','auto_start').checked=app.state.telegram.auto_start;
  for(const [key,value] of Object.entries(app.state.sepay.fields)) {const e=field('sepay-form',key); if(e) e.value=value ?? '';}
  field('sepay-form','enabled').checked=app.state.sepay.enabled;
  quote();
}
function quote() {
  const points=Number(field('job-form','points').value || 0), count=Math.ceil(points/100), rate=Number(app.state?.settings?.sale_rate);
  $('job-accounts').textContent=`(= ${n(count)} accounts)`;
  try {const value=quotedUnits(points,app.state?.settings);$('job-quote').value=value===null?'':money(String(value));}catch{$('job-quote').value='';}
  const combo=field('job-form','combo').checked, scheduled=field('job-form','scheduled').checked;
  $('job-start').textContent=scheduled?(combo?'Tạo lịch combo':'Tạo lịch job'):(combo?'Bắt đầu combo':'Bắt đầu nhập mã');
  field('job-form','scheduled_at').disabled=!scheduled;
  const providers={gmailvip_vn:'GmailVip.vn', '1trieumail_com':'1TrieuMail.com', multi_shop:'Cả hai shop'};
  $('job-source').textContent=$('job-batch').value?'Dùng lô mail: '+$('job-batch').value:'Nguồn mail: '+(providers[app.state?.settings?.provider] || 'cấu hình VPS')+' · Mail, proxy và số luồng được quản lý trong Cài đặt chung.';
}
function selected(name, rows, key='chat_id') {const id=app.selected[name], row=rows.find(r=>String(r[key])===id); if(!row) throw Error('Chọn một dòng trong bảng trước khi thao tác.'); return row;}
function dialog(title, body) {$('dialog-title').textContent=title; $('dialog-body').innerHTML=body; if(!$('dialog').open) $('dialog').showModal();}
const columnNames={job_id:'Mã job', order_id:'Mã đơn', code:'Mã mời', invite_code:'Mã mời', status:'Trạng thái', requested_beans:'Bean yêu cầu', success_count:'Lượt thành công', attempted_count:'Lượt đã thử', failure_count:'Lỗi', created_at:'Tạo lúc', updated_at:'Cập nhật', finalized_at:'Kết thúc', scheduled_at:'Hẹn giờ', charged_units:'Đã thu (VND)', refunded_units:'Đã hoàn (VND)', held_units:'Tạm giữ (VND)', balance_units:'Số dư (VND)', amount_units:'Số tiền (VND)', cost_units:'Chi phí (VND)', purchase_id:'Mã giao dịch mail', quantity:'Số mail', provider:'Shop', product_id:'Sản phẩm', chat_id:'Chat ID', reason:'Lý do', description:'Nội dung', kind:'Loại', state:'Trạng thái', sale_rate_units:'Đơn giá (VND)', settled_at:'Chốt lúc', transaction_id:'Mã giao dịch', name:'Tên'};
function details(result) {
  const titles={rows:'Job của người dùng',history:'Lịch sử giao dịch',orders:'Đơn hàng',purchases:'Giao dịch mua mail',unpriced:'Job chưa có giá'};
  let body='';
  for(const [name,rows] of Object.entries(result)) {if(!Array.isArray(rows)) continue; const keys=[...new Set(rows.flatMap(r=>Object.keys(r)))].filter(k=>k in columnNames); body+=`<h4>${esc(titles[name] || name)}</h4>`;
    body+=rows.length?`<div class="table-wrap"><table><thead><tr>${keys.map(k=>`<th>${esc(columnNames[k])}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${keys.map(k=>`<td>${esc(k.endsWith('_units')?money(r[k]):k.endsWith('_at')?time(r[k]):labels[r[k]] || r[k] || '')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`:'<p class="muted">Chưa có dữ liệu.</p>';
  } dialog('Chi tiết trên VPS',body || '<p>Chưa có dữ liệu.</p>');
}
function download(text, filename, type='text/plain;charset=utf-8') {const url=URL.createObjectURL(new Blob([text],{type})), a=document.createElement('a'); a.href=url; a.download=filename; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);}
function showUserReport(result, expectedOwner) {
  const files=result.files;
  if(String(result.owner?.chat_id)!==String(expectedOwner) || !Array.isArray(files) || files.length!==2 || new Set(files.map(f=>f.kind)).size!==2 || files.some(f=>!['jobs','purchases'].includes(f.kind) || typeof f.text!=='string' || !/^[A-Za-z0-9_-][A-Za-z0-9_.-]*\.csv$/.test(f.filename || '') || /\.\./.test(f.filename))) throw Error('Phản hồi báo cáo CSV không hợp lệ hoặc không đúng người được chọn.');
  app.reportFiles=files;
  const owner=result.owner || {};
  const body=`<p>${esc(owner.name)} · Chat ID ${esc(owner.chat_id)}</p><p class="muted">Dữ liệu tại ${esc(time(result.generated_at))}. Chọn từng file để tải CSV về máy.</p>`+
    files.map((file,index)=>`<div class="subcard"><div class="card-heading"><div><strong>${file.kind==='jobs'?'Lịch sử job':'Lịch sử giao dịch mua mail'}</strong><p class="hint">${esc(file.filename)} · ${n(file.row_count)} dòng</p></div><button class="primary" data-action="report-download" data-index="${index}">↓ Tải CSV</button></div></div>`).join('');
  dialog('Báo cáo chi tiết theo người dùng',body);
}
function csv(rows, keys) {const cell=value=>{let s=String(value ?? ''); if(/^[=+\-@\t\r]/.test(s)) s="'"+s; return '"'+s.replace(/"/g,'""')+'"';}; return '\ufeff'+[keys,...rows.map(r=>keys.map(k=>r[k]))].map(row=>row.map(cell).join(',')).join('\r\n');}
let logCursor=null,logEpoch=null,logBusy=false;
async function logs(jobId) {if(!jobId) throw Error('Chọn Log trên một job trước.');app.logJob=jobId;logCursor=null;logEpoch=null;$('job-log').textContent='';$('log-caption').textContent='Job: '+jobId+' · Tự cập nhật mỗi giây';page('mochi');tab('mochi-workspace','tasks');tab('jobs','logs');await pollLogs();}
async function pollLogs() {
  if(logBusy||!app.connected||!app.logJob||app.page!=='mochi'||document.querySelector('[data-panel="jobs:logs"]').hidden||document.hidden)return;
  logBusy=true;const jobId=app.logJob,generation=app.generation;
  try {const response=await api('/api/command',{action:'job.logs',payload:{job_id:jobId,cursor:logCursor,epoch:logEpoch},request_id:crypto.randomUUID()});
    if(jobId!==app.logJob||generation!==app.generation)return;
    const r=response.result,view=$('job-log'),atEnd=view.scrollTop+view.clientHeight>=view.scrollHeight-12;
    if(r.reset)view.textContent='';const lines=r.lines||[];
    if(lines.length)view.textContent+=(view.textContent?'\n':'')+lines.join('\n');
    logCursor=r.cursor??null;logEpoch=r.epoch??null;if(atEnd)view.scrollTop=view.scrollHeight;
    $('log-caption').textContent='Job: '+jobId+(r.has_more?' · Đang tải log đã lưu…':' · Tự cập nhật mỗi giây');
  } catch(error){$('log-caption').textContent='Job: '+jobId+' · '+error.message;}
  finally {logBusy=false;}
}
async function batches() {await command('batch.list',{},r=>{const prior=$('job-batch').value; $('job-batch').innerHTML='<option value="">Mua mail theo cấu hình VPS</option>'+(r.batches || []).map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join(''); $('job-batch').value=prior; quote();});}
function saveDraft() {const f=$('job-form'); localStorage.setItem('hoshino.jobDraft',JSON.stringify({code:f.code.value,customer:f.customer.value,points:f.points.value,combo:f.combo.checked,scheduled:f.scheduled.checked,scheduled_at:f.scheduled_at.value})); toast('Đã lưu nháp job trên máy này.');}

document.addEventListener('click',async event=>{
  const nav=event.target.closest('[data-page]'); if(nav) {event.preventDefault(); page(nav.dataset.page); return;}
  const tabs=event.target.closest('[data-tab]'); if(tabs) {tab(tabs.closest('[data-tabs]').dataset.tabs,tabs.dataset.tab); if(tabs.dataset.tab==='inventory'&&app.connected) batches(); if(tabs.dataset.tab==='running') poll();if(tabs.dataset.tab==='temporary')management.loadTemporary(); return;}
  const b=event.target.closest('[data-action]');
  if(b) {event.preventDefault(); if(b.disabled) return; const id=b.dataset.id, pool=b.dataset.pool;
    try {switch(b.dataset.action) {
      case 'refresh': await poll(); break;
      case 'new-job': newJob(); break;
      case 'running-jobs': page('mochi'); tab('mochi-workspace','tasks'); tab('jobs','running'); break;
      case 'save-draft': saveDraft(); break;
      case 'job-logs': await logs(id); break;
      case 'job-stop': if(confirm('Bạn có muốn dừng / hủy lịch job '+id+'?')) await command('job.stop',{job_id:id}); break;
      case 'job-continue': await command('job.continue',{job_id:id},r=>{app.focusJob=r.job_id;page('mochi');tab('mochi-workspace','tasks');tab('jobs','running');}); break;
      case 'reload-logs': await logs(app.logJob); break;
      case 'history-export': await command('history.export',{},r=>download('\ufeff'+r.text.replace(/^\ufeff/,''),'job_history.csv','text/csv;charset=utf-8')); break;
      case 'upload-batch': $('batch-file').click(); break;
      case 'batch-list': await batches(); break;
      case 'lookup-cancel': await command('lookup.cancel'); break;
      case 'copy-code': await navigator.clipboard.writeText(id || ''); toast('Đã sao chép mã mời.'); break;
      case 'profiles-export': download(csv(app.state?.lookup?.profiles || [],['email','user_id','display_name','invite_code','beans','updated_at']),'mochi_accounts.csv','text/csv;charset=utf-8'); break;
      case 'proxy-file': $('proxy-file-'+pool).click(); break;
      case 'proxy-check-all': await command('proxy.check',{pool}); break;
      case 'proxy-check': await command('proxy.check',{pool,id}); break;
      case 'proxy-rotate': await command('proxy.rotate',{pool,id}); break;
      case 'proxy-delete': await command('proxy.delete',{pool,id}); break;
      case 'proxy-export': await command('proxy.export',{pool},r=>download(r.text,pool==='account'?'proxy_pool.txt':'proxy_mua_mail.txt')); break;
      case 'proxy-paste': dialog('Nhập danh sách proxy',`<form id="proxy-paste-form" data-pool="${pool}"><p class="muted">Mỗi dòng một proxy. Proxy.vn: ip:port|key_xoay · muaproxyxoay.net: ip:port:user:pass|key_xoay.</p><label>Danh sách proxy<textarea name="text" rows="10" required></textarea></label><div class="actions"><button type="submit" class="primary" data-mutation>Nhập vào VPS</button></div></form>`); controls(); break;
      case 'bot-start': await command('telegram.start'); break;
      case 'bot-stop': await command('telegram.stop'); break;
      case 'broadcast-cancel': await command('telegram.cancel_broadcast'); break;
      case 'recipient-all': app.recipients=new Set(app.eligible.map(r=>String(r.chat_id))); renderRecipients(); break;
      case 'recipient-none': app.recipients.clear(); renderRecipients(); break;
      case 'user-revoke': case 'user-restore': {const r=selected('users',app.state.users); if(String(r.chat_id)==='-1') throw Error('Local là tài khoản hệ thống.'); await command('user.active',{chat_id:r.chat_id,active:b.dataset.action==='user-restore'}); break;}
      case 'user-jobs': await command('user.jobs',{chat_id:selected('users',app.state.users).chat_id},details); break;
      case 'user-report': {const owner=selected('users',app.state.users).chat_id; await command('user.report',{chat_id:owner,kind:'all'},r=>showUserReport(r,owner)); break;}
      case 'report-download': {const file=app.reportFiles[Number(b.dataset.index)]; if(!file) throw Error('Chọn lại báo cáo cần tải.'); download('\ufeff'+file.text.replace(/^\ufeff/,''),file.filename,'text/csv;charset=utf-8'); break;}
      case 'finance-ledger': await command('finance.ledger',{},details); break;
      case 'finance-reconcile': await command('finance.reconcile',{purchase_id:$('finance-id').value.trim(),amount:$('finance-amount').value.trim()}); break;
      case 'finance-price': await command('finance.price',{job_id:$('finance-id').value.trim(),amount:$('finance-amount').value.trim()}); break;
      case 'customer-delete': await command('customer.delete',{chat_id:selected('customers',app.state.customers).chat_id}); break;
      case 'customer-history': await command('customer.history',{chat_id:selected('customers',app.state.customers).chat_id},details); break;
      case 'customer-balance': {const r=selected('customers',app.state.customers); dialog('Sửa số dư khả dụng',`<form id="balance-form" data-id="${esc(r.chat_id)}" data-expected="${esc(r.balance_units)}"><p>${esc(r.name)} · ${esc(r.chat_id)}</p><label>Số dư mới (VND)<input name="amount" inputmode="decimal" value="${esc((BigInt(String(r.balance_units))/100n).toString()+'.'+String(BigInt(String(r.balance_units))%100n).padStart(2,'0'))}" required></label><label>Lý do điều chỉnh<input name="reason" maxlength="500" required></label><div class="actions"><button class="primary" data-mutation type="submit">Lưu số dư</button></div></form>`); controls(); break;}
      case 'customer-settle': await command('customer.settle',{job_id:$('settle-id').value.trim()}); break;
      case 'sepay-start': await command('sepay.start'); break;
      case 'sepay-stop': await command('sepay.stop'); break;
      case 'sepay-reconcile': await command('sepay.reconcile',{transaction_id:selected('transactions',app.state.sepay.transactions,'transaction_id').transaction_id,chat_id:idValue($('review-id').value)}); break;
      case 'reload-settings': loadSettings(); toast('Đã nạp lại cấu hình VPS vào biểu mẫu.'); break;
      case 'close-dialog': $('dialog').close(); break;
    }} catch(error) {toast(error.message,true);} return;
  }
  const row=event.target.closest('tr[data-table][data-key]'); if(!row || !app.state) return;
  const kind=row.dataset.table; app.selected[kind]=row.dataset.key;
  if(kind==='users') {const r=selected(kind,app.state.users); field('user-form','chat_id').value=r.chat_id; field('user-form','name').value=r.name; field('user-form','role').value=r.role; $('user-keys-form').reset(); renderUsers();}
  else if(kind==='customers') renderCustomers();
  else if(kind==='transactions') render();
});

document.addEventListener('submit',async event=>{
  event.preventDefault(); const f=event.target;
  try {switch(f.id) {
    case 'connection-form': {if(app.busy) return; app.busy=true; app.generation++; controls(); try {const response=await api('/api/connect',{url:f.url.value.trim(),token:f.token.value.trim()}); f.token.value=''; app.state=response.state; app.connected=true; app.loaded=false; render(); $('connection-config').textContent='Đã lưu kết nối trên máy, dùng chung với phần mềm.'; status('Đã kết nối VPS.'); toast('Đã kết nối VPS.');} finally {app.busy=false; controls();} break;}
    case 'job-form': {const payload={code:f.code.value.trim(),customer:f.customer.value.trim(),points:Number(f.points.value),batch:$('job-batch').value,combo:f.combo.checked}; if(f.scheduled.checked) {if(!f.scheduled_at.value) throw Error('Chọn ngày giờ chạy theo giờ Việt Nam.'); payload.scheduled_at=f.scheduled_at.value+':00+07:00';} saveDraft(); await command('job.create',payload,r=>{app.focusJob=r.job_id; page('mochi'); tab('mochi-workspace','tasks'); tab('jobs','running');}); break;}
    case 'lookup-form': {const payload={email:f.email.value.trim(),password:f.password.value}; f.password.value=''; await command('lookup.start',payload); break;}
    case 'proxy-paste-form': await command('proxy.import',{pool:f.dataset.pool,provider:$('proxy-provider-'+f.dataset.pool).value,text:f.text.value},()=>$('dialog').close()); break;
    case 'bot-form': await command('telegram.save',{token:f.token.value.trim(),auto_start:f.auto_start.checked},()=>{f.token.value='';}); break;
    case 'broadcast-form': {const ids=recipientIds(); if(!ids.length) throw Error('Chọn ít nhất một admin/partner.'); if(ids.length>2000 || f.text.value.length>4000) throw Error('Tối đa 2000 người nhận và 4000 ký tự mỗi lượt.'); if(app.state?.telegram?.broadcast_recipient_selection!==true) throw Error('VPS chưa hỗ trợ chọn người nhận.'); if(app.broadcastPending || app.state?.telegram?.broadcast_running) return; app.broadcastPending=true; try {await command('telegram.broadcast',{text:f.text.value,recipients:ids});} finally {if(!app.busy) app.broadcastPending=false; controls();} break;}
    case 'user-form': await command('user.save',{chat_id:idValue(f.chat_id.value),name:f.name.value.trim(),role:f.role.value}); break;
    case 'user-keys-form': {const r=selected('users',app.state.users); if(String(r.chat_id)!==field('user-form','chat_id').value.trim() || String(r.chat_id)==='-1') throw Error('Chọn đúng người dùng và Chat ID trước khi lưu API.'); await command('user.mail_keys',{chat_id:r.chat_id,gmailvip_key:f.gmailvip_key.value,one_trieu_key:f.one_trieu_key.value},()=>f.reset()); break;}
    case 'customer-form': await command('customer.save',{chat_id:idValue(f.chat_id.value),name:f.name.value.trim()}); break;
    case 'balance-form': await command('customer.balance',{chat_id:idValue(f.dataset.id),expected_units:idValue(f.dataset.expected),amount:f.amount.value.trim(),reason:f.reason.value.trim()},()=>$('dialog').close()); break;
    case 'sepay-form': {const payload=Object.fromEntries(new FormData(f)); payload.enabled=f.enabled.checked; await command('sepay.save',payload,()=>{f.token.value='';}); break;}
    case 'settings-form': {const payload=Object.fromEntries(new FormData(f)); for(const key of ['use_proxy','google_proxy','app_proxy','redeem_proxy','mail_proxy']) payload[key]=f.elements.namedItem(key).checked; for(const key of ['proxy_slots','concurrency','job_limit']) payload[key]=Number(payload[key]); await command('settings.save',payload,()=>{f.gmailvip_key.value=''; f.one_trieu_key.value='';}); break;}
  }} catch(error) {toast(error.message,true); if(f.id==='connection-form') {app.connected=false; status(error.message); controls();}}
});

$('job-form').addEventListener('input',quote); $('job-batch').addEventListener('change',quote);
$('broadcast-mode').addEventListener('change',renderRecipients); $('broadcast-text').addEventListener('input',broadcastCount);
$('broadcast-people').addEventListener('change',event=>{const id=event.target.dataset.recipient; if(!id) return; if(event.target.checked) app.recipients.add(id); else app.recipients.delete(id); broadcastCount();});
for(const [id,fn] of [['users-search',renderUsers],['customers-search',renderCustomers]]) $(id).addEventListener('input',()=>{if(app.state) {fn(); controls();}});
$('batch-file').addEventListener('change',async event=>{const file=event.target.files[0]; event.target.value=''; if(!file) return; try {if(file.size>1500000) throw Error('File lô mail tối đa 1,5 MB.'); const data=JSON.parse((await file.text()).replace(/^\ufeff/,'')); if(!Array.isArray(data.accounts)) throw Error('File JSON phải chứa danh sách accounts.'); await command('batch.upload',{accounts:data.accounts},r=>{const option=new Option(r.batch,r.batch,true,true); $('job-batch').add(option); quote(); toast('Đã đưa lô mail lên VPS.');});} catch(error) {toast(error.message,true);}});
makeProxy('account'); makeProxy('mail');
field('job-form','scheduled_at').value=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Ho_Chi_Minh',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(Date.now()+300000)).replace(' ','T');
try {const draft=JSON.parse(localStorage.getItem('hoshino.jobDraft') || '{}'); for(const key of ['code','customer','points','combo','scheduled','scheduled_at']) if(key in draft) {const e=field('job-form',key); if(e.type==='checkbox') e.checked=draft[key]===true; else e.value=draft[key];}} catch {}
$('local-address').textContent=location.host; controls();
async function boot() {try {const bootstrap=new URLSearchParams(location.hash.slice(1)).get('session'); if(bootstrap) {await api('/api/session',{}, {'X-Web-Bootstrap':bootstrap}); history.replaceState(null,'',location.pathname);} const config=await api('/api/config'); app.csrf=config.csrf; field('connection-form','url').value=config.url; $('connection-config').textContent=config.has_token?'Đã có token lưu trên máy; để trống để dùng lại.':'Chưa có token. Nhập token quản lý VPS tại đây.'; await poll(); setInterval(poll,2500);} catch(error) {status(error.message); toast(error.message,true);}}
const statistics=installStatistics({$,app,api,esc,money,n,time,table,toast,idValue});
const management=installManagement({$,app,api,command,esc,money,n,time,table,toast,controls,logs});
setInterval(pollLogs,1000);
boot();
