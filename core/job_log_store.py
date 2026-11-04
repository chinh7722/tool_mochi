"""Append-only job logs, stable across combo rounds and service restarts."""
from pathlib import Path
import sqlite3
import threading
import uuid


class JobLogStore:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        self.db=sqlite3.connect(str(self.path),timeout=15,check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=NORMAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS log_metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        self.db.execute('INSERT OR IGNORE INTO log_metadata VALUES (?,?)',('epoch',uuid.uuid4().hex))
        self.epoch=self.db.execute("SELECT value FROM log_metadata WHERE key='epoch'").fetchone()[0]
        self.db.execute('CREATE TABLE IF NOT EXISTS job_logs (id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,line TEXT NOT NULL)')
        self.db.execute('CREATE INDEX IF NOT EXISTS job_log_order ON job_logs(job_id,id)');self.db.commit()

    def append(self,job_id,line):
        with self.lock,self.db:
            self.db.execute('INSERT INTO job_logs(job_id,line) VALUES (?,?)',(job_id,str(line)))

    def read(self,job_id,cursor=None,epoch=None,limit=1000):
        if cursor is not None and (type(cursor) is not int or cursor<0):
            raise ValueError('Vị trí log không hợp lệ.')
        reset=cursor is None or epoch!=self.epoch
        cursor=0 if reset else cursor
        with self.lock:
            rows=self.db.execute('SELECT id,line FROM job_logs WHERE job_id=? AND id>? ORDER BY id LIMIT ?',
                (job_id,cursor,limit+1)).fetchall()
        page=rows[:limit]
        return dict(job_id=job_id,lines=[r[1] for r in page],cursor=page[-1][0] if page else cursor,
            epoch=self.epoch,reset=reset,has_more=len(rows)>limit)

    def close(self):
        with self.lock:
            if self.db is not None:self.db.close();self.db=None
