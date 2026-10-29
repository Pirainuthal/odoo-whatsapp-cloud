# Copyright 2026 Pirainuthal S (https://github.com/Pirainuthal)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Dependency-free phone number normalisation to E.164.

WhatsApp identifies users by their full international number. Numbers typed
into Odoo come in every shape imaginable (``098765 43210``, ``+91-98765-43210``,
``0091 98765 43210`` ...), so before sending or matching we reduce them to a
canonical ``+<country code><national number>`` string.

The rules are deliberately simple and predictable rather than exhaustive
(no carrier metadata is needed):

* everything except digits and a leading ``+`` is stripped;
* ``00`` is treated as the international call prefix and replaced by ``+``;
* a number starting with ``+`` is already international;
* otherwise a single national trunk ``0`` is dropped and the default country
  calling code is prepended - unless the digits already start with that
  country code and are long enough to contain a full national number;
* the result must contain 8 to 15 digits (E.164 limits).
"""

import re

_NON_DIGIT = re.compile(r"\D")
E164_MIN_DIGITS = 8
E164_MAX_DIGITS = 15
# Shortest national significant number we accept when guessing whether the
# country code is already present (e.g. "9198765..." with default code 91).
_MIN_NATIONAL_DIGITS = 7


def normalize_phone(number, default_country_code=None):
    """Return ``number`` in E.164 format (``+919876543210``) or ``None``.

    :param str number: raw phone number as typed by a user or sent by Meta.
    :param default_country_code: calling code used for national numbers,
        e.g. ``91`` or ``"+91"``. When missing, only numbers that are already
        international can be normalised.
    """
    if not number:
        return None
    raw = str(number).strip()
    international = raw.startswith("+")
    digits = _NON_DIGIT.sub("", raw)
    if not digits:
        return None

    if not international and digits.startswith("00"):
        digits = digits[2:]
        international = True

    if not international:
        country = _NON_DIGIT.sub("", str(default_country_code or ""))
        if digits.startswith("0"):
            # National trunk prefix: 098765 43210 -> 9876543210
            digits = digits[1:]
            if not country:
                return None
            digits = country + digits
        elif (
            country
            and digits.startswith(country)
            and (len(digits) - len(country) >= _MIN_NATIONAL_DIGITS and len(digits) > 10)
        ):
            # Country code typed without "+": 919876543210
            pass
        elif country:
            digits = country + digits
        elif len(digits) <= 10:
            # Looks national but we have no country to attach it to.
            return None

    if not E164_MIN_DIGITS <= len(digits) <= E164_MAX_DIGITS:
        return None
    return "+" + digits


def to_wa_id(e164_number):
    """Meta's ``wa_id`` is the E.164 number without the leading ``+``."""
    return (e164_number or "").lstrip("+")
