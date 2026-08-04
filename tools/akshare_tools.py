import re
import time
from datetime import date, datetime, timedelta
from functools import lru_cache
import akshare as ak
import pandas as pd
import requests
import yfinance as yf
from langchain_core.tools import tool
import os
import tushare as ts
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env", override=True)
from tools.stock_name_dict import get_stock_name


def _get_ts_pro():
    token = os.getenv("TUSHARE_TOKEN", "")
    if not token:
        return None
    ts.set_token(token)
    return ts.pro_api()


# ============================================================
# 统一说明
# 1. 成功返回必须带 [TOOL_OK]
# 2. 失败返回必须带 [TOOL_ERROR]
# 3. 失败时明确说明：数据不足，禁止基于假设继续分析
# ============================================================

# 设置全局 User-Agent，尽量减少数据源拦截问题
session = requests.Session()
session.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
)
ak.requests_session = session


def _error(tool_name: str, symbol: str, reason: str) -> str:
    return (
        f"[TOOL_ERROR]\n"
        f"tool={tool_name}\n"
        f"symbol={symbol}\n"
        f"reason={reason}\n"
        f"结论：数据不足，禁止基于假设继续分析。"
    )


def _ok(tool_name: str, symbol: str, body: str) -> str:
    retrieved_at = datetime.now().astimezone().isoformat(timespec="seconds")
    return f"[TOOL_OK]\ntool={tool_name}\nsymbol={symbol}\nretrieved_at={retrieved_at}\n{body}"


def _validate_symbol(symbol: str) -> str | None:
    symbol = (symbol or "").strip()
    if not re.fullmatch(r"\d{6}", symbol):
        return "股票代码格式错误，应为6位数字"
    if symbol[0] not in {"0", "3", "6"}:
        return "当前仅支持A股常见6位代码（0/3/6开头）"
    return None


def _to_yf_symbol(symbol: str) -> str:
    return f"{symbol}.SS" if symbol.startswith("6") else f"{symbol}.SZ"


def _normalize_symbol(symbol: str) -> str:
    return (symbol or "").strip()


# The THS annual abstract is a tabular data source.  Keep the source-to-domain
# mapping explicit so a layout/order change cannot silently turn an old row
# into the latest financial evidence.
_FINANCIAL_FIELD_MAP = {
    "report_period": ("报告期", "报告期末", "截止日期"),
    "revenue": ("营业总收入", "营业收入"),
    "net_profit": ("净利润", "归母净利润"),
    "roe": ("净资产收益率", "ROE"),
    "gross_margin": ("销售毛利率", "毛利率"),
}


def _parse_financial_period(value: object) -> date | None:
    """Normalise an annual/quarterly source period into a period-end date."""

    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "false"}:
        return None
    if re.fullmatch(r"\d{4}", text):
        return date(int(text), 12, 31)
    compact = re.sub(r"[^0-9]", "", text)
    if len(compact) == 8:
        try:
            return date(int(compact[:4]), int(compact[4:6]), int(compact[6:8]))
        except ValueError:
            return None
    match = re.search(r"(20\d{2})[^0-9]+(\d{1,2})[^0-9]+(\d{1,2})", text)
    if not match:
        return None
    try:
        return date(*map(int, match.groups()))
    except ValueError:
        return None


def _source_value(record: pd.Series, field_name: str) -> tuple[object, str | None]:
    for column in _FINANCIAL_FIELD_MAP[field_name]:
        if column in record.index:
            return record[column], column
    return "N/A", None


def _latest_financial_record(df: pd.DataFrame) -> tuple[pd.Series, str, str, str]:
    """Return the newest record using the explicit report-period source field."""

    period_column = next((name for name in _FINANCIAL_FIELD_MAP["report_period"] if name in df.columns), None)
    if not period_column:
        raise ValueError(f"financial source has no report-period field; columns={list(df.columns)}")

    candidates: list[tuple[date, pd.Series, str]] = []
    for _, record in df.iterrows():
        raw_period = record[period_column]
        period_end = _parse_financial_period(raw_period)
        if period_end:
            candidates.append((period_end, record, str(raw_period).strip()))
    if not candidates:
        raise ValueError(f"financial source has no parseable values in {period_column}")

    latest_period, latest_record, raw_period = max(candidates, key=lambda item: item[0])
    return latest_record, period_column, raw_period, latest_period.isoformat()


# 常用股票名称本地缓存，避免频繁调用Tushare stock_basic接口
_STOCK_NAME_CACHE: dict[str, str] = {}


@lru_cache(maxsize=256)
def _get_stock_name_from_tushare(symbol: str) -> str | None:
    """通过Tushare namechange接口获取股票中文名称，无频率限制"""
    try:
        token = os.getenv("TUSHARE_TOKEN", "")
        if not token:
            return None
        ts.set_token(token)
        pro = ts.pro_api()
        ts_code = f"{symbol}.SH" if symbol.startswith("6") else f"{symbol}.SZ"
        df = pro.namechange(ts_code=ts_code, fields="ts_code,name")
        if df is not None and not df.empty:
            return str(df.iloc[0]["name"])
        return None
    except Exception as e:
        print(f"[TushareNameError] {symbol}: {e}")
        return None


def _get_realtime_price_from_tushare(symbol: str) -> dict | None:
    """
    通过Tushare获取实时/最新行情，120积分可用
    返回dict: {name, price, change_pct, volume}
    """
    try:
        pro = _get_ts_pro()
        if pro is None:
            return None
        ts_code = f"{symbol}.SH" if symbol.startswith("6") else f"{symbol}.SZ"
        today = datetime.now().strftime("%Y%m%d")
        df = pro.daily(ts_code=ts_code, start_date="20250101", end_date=today)
        if df is None or df.empty:
            return None
        latest = df.iloc[0]  # 最新一行
        name = _get_stock_name_from_tushare(symbol)
        return {
            "name": name or get_stock_name(symbol),
            "price": float(latest["close"]),
            "change_pct": float(latest["pct_chg"]),
            "volume": float(latest["vol"]),
            "market_cap": None,  # tushare daily不含市值，留None
        }
    except Exception:
        return None


def _get_hist_from_tushare(symbol: str, days: int) -> pd.DataFrame:
    """通过Tushare获取历史K线，返回统一列名DataFrame"""
    try:
        pro = _get_ts_pro()
        if pro is None:
            return pd.DataFrame()
        ts_code = f"{symbol}.SH" if symbol.startswith("6") else f"{symbol}.SZ"
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=max(days * 2, 60))).strftime(
            "%Y%m%d"
        )
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.rename(
            columns={
                "trade_date": "日期",
                "open": "开盘",
                "close": "收盘",
                "high": "最高",
                "low": "最低",
                "vol": "成交量",
                "pct_chg": "涨跌幅",
            }
        )
        df = df[["日期", "开盘", "收盘", "最高", "最低", "成交量", "涨跌幅"]].copy()
        df = df.sort_values("日期").tail(days).reset_index(drop=True)
        return df
    except Exception:
        return pd.DataFrame()


def _get_hist_from_akshare(symbol: str, days: int) -> pd.DataFrame:
    """
    用 AKShare 历史行情接口获取最近 N 天日线。
    返回统一列名的数据框；失败返回空 DataFrame。
    """
    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=max(days * 3, 90))

        df = ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            adjust="qfq",
        )

        if df is None or df.empty:
            return pd.DataFrame()

        rename_map = {
            "日期": "日期",
            "开盘": "开盘",
            "收盘": "收盘",
            "最高": "最高",
            "最低": "最低",
            "成交量": "成交量",
            "涨跌幅": "涨跌幅",
        }
        available = [c for c in rename_map if c in df.columns]
        if not available:
            return pd.DataFrame()

        df = df[available].copy()
        df["日期"] = df["日期"].astype(str)

        for col in ["开盘", "收盘", "最高", "最低", "成交量"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.tail(days).reset_index(drop=True)
        return df

    except Exception:
        return pd.DataFrame()


def _safe_float(value):
    try:
        if pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


@lru_cache(maxsize=128)
def _get_stock_history_cached(symbol: str, days: int, date_key: str) -> pd.DataFrame:
    """
    使用 yfinance 获取历史数据。
    date_key 用于按小时刷新缓存，避免长期脏缓存。
    """
    try:
        yf_symbol = _to_yf_symbol(symbol)
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(period=f"{days}d")

        if df.empty:
            return pd.DataFrame()

        required_cols = {"Open", "Close", "High", "Low", "Volume"}
        if not required_cols.issubset(set(df.columns)):
            return pd.DataFrame()

        df = df.rename(
            columns={
                "Open": "开盘",
                "Close": "收盘",
                "High": "最高",
                "Low": "最低",
                "Volume": "成交量",
            }
        )

        df.index = df.index.strftime("%Y-%m-%d")
        df.index.name = "日期"
        df = df.reset_index()

        return df
    except Exception:
        return pd.DataFrame()


@tool
def get_stock_price(symbol: str) -> str:
    """
    获取A股股票最近行情数据。
    优先 Tushare；失败再回退到 yfinance。
    """
    symbol = _normalize_symbol(symbol)

    err = _validate_symbol(symbol)
    if err:
        return _error("get_stock_price", symbol, err)

    # ---------- 方案A：优先 Tushare ----------
    try:
        data = _get_realtime_price_from_tushare(symbol)
        if data is not None:
            body = (
                f"股票名称：{data['name']}\n"
                f"最新价：{data['price']:.2f}\n"
                f"涨跌幅：{data['change_pct']:.2f}%\n"
                f"成交量：{data['volume']}\n"
                f"数据来源：Tushare日线行情\n"
            )
            return _ok("get_stock_price", symbol, body)
    except Exception:
        pass

    # ---------- 方案B：回退 yfinance ----------
    try:
        yf_symbol = _to_yf_symbol(symbol)
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(period="5d")

        if df.empty:
            return _error(
                "get_stock_price", symbol, "Tushare 与 yfinance 均未获取到行情数据"
            )

        if len(df) < 2:
            return _error(
                "get_stock_price", symbol, "可用交易日不足2天，无法计算涨跌幅"
            )

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        latest_close = _safe_float(latest.get("Close"))
        prev_close = _safe_float(prev.get("Close"))
        latest_volume = _safe_float(latest.get("Volume"))

        if latest_close is None:
            return _error("get_stock_price", symbol, "最新收盘价为空或NaN")
        if prev_close is None or prev_close == 0:
            return _error("get_stock_price", symbol, "前一日收盘价无效，无法计算涨跌幅")
        if latest_volume is None:
            return _error("get_stock_price", symbol, "最新成交量为空或NaN")

        change_pct = (latest_close - prev_close) / prev_close * 100

        info = {}
        try:
            info = ticker.info or {}
        except Exception:
            info = {}

        stock_name = get_stock_name(symbol)
        industry = info.get("industry", "N/A")
        market_cap = info.get("marketCap", "N/A")

        body = (
            f"股票名称：{stock_name}\n"
            f"最新价：{latest_close:.2f}\n"
            f"涨跌幅：{change_pct:.2f}%\n"
            f"成交量：{latest_volume:,.0f}\n"
            f"总市值：{market_cap}\n"
            f"行业：{industry}\n"
            f"数据来源：yfinance 回退链路"
        )
        return _ok("get_stock_price", symbol, body)

    except Exception as e:
        return _error("get_stock_price", symbol, f"{type(e).__name__}: {str(e)}")


@tool
def get_financial_indicator(symbol: str) -> str:
    """获取A股财务指标数据，包括PE、PB、ROE等。"""
    symbol = (symbol or "").strip()
    err = _validate_symbol(symbol)
    if err:
        return _error("get_financial_indicator", symbol, err)

    try:
        stock_name = get_stock_name(symbol)

        df = ak.stock_financial_abstract_ths(symbol=symbol, indicator="按年度")

        if df is None or df.empty:
            return _error("get_financial_indicator", symbol, "财务数据为空")

        latest, report_period_field, report_period_raw, report_period = _latest_financial_record(df)
        revenue, _ = _source_value(latest, "revenue")
        net_profit, _ = _source_value(latest, "net_profit")
        roe, _ = _source_value(latest, "roe")
        gross_margin, _ = _source_value(latest, "gross_margin")

        body = (
            f"report_period={report_period}\n"
            f"report_period_raw={report_period_raw}\n"
            f"report_period_source_field={report_period_field}\n"
            "report_type=annual\n"
            "data_source=AKShare/THS financial abstract\n"
            f"股票名称：{stock_name}\n"
            f"报告期：{report_period}\n"
            f"营业总收入：{revenue}\n"
            f"净利润：{net_profit}\n"
            f"ROE：{roe}\n"
            f"毛利率：{gross_margin}\n"
            f"数据来源：AKShare同花顺财务摘要"
        )
        return _ok("get_financial_indicator", symbol, body)

    except Exception as e:
        return _error(
            "get_financial_indicator", symbol, f"{type(e).__name__}: {str(e)}"
        )


@tool
def get_stock_history(symbol: str, days: int = 30) -> str:
    """
    获取A股股票最近N天历史K线数据。
    优先 Tushare；失败回退 AKShare；再失败回退 yfinance。
    """
    symbol = _normalize_symbol(symbol)

    err = _validate_symbol(symbol)
    if err:
        return _error("get_stock_history", symbol, err)

    if days <= 1 or days > 365:
        return _error(
            "get_stock_history", symbol, "days 参数不合理，应在 2 到 365 之间"
        )

    # ---------- 方案A：优先 Tushare ----------
    try:
        df = _get_hist_from_tushare(symbol, days)
        if not df.empty:
            latest_close = _safe_float(df["收盘"].iloc[-1])
            highest = _safe_float(df["最高"].max())
            lowest = _safe_float(df["最低"].min())
            if latest_close and highest and lowest:
                df_view = df[
                    ["日期", "开盘", "收盘", "最高", "最低", "成交量", "涨跌幅"]
                ].tail(10)
                body = (
                    f"最近{len(df)}天K线数据\n"
                    f"期间最高价：{highest:.2f}\n"
                    f"期间最低价：{lowest:.2f}\n"
                    f"最新收盘价：{latest_close:.2f}\n"
                    f"数据来源：Tushare日线行情\n\n"
                    f"最近10日明细：\n{df_view.to_string(index=False)}"
                )
                return _ok("get_stock_history", symbol, body)
    except Exception:
        pass

    # ---------- 方案B：回退 AKShare ----------
    try:
        df = _get_hist_from_akshare(symbol, days)

        if not df.empty:
            required_cols = {"日期", "开盘", "收盘", "最高", "最低", "成交量"}
            if required_cols.issubset(set(df.columns)):
                if df["收盘"].isna().all():
                    return _error(
                        "get_stock_history", symbol, "AKShare 历史收盘价全部为空或NaN"
                    )

                if "涨跌幅" not in df.columns:
                    df["涨跌幅"] = df["收盘"].pct_change(fill_method=None) * 100

                latest_close = _safe_float(df["收盘"].iloc[-1])
                highest = _safe_float(df["最高"].max())
                lowest = _safe_float(df["最低"].min())

                if (
                    latest_close is not None
                    and highest is not None
                    and lowest is not None
                ):
                    df_view = df[
                        ["日期", "开盘", "收盘", "最高", "最低", "成交量", "涨跌幅"]
                    ].tail(10)
                    body = (
                        f"最近{len(df)}天K线数据\n"
                        f"期间最高价：{highest:.2f}\n"
                        f"期间最低价：{lowest:.2f}\n"
                        f"最新收盘价：{latest_close:.2f}\n"
                        f"数据来源：AKShare/东方财富历史行情\n\n"
                        f"最近10日明细：\n{df_view.to_string(index=False)}"
                    )
                    return _ok("get_stock_history", symbol, body)
    except Exception:
        pass

    # ---------- 方案C：回退 yfinance ----------
    try:
        date_key = datetime.now().strftime("%Y%m%d%H")
        df = _get_stock_history_cached(symbol, days, date_key)

        if df.empty:
            return _error(
                "get_stock_history",
                symbol,
                "Tushare / AKShare / yfinance 均未获取到历史K线数据",
            )

        required_cols = {"日期", "开盘", "收盘", "最高", "最低", "成交量"}
        if not required_cols.issubset(set(df.columns)):
            return _error("get_stock_history", symbol, "历史数据字段不完整")

        if df["收盘"].isna().all():
            return _error("get_stock_history", symbol, "历史收盘价全部为空或NaN")

        df["涨跌幅"] = df["收盘"].pct_change(fill_method=None) * 100

        latest_close = _safe_float(df["收盘"].iloc[-1])
        highest = _safe_float(df["最高"].max())
        lowest = _safe_float(df["最低"].min())

        if latest_close is None or highest is None or lowest is None:
            return _error(
                "get_stock_history", symbol, "关键价格字段存在空值，无法生成技术摘要"
            )

        cols = ["日期", "开盘", "收盘", "最高", "最低", "成交量", "涨跌幅"]
        df_view = df[cols].tail(10)

        body = (
            f"最近{len(df)}天K线数据\n"
            f"期间最高价：{highest:.2f}\n"
            f"期间最低价：{lowest:.2f}\n"
            f"最新收盘价：{latest_close:.2f}\n"
            f"数据来源：yfinance 回退链路\n\n"
            f"最近10日明细：\n{df_view.to_string(index=False)}"
        )
        return _ok("get_stock_history", symbol, body)

    except Exception as e:
        return _error("get_stock_history", symbol, f"{type(e).__name__}: {str(e)}")


@tool
def get_stock_news(symbol: str) -> str:
    """
    获取A股股票相关新闻。
    symbol: 股票代码，如 '002218'
    """
    symbol = (symbol or "").strip()

    err = _validate_symbol(symbol)
    if err:
        return _error("get_stock_news", symbol, err)

    try:
        time.sleep(1)
        df = ak.stock_news_em(symbol=symbol)

        if df.empty:
            return _error("get_stock_news", symbol, "未找到相关新闻")

        required_cols = {"发布时间", "新闻标题"}
        if not required_cols.issubset(set(df.columns)):
            return _error("get_stock_news", symbol, "新闻数据字段不完整")

        news_list = []
        for _, row in df.head(5).iterrows():
            news_time = row.get("发布时间", "")
            news_title = row.get("新闻标题", "")
            if not news_title:
                continue
            news_list.append(f"【{news_time}】{news_title}")

        if not news_list:
            return _error("get_stock_news", symbol, "新闻列表为空")

        body = "最新资讯：\n" + "\n".join(news_list)
        return _ok("get_stock_news", symbol, body)

    except Exception as e:
        return _error("get_stock_news", symbol, f"{type(e).__name__}: {str(e)}")


ALL_TOOLS = [
    get_stock_price,
    get_stock_history,
    get_financial_indicator,
    get_stock_news,
]
