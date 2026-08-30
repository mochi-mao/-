#!/usr/bin/env python3
"""
jp-stock-screener/screener.py

日経225（取得できない場合はフォールバックの主要銘柄リスト）を対象に、
テクニカル指標とファンダメンタル指標を組み合わせた「買い候補スコア」を算出し、
results/latest.json に出力するスクリプト。

GitHub Actions の cron から毎朝実行される想定。
このリポジトリの外部（Claudeのスケジュールタスク等）から
results/latest.json を読み取って通知メッセージを組み立てる。

無料データソース（yfinance = Yahoo Finance 非公式ライブラリ）のみを使用。
"""

import concurrent.futures
import csv
import json
import os
import re
import sys
import time
import warnings
from datetime import datetime, timezone

import pandas as pd
import requests
import yfinance as yf

warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
FALLBACK_CSV = os.path.join(HERE, "tickers_fallback.csv")
OUTPUT_JSON = os.path.join(HERE, "results", "latest.json")

TOP_N = int(os.environ.get("TOP_N", "8"))
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "8"))


def load_fallback_universe():
    """リポジトリ同梱のフォールバック銘柄リストを読み込む。"""
    rows = []
    with open(FALLBACK_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append({"code": row["code"].strip(), "name": row["name"].strip()})
    return rows


def load_universe_from_wikipedia():
    """
    英語版Wikipedia「Nikkei 225」のページから構成銘柄の証券コードを取得する。

    このページは表形式ではなく「Company Name (TYO: CODE)」という
    文章内リスト形式で構成銘柄が列挙されているため、HTMLタグを除去した
    テキストに対して正規表現でコードを抽出する。
    銘柄名はここでは仮値とし、後段でyfinanceから取得した正式名称で上書きする。

    取得できない・件数が少なすぎる場合は None を返す（呼び出し元でフォールバック）。
    """
    try:
        resp = requests.get(
            "https://en.wikipedia.org/wiki/Nikkei_225",
            headers={"User-Agent": "Mozilla/5.0 (compatible; jp-stock-screener/1.0)"},
            timeout=20,
        )
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        print(f"[warn] Wikipedia fetch failed: {e}", file=sys.stderr)
        return None

    # スクリプト/スタイルとタグを除去してプレーンテキスト化
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)

    codes = re.findall(r"TYO\s*:\s*([0-9][0-9A-Za-z]{2,4})", text)
    seen = set()
    uniq_codes = []
    for c in codes:
        c = c.strip()
        if c and c not in seen:
            seen.add(c)
            uniq_codes.append(c)

    if len(uniq_codes) >= 150:  # 日経225相当の件数が取れていれば採用
        return [{"code": c, "name": c} for c in uniq_codes]

    print(f"[warn] Wikipedia parse only found {len(uniq_codes)} codes, using fallback",
          file=sys.stderr)
    return None


def get_universe():
    universe = load_universe_from_wikipedia()
    source = "wikipedia"
    if not universe:
        universe = load_fallback_universe()
        source = "fallback"
    print(f"[info] universe source={source} size={len(universe)}")
    return universe, source


def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    return 100 - (100 / (1 + rs))


def macd(series, fast=12, slow=26, signal=9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line


def analyze_ticker(code, name):
    ticker = f"{code}.T"
    try:
        hist = yf.Ticker(ticker).history(period="9mo", interval="1d", auto_adjust=True)
        if hist is None or len(hist) < 60:
            return None

        close = hist["Close"]
        volume = hist["Volume"]

        sma25 = close.rolling(25).mean()
        sma75 = close.rolling(75).mean()
        rsi14 = rsi(close, 14)
        macd_line, signal_line = macd(close)
        vol_avg20 = volume.rolling(20).mean()

        last = -1
        prev = -2

        # テクニカル分析の理由・スコアは fundamental とは別に保持する
        tech_reasons = []
        tech_score = 0

        # --- ゴールデンクロス ---
        if sma75.iloc[prev] is not None and not pd.isna(sma75.iloc[-4]):
            cross_now = sma25.iloc[last] > sma75.iloc[last]
            cross_before = sma25.iloc[-4] <= sma75.iloc[-4]
            if cross_now and cross_before:
                tech_score += 25
                tech_reasons.append("移動平均ゴールデンクロス")
            elif cross_now:
                tech_score += 10
                tech_reasons.append("移動平均は上昇トレンド")

        # --- RSI 反発 ---
        if not pd.isna(rsi14.iloc[last]):
            r_now = rsi14.iloc[last]
            r_prev = rsi14.iloc[prev]
            if r_now < 35:
                tech_score += 20
                if r_now > r_prev:
                    tech_score += 10
                    tech_reasons.append(f"RSI{r_now:.0f}→反発")
                else:
                    tech_reasons.append(f"RSI{r_now:.0f}（売られすぎ）")

        # --- MACD ゴールデンクロス ---
        if not pd.isna(macd_line.iloc[-4]):
            cross_now = macd_line.iloc[last] > signal_line.iloc[last]
            cross_before = macd_line.iloc[-4] <= signal_line.iloc[-4]
            if cross_now and cross_before:
                tech_score += 20
                tech_reasons.append("MACDゴールデンクロス")

        # --- 出来高急増 ---
        if not pd.isna(vol_avg20.iloc[last]) and vol_avg20.iloc[last] > 0:
            ratio = volume.iloc[last] / vol_avg20.iloc[last]
            if ratio >= 2:
                tech_score += 15
                tech_reasons.append(f"出来高急増({ratio:.1f}倍)")
            elif ratio >= 1.5:
                tech_score += 8
                tech_reasons.append(f"出来高増加({ratio:.1f}倍)")

        # --- 52週安値からの反発 ---
        low_52w = close.min()
        recent_min = close.iloc[-5:].min()
        cur = close.iloc[last]
        if low_52w > 0 and cur <= low_52w * 1.08 and cur >= recent_min * 1.03:
            tech_score += 15
            tech_reasons.append("52週安値圏から反発")

        tech_score = min(100, tech_score)

        result = {
            "code": code,
            "name": name,
            "close": round(float(cur), 1),
            # テクニカル分析（移動平均・RSI・MACD・出来高・52週安値）
            "tech_score": tech_score,
            "tech_reasons": tech_reasons,
            # ファンダメンタル分析（PER・PBR・配当利回り）。値は下で埋める
            "fundamental_score": 0,
            "fundamental_reasons": [],
            "per": None,
            "pbr": None,
            "dividend_yield_pct": None,
        }

        # --- ファンダメンタル（取得できる範囲で。失敗しても続行） ---
        try:
            info = yf.Ticker(ticker).get_info()
            # Wikipediaから取得した場合、name はコードそのままの仮値なので
            # yfinanceの正式名称（取得できれば）で上書きする
            official_name = info.get("longName") or info.get("shortName")
            if official_name:
                result["name"] = official_name
            per = info.get("trailingPE")
            pbr = info.get("priceToBook")
            div_yield = info.get("dividendYield")
            result["per"] = round(per, 1) if isinstance(per, (int, float)) else None
            result["pbr"] = round(pbr, 2) if isinstance(pbr, (int, float)) else None
            result["dividend_yield_pct"] = (
                round(div_yield * 100, 2)
                if isinstance(div_yield, (int, float)) and div_yield < 1
                else (round(div_yield, 2) if isinstance(div_yield, (int, float)) else None)
            )
        except Exception:
            pass  # PER/PBR/配当利回りは取得できなくても続行（Noneのまま）

        return result
    except Exception as e:
        print(f"[warn] {ticker} failed: {e}", file=sys.stderr)
        return None


def add_fundamental_score(results):
    """PER/PBR/配当利回りを使い、割安・高配当ならスコアに加点する（相対比較）。"""
    pers = [r["per"] for r in results if r.get("per") and r["per"] > 0]
    pbrs = [r["pbr"] for r in results if r.get("pbr") and r["pbr"] > 0]
    divs = [r["dividend_yield_pct"] for r in results if r.get("dividend_yield_pct")]

    def percentile_rank(value, pool):
        if not pool or value is None:
            return None
        pool_sorted = sorted(pool)
        idx = sum(1 for v in pool_sorted if v <= value)
        return idx / len(pool_sorted)

    RAW_FUNDAMENTAL_MAX = 30  # PER+PBR+配当利回りの各10点満点の合計

    for r in results:
        fscore_raw = 0
        fund_reasons = []
        per_pct = percentile_rank(r.get("per"), pers)
        pbr_pct = percentile_rank(r.get("pbr"), pbrs)
        div_pct = percentile_rank(r.get("dividend_yield_pct"), divs)

        if per_pct is not None and per_pct <= 0.3:
            fscore_raw += 10
            fund_reasons.append(f"PER{r['per']}倍（同業界内で割安水準）")
        if pbr_pct is not None and pbr_pct <= 0.3:
            fscore_raw += 10
            fund_reasons.append(f"PBR{r['pbr']}倍（同業界内で割安水準）")
        if div_pct is not None and div_pct >= 0.7:
            fscore_raw += 10
            fund_reasons.append(f"配当利回り{r['dividend_yield_pct']}%（相対的に高水準）")

        if not fund_reasons:
            fund_reasons.append("突出した割安・高配当シグナルなし")

        # テクニカル・ファンダを同じ0〜100スケールに揃えて総合評価を出す
        fscore_100 = round(fscore_raw / RAW_FUNDAMENTAL_MAX * 100)

        r["fundamental_score"] = fscore_100
        r["fundamental_reasons"] = fund_reasons
        r["score"] = round((r["tech_score"] + fscore_100) / 2)

    return results


def main():
    universe, source = get_universe()

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {
            ex.submit(analyze_ticker, item["code"], item["name"]): item
            for item in universe
        }
        for fut in concurrent.futures.as_completed(futures):
            r = fut.result()
            if r:
                results.append(r)

    results = add_fundamental_score(results)
    results.sort(key=lambda r: r["score"], reverse=True)
    top = results[:TOP_N]

    output = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "universe_source": source,
        "universe_size": len(universe),
        "analyzed_count": len(results),
        "top_candidates": top,
    }

    os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[info] wrote {OUTPUT_JSON} with {len(top)} candidates "
          f"(analyzed {len(results)}/{len(universe)})")


if __name__ == "__main__":
    main()
