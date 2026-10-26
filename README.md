# Portugal density grid

Population-density map on a 250 m × 250 m grid (EPSG:3035, the INSPIRE/Eurostat grid),
starting with the Área Metropolitana do Porto. Click a cell for its residents, density, dwellings,
households and average household size, its residential buildings split into single-family vs
apartment buildings and into 1–2 vs 3+ floors, and the average floors of its apartment buildings
where OpenStreetMap maps them. When zoomed in (z13+), the cell's buildings are highlighted on the map.

The metro lines and stations are drawn on top; click a station to see the area within a 10-minute
walk of it, how many residents live there, and how often trains call (minutes between trains per
line at weekday peak, midday and evening and at weekends).

**Open `index.html`** — a static page that works straight from disk or any static host.
It loads the grid from `data/amp.js` (a script, so no server is needed); map tiles and the
MapLibre/proj4 libraries come from CDNs. To publish, upload `index.html` and `data/*.js`
(not `data/raw/`).

## Data sources

| What | Source | Used for |
|---|---|---|
| Residents, dwellings, households and residential buildings per statistical subsection (Census 2021, BGRI 2021) | [INE](https://gisserver.ine.pt/arcgis/rest/services/BGRI2021/BGRI/FeatureServer/0), falls back to an ArcGIS Online republication | the counts |
| Building footprints + tags | OpenStreetMap ([Geofabrik](https://download.geofabrik.de/europe/portugal.html)) | placing residents inside each census subsection |
| ML building footprints | [Overture Maps](https://overturemaps.org) (Microsoft, Esri) | filling OSM gaps outside the big cities |
| Metro timetables (GTFS) | [Metro do Porto](https://www.metrodoporto.pt/pages/337) | train frequencies at each station |
| Net building height, population (100 m) | [GHSL](https://human-settlement.emergency.copernicus.eu) GHS-BUILT-H ANBH 2018, GHS-POP 2020 | floors where OSM has none; fallback for subsections with no buildings |

## How the numbers are made, and how accurate they are

Census subsections are irregular (the average resident's subsection is 8.7 ha: 3.4 ha in
Porto, 28 ha in Arouca) and never line up with a square grid, so each subsection's counts are spread
over the cells it touches through its buildings:

- residents, dwellings and households in proportion to residential floor area (footprint × floors);
- residential buildings one per footprint. The subsection's apartment buildings (built for 3+
  dwellings, INE) go to the footprints most likely to be apartments — OSM `apartments` tags first,
  then the largest floor area — and its single-family buildings (built for 1–2 dwellings) to the rest.
  Compared with an even split this cuts the apartment-share error in mixed blocks from 18 to 11 points.
  Buildings with 3+ floors are placed the same way, ranked by mapped OSM floors first; with so few
  buildings mapped this barely beats an even split (8.5 vs 8.6 points per block).
- average floors of apartment buildings: plain mean of OSM `building:levels`, shown only where at
  least one apartment-like building is mapped (20% of cells with apartment buildings, holding 41% of
  them). No satellite estimate: it tracked real floor counts too weakly (r = 0.27).

Accuracy was measured by rebuilding the census from coarser census sections with the same method
and comparing against the real figures, scaled to the equivalent cell size. Median error per cell:

| Cell size | Residents, dwellings, households | Household size | Residential buildings | Apartment share | 3+ floors share |
|---|---|---|---|---|---|
| 100 m | 20–40% | | 12–30% | ±11 pts | |
| 250 m | ~14% | ~3% | ~8% | ±4 pts | ±9 pts |
| 500 m | ~7% | | ~4% | ±3 pts | |
| 1 km | ~5% | | ~3% | | |

These are slightly optimistic, because the comparison grid is itself an estimate. Change the cell
size with `CELL` in `pipeline/build_grid.py`; its `ERRORS` table feeds the popup.

### Metro walking areas

Lines and stations come from OpenStreetMap's Metro do Porto route relations. The Rosa (G) and Rubi (H)
lines, under construction, are drawn dashed from OSM's construction track and station sites; their
colours are our own until official ones exist. Rubi's Arrábida, Candal and Rotunda stations are not in
OSM yet, so they are placed on the track by their described locations (Arrábida Shopping, the Candal
viaduct, Rotunda Edgar Cardoso), within a few hundred metres. A station's walking area is everything
reached in 10 minutes at 4.8 km/h — 800 m along streets, footpaths and stairs (no motorways) —
starting from the streets within 80 m of its platforms. The reached paths are widened by 50 m,
since front doors sit back from the street centre line, and enclosed gaps under 10 ha (block
interiors) are filled. Residents come from the grid, counting the share of each cell inside the
area, so they carry the grid's error. There is no time for stairs, slopes or waiting at crossings,
so in steep parts of Porto 10 minutes is generous.

Frequencies are counted from Metro do Porto's GTFS timetables: trains calling at each station per
hour, averaged over the two directions, on weekdays at peak (7:30–9:30), midday (11–16) and evening
(21–24), and on Saturdays and Sundays (10–19), shown as minutes between trains. Holiday-only services
are left out. GTFS stops are matched to stations by location, since a few names are spelled
differently.

## Rebuild

```sh
mkdir -p data/raw/osm data/raw/ghsl data/raw/gtfs
curl -L -o data/raw/osm/portugal-latest.osm.pbf https://download.geofabrik.de/europe/portugal-latest.osm.pbf
# GHSL tile R5_C18 (covers mainland Portugal's west coast), unzip into data/raw/ghsl/:
#   GHS_BUILT_H_ANBH_E2018_GLOBE_R2023A_54009_100_V1_0_R5_C18.zip
#   GHS_POP_E2020_GLOBE_R2023A_54009_100_V1_0_R5_C18.zip
# from https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/

uv run python pipeline/fetch_census.py amp
uv run python pipeline/extract_osm.py amp
uv run python pipeline/fetch_overture.py amp
uv run python pipeline/build_grid.py amp     # -> data/amp.js
# Metro do Porto timetables ("GTFS Horários" on https://www.metrodoporto.pt/pages/337):
curl -L -o data/raw/gtfs/amp_metro.zip https://www.metrodoporto.pt/metrodoporto/uploads/document/file/794/google_transit_04_09_2026.zip
uv run python pipeline/build_metro.py amp    # -> data/amp_metro.js (needs data/amp.js)

# Not used by the grid yet: DGT LiDAR surface/terrain tiles for building heights
# (needs a free cdd.dgterritorio.gov.pt account in ~/.dgt; ~4.4 GB for the AMP)
uv run python pipeline/fetch_lidar.py amp --dry-run
```

`index.html?region=<key>` loads `data/<key>.js`. New regions go in `pipeline/regions.py`
(an `aml` entry for Lisbon is defined but not yet built).
