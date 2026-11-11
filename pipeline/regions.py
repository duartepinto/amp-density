"""Regions the pipeline knows how to build.

Each region is selected from the INE BGRI 2021 layer with a SQL `where` clause.
NUTS3 codes follow the 2013 nomenclature used by the BGRI2021 service.
`bbox` (lon/lat, slightly padded) limits which OSM features are extracted.

`transit` lists the rail networks drawn on the map (build_transit.py), each found in
OSM by its route relations' `network` tag:
  key      short id used by the web page (one toggle per network)
  network  OSM `network` tag; `routes`: the route types to read
  badge    "ref" to label lines by their OSM ref (A, B...), "name" by the place in
           their route master's name ("CP Porto: Linha de Braga" -> Braga)
  style    "rail" draws every line of the network in one railway style (parallel tracks
           of different lines then read as one corridor); otherwise lines take their colours
  gtfs     optional timetable in data/raw/gtfs/, for frequencies
  include  lines of other OSM networks drawn as part of this one: their route master's
           network and name, and the line's name for the map
  drive_minutes  optional: each open station also gets the area within this drive of it
  line_stats  figures per line in the Lines view (build_lines.py)
  planned  lines under construction: OSM has their track (railway=construction, tagged
           with the line's `name` and the network as `operator`) but no routes yet
  projects instead of OSM routes, a set of planned lines in projects.py (stations placed by
           hand from the plans; track from OSM railway=proposed where mapped)
`interchanges` names connected stations whose names differ: {shown name: [station names]}.
`core` is the central municipality (its name and census DTMN21 code): the Lines view splits
residents inside and outside it.
"""

REGIONS = {
    "amp": {
        "name": "Área Metropolitana do Porto",
        "where": "NUTS3_13 = '11A'",
        "bbox": (-8.82, 40.70, -8.05, 41.50),
        "transit": [
            {
                "key": "metro",
                "label": "Metro",
                "network": "Metro do Porto",
                "routes": ["light_rail", "subway"],
                "badge": "ref",
                "gtfs": "amp_metro.zip",
                "line_stats": True,
                "planned": {
                    # Track name in OSM -> line. The colours are ours (not yet official), chosen
                    # to stay distinct from lines A-F, also for colour-blind readers.
                    "lines": {
                        "Linha Rosa": {"ref": "G", "colour": "#F48FB8", "ends": "São Bento – Casa da Música"},
                        "Linha Rubi": {"ref": "H", "colour": "#9E1A4F", "ends": "Casa da Música – Santo Ovídio"},
                    },
                    # Stations OSM does not map yet, placed on their line's track nearest these
                    # points (lon, lat) from press descriptions of where they will be.
                    "unmapped_stations": {
                        "Arrábida": ("Linha Rubi", -8.6355, 41.1395),  # by Arrábida Shopping
                        "Candal": ("Linha Rubi", -8.6308, 41.1335),  # Av. da República, Candal viaduct
                        "Rotunda": ("Linha Rubi", -8.6270, 41.1300),  # Rotunda Edgar Cardoso
                    },
                },
            },
            {
                # Metro do Porto 3.0: Gondomar II, ISMAI-Muro (Trofa), São Mamede, Maia II.
                "key": "metro_plan",
                "label": "Projected metro",
                "network": "Metro do Porto 3.0",
                "projects": "metro_3_0",
                "proposed_operator": "Metro do Porto",  # whose railway=proposed track to use
                "badge": "ref",
                "line_stats": True,
            },
            {
                # Porto's mobility plan (PMUS, 2026): Campo Alegre, Rosa and Rubi extensions.
                "key": "pmus",
                "label": "PMUS",
                "network": "PMUS",
                "projects": "pmus",
                "badge": "ref",
                "line_stats": True,
            },
            {
                "key": "train",
                "label": "Trains",
                "network": "CP Porto",
                "routes": ["train"],
                "badge": "name",
                "style": "rail",
                "drive_minutes": 10,
                # The narrow-gauge Vouga line is mapped as a CP Regional service.
                "include": [
                    {"network": "CP Regional", "master": "CP Regional: Espinho - Vouga ⇆ Oliveira de Azeméis",
                     "name": "Linha do Vouga"},
                ],
            },
        ],
        "core": {"name": "Porto", "code": "1312"},
        "interchanges": {
            "Gaia - Devesas": ["Gaia", "Devesas"],  # CP Gaia station is Devesas; Rubi's stop connects
            # Projected lines' stations at open ones (projects.py)
            "IPO": ["IPO", "IPO II"],
            "Senhora da Hora": ["Senhora da Hora", "Senhora da Hora II"],
            "Verdes": ["Verdes", "Os Verdes II"],
            "Parque Maia": ["Parque Maia", "Parque Maia II"],
            "Matosinhos Sul": ["Matosinhos Sul", "Matosinhos Sul II"],
        },
    },
    "aml": {
        "name": "Área Metropolitana de Lisboa",
        "where": "NUTS3_13 = '170'",
        "bbox": (-9.52, 38.40, -8.48, 39.08),
        "transit": [
            {"key": "metro", "label": "Metro", "network": "Metropolitano de Lisboa", "routes": ["subway"], "badge": "ref"},
            {"key": "train", "label": "Trains", "network": "CP Lisboa", "routes": ["train"], "badge": "name", "style": "rail"},
        ],
    },
}
