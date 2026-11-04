const labels={waiting:'Chờ tích bean',accumulating:'Đang tích bean',ready:'Đủ bean',stopping:'Đang dừng',paused:'Đã dừng',failed:'Lỗi tích bean',exporting:'Đang xuất',queued:'Chờ mua mail',buying:'Đang mua mail',creating:'Đang tạo acc',uncertain:'Cần đối soát',partial:'Có acc lỗi',completed:'Hoàn tất',pending:'Chưa đóng phiếu'};

export function installStock({$,app,api,command,esc,n,time,table,toast,controls}) {
  let offset=0,exportOffset=0,total=0,busy=false,rows=[],selected=new Set(),exportRecord=null,signature='',pendingFinish='';
  const inventory=document.querySelector('[data-panel="mochi-workspace:inventory"]');
  const form=$('stock-form');
  document.querySelectorAll('[data-stock-type]').forEach(button=>button.addEventListener('click',()=>{
    const stock=button.dataset.stockType==='stock';form.hidden=!stock;$('job-form').hidden=stock;
    document.querySelectorAll('[data-stock-type]').forEach(e=>{e.classList.toggle('primary',e===button);e.setAttribute('aria-pressed',String(e===button));});
  }));
  form.infinite.addEventListener('change',()=>{form.beans.disabled=form.infinite.checked;});
  form.addEventListener('submit',async e=>{
    e.preventDefault();e.stopPropagation();
    const payload={count:Number(form.count.value),beans:Number(form.beans.value),infinite:form.infinite.checked,
      product_ids:form.product_ids.value.trim()||app.state?.settings?.product_ids||''};
    await command('stock.create',payload,r=>{$('stock-create-status').textContent=r.message;});
    load();
  });
  async function read(action,payload={}) {
    const response=await api('/api/command',{action,payload,request_id:crypto.randomUUID()});return response.result;
  }
  function renderAccounts(result) {
    total=result.total;rows=result.rows;
    $('stock-summary').textContent=`Tổng số acc: ${n(total)} · Tổng bean: ${result.beans} · Đã chọn: ${selected.size}`;
    $('stock-page').textContent=`${Math.min(offset+1,total)}–${Math.min(offset+100,total)} / ${n(total)}`;
    $('stock-prev').disabled=!offset;$('stock-next').disabled=offset+100>=total;
    const next=JSON.stringify(rows);
    if(next!==signature) {
      signature=next;
      table('stock-table',rows,[['Chọn',r=>`<input type="checkbox" aria-label="Chọn acc ${r.id}" data-stock-check="${r.id}" ${selected.has(r.id)?'checked':''}>`],
        ['STT','id'],['Email','email'],['Mật khẩu','password'],['Bean','beans'],['Mục tiêu',r=>r.infinite?'Vô hạn':n(r.target)],
        ['Mã mời','invite_code'],['Trạng thái',r=>esc(labels[r.status]||r.status)],
        ['Thao tác',r=>`<button data-stock-stop="${r.id}" data-mutation>Dừng nhập mã</button>`]]);
    }
    table('stock-batches',result.batches,[['Mã lô','id'],['Số acc','count'],['Trạng thái',r=>esc(labels[r.state]||r.state)],['Chi tiết','message'],
      ['Thử lại',r=>r.state==='partial'?`<button data-stock-retry="${esc(r.id)}" data-mutation>Thử tạo lại từ mail đã mua</button>`:'']]);
    controls();
  }
  function renderExports(result) {
    table('stock-exports',result.rows,[['Phiếu xuất','id'],['Số acc','count'],['Người xuất','actor'],['Trạng thái',r=>esc(labels[r.state]||r.state)],
      ['Ngày xuất',r=>esc(time(r.created_at))],['Xem',r=>`<button data-stock-show="${esc(r.id)}">Xem / Sao chép</button>`]]);
    $('stock-export-page').textContent=`Trang ${exportOffset/50+1}`;$('stock-export-prev').disabled=!exportOffset;
    $('stock-export-next').disabled=result.rows.length<50;
  }
  async function load() {
    if(!app.connected||app.busy||busy)return;busy=true;const generation=app.generation;
    try {
      const [result,history]=await Promise.all([read('stock.list',{offset}),read('stock.exports',{offset:exportOffset})]);
      if(generation!==app.generation)return;
      renderAccounts(result);renderExports(history);
      if(offset&&!result.rows.length){offset=Math.max(0,offset-100);setTimeout(load,0);}
    } catch(error){$('stock-summary').textContent=error.message;} finally{busy=false;}
  }
  function ids(){if(!selected.size)throw Error('Tích chọn acc trước khi thao tác.');return [...selected].sort((a,b)=>a-b);}
  function clear(){selected.clear();signature='';}
  function showExport(result) {
    exportRecord=result;$('stock-export-title').textContent='Phiếu xuất '+result.id;
    $('stock-export-content').value=result.content;$('stock-export-dialog').showModal();
  }
  async function finishPending() {
    if(!pendingFinish||app.busy||!app.connected)return;
    const id=pendingFinish;
    await command('stock.export.finish',{export_id:id},()=>{if(pendingFinish===id)pendingFinish='';clear();});
    await load();
  }
  $('stock-export-close').addEventListener('click',()=>$('stock-export-dialog').close());
  $('stock-export-copy').addEventListener('click',async()=>{
    try {await navigator.clipboard.writeText($('stock-export-content').value);toast('Đã sao chép acc.');}
    catch {const text=$('stock-export-content');text.focus();text.select();toast('Nhấn Ctrl+C để sao chép.');}
  });
  $('stock-export-dialog').addEventListener('close',()=>{
    if(exportRecord?.state==='pending'){pendingFinish=exportRecord.id;setTimeout(finishPending,0);}
    exportRecord=null;$('stock-export-content').value='';
  });
  inventory.addEventListener('change',e=>{
    if(!e.target.dataset.stockCheck)return;const id=Number(e.target.dataset.stockCheck);
    if(e.target.checked)selected.add(id);else selected.delete(id);
    $('stock-selection').textContent=`Đã chọn ${selected.size} acc`;
  });
  inventory.addEventListener('click',async e=>{
    try {
      const button=e.target.closest('button');if(!button)return;
      if(button.dataset.stockStop){if(confirm('Bạn có muốn dừng nhập mã cho acc này không?'))await command('stock.stop',{ids:[Number(button.dataset.stockStop)]});}
      else if(button.dataset.stockRetry)await command('stock.retry',{batch_id:button.dataset.stockRetry});
      else if(button.dataset.stockShow){showExport(await read('stock.export.get',{export_id:button.dataset.stockShow}));return;}
      else switch(button.id) {
        case 'stock-select-page':rows.forEach(r=>selected.add(r.id));signature='';break;
        case 'stock-clear-selection':clear();break;
        case 'stock-stop': {const chosen=ids();if(confirm(`Bạn có muốn dừng nhập mã cho ${chosen.length} acc không?`))await command('stock.stop',{ids:chosen});break;}
        case 'stock-resume':await command('stock.resume',{ids:ids()});break;
        case 'stock-delete': {const chosen=ids();if(confirm(`Bạn có muốn xóa ${chosen.length} acc đã chọn không?`))await command('stock.delete',{ids:chosen},clear);break;}
        case 'stock-export':await command('stock.export.prepare',{ids:ids()},showExport);break;
        case 'stock-prev':offset=Math.max(0,offset-100);break;
        case 'stock-next':if(offset+100<total)offset+=100;break;
        case 'stock-export-prev':exportOffset=Math.max(0,exportOffset-50);break;
        case 'stock-export-next':exportOffset+=50;break;
        case 'stock-refresh':break;
        default:return;
      }
      await load();
    } catch(error){toast(error.message,true);}
  });
  setInterval(()=>{finishPending();if(app.page==='mochi'&&!inventory.hidden)load();},2500);
  return {load};
}
