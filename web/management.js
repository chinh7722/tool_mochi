export function priceUnits(text) {
  const value=String(text??'').trim();if(!/^\d+(\.\d{1,2})?$/.test(value))throw Error('Nhập giá VND không âm, tối đa 2 số thập phân.');
  const [whole,fraction='']=value.split('.');return BigInt(whole)*100n+BigInt(fraction.padEnd(2,'0'));
}
export function quotedUnits(points,settings) {
  const count=BigInt(Math.ceil(points/100)),rows=[...(settings?.prices||[])].sort((a,b)=>a.beans-b.beans);
  if(rows.length) {const tier=[...rows].reverse().find(r=>BigInt(r.beans)<=count*100n)||rows[0],base=BigInt(tier.beans);
    const result=(BigInt(tier.price_units)*count*100n+base/2n)/base;return result>0n?result:1n;}
  return settings?.sale_rate?priceUnits(settings.sale_rate)*count:null;
}
export function installManagement({$,app,api,command,esc,money,n,time,table,toast,controls,logs}) {
  let prices=[],dirty=false,offset=0,total=0,busy=false,pending=false,searchVersion=0;
  function priceRows() {
    prices.sort((a,b)=>a.beans-b.beans);
    table('prices-table',prices,[['Mốc bean',r=>n(r.beans)],['Giá bán (VND)',r=>esc(money(r.price_units))],['Thao tác',r=>`<button data-price-edit="${r.beans}">Sửa</button> <button data-price-remove="${r.beans}">Xóa mốc</button>`]]);
  }
  function refreshPrices() {if(!dirty) {prices=[...(app.state?.settings?.prices||[])];priceRows();}}
  $('price-form').addEventListener('submit',e=>{
    e.preventDefault();try {const beans=Number($('price-beans').value),units=priceUnits($('price-vnd').value);
      if(beans<100||beans>1000000||beans%100||units<=0n||units>9223372036854775807n)throw Error('Mốc bean phải là bội số 100; giá phải lớn hơn 0 và hợp lệ.');
      prices=prices.filter(r=>r.beans!==beans);prices.push({beans,price_units:units<=BigInt(Number.MAX_SAFE_INTEGER)?Number(units):String(units)});dirty=true;priceRows();$('prices-status').textContent='Chưa lưu. Bấm Lưu bảng giá để áp dụng trên VPS.';
    } catch(error){toast(error.message,true);}
  });
  $('prices-page').addEventListener('click',e=>{
    const remove=e.target.closest('[data-price-remove]'),edit=e.target.closest('[data-price-edit]');
    if(remove){prices=prices.filter(r=>r.beans!==Number(remove.dataset.priceRemove));dirty=true;priceRows();$('prices-status').textContent='Chưa lưu thay đổi.';}
    if(edit){const r=prices.find(r=>r.beans===Number(edit.dataset.priceEdit));$('price-beans').value=r.beans;const units=BigInt(r.price_units);$('price-vnd').value=String(units/100n)+'.'+String(units%100n).padStart(2,'0');}
  });
  $('prices-save').addEventListener('click',()=>command('pricing.save',{prices},r=>{prices=r.prices;dirty=false;priceRows();$('prices-status').textContent=r.message;}));
  async function loadTemporary() {
    if(!app.connected||app.busy)return;if(busy){pending=true;return;}busy=true;const start=offset,generation=app.generation,version=searchVersion;
    try {const response=await api('/api/command',{action:'history.temporary',payload:{offset:start,query:$('temporary-search').value},request_id:crypto.randomUUID()});if(generation!==app.generation||version!==searchVersion)return;
      const r=response.result;total=r.total;
      table('temporary-table',r.rows,[['Mã job','job_id'],['Người tạo',r=>esc(r.name)+' ('+esc(r.chat_id)+')'],['Mã mời','code'],['Trạng thái',r=>esc(({failed:'Thất bại',completed:'Hoàn thành',cancelled:'Đã hủy'})[r.status]||r.status)],['Bean',r=>n(r.success_count*100)],['Kết thúc',r=>esc(time(r.finalized_at))],['Thao tác',r=>`<button data-action="job-logs" data-id="${esc(r.job_id)}">Log</button> <button data-action="job-continue" data-id="${esc(r.job_id)}" data-mutation ${r.can_continue?'':'data-allowed="false"'}>Tiếp tục</button>`]]);
      $('temporary-pagination').textContent=`${Math.min(start+1,total)}–${Math.min(start+100,total)} / ${total}`;
      $('temporary-prev').disabled=start===0;$('temporary-next').disabled=start+100>=total;controls();
      if(start&&!r.rows.length){offset=Math.max(0,start-100);setTimeout(loadTemporary,0);}
    } catch(error){$('temporary-pagination').textContent=error.message;} finally {busy=false;if(pending){pending=false;setTimeout(loadTemporary,0);}}
  }
  let searchTimer;
  $('temporary-search').addEventListener('input',()=>{offset=0;searchVersion++;clearTimeout(searchTimer);searchTimer=setTimeout(loadTemporary,250);});
  $('temporary-refresh').addEventListener('click',loadTemporary);
  $('temporary-prev').addEventListener('click',()=>{offset=Math.max(0,offset-100);loadTemporary();});
  $('temporary-next').addEventListener('click',()=>{if(offset+100<total){offset+=100;loadTemporary();}});
  $('temporary-clear').addEventListener('click',async()=>{
    if(confirm('Bạn có muốn xóa lịch sử tạm ở Mochi Chat? Lịch sử Thống kê và log vẫn được giữ.')){
      await command('history.temporary.clear',{},()=>{offset=0;});await loadTemporary();
    }
  });
  setInterval(()=>{if(app.page==='mochi'&&!document.querySelector('[data-panel="jobs:temporary"]').hidden)loadTemporary();},2500);
  return {refreshPrices,loadTemporary};
}
