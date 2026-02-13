# app/services/ichimoku_analyzer.py


def rule_based_filter(signal: dict) -> dict:
    """
    Filtre rule-based Ichimoku.
    signal contient: close, tenkan, kijun, ssa, ssb, chikou, chikou_ref_price, direction
    """
    d = signal["direction"]
    close = signal["close"]
    tenkan = signal["tenkan"]
    kijun = signal["kijun"]
    ssa = signal["ssa"]
    ssb = signal["ssb"]
    chikou = signal.get("chikou")
    chikou_ref = signal.get("chikou_ref_price")

    kumo_top = max(ssa, ssb)
    kumo_bottom = min(ssa, ssb)
    reasons = []

    if d == "LONG":
        if close > kumo_top:
            reasons.append("Prix au-dessus du Kumo")
        else:
            return {"valid": False, "direction": d, "reasons": ["Prix pas au-dessus du Kumo"]}
        if tenkan > kijun:
            reasons.append("Tenkan > Kijun (momentum haussier)")
        else:
            return {"valid": False, "direction": d, "reasons": ["Tenkan <= Kijun"]}
        if chikou and chikou_ref and chikou > chikou_ref:
            reasons.append("Chikou confirme (au-dessus du prix passe)")
    elif d == "SHORT":
        if close < kumo_bottom:
            reasons.append("Prix en-dessous du Kumo")
        else:
            return {"valid": False, "direction": d, "reasons": ["Prix pas en-dessous du Kumo"]}
        if tenkan < kijun:
            reasons.append("Tenkan < Kijun (momentum baissier)")
        else:
            return {"valid": False, "direction": d, "reasons": ["Tenkan >= Kijun"]}
        if chikou and chikou_ref and chikou < chikou_ref:
            reasons.append("Chikou confirme (en-dessous du prix passe)")

    return {"valid": True, "direction": d, "reasons": reasons}
