"""Forex helpers: pair-name parsing, watchlists, pip sizes, price precision and lot sizing."""
import re

CCY = {"USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "SEK", "NOK", "DKK", "SGD", "HKD",
       "ZAR", "MXN", "TRY", "PLN", "CNH", "CNY", "HUF", "CZK", "ILS", "INR", "THB"}
# Spot metals are not on Yahoo Finance: COMEX futures are used as a close proxy (prices differ slightly from spot)
METALS = {"XAU": ("GC=F", "gold"), "XAG": ("SI=F", "silver")}

WATCHLISTS = {
    "majors": ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD"],
    "minors": ["EURGBP", "EURJPY", "GBPJPY", "EURCHF", "EURAUD", "AUDJPY", "GBPCHF", "CADJPY", "AUDNZD",
               "EURCAD", "GBPAUD", "CHFJPY", "NZDJPY", "AUDCAD"],
    "metals": ["XAUUSD", "XAGUSD"],
}
WATCHLISTS["forex"] = WATCHLISTS["majors"] + WATCHLISTS["minors"] + WATCHLISTS["metals"]
WATCHLISTS["fx"] = WATCHLISTS["forex"]


def expand(symbols):
    out = []
    for s in symbols:
        out += WATCHLISTS.get(s.lower(), [s])
    return list(dict.fromkeys(out))


def parse(symbol: str):
    """'EURUSD', 'eur/usd', 'EURUSD=X', 'XAUUSD' -> pair info dict, or None if not a forex/metal pair."""
    s = re.sub(r"[/_\-\s]", "", symbol.upper()).removesuffix("=X")
    if len(s) != 6 or not s.isalpha():
        return None
    base, quote = s[:3], s[3:]
    if base in METALS and quote == "USD":
        yahoo, name = METALS[base]
        return {"pair": s, "base": base, "quote": quote, "yahoo": yahoo, "metal": name,
                "pip": 0.1 if base == "XAU" else 0.01, "contract": 100 if base == "XAU" else 5000}
    if base in CCY and quote in CCY and base != quote:
        return {"pair": s, "base": base, "quote": quote, "yahoo": f"{s}=X", "metal": None,
                "pip": 0.01 if quote in ("JPY", "HUF") else 0.0001, "contract": 100_000}
    return None


def decimals(info) -> int:
    """Broker-style quote precision: one decimal more than the pip (5 for EURUSD, 3 for USDJPY, 2 for gold)."""
    return {0.0001: 5, 0.01: 3, 0.1: 2}[info["pip"]]


def pips(info, distance: float) -> float:
    return abs(distance) / info["pip"]


def quote_to_account(info, price: float, account_ccy: str, rate_lookup) -> float:
    """How many account-currency units one unit of the quote currency is worth."""
    q, b = info["quote"], info["base"]
    if q == account_ccy:
        return 1.0
    if b == account_ccy:
        return 1.0 / price
    return rate_lookup(q, account_ccy)


def lot_size(info, entry, stop, risk_money, q2acc):
    """Lots so that hitting the stop loses `risk_money` (account currency)."""
    loss_per_lot = abs(entry - stop) * info["contract"] * q2acc
    pip_value = info["pip"] * info["contract"] * q2acc
    return risk_money / loss_per_lot, pip_value
