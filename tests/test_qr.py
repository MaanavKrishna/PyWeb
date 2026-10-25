"""The terminal QR code (pyweb dev prints one for the LAN or share URL)."""

import hashlib

import pytest

from pyweb import qr


def _blocks_ok(version, data):
    """Every Reed-Solomon block's syndromes are zero (the error correction is right)."""
    cw = qr._codewords(data, version)
    ec_len, groups = qr._BLOCKS[version]
    sizes = [k for n, k in groups for _ in range(n)]
    total = sum(sizes)
    blocks = [[] for _ in sizes]
    i = 0
    for col in range(max(sizes)):                 # undo the interleaving
        for b, k in enumerate(sizes):
            if col < k:
                blocks[b].append(cw[i])
                i += 1
    ecs = [[] for _ in sizes]
    for _col in range(ec_len):
        for b in range(len(sizes)):
            ecs[b].append(cw[i])
            i += 1
    assert i == len(cw) == total + ec_len * len(sizes)
    for block, ec in zip(blocks, ecs):
        word = block + ec
        for s in range(ec_len):
            acc = 0
            for c in word:
                acc = qr._mul(acc, qr._EXP[s]) ^ c
            assert acc == 0


@pytest.mark.parametrize("version", range(1, 11))
def test_error_correction(version):
    cap = qr._capacity(version) - (2 if version < 10 else 3)
    _blocks_ok(version, b"x" * cap)
    _blocks_ok(version, b"http://192.168.1.20:8000/"[:cap])


def _format_bits(m):
    n = len(m)
    bits = [m[i][8] for i in range(6)] + [m[7][8], m[8][8], m[8][7]] + [m[8][14 - i] for i in range(9, 15)]
    second = [m[8][n - 1 - i] for i in range(8)] + [m[n - 15 + i][8] for i in range(8, 15)]
    return sum(b << i for i, b in enumerate(bits)), sum(b << i for i, b in enumerate(second))


@pytest.mark.parametrize("mask", range(8))
def test_format_information_is_written_twice_and_checks(mask):
    m = qr.matrix("https://example.com/", mask=mask)
    a, b = _format_bits(m)
    assert a == b
    value = a ^ 0x5412
    assert value >> 10 == mask                     # level M is 00
    rem = value
    for i in range(14, 9, -1):
        if rem >> i & 1:
            rem ^= 0x537 << (i - 10)
    assert rem == 0


def test_versions_grow_with_the_text_and_have_finder_patterns():
    assert len(qr.matrix("http://localhost:8000/")) == 25           # version 2
    assert len(qr.matrix("x" * 213)) == 57                          # version 10
    with pytest.raises(ValueError):
        qr.matrix("x" * 214)
    m = qr.matrix("hello")
    for x0, y0 in ((0, 0), (len(m) - 7, 0), (0, len(m) - 7)):
        assert all(m[y0][x0 + i] and m[y0 + 6][x0 + i] for i in range(7))
        assert m[y0 + 3][x0 + 3] and not m[y0 + 1][x0 + 1]


GOLDEN = "1a0aae436d78c61c"     # checked with OpenCV's decoder when it was written


def test_output_is_stable():
    m = qr.matrix("http://192.168.1.20:8000/")
    bits = "".join("1" if v else "0" for row in m for v in row)
    assert hashlib.sha256(bits.encode()).hexdigest()[:16] == GOLDEN


def test_terminal_drawing():
    text = qr.terminal("hi", border=2)
    lines = text.split("\n")
    assert len(lines) == (21 + 4 + 1) // 2 and all(line.endswith("\x1b[0m") for line in lines)
    assert lines[0].count("▀") == 25


def test_a_real_decoder_reads_it():
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    for url in ("http://192.168.1.20:8000/", "https://quiet-river-1234.trycloudflare.com/?t=" + "k" * 43):
        m = qr.matrix(url)
        img = np.full(((len(m) + 8) * 6,) * 2, 255, np.uint8)
        for y, row in enumerate(m):
            for x, dark in enumerate(row):
                if dark:
                    img[(y + 4) * 6:(y + 5) * 6, (x + 4) * 6:(x + 5) * 6] = 0
        assert cv2.QRCodeDetector().detectAndDecode(img)[0] == url
