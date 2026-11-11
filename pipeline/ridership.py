"""Metro use: validations by part of the network, from Metro do Porto's annual report
(Relatório e Contas 2025, "Validações por troços da rede", p. 21:
https://www.metrodoporto.pt/metrodoporto/uploads/document/file/797/rc_metro_do_porto_2025_digital_lowquality.pdf).

A validation is one boarding, counted at the station where it happens (transfers between
lines included). The report splits the network into the trunk shared by lines A, B, C, E and
F, line D, and each line's own branch beyond the trunk; it doesn't split the trunk by line,
since a boarding there doesn't say which line it is for. Stations are assigned to parts by the
lines that call at them; Fonte do Cuco, where the branches of B and C part, is named as an end
of both in the report and is counted with B's.
"""

YEAR, PREVIOUS = 2025, 2024
SOURCE = "https://www.metrodoporto.pt/metrodoporto/uploads/document/file/797/rc_metro_do_porto_2025_digital_lowquality.pdf"
TOTAL = {2024: 89_780_000, 2025: 94_540_000}

# key: (line whose branch it is, or None for the trunk; validations in thousands {year: n})
PARTS = {
    "trunk": (None, {2024: 45_152, 2025: 47_115}),  # Estádio do Dragão - Senhora da Hora
    "D": ("D", {2024: 28_528, 2025: 30_878}),  # Vila d'Este - Hospital São João, without Trindade
    "B": ("B", {2024: 4_687, 2025: 4_914}),  # Fonte do Cuco - Póvoa de Varzim
    "A": ("A", {2024: 4_454, 2025: 4_590}),  # Vasco da Gama - Senhor de Matosinhos
    "C": ("C", {2024: 3_047, 2025: 3_079}),  # (after Fonte do Cuco) - ISMAI
    "F": ("F", {2024: 2_669, 2025: 2_665}),  # Contumil - Fânzeres
    "E": ("E", {2024: 1_244, 2025: 1_298}),  # Botica and Aeroporto
}


def part_of(lines: set[str], name: str) -> str:
    """Which part of the network a station's boardings count in, from the lines calling there."""
    if {"A", "B"} <= lines:
        return "trunk"  # every trunk station has A and B (and E, F)
    if "D" in lines and name != "Trindade":
        return "D"
    if name == "Fonte do Cuco":
        return "B"
    for line in ("B", "C", "E", "F", "A"):
        if line in lines:
            return line
    raise ValueError(f"{name}: no part of the network for lines {sorted(lines)}")
