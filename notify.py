#!/usr/bin/env python3
"""
jp-stock-screener/notify.py
results/latest.json（screener.pyが生成）を読み込み、
米国主要指数（yfinanceで取得）とあわせてSlackメッセージを組み立て、
Slack Incoming Webhook経由で送信する。
Claudeのスケジュールタスクを介さず、GitHub Actions単体で完結させることで
配信の確実性を優先した構成。そのため米国市況部分はAIによるニュース分析ではなく、
指数の数値（終値・騰落率）のみの機械的な要約になる。
必要な環境変数:
  SLACK_WEBHOOK_URL - SlackのIncoming Webhook URL（GitHub Secretsで設定）
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta
import requests
import yfinance as yf
HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS_JSON = os.path.join(HERE, "results", "latest.json")
US_INDICES = [
    ("S&P500", "^GSPC"),
    ("NASDAQ総合", "^IXIC"),
    ("NYダウ", "^DJI"),
    ("半導体指数(SOX)", "^SOX"),
]
VIX_TICKER = "^VIX"
TOP_N = int(os.environ.get("NOTIFY_TOP_N", "5"))
def fetch_index_line(label, ticker):
    try:
        hist = yf.Ticker(ticker).history(period="10d", interval="1d")
        if hist is None or len(hist) < 2:
            return f"・{label}: データ取得不可"
        close = hist["Close"]
        last = close.iloc[-1]
        prev = close.iloc[-2]
        pct = (last - prev) / prev * 100
        arrow = "+" if pct >= 0 else ""
        last_date = close.index[-1].strftime("%m/%d")
        return f"・{label} {last:,.1f}（{arrow}{pct:.2f}% / {last_date}時点）"
    except Exception as e:
        print(f"[warn] index fetch failed for {ticker}: {e}", file=sys.stderr)
        return f"・{label}: データ取得エラー"
def fetch_vix_line():
    try:
        hist = yf.Ticker(VIX_TICKER).history(period="5d", interval="1d")
        if hist is None or len(hist) == 0:
            return None
        last = hist["Close"].iloc[-1]
        return f"VIX {last:.1f}"
    except Exception:
        return None
def verdict_comment(tech_score, fundamental_score):
    if tech_score >= 60 and fundamental_score >= 60:
        return "テクニカル・ファンダ両面で良好です。"
    if tech_score >= 60 and fundamental_score < 40:
        return "テクニカル的な勢いは強い一方、ファンダメンタルは平均的です。"
    if fundamental_score >= 60 and tech_score < 40:
        return "割安感や配当などファンダは魅力的ですが、テクニカルはまだ弱めです。"
    if tech_score < 40 and fundamental_score < 40:
        return "テクニカル・ファンダともに突出したシグナルは出ていません。"
    return "テクニカル・ファンダともに標準的な水準です。"
def build_message():
    lines = []
    now_jst = datetime.now(timezone.utc) + timedelta(hours=9)
    lines.append(f"📊 本日の株式まとめ（{now_jst.strftime('%Y/%m/%d')} 配信）")
    lines.append("")
    lines.append("🇺🇸 米国市況（直近営業日終値）")
    for label, ticker in US_INDICES:
        lines.append(fetch_index_line(label, ticker))
    vix_line = fetch_vix_line()
    if vix_line:
        lines.append(f"・{vix_line}")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━")
    lines.append("")
    if not os.path.exists(RESULTS_JSON):
        lines.append("⚠️ 日本株スクリーニング結果（results/latest.json）が見つかりませんでした。")
        return "\n".join(lines)
    with open(RESULTS_JSON, encoding="utf-8") as f:
        data = json.load(f)
    generated_at = data.get("generated_at_utc", "不明")
    candidates = data.get("top_candidates", [])[:TOP_N]
    lines.append(f"🎯 本日の買い候補（総合評価上位{len(candidates)}銘柄・日経225スクリーニング）")
    lines.append(f"（分析データ生成: {generated_at} UTC）")
    lines.append("")
    if not candidates:
        lines.append("⚠️ 候補銘柄がありませんでした。")
    for i, c in enumerate(candidates, 1):
        code = c.get("code", "?")
        name = c.get("name", "?")
        score = c.get("score", 0)
        tech_score = c.get("tech_score", 0)
        fund_score = c.get("fundamental_score", 0)
        tech_reasons = "、".join(c.get("tech_reasons", [])) or "該当シグナルなし"
        fund_reasons = "、".join(c.get("fundamental_reasons", [])) or "該当シグナルなし"
        per = c.get("per")
        pbr = c.get("pbr")
        div = c.get("dividend_yield_pct")
        fund_detail = f"（PER{per}倍・PBR{pbr}倍・配当利回り{div}%）" if per else ""
        lines.append(f"{i}. {code} {name}（総合評価 {score}点）")
        lines.append(f"　【テクニカル分析】{tech_score}点 — {tech_reasons}")
        lines.append(f"　【ファンダ分析】{fund_score}点 — {fund_reasons}{fund_detail}")
        lines.append(f"　【総合評価】{verdict_comment(tech_score, fund_score)}")
        lines.append("")
    lines.append("※本メッセージは自動生成された分析結果です。投資判断は自己責任でお願いします。")
    lines.append("分析対象は日経225銘柄、データソースは無料のyfinanceです。")
    return "\n".join(lines)
def main():
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    message = build_message()
    if not webhook_url:
        print("[warn] SLACK_WEBHOOK_URL not set. Printing message instead:\n")
        print(message)
        return
    resp = requests.post(webhook_url, json={"text": message}, timeout=20)
    if resp.status_code != 200:
        print(f"[error] Slack webhook failed: {resp.status_code} {resp.text}", file=sys.stderr)
        sys.exit(1)
    print("[info] Slack message sent successfully")
if __name__ == "__main__":
    main()
