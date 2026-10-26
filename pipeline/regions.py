"""Regions the pipeline knows how to build.

Each region is selected from the INE BGRI 2021 layer with a SQL `where` clause.
NUTS3 codes follow the 2013 nomenclature used by the BGRI2021 service.
`bbox` (lon/lat, slightly padded) limits which OSM features are extracted.
"""

REGIONS = {
    "amp": {
        "name": "Área Metropolitana do Porto",
        "where": "NUTS3_13 = '11A'",
        "bbox": (-8.82, 40.70, -8.05, 41.50),
    },
    "aml": {
        "name": "Área Metropolitana de Lisboa",
        "where": "NUTS3_13 = '170'",
        "bbox": (-9.52, 38.40, -8.48, 39.08),
    },
}
