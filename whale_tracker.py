"""
J.A.R.V.I.S — Whale / 13F / Politician Trade Tracker
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
미국 고위 공직자/기관 투자자의 최신 거래 및 13F 포트폴리오를 수집하고,
가상 포트폴리오(portfolio_manager.py)를 특정 인물/기관 비중에 맞게
리밸런싱하는 모듈.

지원 소스:
  1) Quiver Quantitative API (선택, QUIVER_API_KEY 필요)
  2) SEC 13F 공개 XML (Berkshire Hathaway / Warren Buffett 기본 지원)
  3) HouseStockWatcher 스타일 공개 API (정치인 거래 폴백)

.env:
  QUIVER_API_KEY=...
  WHALE_WATCHLIST=buffett,pelosi
  WHALE_ALERT_INTERVAL=86400
  WHALE_SELL_ALERT_USD=1000000
  SEC_USER_AGENT="Jarvis taehyeonkim@example.com"
"""

from __future__ import annotations

import json
import os
import re
import time
import threading
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime
from typing import Optional

import requests
try:
    import net  # 느린 WiFi: 연결 재사용·재시도·적응형 타임아웃
except Exception:
    net = requests

try:
    import yfinance as yf
    YF_AVAILABLE = True
except ImportError:
    YF_AVAILABLE = False

from portfolio_manager import get_usdkrw_rate, _fetch_current_price


QUIVER_API_KEY = os.getenv("QUIVER_API_KEY", "")
WHALE_WATCHLIST = [x.strip() for x in os.getenv("WHALE_WATCHLIST", "buffett,pelosi").split(",") if x.strip()]
WHALE_ALERT_INTERVAL = int(os.getenv("WHALE_ALERT_INTERVAL", "86400"))
WHALE_SELL_ALERT_USD = float(os.getenv("WHALE_SELL_ALERT_USD", "1000000"))
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "Jarvis Research taehyeonkim@example.com")

BIG_TECH = {"AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "META", "TSLA"}

WHALE_ALIASES = {
    "buffett": {"display": "워런 버핏", "source": "sec_13f", "cik": "0001067983"},
    "warren buffett": {"display": "워런 버핏", "source": "sec_13f", "cik": "0001067983"},
    "berkshire": {"display": "Berkshire Hathaway", "source": "sec_13f", "cik": "0001067983"},
    "pelosi": {"display": "낸시 펠로시", "source": "house"},
    "nancy pelosi": {"display": "낸시 펠로시", "source": "house"},
}

# SEC 13F는 CUSIP만 제공하는 경우가 많다. 핵심 대형 보유/빅테크는 로컬 매핑으로 보정한다.
CUSIP_TO_TICKER = {
    "037833100": "AAPL", "594918104": "MSFT", "67066G104": "NVDA",
    "02079K305": "GOOGL", "02079K107": "GOOG", "023135106": "AMZN",
    "30303M102": "META", "88160R101": "TSLA", "060505104": "BAC",
    "025816109": "AXP", "191216100": "KO", "166764100": "CVX",
    "46625H100": "JPM", "742718109": "PG", "931142103": "WMT",
    "92826C839": "V", "57636Q104": "MA", "30231G102": "XOM",
    "911312106": "UPS", "002824100": "ABT", "478160104": "JNJ",
}


def _log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] 🐋 [고래추적] {msg}")


def _normalize_person(person: str) -> tuple[str, dict]:
    key = (person or "").strip().lower()
    return key, WHALE_ALIASES.get(key, {"display": person or "Unknown", "source": "quiver"})


def _money_bucket_to_usd(text: str) -> float:
    """
    House disclosure 금액 범위('$1,001 - $15,000')를 대략 중간값 USD로 변환.
    """
    nums = re.findall(r"[\d,]+", text or "")
    vals = [float(n.replace(",", "")) for n in nums if n]
    if not vals:
        return 0.0
    if len(vals) == 1:
        return vals[0]
    return sum(vals[:2]) / 2


def _headers():
    return {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. 데이터 수집
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _fetch_quiver_trades(person: str) -> list[dict]:
    """Quiver Quantitative API 시도. 계정/엔드포인트 정책에 따라 실패할 수 있어 폴백 전제로 사용."""
    if not QUIVER_API_KEY:
        return []
    headers = {"Authorization": f"Bearer {QUIVER_API_KEY}", "Accept": "application/json"}
    candidates = [
        f"https://api.quiverquant.com/beta/historical/congresstrading/{person}",
        f"https://api.quiverquant.com/beta/live/congresstrading",
    ]
    for url in candidates:
        try:
            resp = net.get(url, headers=headers, timeout=(5, 15))
            if resp.status_code != 200:
                continue
            raw = resp.json()
            if isinstance(raw, dict):
                raw = raw.get("data", raw.get("results", []))
            trades = []
            for item in raw[:80]:
                name = str(item.get("Representative", item.get("Name", item.get("Politician", ""))))
                if person and person.lower() not in name.lower():
                    continue
                ticker = str(item.get("Ticker", item.get("ticker", ""))).upper().strip()
                if not ticker:
                    continue
                action = str(item.get("Transaction", item.get("transaction", item.get("Type", "")))).upper()
                if "SALE" in action or action.startswith("S"):
                    side = "SELL"
                elif "PURCHASE" in action or action.startswith("P"):
                    side = "BUY"
                else:
                    side = action or "UNKNOWN"
                amount = _money_bucket_to_usd(str(item.get("Range", item.get("Amount", item.get("amount", "")))))
                trades.append({
                    "person": name or person,
                    "ticker": ticker,
                    "side": side,
                    "amount_usd": amount,
                    "date": str(item.get("Date", item.get("TransactionDate", ""))),
                    "source": "quiver",
                })
            if trades:
                return trades
        except Exception:
            continue
    return []


def _latest_13f_info_table_url(cik: str) -> Optional[str]:
    """SEC submissions JSON에서 최신 13F-HR의 information table XML URL 찾기."""
    cik10 = str(cik).zfill(10)
    sub_url = f"https://data.sec.gov/submissions/CIK{cik10}.json"
    resp = net.get(sub_url, headers=_headers(), timeout=(5, 20))
    if resp.status_code != 200:
        return None
    recent = resp.json().get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    for form, acc in zip(forms, accessions):
        if str(form).upper().startswith("13F-HR"):
            acc_clean = acc.replace("-", "")
            idx_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_clean}/index.json"
            idx = net.get(idx_url, headers=_headers(), timeout=(5, 20))
            if idx.status_code != 200:
                continue
            items = idx.json().get("directory", {}).get("item", [])
            for item in items:
                name = item.get("name", "")
                low = name.lower()
                if low.endswith(".xml") and ("infotable" in low or "form13f" in low or "primary_doc" not in low):
                    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_clean}/{name}"
    return None


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _fetch_sec_13f_portfolio(cik: str, display_name: str) -> list[dict]:
    """SEC 13F information table XML 파싱 → 보유 비중 리스트."""
    try:
        xml_url = _latest_13f_info_table_url(cik)
        if not xml_url:
            return []
        resp = net.get(xml_url, headers=_headers(), timeout=(5, 25))
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.content)
        holdings = []
        for info in root.iter():
            if _strip_ns(info.tag) != "infoTable":
                continue
            row = {}
            for child in list(info):
                row[_strip_ns(child.tag)] = (child.text or "").strip()
            cusip = row.get("cusip", "").upper()
            ticker = CUSIP_TO_TICKER.get(cusip, cusip)
            if not ticker or not re.match(r"^[A-Z.]{1,6}$", ticker):
                continue
            try:
                value_usd = float(row.get("value", "0")) * 1000  # SEC 13F value = $000
            except ValueError:
                value_usd = 0.0
            if value_usd <= 0:
                continue
            holdings.append({
                "person": display_name,
                "ticker": ticker,
                "side": "HOLD",
                "amount_usd": value_usd,
                "value_usd": value_usd,
                "date": "",
                "source": "sec_13f",
                "name": row.get("nameOfIssuer", ticker),
            })
        total = sum(h["value_usd"] for h in holdings)
        if total > 0:
            for h in holdings:
                h["weight_pct"] = round(h["value_usd"] / total * 100, 2)
        return sorted(holdings, key=lambda x: x.get("weight_pct", 0), reverse=True)
    except Exception as e:
        _log(f"SEC 13F 파싱 실패: {e}")
        return []


def _fetch_house_trades(person: str, display_name: str) -> list[dict]:
    """HouseStockWatcher 공개 API 폴백."""
    urls = [
        "https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json",
        "https://housestockwatcher.com/api",
    ]
    for url in urls:
        try:
            resp = net.get(url, timeout=(5, 20))
            if resp.status_code != 200:
                continue
            raw = resp.json()
            if isinstance(raw, dict):
                raw = raw.get("data", raw.get("transactions", []))
            trades = []
            for item in raw[:2000]:
                rep = str(item.get("representative", item.get("representative_name", item.get("owner", ""))))
                if person.lower() not in rep.lower() and display_name.replace(" ", "").lower() not in rep.replace(" ", "").lower():
                    continue
                ticker = str(item.get("ticker", item.get("asset_ticker", ""))).upper().strip()
                if not ticker or ticker in ("--", "N/A"):
                    continue
                tx = str(item.get("type", item.get("transaction", ""))).lower()
                side = "SELL" if "sale" in tx or "sell" in tx else ("BUY" if "purchase" in tx or "buy" in tx else "UNKNOWN")
                amount = _money_bucket_to_usd(str(item.get("amount", item.get("range", ""))))
                trades.append({
                    "person": rep or display_name,
                    "ticker": ticker,
                    "side": side,
                    "amount_usd": amount,
                    "date": str(item.get("transaction_date", item.get("disclosure_date", item.get("date", "")))),
                    "source": "house",
                })
            if trades:
                return trades[:80]
        except Exception:
            continue
    return []


def _sample_portfolio(person: str, display_name: str) -> list[dict]:
    """네트워크/API 부재 시 시스템 동작 검증용 보수적 샘플. source=fallback 명시."""
    key = person.lower()
    if "buffett" in key or "berkshire" in key:
        rows = [("AAPL", 42.0), ("BAC", 10.0), ("AXP", 9.5), ("KO", 7.0), ("CVX", 6.0)]
    else:
        rows = [("NVDA", 35.0), ("AAPL", 25.0), ("MSFT", 20.0), ("GOOGL", 10.0), ("AMZN", 10.0)]
    return [{
        "person": display_name,
        "ticker": t,
        "side": "HOLD",
        "amount_usd": w,
        "value_usd": w,
        "weight_pct": w,
        "date": "",
        "source": "fallback",
    } for t, w in rows]


def fetch_whale_trades(person: str, max_positions: int = 10) -> dict:
    """
    특정 인물/기관의 최신 거래 또는 13F 포트폴리오를 수집하고 비중 리스트를 계산한다.
    """
    key, meta = _normalize_person(person)
    display = meta.get("display", person)

    trades = _fetch_quiver_trades(key)
    if not trades:
        if meta.get("source") == "sec_13f":
            trades = _fetch_sec_13f_portfolio(meta["cik"], display)
        else:
            trades = _fetch_house_trades(key, display)
    if not trades:
        trades = _sample_portfolio(key, display)

    # 거래 데이터만 있는 경우 BUY/HOLD 금액 합산으로 비중 근사.
    weights = {}
    for tr in trades:
        ticker = tr.get("ticker", "").upper()
        if not ticker or tr.get("side") == "SELL":
            continue
        amt = float(tr.get("value_usd", tr.get("amount_usd", 0)) or 0)
        if amt <= 0:
            amt = 1.0
        weights[ticker] = weights.get(ticker, 0.0) + amt
    total = sum(weights.values())
    portfolio = []
    if total > 0:
        for ticker, value in sorted(weights.items(), key=lambda x: x[1], reverse=True)[:max_positions]:
            portfolio.append({"ticker": ticker, "weight_pct": round(value / total * 100, 2)})

    return {
        "success": True,
        "person": display,
        "source": trades[0].get("source", "unknown") if trades else "none",
        "trades": trades[:80],
        "portfolio": portfolio,
        "fetched_at": datetime.now().isoformat(),
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. 미러링 / 리밸런싱
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _ticker_currency(ticker: str) -> str:
    return "KRW" if ticker.endswith(".KS") or ticker.endswith(".KQ") else "USD"


def _price_krw(ticker: str, usdkrw: float) -> tuple[float, float, str]:
    native = _fetch_current_price(ticker)
    currency = _ticker_currency(ticker)
    if native <= 0:
        # 가격 조회 실패 시 주문 수량 0으로 두되 계획은 반환.
        return 0.0, 0.0, currency
    return native * (1 if currency == "KRW" else usdkrw), native, currency


def build_mirror_rebalance_plan(portfolio_manager, person: str, capital_krw: Optional[float] = None,
                                max_positions: int = 8) -> dict:
    """현재 가상 계좌를 고래 포트폴리오 비중에 맞추기 위한 목표 수량/거래 계획 산출."""
    whale = fetch_whale_trades(person, max_positions=max_positions)
    target = whale.get("portfolio", [])[:max_positions]
    if not target:
        return {"success": False, "message": "미러링 가능한 포트폴리오 비중이 없습니다."}

    report = portfolio_manager.get_portfolio_report()
    total_assets = float(capital_krw or report.get("total_assets_krw", 0) or portfolio_manager.data.get("available_cash_krw", 0))
    usdkrw = get_usdkrw_rate()

    current_shares = {}
    for p in report.get("positions", []):
        current_shares[p["ticker"]] = float(p.get("shares", 0))

    orders = []
    for item in target:
        ticker = item["ticker"]
        weight = float(item["weight_pct"])
        target_value_krw = total_assets * weight / 100
        price_krw, native_price, currency = _price_krw(ticker, usdkrw)
        target_shares = int(target_value_krw / price_krw) if price_krw > 0 else 0
        delta = target_shares - int(current_shares.get(ticker, 0))
        orders.append({
            "ticker": ticker,
            "weight_pct": round(weight, 2),
            "target_value_krw": round(target_value_krw),
            "price": native_price,
            "currency": currency,
            "target_shares": target_shares,
            "current_shares": int(current_shares.get(ticker, 0)),
            "delta_shares": delta,
            "action": "BUY" if delta > 0 else ("SELL" if delta < 0 else "HOLD"),
        })

    rows = [[o["ticker"], f"{o['weight_pct']:.1f}%", o["action"], f"{o['delta_shares']:+d}주"]
            for o in orders]
    overlay_table = {
        "type": "table",
        "title": f"WHALE MIRROR | {whale['person']}",
        "headers": ["종목", "비중", "액션", "수량"],
        "rows": rows,
    }
    return {
        "success": True,
        "person": whale["person"],
        "source": whale["source"],
        "capital_krw": total_assets,
        "orders": orders,
        "whale": whale,
        "overlay_table": overlay_table,
    }


def apply_mirror_rebalance(portfolio_manager, plan: dict) -> dict:
    """
    가상 포트폴리오를 목표 비중으로 재설정한다.
    실제 체결 엔진이 아니므로, 미러링 목적에 맞게 holdings/cash를 target state로 저장한다.
    """
    if not plan.get("success"):
        return plan
    with portfolio_manager._lock:
        new_holdings = {}
        invested = 0.0
        for order in plan["orders"]:
            if order["target_shares"] <= 0 or order["price"] <= 0:
                continue
            key = "avg_price_krw" if order["currency"] == "KRW" else "avg_price_usd"
            new_holdings[order["ticker"]] = {
                "shares": order["target_shares"],
                key: round(order["price"], 4),
            }
            invested += order["target_value_krw"]
        portfolio_manager.data["holdings"] = new_holdings
        portfolio_manager.data["available_cash_krw"] = max(0, float(plan["capital_krw"]) - invested)
        portfolio_manager._write(portfolio_manager.data)
    return {"success": True, "holdings": new_holdings, "cash_krw": portfolio_manager.data["available_cash_krw"]}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  3. 백그라운드 고래 매도 감시 워커
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class WhaleTracker:
    def __init__(self, watchlist=None, interval: int = WHALE_ALERT_INTERVAL,
                 sell_threshold_usd: float = WHALE_SELL_ALERT_USD):
        self.watchlist = watchlist if watchlist is not None else list(WHALE_WATCHLIST)
        self.interval = max(3600, int(interval))
        self.sell_threshold_usd = sell_threshold_usd
        self.enabled = bool(self.watchlist)
        self._event_q = deque(maxlen=30)
        self._lock = threading.Lock()
        self._thread = None
        self._running = False
        self._seen = set()
        self._check_count = 0

    def start(self):
        if not self.enabled or self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        _log(f"감시 시작: {', '.join(self.watchlist)} | 간격={self.interval}초")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def get_events(self):
        events = []
        with self._lock:
            while self._event_q:
                events.append(self._event_q.popleft())
        return events

    def requeue(self, events):
        with self._lock:
            for evt in reversed(events):
                self._event_q.appendleft(evt)

    def _loop(self):
        time.sleep(12)
        while self._running:
            self._check_count += 1
            self.check_once()
            slept = 0
            while self._running and slept < self.interval:
                time.sleep(5)
                slept += 5

    def check_once(self):
        sells = []
        for person in self.watchlist:
            data = fetch_whale_trades(person)
            for tr in data.get("trades", []):
                ticker = tr.get("ticker", "").upper()
                if tr.get("side") != "SELL" or ticker not in BIG_TECH:
                    continue
                amount = float(tr.get("amount_usd", 0) or 0)
                if amount < self.sell_threshold_usd:
                    continue
                key = (data.get("person"), ticker, tr.get("date"), round(amount))
                if key in self._seen:
                    continue
                self._seen.add(key)
                sells.append({**tr, "person": data.get("person", person)})

        if not sells:
            return []

        rows = [[s["person"][:10], s["ticker"], "SELL", f"${s['amount_usd']/1_000_000:.1f}M"]
                for s in sells[:8]]
        evt = {
            "source": "whale_tracker",
            "type": "big_sell",
            "message": "선생님, 주요 자산가들의 대량 매도 시그널이 포착되었습니다. 리스크 관리가 필요합니다.",
            "alert_text": "🚨 WHALE SELL ALERT",
            "overlay_table": {
                "type": "table",
                "title": "고래 매도 동향",
                "headers": ["인물", "종목", "구분", "규모"],
                "rows": rows,
                "alert_text": "🚨 주요 자산가 대량 매도 포착",
            },
            "sells": sells,
            "timestamp": datetime.now().isoformat(),
        }
        with self._lock:
            self._event_q.append(evt)
        _log(f"🚨 대량 매도 {len(sells)}건 포착")

        # CV 샌드박스 캐시 갱신 (Gemini 0회)
        if os.getenv("CV_CACHE_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off"):
            try:
                from cross_validation import refresh_market_snapshot_cache
                seen_tickers: set[str] = set()
                for s in sells:
                    t = (s.get("ticker") or "").strip().upper()
                    if not t or t in seen_tickers:
                        continue
                    seen_tickers.add(t)
                    note = (
                        f"고래 {s.get('person', '?')} 대량매도 "
                        f"${float(s.get('amount_usd') or 0) / 1_000_000:.1f}M"
                    )
                    refresh_market_snapshot_cache(t, include_news=False, extra_note=note)
            except Exception:
                pass

        return sells

    @property
    def status(self):
        return {
            "running": self._running,
            "watchlist": self.watchlist,
            "checks": self._check_count,
            "pending": len(self._event_q),
            "interval": f"{self.interval}초",
            "sell_threshold_usd": self.sell_threshold_usd,
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  4. LLM 도구
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WHALE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "fetch_whale_trades",
            "description": "특정 정치인/대가/기관의 최신 주식 거래 또는 13F 포트폴리오 비중을 조회합니다. 예: 낸시 펠로시 거래, 워런 버핏 포트폴리오.",
            "parameters": {
                "type": "object",
                "properties": {
                    "person": {"type": "string", "description": "인물/기관명. 예: buffett, pelosi"},
                },
                "required": ["person"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mirror_whale_portfolio",
            "description": "사용자의 가상 포트폴리오를 특정 고래/대가의 보유 비중에 맞게 자동 리밸런싱합니다. 예: 워런 버핏 포트폴리오 미러링해줘.",
            "parameters": {
                "type": "object",
                "properties": {
                    "person": {"type": "string", "description": "인물/기관명. 예: buffett, warren buffett, pelosi"},
                    "capital_krw": {"type": "integer", "description": "미러링에 사용할 원화 기준 자산. 미지정 시 현재 가상 총자산."},
                    "apply": {"type": "boolean", "description": "실제 portfolio.json에 반영할지 여부. 기본 true."},
                },
                "required": ["person"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_whale_tracker_status",
            "description": "고래 매도 감시 백그라운드 워커 상태를 조회합니다.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

_tracker_instance = None
_portfolio_instance = None


def set_whale_tracker_instance(tracker, portfolio_manager=None):
    global _tracker_instance, _portfolio_instance
    _tracker_instance = tracker
    if portfolio_manager is not None:
        _portfolio_instance = portfolio_manager


def execute_whale_tool(tool_name, arguments):
    global _tracker_instance, _portfolio_instance
    try:
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
    except Exception:
        arguments = {}

    if tool_name == "get_whale_tracker_status":
        if not _tracker_instance:
            return {"success": False, "message": "고래 추적기 없음"}
        return {"success": True, **_tracker_instance.status}

    if tool_name == "fetch_whale_trades":
        person = (arguments.get("person") or "").strip()
        data = fetch_whale_trades(person)
        rows = [[p["ticker"], f"{p['weight_pct']:.1f}%"] for p in data.get("portfolio", [])[:8]]
        return {
            "success": True,
            "person": data["person"],
            "source": data["source"],
            "portfolio": data["portfolio"],
            "trades": data["trades"][:20],
            "overlay_table": {
                "type": "table",
                "title": f"WHALE PORTFOLIO | {data['person']}",
                "headers": ["종목", "비중"],
                "rows": rows,
            },
            "briefing": f"선생님, {data['person']}의 최신 포트폴리오 상위 비중을 화면에 정리했습니다.",
        }

    if tool_name == "mirror_whale_portfolio":
        if not _portfolio_instance:
            return {"success": False, "message": "포트폴리오 매니저 연결 없음"}
        person = (arguments.get("person") or "").strip()
        capital = arguments.get("capital_krw")
        try:
            capital = float(capital) if capital else None
        except Exception:
            capital = None
        apply_raw = arguments.get("apply", True)
        if isinstance(apply_raw, str):
            apply = apply_raw.strip().lower() not in ("false", "0", "no", "아니오", "계산만")
        else:
            apply = bool(apply_raw)
        plan = build_mirror_rebalance_plan(_portfolio_instance, person, capital_krw=capital)
        if not plan.get("success"):
            return plan
        applied = apply_mirror_rebalance(_portfolio_instance, plan) if apply else {"success": True}
        return {
            "success": applied.get("success", False),
            "person": plan["person"],
            "source": plan["source"],
            "orders": plan["orders"],
            "overlay_table": plan["overlay_table"],
            "briefing": f"선생님, {plan['person']} 포트폴리오 비중에 맞춰 가상 자산 리밸런싱 계산을 완료했습니다.",
            "applied": apply,
        }

    return {"success": False, "message": f"알 수 없는 도구: {tool_name}"}

