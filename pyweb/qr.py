"""A small QR code encoder for the terminal (``pyweb dev`` prints one so a phone can open the page).

Byte mode, error correction level M, versions 1 to 10 (up to 213 bytes: any URL you'd print).
``matrix(text)`` gives the modules as rows of booleans (True is dark); ``terminal(text)`` draws them
with half blocks, two rows per line, in explicit black and white so it scans on light and dark themes.
"""

from __future__ import annotations

# version -> (EC codewords per block, [(blocks, data codewords per block), ...]) for level M
_BLOCKS = {
    1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]), 5: (24, [(2, 43)]),
    6: (16, [(4, 27)]), 7: (18, [(4, 31)]), 8: (22, [(2, 38), (2, 39)]), 9: (22, [(3, 36), (2, 37)]),
    10: (26, [(4, 43), (1, 44)]),
}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42],
          9: [6, 26, 46], 10: [6, 28, 50]}
_LEVEL_M = 0b00

# ---------------------------------------------------------------- Reed-Solomon over GF(256)

_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a, b):
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _generator(degree):
    poly = [1]
    for i in range(degree):
        nxt = [0] * (len(poly) + 1)
        for j, c in enumerate(poly):
            nxt[j] ^= c
            nxt[j + 1] ^= _mul(c, _EXP[i])
        poly = nxt
    return poly


def _ec(data, degree):
    gen = _generator(degree)
    rem = list(data) + [0] * degree
    for i in range(len(data)):
        coef = rem[i]
        if coef:
            for j in range(1, len(gen)):
                rem[i + j] ^= _mul(gen[j], coef)
    return rem[len(data):]


# ---------------------------------------------------------------- data

def _capacity(version):
    _ec_len, groups = _BLOCKS[version]
    return sum(n * k for n, k in groups)


def _codewords(data, version):
    count_bits = 8 if version < 10 else 16
    bits = [0, 1, 0, 0] + [(len(data) >> i) & 1 for i in range(count_bits - 1, -1, -1)]
    for byte in data:
        bits += [(byte >> i) & 1 for i in range(7, -1, -1)]
    cap = _capacity(version) * 8
    bits += [0] * min(4, cap - len(bits))
    bits += [0] * (-len(bits) % 8)
    out = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(out) < cap // 8:
        out.append(pad)
        pad ^= 0xEC ^ 0x11
    ec_len, groups = _BLOCKS[version]
    blocks, pos = [], 0
    for n, k in groups:
        for _ in range(n):
            blocks.append(out[pos:pos + k])
            pos += k
    ecs = [_ec(b, ec_len) for b in blocks]
    result = []
    for i in range(max(len(b) for b in blocks)):
        result += [b[i] for b in blocks if i < len(b)]
    for i in range(ec_len):
        result += [e[i] for e in ecs]
    return result


# ---------------------------------------------------------------- the symbol

class _Symbol:
    def __init__(self, version):
        self.version = version
        self.size = version * 4 + 17
        self.dark = [[False] * self.size for _ in range(self.size)]
        self.fixed = [[False] * self.size for _ in range(self.size)]

    def put(self, x, y, dark):
        self.dark[y][x] = dark
        self.fixed[y][x] = True

    def functions(self):
        n = self.size
        for i in range(n):
            self.put(6, i, i % 2 == 0)
            self.put(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        self.put(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = _ALIGN[self.version]
        for i, ax in enumerate(pos):
            for j, ay in enumerate(pos):
                if (i, j) in ((0, 0), (0, len(pos) - 1), (len(pos) - 1, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.put(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.format(0)
        if self.version >= 7:
            rem = self.version
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            bits = self.version << 12 | rem
            for i in range(18):
                bit = (bits >> i) & 1 == 1
                a, b = n - 11 + i % 3, i // 3
                self.put(a, b, bit)
                self.put(b, a, bit)

    def format(self, mask):
        n = self.size
        data = _LEVEL_M << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        bit = [(bits >> i) & 1 == 1 for i in range(15)]
        for i in range(6):
            self.put(8, i, bit[i])
        self.put(8, 7, bit[6])
        self.put(8, 8, bit[7])
        self.put(7, 8, bit[8])
        for i in range(9, 15):
            self.put(14 - i, 8, bit[i])
        for i in range(8):
            self.put(n - 1 - i, 8, bit[i])
        for i in range(8, 15):
            self.put(8, n - 15 + i, bit[i])
        self.put(8, n - 8, True)

    def place(self, codewords):
        n = self.size
        i, total = 0, len(codewords) * 8
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(n):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = n - 1 - vert if upward else vert
                    if not self.fixed[y][x] and i < total:
                        self.dark[y][x] = (codewords[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def masked(self, mask):
        rule = _MASKS[mask]
        out = [row[:] for row in self.dark]
        for y in range(self.size):
            for x in range(self.size):
                if not self.fixed[y][x] and rule(x, y):
                    out[y][x] = not out[y][x]
        return out


_MASKS = [
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
]


def _penalty(m):
    n = len(m)
    score = 0
    lines = m + [list(col) for col in zip(*m)]
    finder = [True, False, True, True, True, False, True]
    for line in lines:
        run, prev = 0, None
        for v in line:
            if v == prev:
                run += 1
            else:
                if run >= 5:
                    score += 3 + run - 5
                run, prev = 1, v
        if run >= 5:
            score += 3 + run - 5
        padded = [False] * 4 + line + [False] * 4
        for i in range(len(padded) - 6):
            if padded[i:i + 7] == finder and (not any(padded[max(0, i - 4):i]) or not any(padded[i + 7:i + 11])):
                score += 40
    for y in range(n - 1):
        for x in range(n - 1):
            if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark = sum(map(sum, m))
    total = n * n
    k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
    return score + max(0, k) * 10


def matrix(text, *, version=None, mask=None):
    """The QR code for ``text`` (str or bytes) as rows of booleans, True for a dark module."""
    data = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    if version is None:
        for v in range(1, 11):
            if len(data) <= _capacity(v) - (2 if v < 10 else 3):
                version = v
                break
        else:
            raise ValueError(f"too long for a QR code here ({len(data)} bytes; at most 213)")
    elif len(data) > _capacity(version) - (2 if version < 10 else 3):
        raise ValueError(f"{len(data)} bytes don't fit in version {version}")
    sym = _Symbol(version)
    sym.functions()
    sym.place(_codewords(data, version))
    best = None
    for m in ([mask] if mask is not None else range(8)):
        sym.format(m)
        candidate = sym.masked(m)
        score = _penalty(candidate)
        if best is None or score < best[0]:
            best = (score, m, candidate)
    sym.format(best[1])
    return sym.masked(best[1])


def terminal(text, *, border=2):
    """``text`` as a QR code drawn with half blocks (black on white), one string with newlines."""
    m = matrix(text)
    n = len(m)
    rows = [[False] * (n + 2 * border) for _ in range(border)]
    rows += [[False] * border + row + [False] * border for row in m]
    rows += [[False] * (n + 2 * border) for _ in range(border)]
    if len(rows) % 2:
        rows.append([False] * (n + 2 * border))
    lines = []
    for top, bottom in zip(rows[0::2], rows[1::2]):
        cells = []
        for t, b in zip(top, bottom):
            fg = "\x1b[38;5;16m" if t else "\x1b[38;5;231m"
            bg = "\x1b[48;5;16m" if b else "\x1b[48;5;231m"
            cells.append(fg + bg + "▀")
        lines.append("".join(cells) + "\x1b[0m")
    return "\n".join(lines)
