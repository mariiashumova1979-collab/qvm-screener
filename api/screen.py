"""
/api/screen — serverless QVM-скрининг для Vercel.

Слой данных: Financial Modeling Prep (FMP) — чистый JSON, работает
с дата-центровых IP (в отличие от yfinance). Ранжирование на чистом
Python, без pandas — функция лёгкая, холодный старт быстрый.

Логика фильтров идентична локальной версии:
  Шаг 1 (Quality)  — отсев слабых компаний
  Шаг 2 (Value)    — топ-N% по Earnings Yield
  Шаг 3 (Momentum) — восходящий тренд 3M и 6M

ENV-переменная (задаётся в настройках Vercel, НЕ в коде):
  FMP_API_KEY — ключ Financial Modeling Prep

ВНИМАНИЕ: точные пути эндпоинтов FMP периодически меняются
(была миграция на /stable/). Перед деплоем сверь URL в FMP_BASE и
функциях ниже с актуальной документацией FMP.
"""

from http.server import BaseHTTPRequestHandler
import json
import math
import os
import urllib.request
import urllib.parse

FMP_KEY = os.environ.get("FMP_API_KEY", "")
FMP_BASE = "https://financialmodelingprep.com/api/v3"

VALUE_TOP_PCT = 0.20
FINAL_PICKS = 15

DEFAULT_UNIVERSE = ["ALAB", "AAPL", "MSFT", "NVDA", "AVGO", "ANET", "LRCX", "AMAT"]


# ----------------------------------------------------------------------------
# СЛОЙ ДАННЫХ (FMP)
# ----------------------------------------------------------------------------

def _fmp_get(path):
    """GET к FMP, ключ подставляется автоматически. Возвращает распарсенный JSON."""
    sep = "&" if "?" in path else "?"
    url = f"{FMP_BASE}/{path}{sep}apikey={FMP_KEY}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read().decode())


def _first(data):
    """FMP отдаёт списки — берём первый (самый свежий) элемент или {}."""
    return data[0] if isinstance(data, list) and data else {}


def fetch_company(ticker):
    """
    Собирает все сырые поля для одной компании.
    Один тикер = несколько лёгких JSON-запросов.
    """
    income = _first(_fmp_get(f"income-statement/{ticker}?limit=1"))
    balance = _first(_fmp_get(f"balance-sheet-statement/{ticker}?limit=1"))
    cashflow = _first(_fmp_get(f"cash-flow-statement/{ticker}?limit=1"))
    ev = _first(_fmp_get(f"enterprise-values/{ticker}?limit=1"))
    change = _first(_fmp_get(f"stock-price-change/{ticker}"))

    return {
        "ticker": ticker,
        # income statement
        "ebit": income.get("operatingIncome"),
        "gross_profit": income.get("grossProfit"),
        "net_income": income.get("netIncome"),
        # balance sheet
        "total_assets": balance.get("totalAssets"),
        "total_debt": balance.get("totalDebt"),
        # cash flow
        "operating_cf": cashflow.get("operatingCashFlow"),
        "fcf": cashflow.get("freeCashFlow"),
        # valuation
        "enterprise_value": ev.get("enterpriseValue"),
        # momentum (готовые поля — без сборки из истории цен!)
        "pi_3m": change.get("3M"),
        "pi_6m": change.get("6M"),
    }


# ----------------------------------------------------------------------------
# ШАГ 1 — QUALITY
# ----------------------------------------------------------------------------

def quality_filter(rows):
    """Отсев слабых. Те же правила, что в ручном разборе."""
    survivors = []
    for r in rows:
        fcf = r.get("fcf")
        debt = r.get("total_debt")
        gp = r.get("gross_profit")
        assets = r.get("total_assets")
        ni = r.get("net_income")
        ocf = r.get("operating_cf")

        # пропускаем компании с дырами в данных
        if None in (fcf, debt, gp, assets, ni, ocf) or assets == 0:
            continue

        # FCF / Total Debt
        if fcf <= 0:
            continue                       # отрицательный FCF → провал
        r["fcf_debt"] = float("inf") if debt == 0 else fcf / debt  # нет долга → топ

        # Gross Margin (Marx) = Gross Profit / Total Assets
        if gp <= 0:
            continue                       # отрицательный Gross Profit → провал
        r["gm_marx"] = gp / assets

        # Accrual Ratio (CF) — информативно, не отсекаем
        r["accrual_ratio"] = (ni - ocf) / assets

        survivors.append(r)
    return survivors


# ----------------------------------------------------------------------------
# ШАГ 2 — VALUE
# ----------------------------------------------------------------------------

def value_filter(rows, top_pct=VALUE_TOP_PCT):
    """Earnings Yield = EBIT / EV. Берём топ-N% (выше = дешевле)."""
    scored = []
    for r in rows:
        ebit = r.get("ebit")
        ev = r.get("enterprise_value")
        if ebit is None or not ev:
            continue
        r["earnings_yield"] = ebit / ev
        scored.append(r)

    scored.sort(key=lambda r: r["earnings_yield"], reverse=True)
    # ceiling от доли: при 7 кандидатах и 20% → 2
    cutoff = max(1, math.ceil(len(scored) * top_pct))
    return scored[:cutoff]


# ----------------------------------------------------------------------------
# ШАГ 3 — MOMENTUM
# ----------------------------------------------------------------------------

def momentum_filter(rows):
    """Оба периода положительны; при выборке >=10 — ещё и выше среднего."""
    valid = [r for r in rows if r.get("pi_3m") is not None and r.get("pi_6m") is not None]
    # жёсткое правило: оба периода > 0
    valid = [r for r in valid if r["pi_3m"] > 0 and r["pi_6m"] > 0]

    if len(valid) >= 10:
        avg_3m = sum(r["pi_3m"] for r in valid) / len(valid)
        avg_6m = sum(r["pi_6m"] for r in valid) / len(valid)
        valid = [r for r in valid if r["pi_3m"] > avg_3m and r["pi_6m"] > avg_6m]
    return valid


# ----------------------------------------------------------------------------
# ОРКЕСТРАЦИЯ
# ----------------------------------------------------------------------------

def run_screen(universe):
    raw = []
    for tk in universe:
        try:
            raw.append(fetch_company(tk))
        except Exception as e:
            print(f"skip {tk}: {e}")

    after_q = quality_filter(raw)
    after_v = value_filter(after_q)
    final = momentum_filter(after_v)

    final.sort(key=lambda r: r["earnings_yield"], reverse=True)
    final = final[:FINAL_PICKS]

    keep = ["ticker", "earnings_yield", "fcf_debt", "gm_marx",
            "accrual_ratio", "pi_3m", "pi_6m"]
    cleaned = [{k: r.get(k) for k in keep} for r in final]

    return {
        "counts": {
            "universe": len(universe),
            "after_quality": len(after_q),
            "after_value": len(after_v),
            "final": len(cleaned),
        },
        "results": cleaned,
    }


# ----------------------------------------------------------------------------
# VERCEL HANDLER
# ----------------------------------------------------------------------------

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # тикеры можно передать как ?tickers=AAPL,MSFT,NVDA
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        tickers_raw = params.get("tickers", [""])[0]
        universe = (
            [t.strip().upper() for t in tickers_raw.split(",") if t.strip()]
            if tickers_raw else DEFAULT_UNIVERSE
        )

        if not FMP_KEY:
            self._send(500, {"error": "FMP_API_KEY не задан в переменных окружения Vercel"})
            return

        try:
            payload = run_screen(universe)
            self._send(200, payload)
        except Exception as e:
            self._send(500, {"error": str(e)})

    def _send(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())
