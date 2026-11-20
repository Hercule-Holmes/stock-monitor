"""指标引擎:
- REGISTRY: 指标注册表(筛选/表格/监控规则三处共用)
- compute_metrics: 对单只股票计算所有指标, 返回 dict
新增个人指标: 在 CUSTOM_INDICATORS 里加一个函数 + 在 REGISTRY 里登记即可。
"""
import numpy as np
import pandas as pd

# ---------------------------------------------------------------
# 指标注册表: key 必须与 stocks 表列名一致
# kind: value=数值(规则用比较运算) flag=0/1(规则用 是/否)
# ---------------------------------------------------------------
REGISTRY = [
    # 行情
    {"key": "code",         "name": "代码",        "group": "行情", "kind": "text"},
    {"key": "name",         "name": "名称",        "group": "行情", "kind": "text"},
    {"key": "close",        "name": "最新价",      "group": "行情", "kind": "value", "fmt": "price"},
    {"key": "pct_chg",      "name": "涨跌幅%",     "group": "行情", "kind": "value", "fmt": "pct"},
    {"key": "turnover",     "name": "换手率%",     "group": "行情", "kind": "value", "fmt": "num2"},
    {"key": "volume_ratio", "name": "量比",        "group": "行情", "kind": "value", "fmt": "num2"},
    {"key": "mktcap",       "name": "总市值(亿)",  "group": "行情", "kind": "value", "fmt": "num0"},
    {"key": "floatcap",     "name": "流通市值(亿)","group": "行情", "kind": "value", "fmt": "num0"},
    # 均线
    {"key": "ma5",    "name": "MA5",    "group": "均线·BOLL", "kind": "value", "fmt": "price"},
    {"key": "ma10",   "name": "MA10",   "group": "均线·BOLL", "kind": "value", "fmt": "price"},
    {"key": "ma20",   "name": "MA20",   "group": "均线·BOLL", "kind": "value", "fmt": "price"},
    {"key": "ma60",   "name": "MA60",   "group": "均线·BOLL", "kind": "value", "fmt": "price"},
    {"key": "ma120",  "name": "MA120",  "group": "均线·BOLL", "kind": "value", "fmt": "price"},
    {"key": "ma250",  "name": "MA250",  "group": "均线·BOLL", "kind": "value", "fmt": "price"},
    {"key": "boll_up",  "name": "BOLL上轨", "group": "均线·BOLL", "kind": "value", "fmt": "price"},
    {"key": "boll_mid", "name": "BOLL中轨", "group": "均线·BOLL", "kind": "value", "fmt": "price"},
    {"key": "boll_low", "name": "BOLL下轨", "group": "均线·BOLL", "kind": "value", "fmt": "price"},
    # 破位标记(flag)
    {"key": "below_ma20",      "name": "跌破MA20",       "group": "破位状态", "kind": "flag"},
    {"key": "below_ma60",      "name": "跌破MA60",       "group": "破位状态", "kind": "flag"},
    {"key": "below_ma120",     "name": "跌破MA120",      "group": "破位状态", "kind": "flag"},
    {"key": "below_ma250",     "name": "跌破MA250(年线)","group": "破位状态", "kind": "flag"},
    {"key": "below_boll_mid",  "name": "低于BOLL中轨",   "group": "破位状态", "kind": "flag"},
    {"key": "below_boll_low",  "name": "低于BOLL下轨",   "group": "破位状态", "kind": "flag"},
    {"key": "above_boll_up",   "name": "高于BOLL上轨",   "group": "破位状态", "kind": "flag"},
    {"key": "target_reached",  "name": "达到预期线",      "group": "破位状态", "kind": "flag",
     "desc": "现价 ≥ 池内预设的预期价(仅在股票池内有效)"},
    # 估值
    {"key": "pe_ttm",    "name": "PE(TTM)",    "group": "估值", "kind": "value", "fmt": "num2"},
    {"key": "pe_static", "name": "PE(静)",     "group": "估值", "kind": "value", "fmt": "num2"},
    {"key": "pb",        "name": "PB市净率",   "group": "估值", "kind": "value", "fmt": "num2"},
    {"key": "peg",       "name": "PEG",        "group": "估值", "kind": "value", "fmt": "num2"},
    {"key": "psr",       "name": "市销率",     "group": "估值", "kind": "value", "fmt": "num2"},
    {"key": "pcr",       "name": "市现率",     "group": "估值", "kind": "value", "fmt": "num2"},
    {"key": "dv_ttm",    "name": "股息率TTM%", "group": "估值", "kind": "value", "fmt": "num2"},
    # 分位(全历史, 0~100, 越低越便宜)
    {"key": "px_pct", "name": "价格历史分位",  "group": "历史分位", "kind": "value", "fmt": "num1"},
    {"key": "pe_pct", "name": "PE历史分位",    "group": "历史分位", "kind": "value", "fmt": "num1"},
    {"key": "pb_pct", "name": "PB历史分位",    "group": "历史分位", "kind": "value", "fmt": "num1"},
    # 个人指标(示例)
    {"key": "ma120_gap",     "name": "120日线偏离度%",  "group": "个人指标", "kind": "value", "fmt": "pct"},
    {"key": "drawdown_250d", "name": "距一年最高回撤%", "group": "个人指标", "kind": "value", "fmt": "pct"},
    {"key": "vol_250d",      "name": "年化波动率%",     "group": "个人指标", "kind": "value", "fmt": "num1"},
]

REG_MAP = {r["key"]: r for r in REGISTRY}
VALUE_KEYS = [r["key"] for r in REGISTRY if r["kind"] == "value"]
FLAG_KEYS = [r["key"] for r in REGISTRY if r["kind"] == "flag"]
INDICATOR_KEYS = VALUE_KEYS + FLAG_KEYS


def _safe_pctile(series: pd.Series, current: float) -> float | None:
    """当前值在全历史中的分位(0~100): 历史上有多少比例的日子低于当前值"""
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty or current is None or (isinstance(current, float) and np.isnan(current)):
        return None
    return round(float((s < current).mean() * 100), 2)


# ---------------------------------------------------------------
# 个人指标插件区 —— 在此扩展
# ---------------------------------------------------------------
def ind_ma120_gap(daily: pd.DataFrame, close: float, ma120: float) -> float | None:
    """收盘价相对120日线的偏离度%"""
    if ma120 is None or ma120 == 0 or (isinstance(ma120, float) and np.isnan(ma120)):
        return None
    return round((close / ma120 - 1) * 100, 2)


def ind_drawdown_250d(daily: pd.DataFrame, close: float) -> float | None:
    """距近250个交易日最高收盘价的回撤%"""
    tail = daily["close"].tail(250)
    if tail.empty:
        return None
    return round((close / float(tail.max()) - 1) * 100, 2)


def ind_vol_250d(daily: pd.DataFrame) -> float | None:
    """近250日涨跌幅的年化波动率%"""
    tail = pd.to_numeric(daily["pct_chg"], errors="coerce").tail(250).dropna()
    if len(tail) < 20:
        return None
    return round(float(tail.std() * np.sqrt(250)), 2)


CUSTOM_INDICATORS = [
    {"key": "ma120_gap",     "fn": lambda d, c, m: ind_ma120_gap(d, c, m)},
    {"key": "drawdown_250d", "fn": lambda d, c, m: ind_drawdown_250d(d, c)},
    {"key": "vol_250d",      "fn": lambda d, c, m: ind_vol_250d(d)},
]


def compute_metrics(daily: pd.DataFrame, val: pd.DataFrame,
                    dv_ttm: float | None) -> dict:
    """输入: 前复权日线全历史 + 估值历史 + 股息率TTM → 全部指标 dict"""
    out: dict = {}
    if daily.empty:
        return out
    last = daily.iloc[-1]
    close = float(last["close"])
    out["daily_date"] = str(last["date"])
    out["close"] = round(close, 3)
    out["pct_chg"] = round(float(last["pct_chg"]), 2) if pd.notna(last["pct_chg"]) else None

    # 均线 + BOLL
    c = daily["close"].astype(float)
    for n in (5, 10, 20, 60, 120, 250):
        out[f"ma{n}"] = round(float(c.rolling(n).mean().iloc[-1]), 3) if len(c) >= n else None
    if len(c) >= 20:
        mid = c.rolling(20).mean().iloc[-1]
        std = c.rolling(20).std(ddof=0).iloc[-1]
        out["boll_mid"] = round(float(mid), 3)
        out["boll_up"] = round(float(mid + 2 * std), 3)
        out["boll_low"] = round(float(mid - 2 * std), 3)
    else:
        out["boll_mid"] = out["boll_up"] = out["boll_low"] = None

    # 破位状态
    def flag(cond: bool) -> int:
        return 1 if cond else 0

    out["below_ma20"] = flag(out.get("ma20") and close < out["ma20"])
    out["below_ma60"] = flag(out.get("ma60") and close < out["ma60"])
    out["below_ma120"] = flag(out.get("ma120") and close < out["ma120"])
    out["below_ma250"] = flag(out.get("ma250") and close < out["ma250"])
    out["below_boll_mid"] = flag(out.get("boll_mid") and close < out["boll_mid"])
    out["below_boll_low"] = flag(out.get("boll_low") and close < out["boll_low"])
    out["above_boll_up"] = flag(out.get("boll_up") and close > out["boll_up"])

    # 估值最新
    if not val.empty:
        v = val.iloc[-1]
        out["val_date"] = str(v["date"])
        for k in ("pe_ttm", "pe_static", "pb", "peg", "pcr", "psr"):
            x = pd.to_numeric(pd.Series([v.get(k)]), errors="coerce").iloc[0]
            out[k] = round(float(x), 3) if pd.notna(x) else None
        mc = pd.to_numeric(pd.Series([v.get("mktcap")]), errors="coerce").iloc[0]
        out["mktcap"] = round(float(mc) / 1e8, 2) if pd.notna(mc) else None
        out["pe_pct"] = _safe_pctile(val["pe_ttm"], out.get("pe_ttm"))
        out["pb_pct"] = _safe_pctile(val["pb"], out.get("pb"))
    else:
        out["val_date"] = None
        for k in ("pe_ttm", "pe_static", "pb", "peg", "pcr", "psr", "mktcap"):
            out[k] = None
        out["pe_pct"] = out["pb_pct"] = None

    # 价格分位(全历史)
    out["px_pct"] = _safe_pctile(daily["close"], close)

    out["dv_ttm"] = dv_ttm

    # 个人指标
    for item in CUSTOM_INDICATORS:
        try:
            out[item["key"]] = item["fn"](daily, close, out.get("ma120"))
        except Exception:
            out[item["key"]] = None
    return out


def kline_payload(daily: pd.DataFrame, days: int = 400) -> dict:
    """K线图数据: 最近N根K + 均线/BOLL 序列(全历史计算后截尾)"""
    if daily.empty:
        return {"dates": [], "k": [], "ma": {}, "boll": {}, "volume": []}
    c = daily["close"].astype(float)
    ma = {f"ma{n}": c.rolling(n).mean().round(3) for n in (5, 10, 20, 60, 120, 250) if len(c) >= n}
    mid = c.rolling(20).mean()
    std = c.rolling(20).std(ddof=0)
    daily = daily.assign(_mid=mid, _up=mid + 2 * std, _low=mid - 2 * std)
    tail = daily.tail(days)
    idx = tail.index
    return {
        "dates": tail["date"].tolist(),
        "k": [[round(float(r.open), 2), round(float(r.close), 2),
               round(float(r.low), 2), round(float(r.high), 2)] for r in tail.itertuples()],
        "ma": {k: [None if pd.isna(v) else round(float(v), 2) for v in ma[k].loc[idx]]
               for k in ma},
        "boll": {"up":  [None if pd.isna(v) else round(float(v), 2) for v in tail["_up"].values],
                 "mid": [None if pd.isna(v) else round(float(v), 2) for v in tail["_mid"].values],
                 "low": [None if pd.isna(v) else round(float(v), 2) for v in tail["_low"].values]},
        "volume": [float(v) for v in tail["volume"].values],
    }


def valuation_payload(val: pd.DataFrame) -> dict:
    """估值走势 + 全历史分位参考线"""
    if val.empty:
        return {"dates": [], "pe_ttm": [], "pb": [], "close": [],
                "pe_lines": {}, "pb_lines": {}, "px_lines": {}}
    dates = val["date"].tolist()
    pe = pd.to_numeric(val["pe_ttm"], errors="coerce")
    pb = pd.to_numeric(val["pb"], errors="coerce")
    close = pd.to_numeric(val["close"], errors="coerce")

    def lines(s: pd.Series) -> dict:
        s = s.dropna()
        return {f"p{p}": round(float(s.quantile(p / 100)), 2)
                for p in (10, 25, 50, 75, 90)} if not s.empty else {}

    def series(s: pd.Series) -> list:
        return [None if pd.isna(v) else round(float(v), 2) for v in s]

    return {"dates": dates, "pe_ttm": series(pe), "pb": series(pb), "close": series(close),
            "pe_lines": lines(pe), "pb_lines": lines(pb)}
