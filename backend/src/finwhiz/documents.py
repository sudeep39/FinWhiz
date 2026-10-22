"""Versioned SEC parsing and table-aware token/semantic chunking."""

import math
import os
import re
from datetime import date, datetime
from io import BytesIO

import tiktoken
from bs4 import BeautifulSoup, NavigableString, Tag
from pypdf import PdfReader

from .config import ROOT, AppError, digest
from .fiscal import fiscal_metadata

PARSER_VERSION = "py-v1"
os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(ROOT / ".cache/tiktoken"))


def encoding():
    return tiktoken.get_encoding("cl100k_base")


def tokens(text):
    return encoding().encode(text, disallowed_special=())


def token_count(text):
    return len(tokens(text))


def clean(text):
    return re.sub(r"\s+", " ", text).strip()


def iso_date(value):
    try:
        return (
            isinstance(value, str)
            and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))
            and date.fromisoformat(value).isoformat() == value
        )
    except ValueError:
        return False


def validate_details(details):
    fields = {"ticker": 10, "company": 200, "reportDate": 10, "form": 4}
    return (
        isinstance(details, list)
        and len(details) <= 32
        and all(
            isinstance(item, dict)
            and all(
                k in fields and isinstance(v, str) and len(v) <= fields[k] for k, v in item.items()
            )
            and (not item.get("ticker") or re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", item["ticker"]))
            and (not item.get("reportDate") or iso_date(item["reportDate"]))
            and (not item.get("form") or item["form"] in ("10-K", "10-Q"))
            for item in details
        )
    )


def html_metadata(html):
    soup = BeautifulSoup(html, "lxml")

    def fact(name):
        node = soup.find(attrs={"name": f"dei:{name}"})
        return node.get_text().strip() if node else ""

    period = fact("DocumentPeriodEndDate")
    if not iso_date(period):
        try:
            period = datetime.strptime(period, "%B %d, %Y").date().isoformat()
        except ValueError:
            period = ""
    return dict(
        **fiscal_metadata(html),
        ticker=fact("TradingSymbol").upper(),
        reportDate=period,
        form=fact("DocumentType").upper(),
        company=fact("EntityRegistrantName"),
        cik=fact("EntityCentralIndexKey"),
    )


def text_metadata(text):
    def match(pattern):
        found = re.search(pattern, text, re.I)
        return found[1] if found else ""

    return {
        "ticker": match(
            r"(?:trading\s+symbol(?:\(s\))?|ticker(?:\s+symbol)?)\s*[:=]\s*([A-Z][A-Z0-9.-]{0,9})\b"
        ).upper(),
        "form": match(r"\b(10-[KQ])\b").upper(),
        "reportDate": match(
            r"(?:fiscal\s+period\s+end|period\s+ended|report\s+date)\s*[:=]?\s*(\d{4}-\d{2}-\d{2})"
        ),
    }


def extract_html(html):
    soup = BeautifulSoup(html, "lxml")
    for node in list(
        soup.find_all(["script", "style", "head", "noscript", "ix:header", "ix:hidden"])
    ):
        node.decompose()
    for node in list(soup.find_all(True)):
        if node.attrs is not None and (
            node.has_attr("hidden")
            or re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", node.get("style", ""), re.I)
        ):
            node.decompose()
    blocks, section, anchor = [], "Cover", ""

    def emit(text, kind="narrative"):
        nonlocal section
        text = text.strip() if kind == "table" else clean(text)
        if not text or re.fullmatch(r"\d{1,4}", text) or text.lower() == "table of contents":
            return
        if (
            kind == "narrative"
            and len(text) < 220
            and re.match(r"(?:part\s+[ivx]+\b|item\s+\d+[a-z]?[. :]|note\s+\d+\b)", text, re.I)
        ):
            section = text
        blocks.append(
            dict(kind=kind, text=text, section=section, anchor=anchor, id=f"b{len(blocks)}")
        )

    def walk(node):
        nonlocal anchor
        anchor = node.get("id") or node.get("name") or anchor
        if node.name == "table" and not node.find("table"):
            links = node.select('a[href^="#"]')
            if (
                len(links) >= 5
                and sum(len(a.get_text()) for a in links) > len(node.get_text()) * 0.4
            ):
                return
            rows, spans = [], {}
            for ri, row in enumerate(node.find_all("tr")):
                cells = {c: value for (r, c), value in spans.items() if r == ri}
                col = 0
                for cell in row.find_all(["td", "th"], recursive=False):
                    while col in cells:
                        col += 1

                    def span(name):
                        try:
                            return min(80, max(1, int(cell.get(name, 1))))
                        except ValueError:
                            return 1

                    width, height, value = span("colspan"), span("rowspan"), clean(cell.get_text())
                    for c in range(width):
                        cells[col + c] = value
                        for r in range(1, height):
                            spans[ri + r, col + c] = value
                    col += width
                if cells:
                    rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
            if len(rows) >= 2 and max(map(len, rows)) >= 2:
                keep = []
                for c in range(max(map(len, rows))):
                    values = [row[c] if c < len(row) else "" for row in rows]
                    if any(values) and (
                        not keep
                        or any(
                            v != (row[keep[-1]] if keep[-1] < len(row) else "")
                            for v, row in zip(values, rows)
                        )
                    ):
                        keep.append(c)
                caption = node.find("caption", recursive=False)
                lines = [clean(caption.get_text())] if caption else []
                lines.extend(
                    " | ".join(row[c] if c < len(row) else "" for c in keep)
                    for row in rows
                    if any(row)
                )
                emit("\n".join(filter(None, lines)), "table")
                return
        pending = ""
        for child in node.children:
            if isinstance(child, NavigableString):
                pending += str(child)
            elif isinstance(child, Tag):
                if re.fullmatch(
                    r"div|p|table|section|article|ul|ol|li|h[1-6]|tr|td|th|blockquote", child.name
                ) or child.select_one("div,p,table,section,li"):
                    emit(pending)
                    pending = ""
                    walk(child)
                else:
                    anchor = child.get("id") or anchor
                    pending += "\n" if child.name == "br" else child.get_text()
        emit(pending)

    walk(soup.body or soup)
    if sum(len(b["text"]) for b in blocks) < 100:
        raise AppError("No readable text found. Scanned PDFs require OCR before upload.")
    return blocks


def extract_plain(text):
    return [
        dict(kind="narrative", text=clean(part), section="Document", anchor="", id=f"b{i}")
        for i, part in enumerate(re.split(r"\n\s*\n", text))
        if clean(part)
    ]


def windows(text, size=600, overlap=90):
    ids, result, start = tokens(text), [], 0
    while start < len(ids):
        end = min(start + size, len(ids))
        while end > start and "\ufffd" in encoding().decode(ids[start:end]):
            end -= 1
        if end <= start:
            raise AppError("Cannot split this document safely.")
        result.append(encoding().decode(ids[start:end]))
        if end == len(ids):
            break
        start = max(start + 1, end - overlap)
        while start < end and "\ufffd" in encoding().decode(ids[start:end]):
            start += 1
    return result


def make_parents(blocks):
    result = []
    for block in blocks:
        last = result[-1] if result else None
        if (
            block["kind"] == "narrative"
            and last
            and last["kind"] == "narrative"
            and last["section"] == block["section"]
        ):
            last["text"] += "\n\n" + block["text"]
        else:
            result.append(
                {
                    **block,
                    "index": len(result),
                    "context": last["text"][-500:] if last and block["kind"] == "table" else "",
                }
            )
    return result


def split_parent(parent, strategy, embed_units=None, prefix=""):
    budget = max(200, 700 - token_count(prefix) - 10)
    if parent["kind"] == "table":
        rows = parent["text"].split("\n")
        header = "\n".join(filter(None, [parent.get("context", ""), *rows[:3]]))
        if token_count(header) > budget / 2:
            return windows(parent["text"], budget, 90)
        pieces, current = [], header
        for row in rows[3:]:
            for piece in windows(row, max(32, budget - token_count(header) - 8), 0):
                if token_count(current + "\n" + piece) > budget and current != header:
                    pieces.append(current)
                    current = header
                current += "\n" + piece
        return pieces + ([current] if current else [])
    if strategy == "fixed" or token_count(parent["text"]) <= budget:
        return windows(parent["text"], budget, 100)
    units = [
        piece
        for sentence in re.split(r"(?<=[.!?;])\s+(?=[A-Z(\d])", parent["text"])
        for piece in windows(sentence, 120, 0)
    ]
    vectors = embed_units([prefix + unit for unit in units])

    def cosine(a, b):
        denominator = math.sqrt(sum(x * x for x in a) * sum(x * x for x in b))
        if not denominator:
            raise AppError("Semantic embedding cannot be zero.")
        return sum(x * y for x, y in zip(a, b)) / denominator

    distances = [1 - cosine(a, b) for a, b in zip(vectors, vectors[1:])]
    ordered = sorted(distances)
    threshold = ordered[math.floor(len(ordered) * 0.8)] if ordered else 1
    pieces, start, end = [], 0, 0
    while end < len(units):
        count = token_count(" ".join(units[start : end + 1]))
        if (
            (count > 1000 and end > start)
            or end == len(units) - 1
            or (
                count >= 350
                and ((end < len(distances) and distances[end] > threshold) or count >= budget)
            )
        ):
            cut = end if count > 1000 and end > start else end + 1
            pieces.append(" ".join(units[start:cut]))
            if cut == len(units):
                break
            next_start = cut
            while (
                next_start > start + 1 and token_count(" ".join(units[next_start - 1 : cut])) <= 100
            ):
                next_start -= 1
            start, end = next_start, cut
        else:
            end += 1
    return pieces


def parse_file(file, store):
    path = (ROOT / file["samplePath"]).resolve() if file.get("samplePath") else None
    if path and not path.is_relative_to(ROOT / "data"):
        raise AppError("Invalid source path.")
    data = path.read_bytes() if path else store.get(file["key"])
    if not data:
        raise AppError(f"Missing uploaded file: {file['name']}")
    checksum = digest(data)
    known = file.get("known")
    manifest_path = ROOT / "data/filings/manifest.json"
    if not known and manifest_path.exists():
        import json

        known = next(
            (
                row
                for row in json.loads(manifest_path.read_text())["entries"]
                if row["sha256"] == checksum
            ),
            None,
        )
    if known and known.get("sha256") and known["sha256"] != checksum:
        raise AppError(
            "A sample filing failed its source checksum. Download it again before processing."
        )
    extension = file["name"].rsplit(".", 1)[-1].lower()
    if extension in ("html", "htm"):
        text = data.decode("utf-8", errors="replace")
        blocks, detected = extract_html(text), html_metadata(text)
    else:
        text = (
            "\n\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(data)).pages)
            if extension == "pdf"
            else data.decode("utf-8", errors="replace")
        )
        blocks, detected = extract_plain(text), text_metadata(text)
    details = {**file.get("details", {}), **{k: v for k, v in detected.items() if v}}
    if known:
        details = {
            **{key: value for key, value in details.items() if key.startswith('fiscal_') or key == 'period_type'},
            **{key: known.get(key, "")
               for key in ("ticker", "company", "cik", "form", "reportDate", "filingDate", "accession")},
        }
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", details.get("ticker", "")):
        raise AppError(f"{file['name']}: specify a ticker in the file details before uploading.")
    if not iso_date(details.get("reportDate")):
        raise AppError(f"{file['name']}: specify its fiscal period end in the file details.")
    if details.get("form") not in ("10-K", "10-Q"):
        raise AppError(f"{file['name']}: select 10-K or 10-Q in the file details.")
    if sum(len(b["text"]) for b in blocks) < 100:
        raise AppError(
            f"{file['name']}: no readable text; scanned files require OCR before upload."
        )
    return {
        **details,
        "id": file["id"],
        "name": file["name"],
        "company": details.get("company") or details["ticker"],
        "checksum": checksum,
        "documentId": checksum,
        "sourceUrl": known.get("url") if known else f"/api/sources/{file['id']}",
        "blocks": blocks,
    }
