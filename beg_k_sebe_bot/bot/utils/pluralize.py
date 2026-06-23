def pluralize(n: int, one: str, few: str, many: str) -> str:
    """Russian plural form selector."""
    n = abs(n)
    if 11 <= n % 100 <= 19:
        return many
    rem = n % 10
    if rem == 1:
        return one
    if 2 <= rem <= 4:
        return few
    return many
