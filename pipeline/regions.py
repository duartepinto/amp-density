"""Regions the pipeline knows how to build.

Each region is selected from the INE BGRI 2021 layer with a SQL `where` clause.
NUTS3 codes follow the 2013 nomenclature used by the BGRI2021 service.
`bbox` (lon/lat, slightly padded) limits which OSM features are extracted.
`metro` is the OSM `network` tag of the region's metro routes (build_metro.py).
`metro_planned` lists lines under construction: OSM has their track (railway=construction,
tagged with the line's `name` and the network as `operator`) but no route relations yet.
"""

REGIONS = {
    "amp": {
        "name": "Área Metropolitana do Porto",
        "where": "NUTS3_13 = '11A'",
        "bbox": (-8.82, 40.70, -8.05, 41.50),
        "metro": "Metro do Porto",
        "metro_planned": {
            # Track name in OSM -> line. The colours are ours (not yet official), chosen to
            # stay distinct from lines A-F, also for colour-blind readers.
            "lines": {
                "Linha Rosa": {"ref": "G", "colour": "#F48FB8", "name": "Linha Rosa: São Bento – Casa da Música"},
                "Linha Rubi": {"ref": "H", "colour": "#9E1A4F", "name": "Linha Rubi: Casa da Música – Santo Ovídio"},
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
    "aml": {
        "name": "Área Metropolitana de Lisboa",
        "where": "NUTS3_13 = '170'",
        "bbox": (-9.52, 38.40, -8.48, 39.08),
        "metro": "Metropolitano de Lisboa",
    },
}
