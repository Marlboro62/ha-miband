"""ECDH sur la courbe binaire NIST B-163 (sect163r2), telle qu'utilisée par Zepp OS.

Implémentation autonome en entiers Python (corps GF(2^163)), compatible octet
pour octet avec le format attendu par les bracelets Huami/Zepp OS :
- clé privée : 24 octets little-endian ;
- clé publique / secret partagé : 48 octets = X (24 octets LE) || Y (24 octets LE).

Les bibliothèques cryptographiques récentes ont retiré les courbes binaires,
d'où cette implémentation pure (le calcul ne sert qu'à l'échange de clé de
session BLE, pas à protéger un secret à long terme).
"""

from __future__ import annotations

import os

CURVE_DEGREE = 163
KEY_SIZE = 24  # octets par coordonnée
PUB_KEY_SIZE = 2 * KEY_SIZE

# f(x) = x^163 + x^7 + x^6 + x^3 + 1
_POLY = (1 << 163) | 0xC9
_A = 1


def _words_to_int(words: list[int]) -> int:
    value = 0
    for idx, word in enumerate(words):
        value |= word << (32 * idx)
    return value


_B = _words_to_int([0x4A3205FD, 0x512F7874, 0x1481EB10, 0xB8C953CA, 0x0A601907, 0x00000002])
_GX = _words_to_int([0xE8343E36, 0xD4994637, 0xA0991168, 0x86A2D57E, 0xF0EBA162, 0x00000003])
_GY = _words_to_int([0x797324F1, 0xB11C5C0C, 0xA2CDD545, 0x71A0094F, 0xD51FBC6C, 0x00000000])
ORDER = _words_to_int([0xA4234C33, 0x77E70C12, 0x000292FE, 0x00000000, 0x00000000, 0x00000004])

Point = tuple[int, int] | None  # None = point à l'infini


def _fmul(a: int, b: int) -> int:
    """Multiplication dans GF(2^163)."""
    result = 0
    while b:
        if b & 1:
            result ^= a
        b >>= 1
        a <<= 1
        if a >> CURVE_DEGREE:
            a ^= _POLY
    return result


def _fsqr(a: int) -> int:
    return _fmul(a, a)


def _finv(a: int) -> int:
    """Inverse par l'algorithme d'Euclide étendu sur les polynômes binaires."""
    if a == 0:
        raise ZeroDivisionError("inverse de zéro")
    u, v = a, _POLY
    g1, g2 = 1, 0
    while u != 1:
        j = u.bit_length() - v.bit_length()
        if j < 0:
            u, v = v, u
            g1, g2 = g2, g1
            j = -j
        u ^= v << j
        g1 ^= g2 << j
    return g1


def on_curve(point: Point) -> bool:
    """Vérifie y² + xy = x³ + a·x² + b."""
    if point is None:
        return False
    x, y = point
    if x >> CURVE_DEGREE or y >> CURVE_DEGREE:
        return False
    x2 = _fsqr(x)
    left = _fsqr(y) ^ _fmul(x, y)
    right = _fmul(x2, x) ^ (x2 if _A else 0) ^ _B
    return left == right


def _double(point: Point) -> Point:
    if point is None:
        return None
    x, y = point
    if x == 0:
        return None
    lam = x ^ _fmul(y, _finv(x))
    x3 = _fsqr(lam) ^ lam ^ _A
    y3 = _fsqr(x) ^ _fmul(lam ^ 1, x3)
    return (x3, y3)


def _add(p: Point, q: Point) -> Point:
    if p is None:
        return q
    if q is None:
        return p
    x1, y1 = p
    x2, y2 = q
    if x1 == x2:
        if y1 == y2:
            return _double(p)
        return None
    lam = _fmul(y1 ^ y2, _finv(x1 ^ x2))
    x3 = _fsqr(lam) ^ lam ^ x1 ^ x2 ^ _A
    y3 = _fmul(lam, x1 ^ x3) ^ x3 ^ y1
    return (x3, y3)


def scalar_mult(k: int, point: Point) -> Point:
    """Multiplication scalaire (double-and-add, bit de poids fort d'abord)."""
    result: Point = None
    for bit in range(k.bit_length() - 1, -1, -1):
        result = _double(result)
        if (k >> bit) & 1:
            result = _add(result, point)
    return result


GENERATOR: Point = (_GX, _GY)


def _clamp_private(private: bytes) -> int:
    """Même contrainte que tiny-ECDH : efface les bits >= degré(ordre) - 1."""
    k = int.from_bytes(private, "little")
    return k & ((1 << (ORDER.bit_length() - 1)) - 1)


def encode_point(point: Point) -> bytes:
    if point is None:
        return bytes(PUB_KEY_SIZE)
    x, y = point
    return x.to_bytes(KEY_SIZE, "little") + y.to_bytes(KEY_SIZE, "little")


def decode_point(data: bytes) -> Point:
    if len(data) != PUB_KEY_SIZE:
        raise ValueError(f"clé publique de {len(data)} octets, 48 attendus")
    return (
        int.from_bytes(data[:KEY_SIZE], "little"),
        int.from_bytes(data[KEY_SIZE:], "little"),
    )


def generate_private_key() -> bytes:
    """Clé privée aléatoire, assez grande (comme tiny-ECDH)."""
    while True:
        private = os.urandom(KEY_SIZE)
        if int.from_bytes(private, "little").bit_length() >= CURVE_DEGREE // 2:
            return private


def public_key(private: bytes) -> bytes:
    return encode_point(scalar_mult(_clamp_private(private), GENERATOR))


def shared_secret(private: bytes, remote_public: bytes) -> bytes:
    remote = decode_point(remote_public)
    if remote == (0, 0) or not on_curve(remote):
        raise ValueError("clé publique distante invalide (hors courbe)")
    return encode_point(scalar_mult(_clamp_private(private), remote))
