# tests/test_news_trading.py
"""
Unit tests for news trading logic.
Run with: python -m tests.test_news_trading (from server/)
"""
import sys
import os
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock Firebase and heavy dependencies before importing app modules
sys.modules["firebase_admin"] = MagicMock()
sys.modules["firebase_admin.credentials"] = MagicMock()
sys.modules["firebase_admin.firestore"] = MagicMock()
sys.modules["app.services.firebase"] = MagicMock()
sys.modules["app.services.log_service"] = MagicMock()
sys.modules["app.services.log_service"].log_to_firestore = MagicMock()
sys.modules["app.services.log_service"].log_trade_event = MagicMock()
sys.modules["app.services.oanda_service"] = MagicMock()
sys.modules["app.services.oanda_service"].DECIMALS_BY_INSTRUMENT = {
    "EUR_USD": 5, "USD_JPY": 3, "USD_CHF": 5, "GBP_USD": 5,
    "EUR_GBP": 5, "EUR_JPY": 3, "GBP_JPY": 3, "AUD_USD": 5,
    "NZD_USD": 5, "USD_CAD": 5, "SPX500_USD": 1,
}
sys.modules["openai"] = MagicMock()


def test_parse_numeric_value():
    from app.services.news_data_service import parse_numeric_value

    tests = [
        ("263K", 263_000),
        ("-0.3%", -0.3),
        ("3.50%", 3.5),
        ("1.234M", 1_234_000),
        ("2.5B", 2_500_000_000),
        ("180K", 180_000),
        ("4.2%", 4.2),
        ("-12K", -12_000),
        ("0.25%", 0.25),
        ("", None),
        (None, None),
        ("N/A", None),
    ]

    passed = 0
    for raw, expected in tests:
        result = parse_numeric_value(raw)
        ok = result == expected
        status = "OK" if ok else "FAIL"
        if not ok:
            print(f"  {status}: parse_numeric_value({raw!r}) = {result}, expected {expected}")
        passed += ok

    print(f"[parse_numeric_value] {passed}/{len(tests)} passed")
    return passed == len(tests)


def test_calculate_surprise():
    from app.services.news_data_service import calculate_surprise

    tests = [
        # (actual, forecast, expected_direction, expected_magnitude)
        (263_000, 180_000, "ABOVE", "LARGE"),     # NFP big beat (+46%)
        (175_000, 180_000, "BELOW", "SMALL"),      # NFP slight miss (-2.8%)
        (180_000, 180_000, "INLINE", "SMALL"),     # Inline
        (200_000, 180_000, "ABOVE", "MEDIUM"),     # +11%
        (3.5, 3.3, "ABOVE", "MEDIUM"),             # CPI beat +6%
        (3.2, 3.5, "BELOW", "MEDIUM"),             # CPI miss -8.6%
        (4.2, 3.5, "ABOVE", "LARGE"),              # Rate surprise +20%
    ]

    passed = 0
    for actual, forecast, exp_dir, exp_mag in tests:
        result = calculate_surprise(actual, forecast)
        dir_ok = result["direction"] == exp_dir
        mag_ok = result["magnitude"] == exp_mag
        ok = dir_ok and mag_ok
        status = "OK" if ok else "FAIL"
        if not ok:
            print(f"  {status}: surprise({actual} vs {forecast}) = {result['direction']}/{result['magnitude']}, "
                  f"expected {exp_dir}/{exp_mag}")
        passed += ok

    print(f"[calculate_surprise] {passed}/{len(tests)} passed")
    return passed == len(tests)


def test_determine_trade_direction():
    from app.strategies.news_trading_strategy import _determine_trade_direction

    tests = [
        # (event_title, country, surprise_direction, instrument, expected_trade_direction)
        # NFP beat → USD bullish
        ("Nonfarm Payrolls", "USD", "ABOVE", "USD_CHF", "LONG"),    # USD is base → LONG
        ("Nonfarm Payrolls", "USD", "ABOVE", "EUR_USD", "SHORT"),   # USD is quote, bullish → pair drops
        ("Nonfarm Payrolls", "USD", "BELOW", "USD_CHF", "SHORT"),   # USD miss → bearish → SHORT
        ("Nonfarm Payrolls", "USD", "BELOW", "EUR_USD", "LONG"),    # USD miss → pair rises

        # CPI beat → EUR bullish
        ("CPI m/m", "EUR", "ABOVE", "EUR_USD", "LONG"),             # EUR is base → LONG
        ("CPI m/m", "EUR", "BELOW", "EUR_USD", "SHORT"),            # EUR miss → SHORT
        ("CPI m/m", "EUR", "ABOVE", "EUR_GBP", "LONG"),             # EUR is base → LONG

        # Inverse events: Unemployment beat = BEARISH for currency
        ("Unemployment Rate", "USD", "ABOVE", "USD_CHF", "SHORT"),  # Higher unemployment = USD bearish
        ("Unemployment Rate", "USD", "ABOVE", "EUR_USD", "LONG"),   # USD bearish = EUR_USD rises
        ("Initial Jobless Claims", "USD", "ABOVE", "USD_JPY", "SHORT"),  # More claims = USD bearish

        # INLINE → None
        ("Nonfarm Payrolls", "USD", "INLINE", "USD_CHF", None),
    ]

    passed = 0
    for title, country, surprise_dir, instrument, expected in tests:
        event = {"title": title, "country": country}
        surprise = {"direction": surprise_dir, "magnitude": "LARGE"}
        result = _determine_trade_direction(event, surprise, instrument)
        ok = result == expected
        status = "OK" if ok else "FAIL"
        if not ok:
            print(f"  {status}: {title} {surprise_dir} on {instrument} = {result}, expected {expected}")
        passed += ok

    print(f"[_determine_trade_direction] {passed}/{len(tests)} passed")
    return passed == len(tests)


def test_post_release_decision():
    from app.services.news_analyzer import post_release_decision

    # LARGE surprise + aligned GPT bias → TRADE
    event = {"title": "Nonfarm Payrolls", "country": "USD"}
    surprise = {"direction": "ABOVE", "magnitude": "LARGE"}
    pre = {"bias": "BULLISH", "confidence": 70}
    result = post_release_decision(event, surprise, pre, "USD_CHF")
    assert result["action"] == "TRADE", f"Expected TRADE, got {result}"

    # SMALL surprise → SKIP
    surprise_small = {"direction": "ABOVE", "magnitude": "SMALL"}
    result2 = post_release_decision(event, surprise_small, pre, "USD_CHF")
    assert result2["action"] == "SKIP", f"Expected SKIP for small surprise, got {result2}"

    # MEDIUM surprise + contrary bias → SKIP
    surprise_med = {"direction": "ABOVE", "magnitude": "MEDIUM"}
    pre_bear = {"bias": "BEARISH", "confidence": 70}
    result3 = post_release_decision(event, surprise_med, pre_bear, "USD_CHF")
    assert result3["action"] == "SKIP", f"Expected SKIP for contrary bias, got {result3}"

    # MEDIUM surprise + neutral bias → TRADE
    pre_neutral = {"bias": "NEUTRAL", "confidence": 50}
    result4 = post_release_decision(event, surprise_med, pre_neutral, "USD_CHF")
    assert result4["action"] == "TRADE", f"Expected TRADE for neutral bias, got {result4}"

    # UNKNOWN surprise → SKIP
    surprise_unk = {"direction": "UNKNOWN", "magnitude": "UNKNOWN"}
    result5 = post_release_decision(event, surprise_unk, pre, "USD_CHF")
    assert result5["action"] == "SKIP", f"Expected SKIP for unknown, got {result5}"

    print("[post_release_decision] 5/5 passed")
    return True


def test_is_inverse_event():
    from app.services.news_analyzer import _is_inverse_event

    assert _is_inverse_event("Unemployment Rate") is True
    assert _is_inverse_event("Initial Jobless Claims") is True
    assert _is_inverse_event("Continuing Claims") is True
    assert _is_inverse_event("Nonfarm Payrolls") is False
    assert _is_inverse_event("CPI m/m") is False
    assert _is_inverse_event("Interest Rate Decision") is False

    print("[_is_inverse_event] 6/6 passed")
    return True


if __name__ == "__main__":
    print("=" * 50)
    print("News Trading Unit Tests")
    print("=" * 50)

    results = [
        test_parse_numeric_value(),
        test_calculate_surprise(),
        test_is_inverse_event(),
        test_determine_trade_direction(),
        test_post_release_decision(),
    ]

    print("=" * 50)
    total = len(results)
    ok = sum(results)
    print(f"Results: {ok}/{total} test suites passed")
    if ok == total:
        print("ALL TESTS PASSED")
    else:
        print("SOME TESTS FAILED")
        sys.exit(1)
