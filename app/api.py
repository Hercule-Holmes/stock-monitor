"""全部 REST API"""
import datetime
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import config, db, ds, indicators, monitor, updater

router = APIRouter(prefix="/api")


# ----------------------------------------------------------------
# 元信息 / 状态
# ----------------------------------------------------------------
@router.get("/meta/indicators")
def meta_indicators():
    return [{"key": r["key"], "name": r["name"], "group": r["group"],
             "kind": r["kind"], "desc": r.get("desc", "")} for r in indicators.REGISTRY]


@router.get("/meta/status")
def meta_status():
    def one(sql, args=()):
        r = db.query(sql, args)
        return r[0][0] if r else 0
    return {
        "stock_count": one("SELECT COUNT(*) FROM stocks"),
        "daily_rows": one("SELECT COUNT(*) FROM daily"),
        "pool_count": one("SELECT COUNT(*) FROM pools"),
        "pool_stock_count": one("SELECT COUNT(DISTINCT code) FROM pool_stocks"),
        "rule_count": one("SELECT COUNT(*) FROM rules WHERE enabled=1"),
        "last_full_update": db.get_setting("last_full_update", "从未"),
        "last_pool_update": db.get_setting("last_pool_update", "从未"),
        "last_snapshot": db.get_setting("last_snapshot", "从未"),
        "job": updater.job_status(),
        "schedule": [{"slot": s, "label": lb, "time": f"{h:02d}:{m:02d}"}
                     for s, lb, (h, m) in config.SCHEDULE_SLOTS],
    }


# ----------------------------------------------------------------
# 筛选 / 搜索 / 详情
# ----------------------------------------------------------------
def _fmt_row(row) -> dict:
    d = dict(row)
    return d


@router.get("/stocks/search")
def stocks_search(q: str = "", filters: str = "", sort: str = "code",
                  dir: str = "asc", limit: int = 100, offset: int = 0):
    where, args = ["1=1"], []
    if q.strip():
        where.append("(code LIKE ? OR name LIKE ?)")
        args += [f"%{q.strip()}%"] * 2
    # 条件筛选
    try:
        conds = json.loads(filters) if filters else []
    except json.JSONDecodeError:
        raise HTTPException(400, "filters 格式错误")
    for c in conds:
        key, op, val = c.get("key"), c.get("op"), c.get("value")
        if key not in indicators.REG_MAP:
            raise HTTPException(400, f"未知指标 {key}")
        kind = indicators.REG_MAP[key]["kind"]
        if key == "code" or key == "name" or kind == "text":
            continue
        if kind == "flag":
            if op == "yes":
                where.append(f"{key}=1")
            elif op == "no":
                where.append(f"({key}=0 OR {key} IS NULL)")
            continue
        opmap = {"lt": "<", "le": "<=", "gt": ">", "ge": ">=", "eq": "="}
        if op not in opmap:
            raise HTTPException(400, f"未知算子 {op}")
        where.append(f"{key} IS NOT NULL AND {key} {opmap[op]} ?")
        args.append(float(val))

    # 排序白名单
    sortable = set(indicators.REG_MAP) | {"updated_at"}
    if sort not in sortable:
        sort = "code"
    dirsql = "DESC" if dir.lower() == "desc" else "ASC"

    where_sql = " AND ".join(where)
    total = db.query(f"SELECT COUNT(*) FROM stocks WHERE {where_sql}", tuple(args))[0][0]
    rows = db.query(
        f"SELECT * FROM stocks WHERE {where_sql} "
        f"ORDER BY {sort} {dirsql} NULLS LAST, code LIMIT ? OFFSET ?",
        tuple(args) + (min(limit, 1000), offset))
    return {"total": total, "items": [_fmt_row(r) for r in rows]}


@router.get("/stocks/{code}")
def stock_detail(code: str):
    rows = db.query("SELECT * FROM stocks WHERE code=?", (code,))
    if not rows:
        raise HTTPException(404, "未收录该股票, 请先更新数据")
    pools = db.query(
        "SELECT p.id, p.name, ps.tag, ps.note, ps.target_price FROM pool_stocks ps "
        "JOIN pools p ON p.id=ps.pool_id WHERE ps.code=?", (code,))
    dv = db.query("SELECT ex_date, cash FROM dividends WHERE code=? "
                  "ORDER BY ex_date DESC LIMIT 10", (code,))
    return {"stock": _fmt_row(rows[0]),
            "pools": [dict(r) for r in pools],
            "dividends": [dict(r) for r in dv]}


@router.get("/stocks/{code}/kline")
def stock_kline(code: str, days: int = 400):
    rows = db.query("SELECT date,open,high,low,close,volume,amount,pct_chg FROM daily "
                    "WHERE code=? ORDER BY date", (code,))
    import pandas as pd
    df = pd.DataFrame([dict(r) for r in rows])
    return indicators.kline_payload(df, days=min(days, 1500))


@router.get("/stocks/{code}/valuation")
def stock_valuation(code: str):
    rows = db.query("SELECT date,close,pe_ttm,pb FROM valuation WHERE code=? ORDER BY date",
                    (code,))
    import pandas as pd
    df = pd.DataFrame([dict(r) for r in rows])
    return indicators.valuation_payload(df)


# ----------------------------------------------------------------
# 股票池
# ----------------------------------------------------------------
class PoolIn(BaseModel):
    name: str
    note: str = ""


class PoolStockIn(BaseModel):
    code: str = ""  # 仅加入池时必填; 编辑标签/备注/预期价时可省略
    tag: str = ""
    note: str = ""
    target_price: float | None = None


@router.get("/pools")
def pools_list():
    rows = db.query(
        "SELECT p.*, COUNT(DISTINCT ps.code) AS stock_count FROM pools p "
        "LEFT JOIN pool_stocks ps ON ps.pool_id=p.id GROUP BY p.id ORDER BY p.id")
    return [dict(r) for r in rows]


@router.post("/pools")
def pools_create(p: PoolIn):
    try:
        db.execute("INSERT INTO pools(name,note,created_at) VALUES(?,?,?)",
                   (p.name.strip(), p.note, ds.today_str()))
    except Exception:
        raise HTTPException(400, "池名称已存在")
    return {"ok": True}


@router.delete("/pools/{pool_id}")
def pools_delete(pool_id: int):
    db.execute("DELETE FROM pool_stocks WHERE pool_id=?", (pool_id,))
    db.execute("DELETE FROM pools WHERE id=?", (pool_id,))
    db.execute("UPDATE rules SET pool_id=NULL WHERE pool_id=?", (pool_id,))
    return {"ok": True}


@router.get("/pools/{pool_id}/stocks")
def pool_stocks(pool_id: int):
    rows = db.query(
        "SELECT ps.tag, ps.note, ps.target_price, ps.added_at, s.* "
        "FROM pool_stocks ps LEFT JOIN stocks s ON s.code=ps.code "
        "WHERE ps.pool_id=? ORDER BY ps.code", (pool_id,))
    return [_fmt_row(r) for r in rows]


@router.post("/pools/{pool_id}/stocks")
async def pool_add_stock(pool_id: int, body: PoolStockIn):
    if not db.query("SELECT 1 FROM pools WHERE id=?", (pool_id,)):
        raise HTTPException(404, "池不存在")
    code = body.code.strip()
    if not code:
        raise HTTPException(400, "缺少股票代码")
    # 先入池, 再拉数据(池内更新依赖 pool_stocks 里的成员列表)
    db.execute("INSERT OR REPLACE INTO pool_stocks(pool_id,code,tag,note,target_price,added_at) "
               "VALUES(?,?,?,?,?,?)",
               (pool_id, code, body.tag, body.note, body.target_price, ds.today_str()))
    row = db.query("SELECT daily_date FROM stocks WHERE code=?", (code,))
    if not row or not row[0]["daily_date"]:
        # 未收录或无数据: 补名称后触发池内更新拉取
        if not row:
            try:
                name = ds.stock_name(code)
            except Exception:
                raise HTTPException(400, f"无法识别股票 {code}")
            db.execute("INSERT OR IGNORE INTO stocks(code,name,market,updated_at) VALUES(?,?,?,?)",
                       (code, name, ds.market_of(code), ds.today_str()))
        ok, msg = updater.run_pool_update(trigger="manual")
        if not ok:
            raise HTTPException(500, f"数据拉取失败: {msg}")
    return {"ok": True}


@router.put("/pools/{pool_id}/stocks/{code}")
def pool_edit_stock(pool_id: int, code: str, body: PoolStockIn):
    db.execute("UPDATE pool_stocks SET tag=?,note=?,target_price=? WHERE pool_id=? AND code=?",
               (body.tag, body.note, body.target_price, pool_id, code))
    return {"ok": True}


@router.delete("/pools/{pool_id}/stocks/{code}")
def pool_remove_stock(pool_id: int, code: str):
    db.execute("DELETE FROM pool_stocks WHERE pool_id=? AND code=?", (pool_id, code))
    return {"ok": True}


# ----------------------------------------------------------------
# 监控规则 / 信号
# ----------------------------------------------------------------
class RuleIn(BaseModel):
    name: str = ""
    indicator: str
    op: str
    value: float | None = None
    pool_id: int | None = None
    email: bool = False
    enabled: bool = True


@router.get("/rules")
def rules_list():
    rows = db.query(
        "SELECT r.*, p.name AS pool_name FROM rules r "
        "LEFT JOIN pools p ON p.id=r.pool_id ORDER BY r.id")
    out = []
    for r in rows:
        d = dict(r)
        ind = indicators.REG_MAP.get(r["indicator"], {})
        d["indicator_name"] = ind.get("name", r["indicator"])
        d["kind"] = ind.get("kind", "value")
        d["desc"] = monitor._rule_desc(r)
        out.append(d)
    return out


@router.post("/rules")
def rules_create(r: RuleIn):
    if r.indicator not in indicators.REG_MAP:
        raise HTTPException(400, "未知指标")
    db.execute("INSERT INTO rules(name,indicator,op,value,pool_id,email,enabled,created_at) "
               "VALUES(?,?,?,?,?,?,?,?)",
               (r.name or f"{indicators.REG_MAP[r.indicator]['name']}监控",
                r.indicator, r.op, r.value, r.pool_id,
                1 if r.email else 0, 1 if r.enabled else 0, ds.today_str()))
    return {"ok": True}


@router.put("/rules/{rule_id}")
def rules_update(rule_id: int, r: RuleIn):
    db.execute("UPDATE rules SET name=?,indicator=?,op=?,value=?,pool_id=?,email=?,enabled=? "
               "WHERE id=?",
               (r.name, r.indicator, r.op, r.value, r.pool_id,
                1 if r.email else 0, 1 if r.enabled else 0, rule_id))
    return {"ok": True}


@router.delete("/rules/{rule_id}")
def rules_delete(rule_id: int):
    db.execute("DELETE FROM signals WHERE rule_id=?", (rule_id,))
    db.execute("DELETE FROM rules WHERE id=?", (rule_id,))
    return {"ok": True}


@router.post("/rules/evaluate")
def rules_evaluate():
    new = monitor.evaluate_rules()
    return {"ok": True, "new_signals": len(new)}


@router.get("/signals")
def signals_list(days: int = 14, limit: int = 300):
    rows = db.query(
        "SELECT si.*, r.name AS rule_name, r.email, s.name AS stock_name "
        "FROM signals si LEFT JOIN rules r ON r.id=si.rule_id "
        "LEFT JOIN stocks s ON s.code=si.code "
        "WHERE si.trade_date >= ? ORDER BY si.id DESC LIMIT ?",
        ((datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y-%m-%d"),
         min(limit, 1000)))
    return [dict(r) for r in rows]


# ----------------------------------------------------------------
# 管理: 更新任务 / 设置
# ----------------------------------------------------------------
class SettingsIn(BaseModel):
    smtp_host: str = ""
    smtp_port: str = "465"
    smtp_ssl: str = "1"
    smtp_user: str = ""
    smtp_pass: str = ""
    mail_to: str = ""


@router.post("/admin/update/{kind}")
def admin_update(kind: str):
    """full/pool/snapshot 均为后台线程执行, 进度经 /api/admin/job 或 /api/meta/status 查询"""
    import threading
    if kind == "evaluate":
        new = monitor.evaluate_rules()
        return {"ok": True, "msg": f"评估完成, 新增信号 {len(new)} 条"}
    if kind == "full":
        target, label = updater.run_full_update, "全市场全量更新"
    elif kind == "pool":
        target = lambda: updater.run_pool_update(trigger="manual")
        label = "池内更新"
    elif kind == "snapshot":
        target, label = updater.run_snapshot_refresh, "盘中快照刷新"
    else:
        raise HTTPException(404, "未知任务")
    if updater.job_status()["status"] == "running":
        return {"ok": False, "msg": "已有任务在运行, 请稍候"}
    threading.Thread(target=target, daemon=True).start()
    return {"ok": True, "msg": f"{label}已开始, 进度可在左侧边栏查看"}


@router.get("/admin/job")
def admin_job():
    return updater.job_status()


@router.get("/settings")
def settings_get():
    rows = db.query("SELECT key,value FROM settings WHERE key LIKE 'smtp%' OR key='mail_to'")
    d = {r["key"]: r["value"] for r in rows}
    for k in ("smtp_host", "smtp_port", "smtp_ssl", "smtp_user", "smtp_pass", "mail_to"):
        d.setdefault(k, "")
    return d


@router.post("/settings")
def settings_save(s: SettingsIn):
    for k, v in s.model_dump().items():
        db.set_setting(k, v)
    return {"ok": True}


@router.post("/settings/test_email")
def settings_test_email():
    ok, msg = monitor.send_email(
        "【股票监控】测试邮件",
        f"这是一封测试邮件, SMTP 配置有效。\n{datetime.datetime.now()}")
    return {"ok": ok, "msg": msg}
