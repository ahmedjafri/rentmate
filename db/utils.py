import re


def normalize_phone(phone: str, default_country_code: str = "+1") -> str | None:
    """
    Normalize phone numbers:
      - strip whitespace
      - remove spaces, dashes, parentheses, dots
      - preserve leading country codes (`+44`, `+92`, etc)
      - if no country code, prepend default (default: +1)
      - return None for blank or invalid numbers
    """
    if not phone:
        return None

    # Trim whitespace
    phone = phone.strip()

    # Remove chars like (), -, ., spaces
    digits = re.sub(r"[^\d+]", "", phone)

    if not digits:
        return None

    # Case 1: already has a country code (e.g., +92xxxx, +44xxxx)
    if digits.startswith("+"):
        # Remove any non-digit after the plus (shouldn't be needed but safe)
        digits = "+" + re.sub(r"[^\d]", "", digits[1:])
        return digits

    # Case 2: pure digits, US-length (assume default country)
    if len(digits) == 10:
        return f"{default_country_code}{digits}"

    # Case 3: digits longer than 10 (may include country code w/o +, like 19995551234)
    if len(digits) > 10:
        # If it starts with "1" and is 11 digits, treat as US
        if digits.startswith("1") and len(digits) == 11:
            return f"+{digits}"
        # Otherwise treat it as an international number missing the "+" prefix
        return f"+{digits}"

    # If it's too short, invalid
    return None
