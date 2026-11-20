"""数据更新引擎。
任务类型:
  full     全市场全量更新(首次初始化 / 手动触发, 约20~30分钟)
  pool     池内股票更新(自动: 中午+收盘后; 也可手动), 更新后评估监控规则
  snapshot 盘中快照(手动): 仅用全市场最新价刷新行情列, 均线/估值不变
设计: 所有任务幂等; 服务启动/唤醒时 catchup() 检查过期即补跑(用户明确不做开机自启,
     此处只在手动启动服务后运行)。
"""
import datetime
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config, db, ds, indicators
from .monitor import evaluate_rules

_job_lock = threading.Lock()
_job = {"name": None, "status": "idle", "progress": 0, "total": 0,
        "message": "", "started_at": None, "error": None}


def job_status() -> dict:
    return dict(_job)


def _set_job(**kw):
    _job.update(kw)


# ----------------------------------------------------------------
# 单只股票: 拉取+入库+算指标
# ----------------------------------------------------------------
def _update_one_stock(code: str, with_dividend: bool = True) -> dict | None:
    daily = ds.daily_hist(code)
    if daily.empty:
        return None
    val = ds.valuation_hist(code)

    # 入库日线/估值(整只覆盖)
    conn = db.get_db()
    conn.execute("DELETE FROM daily WHERE code=?", (code,))
    conn.executemany(
        "INSERT OR REPLACE INTO daily(code,date,open,high,low,close,volume,amount,pct_chg) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        [(code, r.date, r.open, r.high, r.low, r.close, r.volume, r.amount, r.pct_chg)
         for r in daily.itertuples()])
    conn.execute("DELETE FROM valuation WHERE code=?", (code,))
    if not val.empty:
        conn.executemany(
            "INSERT OR REPLACE INTO valuation(code,date,close,pe_ttm,pe_static,pb,peg,pcr,psr,mktcap) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            [(code, r.date, r.close, getattr(r, "pe_ttm", None), getattr(r, "pe_static", None),
              getattr(r, "pb", None), getattr(r, "peg", None), getattr(r, "pcr", None),
              getattr(r, "psr", None), getattr(r, "mktcap", None)) for r in val.itertuples()])

    dv_ttm = ds.dv_ttm_from(code, float(daily.iloc[-1]["close"]),
                            str(daily.iloc[-1]["date"])) if with_dividend else _dv_keep(code)
    metrics = indicators.compute_metrics(daily, val, dv_ttm)
    metrics["code"] = code
    _upsert_stock_row(metrics)
    conn.commit()
    return metrics


def _dv_keep(code: str) -> float | None:
    rows = db.query("SELECT dv_ttm FROM stocks WHERE code=?", (code,))
    return rows[0]["dv_ttm"] if rows else None


def _upsert_stock_row(m: dict):
    code = m.pop("code")
    m["updated_at"] = ds.today_str()
    cols = list(m.keys())
    conn = db.get_db()
    # 保证列存在(个人指标插件可动态扩展)
    existing = {r[1] for r in conn.execute("PRAGMA table_info(stocks)")}
    for c in cols:
        if c not in existing:
            conn.execute(f"ALTER TABLE stocks ADD COLUMN {c} REAL")
            existing.add(c)
    ph = ",".join(["?"] * len(cols))
    updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "code")
    sql = (f"INSERT INTO stocks({','.join(cols)},code) VALUES({ph},?) "
           f"ON CONFLICT(code) DO UPDATE SET {updates}")
    conn.execute(sql, tuple(m[c] for c in cols) + (code,))


# ----------------------------------------------------------------
# 任务: 全市场全量
# ----------------------------------------------------------------
def run_full_update():
    if not _job_lock.acquire(blocking=False):
        return False, "已有任务在运行"
    try:
        _set_job(name="full", status="running", progress=0, total=0,
                 message="拉取全市场快照...", started_at=time.time(), error=None)
        spot = ds.spot_snapshot()
        codes = spot["code"].tolist()
        # 基础信息先入库(名称/行情), 即使个股历史拉取失败也能搜索
        _upsert_spot(spot)
        _set_job(total=len(codes), message=f"开始拉取 {len(codes)} 只股票的历史数据...")

        ok, fail = 0, 0
        def work(c):
            return _update_one_stock(c, with_dividend=True)

        with ThreadPoolExecutor(max_workers=config.UPDATE_WORKERS) as ex:
            futs = {ex.submit(work, c): c for c in codes}
            for i, fut in enumerate(as_completed(futs), 1):
                try:
                    if fut.result() is not None:
                        ok += 1
                except Exception:
                    fail += 1
                if i % 50 == 0 or i == len(codes):
                    _set_job(progress=i, message=f"全市场更新 {i}/{len(codes)} (成功{ok} 失败{fail})")
        db.set_setting("last_full_update", ds.today_str())
        _set_job(status="done", message=f"全市场更新完成: 成功{ok} 失败{fail}", progress=len(codes))
        return True, f"成功{ok} 失败{fail}"
    except Exception as e:
        _set_job(status="error", error=str(e), message="任务失败")
        traceback.print_exc()
        return False, str(e)
    finally:
        _job_lock.release()


def _upsert_spot(spot):
    """快照基础信息入库(名称/最新行情), 不动指标列"""
    conn = db.get_db()
    for r in spot.itertuples():
        code = r.code
        m = {"market": ds.market_of(code), "name": getattr(r, "name", None),
             "close": getattr(r, "close", None),
             "pct_chg": getattr(r, "pct_chg", None),
             "turnover": getattr(r, "turnover", None),
             "volume_ratio": getattr(r, "volume_ratio", None),
             "updated_at": ds.today_str()}
        cols = list(m.keys())
        ph = ",".join(["?"] * len(cols))
        updates = ",".join(f"{c}=excluded.{c}" for c in cols)
        conn.execute(f"INSERT INTO stocks(code,{','.join(cols)}) VALUES(?,{ph}) "
                     f"ON CONFLICT(code) DO UPDATE SET {updates}",
                     (code,) + tuple(m[c] for c in cols))
    conn.commit()


# ----------------------------------------------------------------
# 任务: 池内更新(自动/手动), 更新完评估监控规则
# ----------------------------------------------------------------
def run_pool_update(trigger: str = "manual") -> tuple[bool, str]:
    if not _job_lock.acquire(blocking=False):
        return False, "已有任务在运行"
    try:
        codes = [r["code"] for r in db.query(
            "SELECT DISTINCT code FROM pool_stocks ORDER BY code")]
        slot = "close" if trigger == "close" else ("noon" if trigger == "noon" else "manual")
        label = {"manual": "池内更新", "noon": "中午更新", "close": "收盘更新"}[slot]
        _set_job(name="pool", status="running", progress=0, total=max(len(codes), 1),
                 message=f"{label}: 共 {len(codes)} 只", started_at=time.time(), error=None)
        if not codes:
            _set_job(status="done", message="股票池为空, 无需更新")
            return True, "池为空"

        ok, fail = 0, 0
        with ThreadPoolExecutor(max_workers=config.UPDATE_WORKERS) as ex:
            futs = {ex.submit(_update_one_stock, c, True): c for c in codes}
            for i, fut in enumerate(as_completed(futs), 1):
                try:
                    if fut.result() is not None:
                        ok += 1
                except Exception:
                    fail += 1
                _set_job(progress=i, message=f"{label} {i}/{len(codes)}")

        new_signals = evaluate_rules()
        db.set_setting(f"last_pool_update_{slot}", ds.today_str())
        db.set_setting("last_pool_update", f"{ds.today_str()} ({label})")
        _set_job(status="done",
                 message=f"{label}完成: 成功{ok} 失败{fail}, 新增信号 {len(new_signals)} 条")
        return True, f"成功{ok} 失败{fail}, 新增信号{len(new_signals)}"
    except Exception as e:
        _set_job(status="error", error=str(e), message="任务失败")
        traceback.print_exc()
        return False, str(e)
    finally:
        _job_lock.release()


# ----------------------------------------------------------------
# 任务: 盘中快照(手动) —— 快照价刷新行情列+重算破位标记, 77秒左右
# ----------------------------------------------------------------
def run_snapshot_refresh():
    if not _job_lock.acquire(blocking=False):
        return False, "已有任务在运行"
    try:
        import pandas as pd
        _set_job(name="snapshot", status="running", progress=0, total=0,
                 message="拉取全市场盘中快照...", started_at=time.time(), error=None)
        spot = ds.spot_snapshot()
        _upsert_spot(spot)

        # 用已存均线重算破位标记(盘中口径: 均线仍基于已存日线)
        rows = db.query("SELECT code,close,ma20,ma60,ma120,ma250,boll_mid,boll_low,boll_up "
                        "FROM stocks")
        df = pd.DataFrame([dict(r) for r in rows])
        df = df.dropna(subset=["close"])
        def f(series, cmp):
            s = pd.to_numeric(series, errors="coerce")
            return (cmp(df["close"], s) & s.notna()).astype(int)
        df["below_ma20"] = f(df["ma20"], lambda a, b: a < b)
        df["below_ma60"] = f(df["ma60"], lambda a, b: a < b)
        df["below_ma120"] = f(df["ma120"], lambda a, b: a < b)
        df["below_ma250"] = f(df["ma250"], lambda a, b: a < b)
        df["below_boll_mid"] = f(df["boll_mid"], lambda a, b: a < b)
        df["below_boll_low"] = f(df["boll_low"], lambda a, b: a < b)
        df["above_boll_up"] = f(df["boll_up"], lambda a, b: a > b)

        conn = db.get_db()
        cols = ["below_ma20", "below_ma60", "below_ma120", "below_ma250",
                "below_boll_mid", "below_boll_low", "above_boll_up"]
        for r in df.itertuples():
            conn.execute(f"UPDATE stocks SET {','.join(f'{c}=?' for c in cols)} WHERE code=?",
                         tuple(getattr(r, c) for c in cols) + (r.code,))
        conn.commit()
        db.set_setting("last_snapshot", ds.today_str() + " " + datetime.datetime.now().strftime("%H:%M"))
        _set_job(status="done", message=f"盘中快照完成: 刷新 {len(df)} 只")
        return True, f"刷新{len(df)}只"
    except Exception as e:
        _set_job(status="error", error=str(e), message="任务失败")
        traceback.print_exc()
        return False, str(e)
    finally:
        _job_lock.release()


# ----------------------------------------------------------------
# 调度: 中午/收盘两次池内更新 + 启动补跑
# ----------------------------------------------------------------
def _slot_done_today(slot: str) -> bool:
    return db.get_setting(f"last_pool_update_{slot}") == ds.today_str()


def scheduler_loop(stop_event: threading.Event):
    """后台线程: 工作日在 12:30 / 15:30 触发池内更新"""
    while not stop_event.is_set():
        try:
            now = datetime.datetime.now()
            if now.weekday() < 5:  # 周一~周五
                for slot, _, (hh, mm) in config.SCHEDULE_SLOTS:
                    if (now.hour, now.minute) >= (hh, mm) and not _slot_done_today(slot):
                        if _job["status"] not in ("running",):
                            run_pool_update(trigger=slot)
                            break
        except Exception:
            traceback.print_exc()
        stop_event.wait(30)


def catchup():
    """启动/唤醒后补跑: 当天错过的池内更新立即执行一次"""
    now = datetime.datetime.now()
    if now.weekday() >= 5:
        return "周末不补跑"
    need = None
    for slot, _, (hh, mm) in config.SCHEDULE_SLOTS:
        if now >= now.replace(hour=hh, minute=mm) and not _slot_done_today(slot):
            need = slot  # 取最晚一个错过的时段
    if need:
        ok, msg = run_pool_update(trigger=need)
        return f"补跑{need}: {msg}"
    return "数据已是最新"
