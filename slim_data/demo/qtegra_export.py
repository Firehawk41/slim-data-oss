"""Builds an invented Qtegra SpreadsheetML export (D-89): the layout of a real one, made-up numbers
(D-05). Four header rows (block, sweep, isotope or field, unit), then one row per measurement."""

from xml.sax.saxutils import escape

ELEMENTS = ("7Li", "63Cu", "66Zn")  # Li is measured but isn't an analyte of the demo analysis
ISTD = "115In"
INSTRUMENT = "iCAPevoRQ"
FIELDS = ("Evaluate", "Sample Type", "Standard", "Dilution Factor", "QC Action")


def measurement(
    kind: str,
    label: str,
    comment: str = "<Comment>",
    values: dict | None = None,
    rsd: dict | None = None,
    istd: str | None = "98.5",
    dilution: str = "1",
    qc_action: str = "None",
    standard: str = "",
    reference: dict | None = None,
    calibration: dict | None = None,
    start: str = "2026-10-19T09:30:00.1234567",
) -> dict:
    return dict(
        kind=kind,
        label=label,
        comment=comment,
        values=values or {},
        rsd=rsd or {},
        istd=istd,
        dilution=dilution,
        qc_action=qc_action,
        standard=standard,
        reference=reference or {},
        calibration=calibration or {},
        start=start,
    )


CALIBRATED = {e: ("0.9998", "0.9996", "0.020", "0.004") for e in ELEMENTS}  # r, R², LOD, BEC

RUN = (
    measurement(
        "BLK", "Blank", values={e: "0" for e in ELEMENTS}, istd="100", start="2026-10-19T09:30:00.1234567"
    ),
    *(
        measurement(
            "STD",
            f"{level} ppb",
            standard=f"{level} ppb",
            values={e: level for e in ELEMENTS},
            calibration=CALIBRATED,
            start=f"2026-10-19T09:3{i}:00.0000001",
        )
        for i, level in enumerate(("1", "5", "10"), start=1)
    ),
    measurement("UNKNOWN", "Blank verification", values={"63Cu": "0.010", "66Zn": "0.020"}),
    measurement("UNKNOWN", "11 Prep blank", comment="BLANK", values={"63Cu": "0.030", "66Zn": "0.040"}),
    measurement(
        "UNKNOWN",
        "12 Wafer A",
        comment="TR00042-01 A",
        values={"7Li": "0.5", "63Cu": "10.00", "66Zn": "4.00"},
        rsd={"63Cu": "1.2", "66Zn": "2.5"},
        istd="91.0",
    ),
    measurement("QC", "CCV", qc_action="CCV", standard="5 ppb", values={}),  # aborted: no values
    measurement(
        "UNKNOWN",
        "12 Wafer B",
        comment="TR00042-01 B",
        values={"63Cu": "10.20", "66Zn": "4.10"},
        rsd={"63Cu": "0.8", "66Zn": "3.1"},
        istd="89.5",
    ),
    measurement(
        "UNKNOWN",
        "13 Wafer A",
        comment="TR00042-02 A",
        dilution="10",
        values={"63Cu": "51.0", "66Zn": "22.0"},
        rsd={"63Cu": "1.0", "66Zn": "1.5"},
    ),
    measurement(
        "QC",
        "CCV",
        qc_action="CCV",
        standard="5 ppb",
        values={"63Cu": "4.90", "66Zn": "5.20"},
        reference={"63Cu": "5", "66Zn": "5"},
        istd="97.0",
    ),
)


def export(run=RUN, elements=ELEMENTS, unit="ppb", leave_out=()) -> bytes:
    """`leave_out` drops whole blocks or fields, as a scheme that doesn't tick them would."""
    columns = [("SampleList", "SampleId", "", ""), ("SampleList", "SampleLine", "", "")]
    columns += [("Info", "UserName", "", ""), ("StartTime", "", "", "")]
    columns += [(INSTRUMENT, "Label", "", ""), (INSTRUMENT, "Comment", "", "")]
    columns += [(INSTRUMENT, "ExtCal", f, "") for f in FIELDS]
    columns += [("Raw.Average", "0", f"{e} (KED)", "Y (cps)") for e in elements]  # unused: ignored
    for block in ("ExtCal.Average", "ExtCal.RSD"):
        for e in (*elements, ISTD):
            columns.append(
                (block, "0", f"{e} (KED)", "Y (%)" if e == ISTD or block == "ExtCal.RSD" else f"Y ({unit})")
            )
    columns += [
        ("ExtCal.QC.ReferenceAmount", "Calibration Test", f"{e} (KED)", f"Y ({unit})") for e in elements
    ]
    for block in (
        "ExtCal.CorrelationCoefficient",
        "ExtCal.CoefficientOfDetermination",
        "ExtCal.LOD",
        "ExtCal.BEC",
    ):
        columns += [
            (block, "0", f"{e} (KED)", f"Y ({unit})" if "LOD" in block or "BEC" in block else "Y")
            for e in (*elements, ISTD)
        ]
    columns = [c for c in columns if not set(c[:3]) & set(leave_out)]

    def cell(index, text, kind="String"):
        if text is None:
            return ""
        return (
            f'<ss:Cell ss:Index="{index}"><ss:Data ss:Type="{kind}">{escape(str(text))}</ss:Data></ss:Cell>'
        )

    lines = ['<?xml version="1.0" encoding="utf-8"?>', '<?mso-application progid="Excel.Sheet"?>']
    lines.append('<ss:Workbook xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">')
    lines.append('<ss:Worksheet ss:Name="Data"><ss:Table>')
    for r in range(4):
        cells = "".join(cell(i, c[r] or None) for i, c in enumerate(columns, start=1))
        lines.append(f'<ss:Row ss:Index="{r + 1}">{cells}</ss:Row>')
    for n, m in enumerate(run, start=5):
        cells = []
        for i, (block, sub, name, _) in enumerate(columns, start=1):
            element = name.split(" ")[0]
            figures = (
                "ExtCal.CorrelationCoefficient",
                "ExtCal.CoefficientOfDetermination",
                "ExtCal.LOD",
                "ExtCal.BEC",
            )
            value = {
                ("SampleList", "SampleId"): (n - 3, "Number"),
                ("SampleList", "SampleLine"): (m["label"], "String"),
                ("Info", "UserName"): ("ICP-PC\\Shared", "String"),
                ("StartTime", ""): (m["start"], "DateTime"),
                (INSTRUMENT, "Label"): (m["label"], "String"),
                (INSTRUMENT, "Comment"): (m["comment"], "String"),
            }.get((block, sub))
            if block == INSTRUMENT and sub == "ExtCal":
                value = (
                    {
                        "Evaluate": "1",
                        "Sample Type": m["kind"],
                        "Standard": m["standard"],
                        "Dilution Factor": m["dilution"],
                        "QC Action": m["qc_action"],
                    }[name],
                    "String",
                )
            elif block in ("Raw.Average", "ExtCal.Average"):
                text = m["istd"] if element == ISTD else m["values"].get(element)
                value = (text if m["values"] else None, "Number")
            elif block == "ExtCal.RSD":
                value = (m["rsd"].get(element), "Number")
            elif block == "ExtCal.QC.ReferenceAmount":
                value = (m["reference"].get(element), "Number")
            elif block in figures and element in m["calibration"]:
                value = (m["calibration"][element][figures.index(block)], "Number")
            elif block in figures and element == ISTD and m["calibration"]:
                value = ("0", "Number")  # Qtegra writes zeros for the internal standard
            if value and value[0] not in (None, ""):
                cells.append(cell(i, value[0], value[1]))
        lines.append(f'<ss:Row ss:Index="{n}">{"".join(cells)}</ss:Row>')
    lines.append("</ss:Table></ss:Worksheet></ss:Workbook>")
    return "\n".join(lines).encode()
