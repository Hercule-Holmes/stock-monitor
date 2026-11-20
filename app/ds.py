"""数据源适配层 —— 全项目唯一允许出现 akshare 接口名的模块。
akshare 接口如有变动, 只需修改此文件。
"""
import datetime
import time

import akshare as ak
import pandas as pd


def _retry(fn, *args, retries=2, wait=2, **kwargs):
    last = None
    for i in range(retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            last = e
            if i < retries:
                time.sleep(wait)
    raise last


def market_of(code: str) -> str:
    if code.startswith(("6",)):
        return "SH"
    if code.startswith(("0", "3")):
        return "SZ"
    return "BJ"


def spot_snapshot() -> pd.DataFrame:
    """全市场快照: 代码/名称/最新价/涨跌幅/换手率/量比/总市值/流通市值"""
    df = _retry(ak.stock_zh_a_spot_em)
    cols = {}
    for src, dst in [("代码", "code"), ("名称", "name"), ("最新价", "close"),
                     ("涨跌幅", "pct_chg"), ("换手率", "turnover"),
                     ("量比", "volume_ratio"), ("总市值", "mktcap"),
                     ("流通市值", "floatcap")]:
        if src in df.columns:
            cols[src] = dst
    df = df[list(cols)].rename(columns=cols)
    df = df.dropna(subset=["close"]).reset_index(drop=True)
    return df


def daily_hist(code: str) -> pd.DataFrame:
    """前复权日线全历史: date/open/high/low/close/volume/amount/pct_chg"""
    df = _retry(ak.stock_zh_a_hist_em, code, period="daily", adjust="qfq")
    df = df.rename(columns={
        "日期": "date", "开盘": "open", "最高": "high", "最低": "low",
        "收盘": "close", "成交量": "volume", "成交额": "amount",
        "涨跌幅": "pct_chg"})
    keep = ["date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]
    df = df[keep].dropna(subset=["close"]).reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df


_VAL_COLS = {"数据日期": "date", "当日收盘价": "close", "总市值": "mktcap",
             "流通市值": "floatcap", "PE(TTM)": "pe_ttm", "PE(静)": "pe_static",
             "市净率": "pb", "PEG值": "peg", "市现率": "pcr", "市销率": "psr"}


def valuation_hist(code: str) -> pd.DataFrame:
    """估值历史(东财, 一般自2018年起): date/close/mktcap/pe_ttm/pe_static/pb/peg/pcr/psr"""
    df = _retry(ak.stock_value_em, code)
    cols = {k: v for k, v in _VAL_COLS.items() if k in df.columns}
    df = df[list(cols)].rename(columns=cols)
    df = df.dropna(subset=["close"]).reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df


def dividend_detail(code: str) -> pd.DataFrame:
    """分红明细(新浪): 除权除息日 + 每10股派息 → 每股现金"""
    df = _retry(ak.stock_history_dividend_detail, code, indicator="分红")
    need = [c for c in ("除权除息日", "派息", "进度") if c in df.columns]
    if len(need) < 3:
        return pd.DataFrame(columns=["ex_date", "cash"])
    df = df[need]
    df = df[df["进度"] == "实施"].dropna(subset=["除权除息日", "派息"])
    out = pd.DataFrame({
        "ex_date": pd.to_datetime(df["除权除息日"]).dt.strftime("%Y-%m-%d"),
        "cash": pd.to_numeric(df["派息"], errors="coerce") / 10.0,  # 每10股→每股
    }).dropna()
    return out[out["cash"] > 0].reset_index(drop=True)


def stock_name(code: str) -> str:
    df = _retry(ak.stock_individual_info_em, code)
    row = df[df["item"] == "股票简称"]
    return str(row.iloc[0]["value"]) if len(row) else code


def dv_ttm_from(code: str, cur_close: float, cur_date: str | None = None) -> float | None:
    """股息率TTM: 近365天除权除息的每股现金分红 / 现价 * 100"""
    if not cur_close or cur_close <= 0:
        return None
    try:
        dv = dividend_detail(code)
    except Exception:
        return None
    if dv.empty:
        return 0.0
    end = pd.Timestamp(cur_date) if cur_date else pd.Timestamp.today()
    start = end - pd.Timedelta(days=365)
    m = pd.to_datetime(dv["ex_date"])
    total = dv.loc[(m > start) & (m <= end), "cash"].sum()
    return round(float(total) / cur_close * 100, 3)


def today_str() -> str:
    return datetime.date.today().strftime("%Y-%m-%d")
