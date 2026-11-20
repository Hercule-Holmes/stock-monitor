"""监控规则引擎: 规则 = 指标 + 算子 + 阈值, 仅对股票池成员评估。
特殊指标 target_reached: 现价 ≥ 池内预设预期价。
命中写入 signals(按 规则×股票×交易日 去重), 带 email 标记的规则命中后发邮件汇总。
"""
import datetime
import smtplib
from email.header import Header
from email.mime.text import MIMEText

from . import db
from .indicators import REG_MAP

OP_TEXT = {"lt": "<", "le": "≤", "gt": ">", "ge": "≥", "yes": "为真", "no": "为假"}


def _rule_desc(rule) -> str:
    ind = REG_MAP.get(rule["indicator"], {})
    iname = ind.get("name", rule["indicator"])
    if rule["indicator"] == "target_reached":
        return "现价达到预期线"
    if ind.get("kind") == "flag":
        return f"{iname} {OP_TEXT.get(rule['op'], rule['op'])}"
    return f"{iname} {OP_TEXT.get(rule['op'], rule['op'])} {rule['value']}"


def _rule_where(rule) -> tuple[str, list]:
    """规则 → SQL 条件 (只针对 stocks 表列, target_reached 单独处理)"""
    ind = rule["indicator"]
    op, val = rule["op"], rule["value"]
    if ind == "target_reached":
        return "1=0", []  # 占位, 实际走专用逻辑
    if ind not in REG_MAP:
        return "1=0", []
    if REG_MAP[ind]["kind"] == "flag":
        if op == "yes":
            return f"{ind}=1", []
        if op == "no":
            return f"({ind}=0 OR {ind} IS NULL)", []
        return "1=0", []
    if op == "lt":
        return f"{ind} IS NOT NULL AND {ind} < ?", [val]
    if op == "le":
        return f"{ind} IS NOT NULL AND {ind} <= ?", [val]
    if op == "gt":
        return f"{ind} IS NOT NULL AND {ind} > ?", [val]
    if op == "ge":
        return f"{ind} IS NOT NULL AND {ind} >= ?", [val]
    return "1=0", []


def _pool_codes(pool_id: int | None) -> list[str]:
    if pool_id:
        rows = db.query("SELECT code FROM pool_stocks WHERE pool_id=?", (pool_id,))
    else:
        rows = db.query("SELECT DISTINCT code FROM pool_stocks")
    return [r["code"] for r in rows]


def evaluate_rules() -> list[dict]:
    """评估所有启用规则, 返回本次新增信号"""
    rules = db.query("SELECT * FROM rules WHERE enabled=1")
    today = ds_today()
    new_signals: list[dict] = []
    for rule in rules:
        codes = _pool_codes(rule["pool_id"])
        if not codes:
            continue
        matched: list[tuple[str, str]] = []  # (code, detail)
        if rule["indicator"] == "target_reached":
            sql = ("SELECT ps.code, ps.target_price, s.close, s.name "
                   "FROM pool_stocks ps LEFT JOIN stocks s ON s.code=ps.code "
                   "WHERE ps.target_price IS NOT NULL")
            args: tuple = ()
            if rule["pool_id"]:
                sql += " AND ps.pool_id=?"
                args = (rule["pool_id"],)
            rows = db.query(sql, args)
            for r in rows:
                if r["close"] is not None and r["close"] >= r["target_price"]:
                    matched.append((r["code"],
                                    f"现价{r['close']} ≥ 预期线{r['target_price']}"))
        else:
            where, args = _rule_where(rule)
            iname = REG_MAP[rule["indicator"]]["name"]
            is_flag = REG_MAP[rule["indicator"]]["kind"] == "flag"
            desc = _rule_desc(rule)
            chunk = 500
            for i in range(0, len(codes), chunk):
                part = codes[i:i + chunk]
                qmarks = ",".join("?" * len(part))
                rows = db.query(
                    f"SELECT code, name, {rule['indicator']} AS v FROM stocks "
                    f"WHERE code IN ({qmarks}) AND {where}", tuple(part) + tuple(args))
                for r in rows:
                    if is_flag:
                        matched.append((r["code"], f"{iname}成立"))
                    else:
                        matched.append((r["code"], f"{desc} (当前 {iname}={r['v']})"))
        for code, detail in matched:
            cur = db.get_db().execute(
                "INSERT OR IGNORE INTO signals(rule_id,code,trade_date,detail,created_at) "
                "VALUES(?,?,?,?,?)",
                (rule["id"], code, today, detail,
                 datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            if cur.rowcount == 1:
                new_signals.append({"rule_id": rule["id"], "code": code,
                                    "trade_date": today, "detail": detail,
                                    "email": bool(rule["email"])})
    db.get_db().commit()
    _send_email_for(new_signals)
    return new_signals


def ds_today() -> str:
    return datetime.date.today().strftime("%Y-%m-%d")


# ----------------------------------------------------------------
# 邮件
# ----------------------------------------------------------------
def _smtp_conf() -> dict | None:
    keys = ["smtp_host", "smtp_port", "smtp_ssl", "smtp_user", "smtp_pass", "mail_to"]
    rows = db.query(f"SELECT key,value FROM settings WHERE key IN "
                    f"({','.join('?' * len(keys))})", tuple(keys))
    conf = {r["key"]: r["value"] for r in rows}
    if not conf.get("smtp_host") or not conf.get("mail_to"):
        return None
    conf.setdefault("smtp_port", "465")
    conf.setdefault("smtp_ssl", "1")
    return conf


def send_email(subject: str, body: str) -> tuple[bool, str]:
    conf = _smtp_conf()
    if not conf:
        return False, "SMTP 未配置"
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = conf["smtp_user"]
        msg["To"] = conf["mail_to"]
        port = int(conf.get("smtp_port") or 465)
        if conf.get("smtp_ssl", "1") == "1":
            server = smtplib.SMTP_SSL(conf["smtp_host"], port, timeout=20)
        else:
            server = smtplib.SMTP(conf["smtp_host"], port, timeout=20)
            server.starttls()
        try:
            server.login(conf["smtp_user"], conf["smtp_pass"] or "")
            server.sendmail(conf["smtp_user"], [conf["mail_to"]], msg.as_string())
        finally:
            server.quit()
        return True, "已发送"
    except Exception as e:  # noqa: BLE001
        return False, f"发送失败: {e}"


def _send_email_for(new_signals: list[dict]):
    """对开启了邮件的规则的新增信号发汇总邮件"""
    hits = [s for s in new_signals if s["email"]]
    if not hits:
        return
    rules = {r["id"]: r for r in db.query("SELECT * FROM rules")}
    stocks = {}
    for s in hits:
        if s["code"] not in stocks:
            r = db.query("SELECT name FROM stocks WHERE code=?", (s["code"],))
            stocks[s["code"]] = r[0]["name"] if r else ""
    lines = [f"[{stocks[s['code']]} {s['code']}] {s['detail']} "
             f"(规则: {rules.get(s['rule_id'], {}).get('name', s['rule_id'])})"
             for s in hits]
    body = ("股票监控信号提醒\n\n" + "\n".join(lines) +
            f"\n\n共 {len(hits)} 条 · {ds_today()}\n(本邮件由本地股票筛选系统发送)")
    ok, msg = send_email(f"【股票监控】{ds_today()} {len(hits)}条信号", body)
    if ok:
        for s in hits:
            db.execute("UPDATE signals SET emailed=1 WHERE rule_id=? AND code=? "
                       "AND trade_date=?",
                       (s["rule_id"], s["code"], s["trade_date"]))
