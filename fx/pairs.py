"""The instruments, their typical costs, and which currencies are managed by their central bank."""
from __future__ import annotations

MAJORS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD"]
CROSSES = ["EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD", "EURNZD", "GBPJPY", "GBPCHF", "GBPAUD", "GBPCAD",
           "GBPNZD", "AUDJPY", "AUDNZD", "AUDCAD", "AUDCHF", "NZDJPY", "NZDCAD", "NZDCHF", "CADJPY", "CADCHF", "CHFJPY"]
METALS = {"XAUUSD": ("GC=F", "Gold (futures price)"), "XAGUSD": ("SI=F", "Silver (futures price)")}
EXOTICS = ["USDNGN", "USDZAR", "USDKES", "USDGHS", "USDEGP", "USDTRY", "USDMXN", "USDBRL", "USDINR"]
# central bank manages or heavily steers the rate: the official price can differ from what you can actually trade
MANAGED = {"NGN": "Nigeria", "EGP": "Egypt", "INR": "India", "GHS": "Ghana", "KES": "Kenya", "TRY": "Turkey"}
NAMES = {"USD": "US dollar", "EUR": "euro", "GBP": "British pound", "JPY": "Japanese yen", "CHF": "Swiss franc",
         "AUD": "Australian dollar", "CAD": "Canadian dollar", "NZD": "New Zealand dollar", "NGN": "Nigerian naira",
         "ZAR": "South African rand", "KES": "Kenyan shilling", "GHS": "Ghanaian cedi", "EGP": "Egyptian pound",
         "TRY": "Turkish lira", "MXN": "Mexican peso", "BRL": "Brazilian real", "INR": "Indian rupee",
         "XAU": "gold", "XAG": "silver"}


def yahoo(pair: str) -> str:
    return METALS[pair][0] if pair in METALS else f"{pair}=X"


def label(pair: str) -> str:
    return METALS[pair][1] if pair in METALS else f"{pair[:3]}/{pair[3:]}"


def kind(pair: str) -> str:
    return "metal" if pair in METALS else "exotic" if pair in EXOTICS else "major" if pair in MAJORS else "cross"


def pip(pair: str) -> float:
    if pair in METALS:
        return 0.1 if pair == "XAUUSD" else 0.01
    return 0.01 if pair.endswith("JPY") else 0.0001


def spread(pair: str) -> float:
    """Typical retail round-trip spread as a fraction of the price (varies by broker and time of day)."""
    k = kind(pair)
    if k == "major":
        return 0.00010
    if k == "cross":
        return 0.00025
    if k == "metal":
        return 0.0004 if pair == "XAUUSD" else 0.0010
    return 0.0060           # exotics: often 0.3-1%+, much wider for managed currencies


SWAP_PER_NIGHT = 0.0001     # rough cost of holding past 5 pm New York, each night (varies by broker and rates)


def round_step(pair: str) -> float:
    """Price levels traders watch: every 50 pips (e.g. 1.1250, 1.1300), $5 on gold."""
    return 5.0 if pair == "XAUUSD" else 0.5 if pair == "XAGUSD" else pip(pair) * 50


def currencies(pair: str) -> tuple[str, str]:
    return pair[:3], pair[3:]


ALL = MAJORS + CROSSES + list(METALS) + EXOTICS
