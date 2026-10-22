"""A tiny text-PDF writer for test fixtures (no dependencies). Lines are drawn top to bottom in Helvetica; a blank
entry starts a new paragraph gap. Good enough for pypdf to extract text from, which is what the builder needs."""


def make_pdf(lines, per_page=45):
    def esc(t):
        return t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    pages = [lines[i:i + per_page] for i in range(0, max(len(lines), 1), per_page)]
    objs = []                                           # 1 catalog, 2 pages, 3 font, then page/content pairs
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(len(pages)))
    objs.append("<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>")
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    for i, pg in enumerate(pages):
        body = ["BT", "/F1 10 Tf", "13 TL", "40 800 Td"]
        for ln in pg:
            body.append(f"({esc(ln)}) Tj T*")
        body.append("ET")
        stream = "\n".join(body)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>")
        objs.append(f"<< /Length {len(stream.encode('latin-1'))} >>\nstream\n{stream}\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
