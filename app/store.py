import sqlite3,json,os
from pathlib import Path
from app.errors import DomainError

def connection():
    path=Path(os.environ.get('DB_PATH','data/records.db'));path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=10);db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, body TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, decision TEXT NOT NULL DEFAULT "待复核")')
    db.execute('CREATE TABLE IF NOT EXISTS review_tasks (task_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, bucket TEXT NOT NULL, zone TEXT NOT NULL, status TEXT NOT NULL, score REAL NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT "", body TEXT NOT NULL DEFAULT "")')
    db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    return db

def save(run_id, body):
    with connection() as db:
        db.execute('INSERT OR IGNORE INTO runs(id,body) VALUES (?,?)',(run_id,json.dumps(body,ensure_ascii=False)))
        # 任务按(设备,时段)确定性主键幂等入库，同一批数据重跑不新增；与批次、最新指针同事务提交
        for t in body.get('review_tasks',[]):
            db.execute("INSERT INTO review_tasks(task_id,run_id,bucket,zone,status,score,reason,body) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET run_id=excluded.run_id,status=excluded.status,score=excluded.score,reason=excluded.reason,body=excluded.body",
                (t['task_id'],run_id,t['bucket'],t['zone'],t['status'],t['score'],t['reason'],json.dumps(t,ensure_ascii=False)))
        db.execute("INSERT INTO meta(key,value) VALUES ('latest',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(run_id,))
    return get(run_id)

def get(run_id):
    with connection() as db:r=db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if not r:raise DomainError('NOT_FOUND','批次不存在',404)
    body=json.loads(r['body']);body.update(run_id=r['id'],review_version=r['version'],decision=r['decision']);return body

def history():
    with connection() as db:rows=db.execute("SELECT id,version,decision FROM runs ORDER BY CASE WHEN id=(SELECT value FROM meta WHERE key='latest') THEN 0 ELSE 1 END,rowid DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]

def tasks():
    with connection() as db:rows=db.execute('SELECT task_id,run_id,bucket,zone,status,score,reason FROM review_tasks ORDER BY task_id').fetchall()
    return [dict(r) for r in rows]

def review(run_id, version, decision):
    if decision not in ('通过','退回'):raise DomainError('DECISION','复核状态无效')
    with connection() as db:
        cur=db.execute('UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?',(decision,run_id,version))
        if cur.rowcount!=1:
            if not db.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():raise DomainError('NOT_FOUND','批次不存在',404)
            raise DomainError('STALE','复核版本已更新，请刷新',409)
    return get(run_id)
