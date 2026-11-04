"""Customer work preempts background bean accumulation at a safe account boundary."""
from datetime import datetime
from core.job_schedule import VIETNAM_TIME


def is_stock(record):
    return bool(getattr(record,'stock_account_id',None))


def customer_pending(page,now=None):
    now=now or datetime.now(VIETNAM_TIME)
    for jid in page._active_job_ids | set(page._pending_jobs):
        record=page._session_jobs.get(jid)
        if record is not None and not is_stock(record) and (jid in page._active_job_ids or not record.is_terminal):return True
    return any(not is_stock(record) and not record.is_terminal and record.scheduled_at<=now
        for record in page._scheduled_jobs.values())


def request_stock_pause(page):
    for jid in list(page._active_job_ids):
        record=page._session_jobs.get(jid);worker=page._workers.get(jid)
        if not is_stock(record) or worker is None or record.combo_cancel_requested:continue
        if not worker._pause_event.is_set():
            worker.request_pause()
            record.status='Đang tạm dừng để ưu tiên khách'
            page._append_log_line('Có job khách: tạm dừng sau request đang gửi, giữ mail đã mua và tiến độ.',job_id=jid)
    stock=getattr(page,'stock_controller',None)
    if stock is not None:stock.pause_preparation()


def save_stock_pause(page,record):
    worker=page._workers.get(record.job_id)
    runner=getattr(worker,'runner',None)
    batch=getattr(runner,'mail_batch_path',None) or getattr(record,'resume_mail_batch','') or ''
    streak=getattr(runner,'failure_streak',getattr(record,'priority_failure_streak',0))
    when=datetime.now(VIETNAM_TIME)
    save=getattr(page,'priority_job_reschedule',None)
    if save is None or not save(record.job_id,when,record.success_count,record.attempted_count,
            record.failure_count,batch=str(batch),failure_streak=streak):
        raise RuntimeError('Không lưu được tiến độ tạm dừng kho acc.')
    record.priority_paused=True;record.resume_mail_batch=str(batch);record.priority_failure_streak=streak
    record.scheduled_at=when;record.status='Tạm dừng ưu tiên khách'
    page._scheduled_jobs[record.job_id]=record
    page._append_log_line('Đã lưu tạm dừng. Tự tiếp tục khi hết job khách đang chạy/chờ.',job_id=record.job_id)
    page._render_jobs_table();page._update_job_queue_label()


def save_resume_state(page,record):
    runner=getattr(page._workers.get(record.job_id),'runner',None)
    batch=getattr(runner,'mail_batch_path',None) or getattr(record,'resume_mail_batch','') or ''
    streak=getattr(runner,'failure_streak',getattr(record,'priority_failure_streak',0))
    if not page.stock_resume_save(record.job_id,str(batch),streak):raise RuntimeError('Chưa lưu được mail đã mua để tiếp tục.')
    record.resume_mail_batch=str(batch);record.priority_failure_streak=streak
    record.priority_paused=False
