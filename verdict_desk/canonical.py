"""JSON canonico (RFC 8785, JCS) para o hash do dossie.

O mesmo texto canonico sai em Python e em JavaScript (`web/canonical.js`), entao
qualquer pessoa confere o hash no navegador sem rodar o nosso codigo:

- chaves ordenadas por unidade de codigo UTF-16 (como o `sort()` do JavaScript);
- numeros escritos como o ECMAScript escreve (100.0 vira 100, 5.8e-05 vira 0.000058);
- texto sem espacos, strings no escape de `JSON.stringify`.

Inteiros fora de +-2**53, NaN e infinito sao recusados: JSON de interoperabilidade
(I-JSON) nao os representa sem perda.
"""

import json
import math
from decimal import Decimal


def es_number(x) -> str:
    """Escreve um numero como `Number.prototype.toString` do ECMAScript."""
    if isinstance(x, bool):
        raise TypeError("bool is not a number")
    if isinstance(x, int):
        if abs(x) > 2**53:
            raise ValueError("integer outside the IEEE-754 safe range")
        x = float(x)
    if math.isnan(x) or math.isinf(x):
        raise ValueError("NaN and Infinity cannot be canonicalized")
    if x == 0:
        return "0"
    sign = "-" if x < 0 else ""
    parts = Decimal(repr(abs(x))).as_tuple()  # repr devolve os digitos mais curtos que fazem ida e volta
    n = len(parts.digits) + parts.exponent
    digits = "".join(map(str, parts.digits)).rstrip("0")
    k = len(digits)
    if k <= n <= 21:
        body = digits + "0" * (n - k)
    elif 0 < n <= 21:
        body = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        body = "0." + "0" * (-n) + digits
    else:
        exp = n - 1
        tail = ("e+" if exp >= 0 else "e-") + str(abs(exp))
        body = digits + tail if k == 1 else digits[0] + "." + digits[1:] + tail
    return sign + body


def _text(obj) -> str:
    if obj is None:
        return "null"
    if obj is True:
        return "true"
    if obj is False:
        return "false"
    if isinstance(obj, str):
        return json.dumps(obj, ensure_ascii=False)
    if isinstance(obj, (int, float)):
        return es_number(obj)
    if isinstance(obj, (list, tuple)):
        return "[" + ",".join(_text(item) for item in obj) + "]"
    if isinstance(obj, dict):
        keys = sorted(obj, key=lambda key: key.encode("utf-16-be"))
        return "{" + ",".join(json.dumps(key, ensure_ascii=False) + ":" + _text(obj[key]) for key in keys) + "}"
    raise TypeError(f"cannot canonicalize {type(obj).__name__}")


def canonical(obj) -> bytes:
    return _text(obj).encode("utf-8")
