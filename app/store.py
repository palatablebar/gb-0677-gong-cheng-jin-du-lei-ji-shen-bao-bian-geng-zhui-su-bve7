"""批次存储：同一申报事实只保留一个现行结论，被替代版本完整保留。

写入规则：
- 结论身份由输入摘要、规则版本与处理参数决定（app/conclusion.py 的
  fingerprint）。相同身份重跑直接复用原记录，不新增版本、不改动版本号。
- 新结论产生时，原现行结论在同一事务内转为“被替代”，新结论成为唯一现行。
  被替代记录的结论内容、复核意见与创建时的对照关系都不改写。
- 切换现行结论同样在一个事务内完成：目标版本或并发现行版本校验失败则整体
  回滚，原现行结论保持不变。
- 复核、写入与现行切换都追加写入审计表，可按 run_id 回查。

字段口径：
- replaced_run_id：本版最近一次成为现行时被它替代的版本；切换现行时随之更新，
  用于“从现行结论回到被替代版本”。
- origin_run_id：本版首次写入时紧随的版本，不可变，用于复核当时的生成脉络。
- superseded_by：把本版从现行位置上替换掉的版本。
- replaced_at：最近一次成为现行的时刻。
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from app import conclusion
from app.config import DATA_DIR
from app.errors import DomainError

CURRENT = "current"
SUPERSEDED = "superseded"
_initialised = set()
_EXTRA_COLUMNS = {
    "fingerprint": "TEXT",
    "state": "TEXT NOT NULL DEFAULT '%s'" % SUPERSEDED,
    "replaced_run_id": "TEXT",
    "origin_run_id": "TEXT",
    "replaced_at": "TEXT",
    "superseded_by": "TEXT",
    "created_at": "TEXT",
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "records.db"
    db = sqlite3.connect(path, timeout=20, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute(
        "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY,payload TEXT NOT NULL,"
        "version INTEGER NOT NULL DEFAULT 1,decision TEXT NOT NULL DEFAULT '待复核')"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS reviews (id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT,"
        "version INTEGER,decision TEXT,reviewed_at TEXT)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS activations (id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT,"
        "version INTEGER,superseded_run_id TEXT,activated_at TEXT)"
    )
    if path.resolve() not in _initialised:
        _migrate(db)
        _initialised.add(path.resolve())
    return db


def _migrate(db):
    """兼容初始快照留下的旧库：补列、补指纹，并保证只有一条现行结论。"""
    existing = {row["name"] for row in db.execute("PRAGMA table_info(runs)")}
    for name, ddl in _EXTRA_COLUMNS.items():
        if name not in existing:
            try:
                db.execute("ALTER TABLE runs ADD COLUMN %s %s" % (name, ddl))
            except sqlite3.OperationalError:
                pass
    db.execute("UPDATE runs SET fingerprint='' WHERE fingerprint IS NULL")
    for row in db.execute("SELECT id,payload FROM runs WHERE fingerprint=''"):
        try:
            payload = json.loads(row["payload"])
            mark = conclusion.fingerprint(payload.get("manifest") or {})
        except (ValueError, TypeError):
            mark = ""
        db.execute("UPDATE runs SET fingerprint=? WHERE id=? AND fingerprint=''", (mark, row["id"]))
    db.execute("UPDATE runs SET created_at=? WHERE created_at IS NULL", (_now(),))
    current = db.execute("SELECT id FROM runs WHERE state=? ORDER BY rowid DESC", (CURRENT,)).fetchall()
    if not current:
        latest = db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
        if latest is not None:
            db.execute("UPDATE runs SET state=? WHERE id=?", (CURRENT, latest["id"]))
    else:
        for row in current[1:]:
            db.execute("UPDATE runs SET state=? WHERE id=?", (SUPERSEDED, row["id"]))
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS runs_single_current ON runs(state) WHERE state='%s'" % CURRENT)
    # 旧库没有替代关系：按写入先后把相邻两版串起来，回查即可看到前后版本。
    ordered = [row["id"] for row in db.execute("SELECT id FROM runs ORDER BY rowid").fetchall()]
    for index, current_id in enumerate(ordered):
        if index + 1 < len(ordered):
            successor = ordered[index + 1]
            db.execute(
                "UPDATE runs SET origin_run_id=COALESCE(origin_run_id,?),replaced_run_id=COALESCE(replaced_run_id,?),"
                "superseded_by=COALESCE(superseded_by,?) WHERE id=?",
                (ordered[index - 1] if index else None, ordered[index - 1] if index else None, successor, current_id),
            )
        else:
            db.execute(
                "UPDATE runs SET origin_run_id=COALESCE(origin_run_id,?),replaced_run_id=COALESCE(replaced_run_id,?)"
                " WHERE id=?",
                (ordered[index - 1] if index else None, ordered[index - 1] if index else None, current_id),
            )


@contextmanager
def transaction():
    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.execute("COMMIT")
    except Exception:
        try:
            db.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise
    finally:
        db.close()


def _row(db, run_id):
    return db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()


def _audit(db, table, run_id):
    columns = "version,decision,reviewed_at" if table == "reviews" else "version,superseded_run_id,activated_at"
    return [
        {column: row[column] for column in columns.split(",")}
        for row in db.execute("SELECT %s FROM %s WHERE run_id=? ORDER BY id" % (columns, table), (run_id,))
    ]


def _present(db, row, with_comparison=True):
    payload = json.loads(row["payload"])
    record = {
        **payload,
        "run_id": row["id"],
        "version": row["version"],
        "decision": row["decision"],
        "fingerprint": row["fingerprint"],
        "state": row["state"],
        "is_current": row["state"] == CURRENT,
        "replaced_run_id": row["replaced_run_id"],
        "origin_run_id": row["origin_run_id"],
        "superseded_by": row["superseded_by"],
        "replaced_at": row["replaced_at"],
        "created_at": row["created_at"],
        "reviews": _audit(db, "reviews", row["id"]),
        "activations": _audit(db, "activations", row["id"]),
    }
    if with_comparison:
        record["comparison"] = _comparison(db, row)
    return record


def _comparison(db, row, against=None):
    """差异说明固定以“本版生成时的前一版”为基准，复核依据不随切换而变。

    replaced_run_id 描述切换关系（本版最近替代了谁，随切换更新），
    origin_run_id 才是生成时的比较基准且不可变，因此回看任何一版看到的
    差异都与生成时完全一致。切换带来的变化另由 replaced_run_id 与
    activations 记录表达；需要横向复核时用 against 显式指定对照版本。
    """
    target = against if against is not None else (row["origin_run_id"] or row["replaced_run_id"])
    if not target:
        return conclusion.baseline(json.loads(row["payload"]))
    previous = _row(db, target)
    if previous is None:
        raise DomainError("NOT_FOUND", "对照批次不存在", 404)
    return conclusion.compare(json.loads(previous["payload"]), json.loads(row["payload"]))


def _fingerprint_owner(db, mark):
    return db.execute("SELECT * FROM runs WHERE fingerprint=? ORDER BY rowid LIMIT 1", (mark,)).fetchone()


def save(run_id, payload):
    """保存结论；相同输入与处理规则重跑复用原记录，不新增版本。"""
    mark = conclusion.fingerprint(payload.get("manifest") or {})
    with transaction() as db:
        existing = _fingerprint_owner(db, mark)
        if existing is not None:
            record = _present(db, existing)
            record["reused"] = True
            return record
        if _row(db, run_id) is not None:
            raise DomainError("RUN_ID_COLLISION", "批次编号冲突，请重试", 409)
        previous = db.execute("SELECT id FROM runs WHERE state=?", (CURRENT,)).fetchone()
        if previous is not None:
            changed = db.execute(
                "UPDATE runs SET state=?,superseded_by=? WHERE id=? AND state=?",
                (SUPERSEDED, run_id, previous["id"], CURRENT),
            ).rowcount
            if changed != 1:
                raise DomainError("CONFLICT", "现行结论已被更新，请刷新后重试", 409)
        stamp = _now()
        db.execute(
            "INSERT INTO runs(id,payload,version,decision,fingerprint,state,replaced_run_id,origin_run_id,"
            "replaced_at,created_at) VALUES (?,?,1,'待复核',?,?,?,?,?,?)",
            (
                run_id,
                json.dumps(payload, ensure_ascii=False),
                mark,
                CURRENT,
                previous["id"] if previous is not None else None,
                previous["id"] if previous is not None else None,
                stamp,
                stamp,
            ),
        )
        return _present(db, _row(db, run_id))


def get(run_id, with_comparison=True, against=None):
    db = connect()
    try:
        row = _row(db, run_id)
        if row is None:
            raise DomainError("NOT_FOUND", "批次不存在", 404)
        record = _present(db, row, with_comparison=False)
        if with_comparison:
            record["comparison"] = _comparison(db, row, against)
        return record
    finally:
        db.close()


def current():
    db = connect()
    try:
        row = db.execute("SELECT * FROM runs WHERE state=? ORDER BY rowid DESC LIMIT 1", (CURRENT,)).fetchone()
        return _present(db, row) if row is not None else None
    finally:
        db.close()


def latest():
    """最近写入的批次，不论是否已被替代。"""
    db = connect()
    try:
        row = db.execute("SELECT * FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
        return _present(db, row) if row is not None else None
    finally:
        db.close()


def history():
    db = connect()
    try:
        return [
            {
                "run_id": row["id"],
                "version": row["version"],
                "decision": row["decision"],
                "state": row["state"],
                "is_current": row["state"] == CURRENT,
                "fingerprint": row["fingerprint"],
                "replaced_run_id": row["replaced_run_id"],
                "origin_run_id": row["origin_run_id"],
                "superseded_by": row["superseded_by"],
                "created_at": row["created_at"],
                "replaced_at": row["replaced_at"],
                "rule_version": (json.loads(row["payload"]).get("manifest") or {}).get("rule_version"),
            }
            for row in db.execute("SELECT * FROM runs ORDER BY rowid DESC")
        ]
    finally:
        db.close()


def compare_runs(run_id, against=None):
    if against is None:
        against = get(run_id, with_comparison=False)["origin_run_id"]
    if against is None:
        raise DomainError("NO_BASELINE", "该批次是首版，可用 against 指定对照版本", 409)
    if run_id == against:
        raise DomainError("BAD_COMPARISON", "不能与自身比较")
    return {"run_id": run_id, "against": against, "comparison": get(run_id, against=against)["comparison"]}


def activate(run_id, version=None, expected_current=None):
    """把被替代版本切回现行；任一校验失败整体回滚，原现行结论保持不变。"""
    with transaction() as db:
        target = _row(db, run_id)
        if target is None:
            raise DomainError("NOT_FOUND", "批次不存在", 404)
        if version is not None and target["version"] != version:
            raise DomainError("STALE_VERSION", "版本已更新，请刷新", 409)
        if target["state"] == CURRENT:
            return _present(db, target)
        current_row = db.execute("SELECT id FROM runs WHERE state=?", (CURRENT,)).fetchone()
        if expected_current is not None and (current_row is None or current_row["id"] != expected_current):
            raise DomainError("CURRENT_MOVED", "现行结论已变化，请刷新", 409)
        if current_row is not None:
            changed = db.execute(
                "UPDATE runs SET state=?,superseded_by=? WHERE id=? AND state=?",
                (SUPERSEDED, run_id, current_row["id"], CURRENT),
            ).rowcount
            if changed != 1:
                raise DomainError("CURRENT_MOVED", "现行结论已变化，请刷新", 409)
        changed = db.execute(
            "UPDATE runs SET state=?,superseded_by=NULL,replaced_run_id=?,replaced_at=? WHERE id=? AND state=?",
            (
                CURRENT,
                current_row["id"] if current_row is not None else None,
                _now(),
                run_id,
                SUPERSEDED,
            ),
        ).rowcount
        if changed != 1:
            raise DomainError("CONFLICT", "结论状态已变化，请刷新", 409)
        db.execute(
            "INSERT INTO activations(run_id,version,superseded_run_id,activated_at) VALUES (?,?,?,?)",
            (run_id, target["version"], current_row["id"] if current_row is not None else None, _now()),
        )
        return _present(db, _row(db, run_id))


def review(run_id, version, decision):
    """复核现行结论。被替代版本保留原有复核记录，但不再接受新的签认。"""
    if decision not in {"已复核", "退回复核"}:
        raise DomainError("BAD_DECISION", "复核状态无效")
    with transaction() as db:
        row = _row(db, run_id)
        if row is None:
            raise DomainError("NOT_FOUND", "批次不存在", 404)
        if row["state"] != CURRENT:
            raise DomainError("SUPERSEDED", "该版本已被替代，请复核现行结论", 409)
        cursor = db.execute(
            "UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?", (decision, run_id, version)
        )
        if cursor.rowcount != 1:
            raise DomainError("STALE_VERSION", "版本已更新，请刷新", 409)
        db.execute(
            "INSERT INTO reviews(run_id,version,decision,reviewed_at) VALUES (?,?,?,?)",
            (run_id, version + 1, decision, _now()),
        )
        return _present(db, _row(db, run_id))
