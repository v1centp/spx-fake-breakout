# app/utils/symbols.py
def normalize_symbol(s: str) -> str:
    if not s:
        return s
    # retire "AM." (et autres préfixes éventuels) devant "I:..."
    for pref in ("AM.", "Q.", "T."):
        if s.startswith(pref):
            return s[len(pref):]
    return s
