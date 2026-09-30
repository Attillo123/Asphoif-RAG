"""Minimal CID/ToUnicode PDF text extractor (stdlib only)."""
import re
import sys
import zlib

path = sys.argv[1]
out_path = sys.argv[2]
data = open(path, "rb").read()

# ---- 1. Parse all indirect objects (handles plain objects, not ObjStm) ----
objs = {}
for m in re.finditer(rb"(\d+)\s+0\s+obj(.*?)endobj", data, re.S):
    objs[int(m.group(1))] = m.group(2)

print("objects:", len(objs), file=sys.stderr)


def get_stream(body: bytes):
    m = re.search(rb"stream\r?\n", body)
    if not m:
        return None
    s = m.end()
    e = body.find(b"endstream", s)
    raw = body[s:e]
    if b"FlateDecode" in body[: m.start()]:
        try:
            return zlib.decompress(raw)
        except Exception:
            try:
                return zlib.decompressobj().decompress(raw)
            except Exception:
                return None
    return raw


# ---- 2. Build ToUnicode CMaps keyed by font object number ----
def parse_cmap(content: bytes):
    table = {}
    for m in re.finditer(rb"beginbfchar(.*?)endbfchar", content, re.S):
        for pair in re.finditer(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", m.group(1)):
            src = int(pair.group(1), 16)
            dst_hex = pair.group(2).decode()
            try:
                table[src] = bytes.fromhex(dst_hex).decode("utf-16-be", errors="replace")
            except Exception:
                pass
    for m in re.finditer(rb"beginbfrange(.*?)endbfrange", content, re.S):
        block = m.group(1)
        for r in re.finditer(
            rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<([0-9A-Fa-f]+)>|\[(.*?)\])",
            block,
            re.S,
        ):
            lo = int(r.group(1), 16)
            hi = int(r.group(2), 16)
            if r.group(4):
                base = bytes.fromhex(r.group(4).decode()).decode("utf-16-be", errors="replace")
                if len(base) == 1:
                    for i in range(hi - lo + 1):
                        table[lo + i] = chr(ord(base) + i)
                else:
                    for i in range(hi - lo + 1):
                        table[lo + i] = base
            elif r.group(5):
                items = re.findall(rb"<([0-9A-Fa-f]+)>", r.group(5))
                for i, it in enumerate(items):
                    table[lo + i] = bytes.fromhex(it.decode()).decode("utf-16-be", errors="replace")
    return table


font_cmaps = {}  # font obj num -> table
font_refs = {}  # resource name (/F5) -> font obj num

for num, body in objs.items():
    if b"/Font" not in body and b"/ToUnicode" not in body:
        continue
    tm = re.search(rb"/ToUnicode\s+(\d+)\s+0\s+R", body)
    if tm:
        target = int(tm.group(1))
        st = get_stream(objs.get(target, b""))
        if st:
            font_cmaps[num] = parse_cmap(st)

print("fonts with cmap:", len(font_cmaps), file=sys.stderr)

# Map resource names: find page resources /Font << /F5 12 0 R >>
for num, body in objs.items():
    for m in re.finditer(rb"/(F\d+)\s+(\d+)\s+0\s+R", body):
        name = m.group(1).decode()
        font_refs.setdefault(name, int(m.group(2)))

print("font refs:", font_refs, file=sys.stderr)

# ---- 3. Walk content streams, track font + position ----
items = []  # (y, x, text)
for num, body in objs.items():
    if b"/Contents" in body and b"stream" not in body:
        continue
    content = get_stream(body)
    if not content or (b"Tj" not in content and b"TJ" not in content):
        continue

    cur_font = None
    cur_x = cur_y = 0.0
    for m in re.finditer(
        rb"/(F\d+)\s+[\d.]+\s+Tf"
        rb"|([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+Tm"
        rb"|([-\d.]+)\s+([-\d.]+)\s+Td"
        rb"|<([0-9A-Fa-f]+)>\s*Tj"
        rb"|<([0-9A-Fa-f]+)>\s*TJ"
        rb"|\[(.*?)\]\s*TJ",
        content,
        re.S,
    ):
        g = m.groups()
        if g[0]:
            cur_font = g[0].decode()
        elif g[1] is not None:
            cur_x, cur_y = float(g[5]), float(g[6])
        elif g[7] is not None:
            cur_x += float(g[7])
            cur_y += float(g[8])
        elif g[9] is not None or g[10] is not None:
            hexs = g[9] or g[10]
            table = font_cmaps.get(font_refs.get(cur_font, -1), {})
            text = "".join(
                table.get(int(hexs[i : i + 4], 16), "")
                for i in range(0, len(hexs), 4)
            )
            if text:
                items.append((round(cur_y, 1), round(cur_x, 1), text))
        elif g[11] is not None:
            table = font_cmaps.get(font_refs.get(cur_font, -1), {})
            parts = []
            for hm in re.finditer(rb"<([0-9A-Fa-f]+)>", g[11]):
                h = hm.group(1)
                parts.append(
                    "".join(
                        table.get(int(h[i : i + 4], 16), "") for i in range(0, len(h), 4)
                    )
                )
            text = "".join(parts)
            if text:
                items.append((round(cur_y, 1), round(cur_x, 1), text))

print("text items:", len(items), file=sys.stderr)

# ---- 4. Group by line (y), sort by x ----
lines = {}
for y, x, t in items:
    key = round(y / 6.0)
    lines.setdefault(key, []).append((x, t))

out_lines = []
for key in sorted(lines):
    parts = sorted(lines[key], key=lambda p: p[0])
    merged = ""
    prev_x = None
    for x, t in parts:
        if prev_x is not None and x - prev_x > 30:
            merged += "    "
        merged += t
        prev_x = x
    out_lines.append(merged.rstrip())

text = "\n".join(out_lines)
open(out_path, "w", encoding="utf-8").write(text)
cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
print("chars:", len(text), "CJK:", cjk, file=sys.stderr)
