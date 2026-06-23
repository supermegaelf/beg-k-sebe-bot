def parse_wheel_value(text: str) -> int | None:
    try:
        v = int(text.strip())
        return v if 1 <= v <= 10 else None
    except ValueError:
        return None
