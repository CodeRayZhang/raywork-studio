import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


def now():
    return datetime.now(UTC).isoformat()


def uid():
    return uuid.uuid4().hex


def event(project, action, detail, actor="工作室"):
    project["events"].append(
        {"id": uid(), "at": now(), "action": action, "detail": detail, "actor": actor}
    )
    project["updated_at"] = now()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, key TEXT UNIQUE NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS shares (hash TEXT PRIMARY KEY, project_id TEXT NOT NULL, expires TEXT NOT NULL);
            """)
            # Interrupted work is recoverable by an explicit retry, never silently replayed/billed.
            for row in db.execute("SELECT id, data FROM jobs").fetchall():
                job = json.loads(row[1])
                if job["status"] in ("queued", "running"):
                    job.update(
                        status="failed",
                        error="服务重启中断了任务；确认后可重试。",
                        finished_at=now(),
                    )
                    db.execute(
                        "UPDATE jobs SET data=? WHERE id=?", (json.dumps(job), row[0])
                    )

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, pid, db=None):
        if db is None:
            with self.connection() as conn:
                return self.get(pid, conn)
        row = db.execute("SELECT data FROM projects WHERE id=?", (pid,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, project, db):
        db.execute(
            "INSERT INTO projects(id,data) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            (project["id"], json.dumps(project, ensure_ascii=False)),
        )

    def mutate(self, pid, fn):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            project = self.get(pid, db)
            if project is None:
                return None
            fn(project)
            self.save(project, db)
            return project

    def projects(self):
        with self.connection() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT data FROM projects")]

    def job(self, jid, db=None):
        if db is None:
            with self.connection() as conn:
                return self.job(jid, conn)
        row = db.execute("SELECT data FROM jobs WHERE id=?", (jid,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_job(self, job, db):
        db.execute(
            "INSERT INTO jobs(id,project_id,key,data) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            (
                job["id"],
                job["project_id"],
                job["key"],
                json.dumps(job, ensure_ascii=False),
            ),
        )

    def jobs(self, pid):
        with self.connection() as db:
            return [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT data FROM jobs WHERE project_id=? ORDER BY rowid DESC LIMIT 30",
                    (pid,),
                )
            ]
