"""Pure arithmetic and conservative cash-flow table extraction."""

import math
import re
from .config import AppError


def _calculate(operation, values):
    if not values or any(type(v) not in (float, int) or not math.isfinite(v) for v in values):
        raise AppError("Calculation inputs must be finite numbers.")
    if operation == "sum":
        return sum(values)
    if (
        operation == "gross_margin_excluding_revenue"
        and len(values) == 3
        and values[0] - values[2] != 0
    ):
        return (values[0] - values[2] - values[1]) / (values[0] - values[2]) * 100
    if len(values) == 2:
        a, b = values
        if operation == "difference":
            return a - b
        if operation == "gross_margin" and a != 0:
            return (a - b) / a * 100
        if b != 0:
            if operation == "ratio":
                return a / b
            if operation == "percentage":
                return a / b * 100
            if operation == "percent_change":
                return (a - b) / abs(b) * 100
    raise AppError("Unsupported operation or invalid denominator.")


def calculate(operation, values):
    result = _calculate(operation, values)
    if not math.isfinite(result):
        raise AppError("Calculation result must be finite.")
    return result


def supports_number(text, value):
    for raw in re.findall(r"\(?-?\d[\d,]*(?:\.\d+)?%?\)?", text):
        number = float(re.sub(r"[(),%]", "", raw)) * (-1 if raw.startswith("(") else 1)
        if abs(number - value) <= max(1e-9, abs(value) * 1e-9):
            return True
    return False


def finalize_margins(result):
    calculations = result["calculations"]
    adjusted = [c for c in calculations if c["operation"] == "gross_margin_excluding_revenue"]
    if result["status"] != "answered" or not adjusted:
        return result
    descriptions = []
    for calculation in adjusted:
        baseline = calculate("gross_margin", calculation["values"][:2])
        if not any(
            c["operation"] == "gross_margin"
            and c["values"] == calculation["values"][:2]
            and c["source_ids"] == calculation["source_ids"]
            for c in calculations
        ):
            calculations.append(
                {
                    **calculation,
                    "label": calculation["label"] + " (including excluded revenue)",
                    "operation": "gross_margin",
                    "values": calculation["values"][:2],
                    "unit": "%",
                    "result": baseline,
                }
            )
        label = (
            re.sub(
                r"gross[_ ]margin[_ ]excluding[_ ]revenue[_ ]?",
                "",
                calculation["label"],
                flags=re.I,
            )
            .replace("_", " ")
            .strip()
            or calculation["label"]
        )
        citations = " ".join(f"[{s}]" for s in calculation["source_ids"])
        descriptions.append(
            f"For {label}, gross margin was {baseline:.2f}% including the excluded revenue and {calculation['result']:.2f}% excluding it, with reported costs unchanged {citations}."
        )
    commentary = " ".join(
        s
        for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\[])", result["answer"])
        if not re.search(r"gross margin|gross_margin", s, re.I)
    )
    return {
        **result,
        "answer": " ".join(filter(None, [commentary, *descriptions])),
        "citation_ids": list(
            dict.fromkeys(result["citation_ids"] + [s for c in adjusted for s in c["source_ids"]])
        ),
    }


def year_value(row, header, year):
    cells = [
        row[i]
        for i, cell in enumerate(header)
        if cell == year and i < len(row) and re.fullmatch(r"\(?-?\d[\d,]*(?:\.\d+)?\)?", row[i])
    ]
    numbers = [
        float(re.sub(r"[(),]", "", cell)) * (-1 if cell.startswith("(") else 1) for cell in cells
    ]
    return numbers[0] if numbers and all(n == numbers[0] for n in numbers) else None


def empty_chart():
    return {"type": "none", "title": "", "unit": "", "points": []}


def cash_flow_answer(question, sources):
    if not re.search(r"operating cash flow", question, re.I) or not re.search(
        r"first six months|first half|\bH1\b|six months (?:ended|ending) June 30", question, re.I
    ):
        return None
    years = list(dict.fromkeys(re.findall(r"\b20\d{2}\b", question)))
    if len({s["ticker"] for s in sources}) != 1:
        return None
    adjustment = bool(re.search("adjustment", question, re.I)) and len(years) == 1
    fcf = len(years) == 2 and all(
        re.search(pattern, question, re.I)
        for pattern in [
            r"free cash flow",
            "minus",
            r"purchases of property and equipment excluding finance leases,?\s+net of sales",
        ]
    )
    if not adjustment and not fcf:
        return None
    matches, periods = [], {}
    query_words = set(re.findall(r"[a-z0-9]+", question.lower()))
    for source in sources:
        if not re.search(r"\((?:dollars )?in millions\)", source["text"], re.I):
            continue
        lines = source["text"].split("\n")
        for i, line in enumerate(lines):
            header = [c.strip() for c in line.split("|")]
            if not all(year in header for year in years) or not re.search(
                "Six Months Ended June 30", " ".join(lines[max(0, i - 2) : i]), re.I
            ):
                continue
            rows = []
            for line2 in lines[i + 1 :]:
                if "|" not in line2:
                    break
                rows.append([c.strip() for c in line2.split("|")])
            if adjustment:
                operating, reconciling = False, False
                for row in rows:
                    label = row[0]
                    operating |= bool(
                        re.fullmatch("Cash Flows from Operating Activities", label, re.I)
                    )
                    reconciling |= bool(
                        re.match("Adjustments to reconcile net income", label, re.I)
                    )
                    if re.match(
                        r"Changes in operating assets|Net cash provided|Cash Flows from (?:Investing|Financing)",
                        label,
                        re.I,
                    ):
                        break
                    words = [
                        w
                        for w in re.findall(r"[a-z0-9]+", label.lower())
                        if w not in ("and", "the", "of", "net")
                    ]
                    value = year_value(row, header, years[0])
                    if (
                        operating
                        and reconciling
                        and len(words) >= 3
                        and all(w in query_words for w in words)
                        and value is not None
                    ):
                        matches.append((label, value, source["id"]))
            if fcf:
                operating = next(
                    (
                        row
                        for row in rows
                        if re.fullmatch(
                            r"Net cash provided by(?: \(used in\))? operating activities",
                            row[0],
                            re.I,
                        )
                    ),
                    None,
                )
                capex = next(
                    (
                        row
                        for row in rows
                        if re.fullmatch(
                            "Purchases of property and equipment excluding finance leases, net of sales",
                            row[0],
                            re.I,
                        )
                    ),
                    None,
                )
                if not operating or not capex:
                    continue
                for year in years:
                    inflow, outflow = year_value(operating, header, year), year_value(
                        capex, header, year
                    )
                    if inflow is None or outflow is None or outflow > 0:
                        continue
                    if year in periods and periods[year][:2] != (inflow, outflow):
                        return None
                    periods[year] = inflow, outflow, source["id"]

    def fmt(value):
        return f"{value:,.2f}".rstrip("0").rstrip(".")

    if adjustment:
        if not matches or any(m[:2] != matches[0][:2] for m in matches):
            return None
        label, value, source_id = matches[0]
        signed = ("−$" if value < 0 else "$") + fmt(abs(value))
        explanation = (
            (
                "It was deducted when reconciling net income to operating cash flow"
                + (
                    " to remove the noncash gain"
                    if re.search("unrealized gain", label, re.I)
                    else ""
                )
            )
            if value < 0
            else (
                "It was added when reconciling net income to operating cash flow"
                if value > 0
                else "It had no effect on the reconciliation"
            )
        )
        return {
            "status": "answered",
            "answer": f"For the first six months of {years[0]}, the {label} adjustment was {signed} million"
            + (" (a negative adjustment)" if value < 0 else "")
            + f". {explanation} [{source_id}].",
            "citation_ids": [source_id],
            "chart": empty_chart(),
            "calculations": [],
        }
    if not all(year in periods for year in years):
        return None
    calculations, sentences = [], []
    for year in years:
        inflow, outflow, source = periods[year]
        calculations.append(
            dict(
                label=f"Free cash flow, first six months of {year}",
                operation="sum",
                unit="USD millions",
                values=[inflow, outflow],
                source_ids=[source],
                result=inflow + outflow,
            )
        )
        sentences.append(
            f"For the first six months of {year}, free cash flow was ${fmt(inflow+outflow)} million: ${fmt(inflow)} million of operating cash flow less ${fmt(-outflow)} million of purchases of property and equipment excluding finance leases, net of sales [{source}]."
        )
    earlier, later = sorted(years)
    change = sum(periods[later][:2]) - sum(periods[earlier][:2])
    if periods[later][0] > periods[earlier][0]:
        sentences.append(
            "Yes. Stronger operating cash flow translated into higher free cash flow."
            if change > 0
            else f"No. Operating cash flow increased, but free cash flow {'fell' if change < 0 else 'was unchanged'} because capital spending {'increased by more' if change < 0 else 'rose by the same amount'}."
        )
    else:
        sentences.append("Operating cash flow did not increase over these periods.")
    ids = list(dict.fromkeys(c["source_ids"][0] for c in calculations))
    sentences.append(
        f"Free cash flow {'decreased' if change < 0 else 'increased' if change > 0 else 'changed'} by ${fmt(abs(change))} million from {earlier} to {later} "
        + " ".join(f"[{s}]" for s in ids)
        + "."
    )
    return dict(
        status="answered",
        answer=" ".join(sentences),
        citation_ids=ids,
        chart=empty_chart(),
        calculations=calculations,
    )
