"""保守切分：整页/整章保留，跨单元上下文显式链接，不按字数截断。"""
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET



class HtmlText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag in ("p", "div", "tr", "li", "h1", "h2", "h3", "br"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, text):
        if not self.hidden:
            self.parts.append(text)


def units(path):
    suffix = path.suffix.lower()
    if suffix in (".md", ".txt"):
        text = path.read_text(encoding="utf-8-sig")
        # 页标记或一级/二级章标题为定位单元；保留标记本身与原始偏移。
        pattern = r"(?m)^(?:<!--\s*PDF Page\s+\d+\s*-->|##\s*PDF 第\s*\d+\s*页|#{1,2}\s+.+)$"
        starts = sorted({0, *(m.start() for m in re.finditer(pattern, text))})
        for i, start in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else len(text)
            yield f"text:{start}:{end}", text[start:end], None, "utf8"
    elif suffix == ".pdf":
        from pypdf import PdfReader
        for i, page in enumerate(PdfReader(path).pages, 1):
            yield f"pdf:{i}", page.extract_text() or "", i, "pypdf"
    elif suffix == ".docx":
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("word/document.xml"))
            notes = [ET.fromstring(z.read(name)) for name in ("word/footnotes.xml", "word/endnotes.xml") if name in z.namelist()]
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        text = "\n".join("".join(t.text or "" for t in p.findall(".//w:t", ns)) for p in root.findall(".//w:p", ns))
        for note in notes:
            text += "\n[注释原文]\n" + "\n".join("".join(t.text or "" for t in p.findall(".//w:t", ns)) for p in note.findall(".//w:p", ns))
        yield "docx:document", text, None, "docx_xml"
    elif suffix == ".epub":
        with zipfile.ZipFile(path) as z:
            container = ET.fromstring(z.read("META-INF/container.xml"))
            opf_path = container.find(".//{*}rootfile").attrib["full-path"]
            opf = ET.fromstring(z.read(opf_path))
            manifest = {x.attrib["id"]: x.attrib["href"] for x in opf.findall(".//{*}manifest/{*}item")}
            from urllib.parse import unquote
            for i, item in enumerate(opf.findall(".//{*}spine/{*}itemref")):
                href = str(PurePosixPath(opf_path).parent / unquote(manifest[item.attrib["idref"]]))
                parser = HtmlText()
                parser.feed(z.read(href).decode("utf-8-sig"))
                yield f"epub:{i}:{href}", "".join(parser.parts), None, "epub_spine"
    else:
        raise ValueError(f"不支持的格式：{suffix}")

