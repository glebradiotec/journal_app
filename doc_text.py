"""Текст из .doc (Word 97–2003) на чистом Python, без внешних программ.

Запасной путь для pdf_parser: catdoc есть не везде (в Homebrew его нет), а штатный textutil
в macOS отдельные документы прочитать не может и молча возвращает двоичный мусор.

Формат: составной файл OLE2 (потоки «WordDocument» и «0Table»/«1Table»). Текст лежит кусками,
их таблица (piece table) — в структуре Clx табличного потока; кусок либо UTF-16, либо
однобайтный cp1252. Берём только основной текст документа (без сносок и колонтитулов).

Мягкий перенос строки (0x0B) помечается так же, как в pdf_parser для textutil: строка после
него начинается знаком U+2028, и такая же метка стоит в самом конце текста.
"""
import struct

_FREE = 0xFFFFFFFF
_END = 0xFFFFFFFE
SOFT_BREAK_MARK = " "


class DocFormatError(Exception):
    pass


def _streams(data):
    """Потоки составного файла OLE2 -> {имя: bytes}."""
    if data[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise DocFormatError("не файл Word 97–2003")
    sec_size = 1 << struct.unpack_from("<H", data, 0x1E)[0]
    mini_size = 1 << struct.unpack_from("<H", data, 0x20)[0]
    dir_start, = struct.unpack_from("<I", data, 0x30)
    mini_cutoff, mini_fat_start, n_mini_fat, difat_start, n_difat = struct.unpack_from("<5I", data, 0x38)

    def sector(n):
        off = (n + 1) * sec_size
        return data[off:off + sec_size]

    per_sector = sec_size // 4
    difat = [s for s in struct.unpack_from("<109I", data, 0x4C) if s not in (_FREE, _END)]
    nxt = difat_start
    for _ in range(n_difat):
        if nxt in (_FREE, _END):
            break
        entries = struct.unpack(f"<{per_sector}I", sector(nxt))
        difat += [s for s in entries[:-1] if s not in (_FREE, _END)]
        nxt = entries[-1]
    fat = []
    for s in difat:
        fat += struct.unpack(f"<{per_sector}I", sector(s))

    def chain(start, table, limit):
        out, seen = [], 0
        while start not in (_FREE, _END) and start < len(table) and seen < limit:
            out.append(start)
            start = table[start]
            seen += 1
        return out

    def read(start, size=None):
        buf = b"".join(sector(s) for s in chain(start, fat, len(fat) + 1))
        return buf if size is None else buf[:size]

    directory = read(dir_start)
    raw_entries = []
    for off in range(0, len(directory) - 127, 128):
        e = directory[off:off + 128]
        name_len, kind = struct.unpack_from("<HB", e, 0x40)
        name = e[:max(name_len - 2, 0)].decode("utf-16-le", errors="replace")
        left, right, child = struct.unpack_from("<III", e, 0x44)
        start, size = struct.unpack_from("<IQ", e, 0x74)
        if sec_size == 512:
            size &= 0xFFFFFFFF
        raw_entries.append((name, kind, start, size, left, right, child))

    # Нужны только потоки верхнего уровня: у встроенных в документ объектов Word (формулы,
    # вставки) свои «WordDocument»/«1Table» в подпапках — их брать нельзя.
    root = raw_entries[0] if raw_entries and raw_entries[0][1] == 5 else None
    entries, todo, seen = [], [root[6]] if root else [], set()
    while todo:
        i = todo.pop()
        if i in (_FREE, _END) or i >= len(raw_entries) or i in seen:
            continue
        seen.add(i)
        name, kind, start, size, left, right, _child = raw_entries[i]
        entries.append((name, kind, start, size))
        todo += [left, right]
    mini_stream = read(root[2], root[3]) if root else b""
    mini_fat_raw = read(mini_fat_start) if n_mini_fat else b""
    mini_fat = list(struct.unpack(f"<{len(mini_fat_raw) // 4}I", mini_fat_raw)) if mini_fat_raw else []

    out = {}
    for name, kind, start, size in entries:
        if kind != 2:
            continue
        if size < mini_cutoff:
            buf = b"".join(mini_stream[s * mini_size:(s + 1) * mini_size]
                           for s in chain(start, mini_fat, len(mini_fat) + 1))
            out[name] = buf[:size]
        else:
            out[name] = read(start, size)
    return out


def _pieces(table, fc_clx, lcb_clx):
    """Clx -> [(cp_start, cp_end, смещение в WordDocument, сжатый?)]."""
    clx = table[fc_clx:fc_clx + lcb_clx]
    pos = 0
    while pos < len(clx) and clx[pos] == 0x01:  # Prc — описания свойств, пропускаем
        pos += 3 + struct.unpack_from("<H", clx, pos + 1)[0]
    if pos >= len(clx) or clx[pos] != 0x02:
        raise DocFormatError("не найдена таблица кусков текста")
    lcb, = struct.unpack_from("<I", clx, pos + 1)
    plc = clx[pos + 5:pos + 5 + lcb]
    n = (len(plc) - 4) // 12
    cps = struct.unpack_from(f"<{n + 1}I", plc, 0)
    out = []
    for i in range(n):
        fc, = struct.unpack_from("<I", plc, (n + 1) * 4 + i * 8 + 2)
        compressed = bool(fc & 0x40000000)
        fc &= 0x3FFFFFFF
        out.append((cps[i], cps[i + 1], fc // 2 if compressed else fc, compressed))
    return out


# Знак, вставленный через «Вставка → Символ», в тексте стоит заглушкой, а сам знак записан в
# свойствах символа (sprmCSymbol). Шрифт у таких знаков почти всегда Symbol: его коды ниже
# переведены в Юникод. Что не знаем — оставляем как есть.
_SPRM_CSYMBOL = 0x6A09
_SYMBOL_FONT = {
    0x2D: "−", 0xB1: "±", 0xB4: "×", 0xB8: "÷", 0xD7: "⋅", 0xB7: "•", 0xA3: "≤", 0xB3: "≥", 0xB9: "≠",
    0xBB: "≈", 0xBA: "≡", 0xA5: "∞", 0xB5: "∝", 0xB6: "∂", 0xD1: "∇", 0xD6: "√", 0xF2: "∫", 0xE5: "∑",
    0xD5: "∏", 0xCE: "∈", 0xCF: "∉", 0xC7: "∩", 0xC8: "∪", 0xCC: "⊂", 0xCD: "⊆", 0xC6: "∅", 0xD0: "∠",
    0xAE: "→", 0xAC: "←", 0xAD: "↑", 0xAF: "↓", 0xAB: "↔", 0xDE: "⇒", 0xDB: "⇔", 0xB0: "°", 0xA2: "′",
    0xB2: "″", 0xBC: "…", 0xC4: "⊗", 0xC5: "⊕", 0xD8: "¬", 0xD9: "∧", 0xDA: "∨", 0x22: "∀", 0x24: "∃",
    0x40: "≅", 0x5C: "∴", 0x5E: "⊥", 0x7E: "∼", 0xE0: "◊", 0xE1: "〈", 0xF1: "〉", 0xA0: "€",
    0x41: "Α", 0x42: "Β", 0x47: "Γ", 0x44: "Δ", 0x45: "Ε", 0x5A: "Ζ", 0x48: "Η", 0x51: "Θ", 0x49: "Ι",
    0x4B: "Κ", 0x4C: "Λ", 0x4D: "Μ", 0x4E: "Ν", 0x58: "Ξ", 0x4F: "Ο", 0x50: "Π", 0x52: "Ρ", 0x53: "Σ",
    0x54: "Τ", 0x55: "Υ", 0x46: "Φ", 0x43: "Χ", 0x59: "Ψ", 0x57: "Ω",
    0x61: "α", 0x62: "β", 0x67: "γ", 0x64: "δ", 0x65: "ε", 0x7A: "ζ", 0x68: "η", 0x71: "θ", 0x69: "ι",
    0x6B: "κ", 0x6C: "λ", 0x6D: "μ", 0x6E: "ν", 0x78: "ξ", 0x6F: "ο", 0x70: "π", 0x72: "ρ", 0x73: "σ",
    0x74: "τ", 0x75: "υ", 0x66: "φ", 0x63: "χ", 0x79: "ψ", 0x77: "ω", 0x4A: "ϑ", 0x6A: "ϕ", 0x76: "ϖ",
    0x56: "ς",
}
_SPRM_SIZE = {0: 1, 1: 1, 2: 2, 3: 4, 4: 2, 5: 2, 7: 3}


def _symbol_runs(word, table):
    """-> [(fc_start, fc_end, знак)] — участки файла, где символ задан через sprmCSymbol."""
    fc_plc, lcb_plc = struct.unpack_from("<II", word, 0x00FA)  # PlcfBteChpx
    if not lcb_plc:
        return []
    plc = table[fc_plc:fc_plc + lcb_plc]
    n = (len(plc) - 4) // 8
    runs = []
    for pn in struct.unpack_from(f"<{n}I", plc, (n + 1) * 4):
        page = word[(pn & 0x3FFFFF) * 512:(pn & 0x3FFFFF) * 512 + 512]
        if len(page) < 512:
            continue
        crun = page[511]
        fcs = struct.unpack_from(f"<{crun + 1}I", page, 0)
        for k in range(crun):
            off = page[(crun + 1) * 4 + k] * 2
            if not off:
                continue
            grpprl = page[off + 1:off + 1 + page[off]]
            pos = 0
            while pos + 2 <= len(grpprl):
                sprm, = struct.unpack_from("<H", grpprl, pos)
                pos += 2
                spra = sprm >> 13
                size = _SPRM_SIZE.get(spra)
                if size is None:  # spra == 6: длина в первом байте операнда
                    if pos >= len(grpprl):
                        break
                    size = grpprl[pos] + 1
                if sprm == _SPRM_CSYMBOL and pos + 4 <= len(grpprl):
                    _ftc, xchar = struct.unpack_from("<HH", grpprl, pos)
                    code = xchar & 0xFF if (xchar & 0xFF00) == 0xF000 else xchar
                    ch = _SYMBOL_FONT.get(code) if code < 0x100 else chr(xchar)
                    if ch:
                        runs.append((fcs[k], fcs[k + 1], ch))
                pos += size
    return runs


def extract_text(path):
    """Основной текст документа. Абзацы разделены «\\n»; коды полей Word (HYPERLINK, PAGEREF…)
    убраны, видимый результат поля оставлен."""
    with open(path, "rb") as f:
        data = f.read()
    streams = _streams(data)
    word = streams.get("WordDocument")
    if not word or len(word) < 0x1AA:
        raise DocFormatError("нет потока WordDocument")
    flags, = struct.unpack_from("<H", word, 0x0A)
    if flags & 0x0100:
        raise DocFormatError("документ зашифрован")
    table = streams.get("1Table" if flags & 0x0200 else "0Table")
    if table is None:
        raise DocFormatError("нет табличного потока")
    ccp_text, = struct.unpack_from("<I", word, 0x4C)
    fc_clx, lcb_clx = struct.unpack_from("<II", word, 0x01A2)

    try:
        symbols = _symbol_runs(word, table)
    except (struct.error, IndexError):
        symbols = []

    chars = []
    for cp_start, cp_end, off, compressed in _pieces(table, fc_clx, lcb_clx):
        if cp_start >= ccp_text:
            break
        n = min(cp_end, ccp_text) - cp_start
        width = 1 if compressed else 2
        piece = list(word[off:off + n * width].decode("cp1252" if compressed else "utf-16-le", errors="replace"))
        for fc0, fc1, ch in symbols:
            lo, hi = max(fc0, off), min(fc1, off + n * width)
            for fc in range(lo, hi, width):
                k = (fc - off) // width
                if 0 <= k < len(piece) and piece[k] == "(":  # заглушка Word на месте знака
                    piece[k] = ch
        chars.append("".join(piece))
    raw = "".join(chars)

    out = []
    stack = []  # для вложенных полей: True — сейчас идёт код поля (до 0x14), False — его результат
    for ch in raw:
        code = ord(ch)
        if code == 0x13:
            stack.append(True)
            continue
        if code == 0x14:
            if stack:
                stack[-1] = False
            continue
        if code == 0x15:
            if stack:
                stack.pop()
            continue
        if any(stack):
            continue
        if 0xF020 <= code <= 0xF0FF:  # знак шрифта Symbol, записанный напрямую
            out.append(_SYMBOL_FONT.get(code & 0xFF, ch))
            continue
        if code == 0x0D or code == 0x07:
            out.append("\n")
        elif code == 0x0B:
            out.append("\n" + SOFT_BREAK_MARK)
        elif code == 0x0C:
            out.append("\n")
        elif code == 0x1E:
            out.append("-")
        elif code in (0x1F, 0x01, 0x08, 0x02, 0x05):
            continue
        elif code < 0x20 and code != 0x09:
            continue
        else:
            out.append(ch)
    text = "".join(out).strip()
    if not text:
        raise DocFormatError("в документе нет текста")
    return text + "\n" + SOFT_BREAK_MARK
