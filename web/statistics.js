export function installStatistics({$,app,api,esc,money,n,time,table,toast,idValue}) {
  let busy=false,pending=false,day=null,mode='jobs',offset=0,total=0;
  const colors=['#60a5fa','#34d399','#fbbf24','#fb7185','#a78bfa','#22d3ee','#f97316'];
  $('stats-month').value=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Ho_Chi_Minh',year:'numeric',month:'2-digit'}).format(new Date());
  const chart=(container,rows,field)=>{
    const values=rows.map(r=>Number(r[field])), hi=Math.max(1,...values),lo=Math.min(0,...values),height=180,zero=hi/(hi-lo)*height;
    $(container).innerHTML=`<svg viewBox="0 0 800 245" role="img" aria-label="Biểu đồ ${esc(field)} theo ngày"><line x1="50" y1="${35+zero}" x2="795" y2="${35+zero}" stroke="#64748b"/><text x="0" y="22" fill="#94a3b8" font-size="12">${esc(field==='profit_units'?money(String(Math.trunc(hi))):n(hi))}</text>${rows.map((r,i)=>{
      const step=740/rows.length,x=50+i*step,v=Number(r[field]),y=35+zero-v/(hi-lo)*height;
      return `<g role="button" tabindex="0" data-stats-day="${esc(r.day)}" data-stats-mode="${field==='customers'?'customers':'jobs'}" aria-label="${esc(r.day)}: ${esc(field==='profit_units'?money(r[field]):n(r[field]))}"><title>${esc(r.day)}: ${esc(field==='profit_units'?money(r[field])+' VND':n(r[field]))}${r.pending_jobs?' · Còn dữ liệu chờ đối soát':''}</title><rect x="${x}" y="30" width="${step}" height="200" fill="transparent"/><rect x="${x+2}" y="${Math.min(y,35+zero)}" width="${Math.max(2,step-4)}" height="${Math.max(2,Math.abs(y-35-zero))}" fill="${v<0?'#fb7185':'#60a5fa'}"/>${i%2===0?`<text x="${x+step/2}" y="240" text-anchor="middle" fill="#94a3b8" font-size="12">${r.day.slice(-2)}</text>`:''}</g>`;
    }).join('')}</svg>`;
  };
  function pie(rows) {
    const count=rows.reduce((a,r)=>a+r.count,0);let angle=-Math.PI/2;
    $('stats-reasons').innerHTML=count?`<div class="stats-pie"><svg viewBox="0 0 220 220" aria-label="Tỷ lệ lý do thất bại">${rows.map((r,i)=>{
      const start=angle;angle+=r.count/count*2*Math.PI;
      const x=a=>110+98*Math.cos(a),y=a=>110+98*Math.sin(a);
      return rows.length===1?`<circle cx="110" cy="110" r="98" fill="${colors[i%colors.length]}"/>`:`<path d="M110 110 L${x(start)} ${y(start)} A98 98 0 ${angle-start>Math.PI?1:0} 1 ${x(angle)} ${y(angle)} Z" fill="${colors[i%colors.length]}"/>`;
    }).join('')}</svg><ul>${rows.map((r,i)=>`<li><span class="reason-dot" style="background:${colors[i%colors.length]}"></span>${esc((r.count/count*100).toFixed(1))}% (${n(r.count)}) · ${esc(r.reason)}</li>`).join('')}</ul></div>`:'<p class="empty">Chưa có đợt chạy thất bại trong tháng.</p>';
  }
  function render(r) {
    const prior=$('stats-owner').value;
    $('stats-owner').innerHTML='<option value="">Tất cả mọi người</option>'+r.owners.map(o=>`<option value="${esc(o.chat_id)}">${esc(o.name)} (${esc(o.chat_id)})</option>`).join('');
    $('stats-owner').value=prior;
    $('stats-note').textContent=r.note;
    const c=r.customer_counts;
    $('stats-customers-summary').textContent=`Lượt khách: hôm nay ${n(c.today)} · tuần này ${n(c.week)} · tháng ${r.month}: ${n(c.month)}`;
    chart('stats-profit',r.days,'profit_units');chart('stats-jobs',r.days,'jobs');chart('stats-customers',r.days,'customers');pie(r.reasons);
    table('stats-users',r.users,[['Người / Chat ID',p=>esc(p.name)+' ('+esc(p.chat_id)+')'],['Số job',p=>n(p.jobs)],['Doanh thu',p=>esc(money(p.revenue_units))],['Chi phí đã biết',p=>esc(money(p.cost_units))],['Lợi nhuận đã xác định',p=>esc(money(p.profit_units))],['Job chờ đối soát',p=>n(p.pending_jobs)]]);
    const customers=mode==='customers',rows=customers?r.customer_history:r.history;
    const body=[];
    const add=p=>body.push({...p,person:p.name+' ('+p.chat_id+')',beans:p.success_count*100,
      finished:p.finished_at||p.finalized_at});
    if(customers) rows.forEach(add);
    else for(const person of r.users) {
      rows.filter(p=>String(p.chat_id)===String(person.chat_id)).forEach(add);
      body.push({...person,person:'TỔNG '+person.name+' ('+person.chat_id+')',summary:true});
    }
    table('history-table',body,[['Người / Chat ID','person'],['Mã job','job_id'],['Mã mời','code'],['Trạng thái','status'],['Bean','beans'],
      ['Doanh thu',p=>customers?'':esc(money(p.revenue_units))],['Chi phí đã biết',p=>customers?'':esc(money(p.cost_units))],
      ['Lợi nhuận',p=>customers?'':p.profit_units===null?'Chờ đối soát':esc(money(p.profit_units))],['Kết thúc',p=>esc(time(p.finished))]]);
    total=customers?r.customer_total:r.total;
    $('stats-detail-caption').textContent=(customers?'Danh sách mã mời / lượt khách':'Lịch sử job theo người')+' · '+(day||r.month);
    $('stats-pagination').textContent=`${Math.min(offset+1,total)}–${Math.min(offset+100,total)} / ${total}`;
    $('stats-prev').disabled=offset===0;$('stats-next').disabled=offset+100>=total;
  }
  async function load() {
    if(!app.connected||app.state?.closing)return;
    if(busy){pending=true;return;}busy=true;
    const payload={month:$('stats-month').value,offset};if(day)payload.day=day;
    if($('stats-owner').value)payload.owner_id=idValue($('stats-owner').value);
    const generation=app.generation;
    try {const response=await api('/api/command',{action:'statistics.get',payload,request_id:crypto.randomUUID()});if(generation===app.generation&&!pending)render(response.result);}
    catch(error){$('stats-note').textContent=error.message;}
    finally {busy=false;if(pending){pending=false;load();}}
  }
  $('stats-month').addEventListener('change',()=>{day=null;offset=0;load();});
  $('stats-owner').addEventListener('change',()=>{offset=0;load();});
  $('stats-refresh').addEventListener('click',load);
  $('stats-all-days').addEventListener('click',()=>{day=null;offset=0;mode='jobs';load();});
  $('stats-prev').addEventListener('click',()=>{offset=Math.max(0,offset-100);load();});
  $('stats-next').addEventListener('click',()=>{if(offset+100<total){offset+=100;load();}});
  const drill=target=>{const bar=target.closest('[data-stats-day]');if(!bar)return;day=bar.dataset.statsDay;mode=bar.dataset.statsMode;offset=0;load();$('stats-detail').scrollIntoView({behavior:'smooth'});};
  $('statistics').addEventListener('click',e=>drill(e.target));
  $('statistics').addEventListener('keydown',e=>{if((e.key==='Enter'||e.key===' ')&&e.target.closest('[data-stats-day]')){e.preventDefault();drill(e.target);}});
  setInterval(()=>{if(app.page==='statistics')load();},10000);
  return {load};
}
