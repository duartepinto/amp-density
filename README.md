# Portugal density grid

Population-density map on a 250 m × 250 m grid (EPSG:3035, the INSPIRE/Eurostat grid),
starting with the Área Metropolitana do Porto. Click a cell for its residents and density.

**Open `index.html`** — a static page that works straight from disk or any static host.
It loads the grid from `data/amp.js` (a script, so no server is needed); map tiles and the
MapLibre/proj4 libraries come from CDNs. To publish, upload `index.html` and `data/*.js`
(not `data/raw/`).

## Data sources

| What | Source | Used for |
|---|---|---|
| Residents per statistical subsection (Census 2021, BGRI 2021) | [INE](https://gisserver.ine.pt/arcgis/rest/services/BGRI2021/BGRI/FeatureServer/0), falls back to an ArcGIS Online republication | the population counts |
| Building footprints + tags | OpenStreetMap ([Geofabrik](https://download.geofabrik.de/europe/portugal.html)) | placing residents inside each census subsection |
| ML building footprints | [Overture Maps](https://overturemaps.org) (Microsoft, Esri) | filling OSM gaps outside the big cities |
| Net building height, population (100 m) | [GHSL](https://human-settlement.emergency.copernicus.eu) GHS-BUILT-H ANBH 2018, GHS-POP 2020 | floors where OSM has none; fallback for subsections with no buildings |

## How the numbers are made, and how accurate they are

Census subsections are irregular (the average resident's subsection is 8.7 ha: 3.4 ha in
Porto, 28 ha in Arouca) and never line up with a square grid, so each subsection's residents are spread over
the cells it touches in proportion to the residential floor area (footprint × floors) of its
buildings.

Accuracy was measured by rebuilding the census from coarser census sections with the same method
and comparing against the real figures, scaled to the equivalent cell size. Median error per cell:

| Cell size | Residents |
|---|---|
| 100 m | 20–40% |
| 250 m | ~14% |
| 500 m | ~7% |
| 1 km | ~5% |

These are slightly optimistic, because the comparison grid is itself an estimate. Change the cell
size with `CELL` in `pipeline/build_grid.py`, and update its `TYPICAL_ERROR` table to match.

## Rebuild

```sh
mkdir -p data/raw/osm data/raw/ghsl
curl -L -o data/raw/osm/portugal-latest.osm.pbf https://download.geofabrik.de/europe/portugal-latest.osm.pbf
# GHSL tile R5_C18 (covers mainland Portugal's west coast), unzip into data/raw/ghsl/:
#   GHS_BUILT_H_ANBH_E2018_GLOBE_R2023A_54009_100_V1_0_R5_C18.zip
#   GHS_POP_E2020_GLOBE_R2023A_54009_100_V1_0_R5_C18.zip
# from https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/

uv run python pipeline/fetch_census.py amp
uv run python pipeline/extract_osm.py amp
uv run python pipeline/fetch_overture.py amp
uv run python pipeline/build_grid.py amp     # -> data/amp.js
```

`index.html?region=<key>` loads `data/<key>.js`. New regions go in `pipeline/regions.py`
(an `aml` entry for Lisbon is defined but not yet built).
