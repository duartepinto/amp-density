# Portugal density grid

An interactive map of where people live in the Área Metropolitana do Porto (AMP), on a
250 m × 250 m grid, with the metro and suburban train networks and how many residents each
station serves.

- **Cells** are coloured by residents per km² (Census 2021). Click one for its residents,
  dwellings, households, average household size, residential buildings (single-family vs
  apartment buildings, 1–2 vs 3+ floors) and the average floors of its apartment buildings. From
  zoom 13 the cell's buildings are highlighted on the map.
- **Metro do Porto** lines are drawn in their colours, side by side where they share track; the
  Rosa (G) and Rubi (H) lines under construction are dashed. **CP Porto** suburban trains are drawn
  as a railway (dark line, white dashes). Each network has its own toggle in the title card.
- **Click a station** to fade everything outside the area within a 10-minute walk of it, and see
  the residents in that area, its lines and, for the metro, how often trains call.

**Open `index.html`.** It is a static page that works straight from disk or any static host. It loads
the grid from `data/amp.js` and the rail layer from `data/amp_transit.js` (scripts, not fetched JSON,
so no server is needed). Map tiles (CARTO), MapLibre GL and proj4 come from CDNs. To publish,
upload `index.html` and `data/*.js`, not `data/raw/`.

## Data sources

| What | Source | Used for |
|---|---|---|
| Residents, dwellings, households and residential buildings per statistical subsection — Census 2021, BGRI 2021 | [INE](https://gisserver.ine.pt/arcgis/rest/services/BGRI2021/BGRI/FeatureServer/0) (CC BY 4.0); an ArcGIS Online republication of the same layer when INE's server is down | every count on the map |
| Building footprints and tags; non-residential areas; rail routes, stops and construction sites; streets and footpaths | OpenStreetMap via [Geofabrik](https://download.geofabrik.de/europe/portugal.html) (ODbL) | placing residents inside each subsection; rail lines and stations; walking areas |
| Machine-learned building footprints (Microsoft, Esri) | [Overture Maps](https://overturemaps.org) buildings, release 2026-10-21.0 (ODbL) | filling gaps where OSM has no building |
| Net building height and population, 100 m | [GHSL](https://human-settlement.emergency.copernicus.eu) GHS-BUILT-H ANBH 2018, GHS-POP 2020 (CC BY 4.0) | floors where OSM has none; subsections with no building at all |
| Metro timetables (GTFS) | [Metro do Porto](https://www.metrodoporto.pt/pages/337) | metro frequencies |
| Basemap | © OpenStreetMap contributors, © CARTO (Positron / Dark Matter) | background, streets, building shapes for the highlight |

## How the population grid is made

INE publishes census counts per **statistical subsection** (BGRI): 18,407 in the AMP, irregular
blocks that never line up with a square grid (the average resident's subsection is 8.7 ha: 3.4 ha in
Porto, 28 ha in Arouca). Each subsection's counts are spread over the cells it touches **through the
buildings inside it** (a dasymetric method), so people end up where the homes are, not evenly over
fields, parks and roads. Every count is preserved: the grid adds up to the census.

The grid is the INSPIRE/Eurostat grid (EPSG:3035), so cells line up with official European grids.

### 1. Buildings

OpenStreetMap footprints (374,258 in the AMP) plus Overture's machine-learned footprints that are
not from OSM (744,419) — Overture leaves out ML footprints where OSM already has a building, so they
fill OSM's gaps outside the big cities. ML footprints carry no tags and are treated like an OSM
`building=yes`. Footprints under 20 m² are ignored.

### 2. Which buildings can be homes

Most OSM buildings are tagged just `building=yes`, so the use has to be read from other clues. A
building is **not** a home when any of these holds:

| Rule | Examples |
|---|---|
| Its `building` tag is non-residential | garage, shed, roof, ruins, kiosk, greenhouse; commercial, retail, office, supermarket, hotel; industrial, warehouse, factory; school, church, hospital, university, stadium, train_station… |
| `building:use` is commercial or industrial | |
| It carries an `amenity` (other than a care facility or shelter), `shop`, `office` or `tourism` tag (other than a holiday apartment) | a building tagged amenity=restaurant, tourism=aquarium |
| It carries `ruins`, `abandoned`, `disused`, `leisure`, `sport`, `man_made` or `healthcare`, or a `historic` tag other than building/manor/house | sports halls, water mills, castles, ruins |
| It is a `building=yes` whose **name** starts with a non-residential word | Pavilhão, Escola, Igreja, Capela, Centro, Junta, Mercado, Armazém, Fábrica, Museu, Piscina, Bombeiros, Lidl, Continente… (names of homes such as Casa, Edifício, Bloco, Torre, Moradia, Residência, Lar are not listed) |
| It stands (by its centre) inside a **non-residential area** and is not tagged as a home | `amenity` university, college, school, hospital, grave_yard, parking; `landuse` education, cemetery, industrial, military, railway, religious, landfill, quarry, retail, garages, recreation_ground, port; `leisure` park, stadium, sports_centre, golf_course, pitch, track, water_park; airport; zoo, theme park; power plants and substations; wastewater plants and works |

Buildings tagged as homes (`apartments`, `residential`, `dormitory`, `house`, `detached`,
`semidetached_house`, `terrace`, `bungalow`, `farm`) always stay. The area rule only applies when a
subsection has other buildings: if all of a subsection's buildings stand in such areas (untagged
student residences on a campus, say), its residents stay on them rather than being lost.

Prisons and care homes are **not** excluded: the census counts inmates and care-home residents as
residents of those places.

### 3. Floors

`building:levels` from OSM (or `num_floors` on an Overture footprint), between 1 and 60; else GHSL
net building height ÷ 3 m (at least 1); else 1. Only OSM floors count as "mapped" for the floors
ranking and the average-floors figure.

### 4. Cutting along subsection borders

Buildings are cut where subsection borders cross them (terraced rows are often mapped as one
polygon spanning several blocks); slivers under 5 m² are dropped. Each piece belongs to one
subsection, and its counts go to the cell that contains the piece's centre.

### 5. Spreading each subsection's counts

| Census count | Spread over the subsection's buildings… |
|---|---|
| Residents, dwellings, private households | in proportion to **floor area** (footprint × floors), so a 6-floor block gets 6× a one-floor house of the same footprint |
| Residential buildings (with at least one dwelling) | **one per footprint**; a building cut in two counts as half on each side |
| Single-family (built for 1–2 dwellings) vs apartment buildings (built for 3+) | **ranked**: the subsection's apartment buildings go to the footprints most likely to be apartments — OSM `apartments`/`residential`/`dormitory` tags first, then the largest floor area; `house`-type tags go last — and its single-family ones to the rest |
| 1–2 floors vs 3+ floors | **ranked** the same way: buildings with 3+ mapped OSM floors first, those with 1–2 mapped floors last, the rest by the apartment ranking |

The ranked split walks a subsection's footprints from most to least likely and gives the
"apartment" (or "3+ floors") count to the first ones until the census share is reached, with the
boundary footprint split fractionally. Compared with spreading evenly, it cuts the apartment-share
error in mixed blocks from 18 to 11 points; for floors it barely helps (8.5 vs 8.6 points), because
few buildings have floors mapped.

### 6. Subsections without any building

147 populated subsections (about 5,700 residents) have no candidate building at all. Their counts
are spread over a 100 m sub-grid in proportion to the GHSL 2020 population surface, or by area where
GHSL sees nobody.

### 7. Cells and derived values

- A cell is kept when it has at least 0.5 residents or 0.5 residential buildings (19,851 cells).
- **Density** = residents ÷ 0.0625 km², a gross density over the whole cell (streets and parks
  included).
- **Average household size** = residents ÷ private households. Residents include people in
  collective housing (care homes, residences, prisons), so it reads high in cells that hold them.
- **Average floors of apartment buildings** = plain mean of OSM `building:levels` over the
  apartment-like buildings in the cell (tagged apartments/residential/dormitory, or 3+ mapped floors).
  It is shown only where at least one is mapped: 20% of the cells with apartment buildings, holding
  41% of them. A satellite estimate (GHSL height) tracked real floor counts too weakly (r = 0.27) to
  be used.

## How accurate it is

The real distribution inside a subsection is unknown, so accuracy was measured one level up: the
same method spreads the counts of census **sections** (1,443, about 13× larger) over their buildings,
and the result is compared with the real subsection figures. Because test units are larger than
real ones, test cells are scaled up by the same factor (a 1 km test cell behaves like a ~260 m real
one). Median error per cell:

| Cell size | Residents, dwellings, households | Household size | Residential buildings | Apartment share | 3+ floors share |
|---|---|---|---|---|---|
| 100 m | 20–40% | | 12–30% | ±11 pts | |
| 250 m | ~14% | ~3% | ~8% | ±4 pts | ±9 pts |
| 500 m | ~7% | | ~4% | ±3 pts | |
| 1 km | ~5% | | ~3% | | |

These are slightly optimistic, because the comparison grid is itself an estimate. They were
measured before the non-residential rules of step 2, which, in the same test, cut the residents
misplaced from 21.9% to 19.2% and the residents put in subsections that really have none by 63%
(21,900 → 8,100), so the 250 m errors are now somewhat lower. The cell size is `CELL` in
`pipeline/build_grid.py`; its `ERRORS` table feeds the popup notes.

## How the rail layer is made

The networks are configured under `transit` in `pipeline/regions.py`: for the AMP, Metro do Porto
(light rail) and CP Porto (suburban trains).

### Lines

- Read from each network's OSM route relations (`type=route`, by `network` tag) and grouped into
  lines by their route master, so service variants join their line (the B express, Bx, is drawn as B).
- Each line is drawn along one direction of travel, so double track shows once. Directions are
  chosen so that lines sharing track run the same way, and lines on the same track piece are offset
  side by side. Line ends are named after the first and last stops.
- **Metro** lines take their OSM colours. **Trains** are drawn in one railway style: different CP
  lines often run on parallel tracks a few metres apart, which can't be drawn side by side, and two
  CP colours are identical to metro lines A and D. Line identity is in the station panel and tooltip.
- Only track within 1.5 km of the AMP is kept.

### Stations

- A station is a line's stop positions (route members with a `stop` role) grouped by name; unnamed
  stop positions take the name of the nearest named one within 600 m. Only stations inside the AMP
  (within 200 m of its edge) are kept: 85 metro, 47 train.
- **Interchanges.** Stations with the same name (ignoring a "Porto - " prefix) are one station when
  their closest stops are within 250 m — Campanhã, São Bento, General Torres. Further apart they
  stay separate: Hospital São João (metro and train stops 390 m apart) and Contumil (480 m).
  Connected stations with different names are listed under `interchanges` (CP's Gaia station and
  Rubi's future Devesas station: *Gaia - Devesas*).
- An interchange keeps a marker at each of its stations, linked by a capsule-shaped connector, the
  usual transit-map sign; markers closer than 25 m merge into one.

### Lines under construction

OSM has the Rosa and Rubi lines only as construction track (`railway=construction`, named after the
line, `operator=Metro do Porto`), with no routes. Track named after a line belongs to it; pieces in
between with another name (the Ponte D. Antónia Ferreira) join the line they touch. Their stations
are the construction sites tagged as stations within 150 m of the track. Rubi's Arrábida, Candal and
Rotunda stations are not in OSM yet; they are placed on the track nearest their described locations
(Arrábida Shopping, the Candal viaduct, Rotunda Edgar Cardoso), 90–280 m from those points, and
marked as approximate. A future station sharing a name with an open one becomes part of it (São
Bento, Casa da Música, Santo Ovídio). Line letters G and H follow Metro do Porto; the pink and ruby
colours are ours, chosen to stay distinct from lines A–F also for colour-blind readers, until
official ones exist. The opening year comes from OSM's `opening_date` where tagged (Rosa: 2027).

### Walking areas

- **Network:** OSM ways with a `highway` tag, except motorways, ways under construction or
  proposed, raceways and bus guideways; `foot=no` and `access=no/private` are excluded, `foot=yes`
  always included; trunk roads only where a sidewalk is mapped.
- **Start:** every street point within 80 m of the station's stop positions (or the nearest one),
  with the straight-line distance already walked. For an interchange, all its open stations' stops.
  A future station starts from its site, or from the open station it joins.
- **Reach:** shortest paths along the network up to 800 m (10 minutes at 4.8 km/h), including the
  reached part of partly walked streets.
- **Area:** the reached paths widened by 50 m (front doors sit back from the street centre line),
  with enclosed gaps under 10 ha (block interiors) filled, simplified to 5 m.
- **Residents:** from the grid, each cell counted by the share of its area inside the walking area;
  so they carry the grid's error.
- **Coverage** in the title card is the union of the walking areas of each network's open stations:
  17% of AMP residents live within a 10-minute walk of a metro station (18% once Rosa and Rubi open),
  9% of a train station, 23% of either.

No time is added for stairs, slopes, crossings or reaching the platform, so in steep parts of Porto
10 minutes is generous.

### Frequencies

From Metro do Porto's GTFS feed (no CP timetable yet). For each station and period, the trains
departing are counted per line and direction:

| Period | Days (`calendar.txt`) | Hours |
|---|---|---|
| Peak | weekdays | 7:30–9:30 |
| Midday | weekdays | 11:00–16:00 |
| Evening | weekdays | 21:00–24:00 |
| Sat | Saturdays | 10:00–19:00 |
| Sun | Sundays and holidays | 10:00–19:00 |

- Trains per hour of a line = (trains in both directions ÷ 2) ÷ hours; shown as **minutes between
  trains** = 60 ÷ trains per hour (one decimal under 5 minutes). Service variants keep their own row
  (Bx).
- The **All** row is all lines together, per direction; the headline is all trains in both
  directions at weekday peak (Trindade: 73 an hour).
- Holiday-only services (`calendar_dates.txt` additions) are left out.
- GTFS stops are matched to stations by location (within 300 m of a stop position), because a few
  names are spelled differently.
- The panel shows the feed's validity dates. The current feed covers 7 Sep – 25 Oct 2026; refresh it
  when Metro do Porto publishes a new one.

## Display rules on the page

- **Density classes** (residents/km²): 0–100, 100–500, 500–1k, 1k–2.5k, 2.5k–5k, 5k–10k, 10k–15k,
  15k+ (63 cells, mostly central Porto), on a
  green → orange → red ramp that also darkens step by step, so the order reads without colour.
  Cells are 85% opaque at zoom 9, fading to 60% at zoom 15.
- **Layer order:** basemap land and water, basemap buildings, cells, the selected cell's buildings,
  streets, rail lines and stations, labels — so streets stay readable over the cells.
- **Building highlight:** from zoom 13 (where the basemap has building shapes), buildings whose
  outline centre falls inside the selected cell.
- **Stations:** interchanges (more than one line) get bigger markers; future stations have a grey
  outline; planned lines are dashed; the selected station's marker is filled.
- **Errors:** the popup notes show the typical error for the cell size (from `ERRORS`).
- Light and dark themes follow the system setting. The URL hash keeps the view;
  `index.html?region=<key>` loads `data/<key>.js` and `data/<key>_transit.js`.

## Rebuild

```sh
mkdir -p data/raw/osm data/raw/ghsl data/raw/gtfs
curl -L -o data/raw/osm/portugal-latest.osm.pbf https://download.geofabrik.de/europe/portugal-latest.osm.pbf
# GHSL tile R5_C18 (covers mainland Portugal's west coast), unzip into data/raw/ghsl/:
#   GHS_BUILT_H_ANBH_E2018_GLOBE_R2023A_54009_100_V1_0_R5_C18.zip
#   GHS_POP_E2020_GLOBE_R2023A_54009_100_V1_0_R5_C18.zip
# from https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/
# Metro do Porto timetables ("GTFS Horários" on https://www.metrodoporto.pt/pages/337):
curl -L -o data/raw/gtfs/amp_metro.zip https://www.metrodoporto.pt/metrodoporto/uploads/document/file/794/google_transit_04_09_2026.zip

uv run python pipeline/fetch_census.py amp      # census subsections   -> data/raw/amp/bgri2021.gpkg
uv run python pipeline/extract_osm.py amp       # OSM buildings, areas -> data/raw/amp/osm_*.gpkg
uv run python pipeline/fetch_overture.py amp    # ML footprints        -> data/raw/amp/overture_buildings.gpkg
uv run python pipeline/build_grid.py amp        # population grid      -> data/amp.js
uv run python pipeline/build_transit.py amp     # rail layer           -> data/amp_transit.js (needs data/amp.js)
```

`build_grid.py` takes about half a minute and `build_transit.py` under a minute; each prints the
census totals or coverage it produced, to check against.

Not used yet: `pipeline/fetch_lidar.py` downloads DGT LiDAR surface and terrain models (2 m) for
real building heights, which would give floors everywhere instead of only where OSM maps them. It
needs a free [cdd.dgterritorio.gov.pt](https://cdd.dgterritorio.gov.pt) account in `~/.dgt` and
about 4.4 GB for the AMP (`--dry-run` lists the tiles without downloading).

## Configuration

| Where | What |
|---|---|
| `pipeline/regions.py` | regions: name, census `where` clause, bounding box, rail networks (`transit`), linked stations (`interchanges`), lines under construction (`planned`). An `aml` entry for Lisbon is defined but not yet built. |
| `pipeline/build_grid.py` | `CELL` (cell size), `ERRORS`, `FLOOR_HEIGHT_M`, the building rules (`NOT_A_DWELLING`, `COMMERCIAL`, `INDUSTRIAL`, `PUBLIC`, `NOT_A_HOME_NAME`, `NOT_A_HOME_TAGS`…) |
| `pipeline/extract_osm.py` | `NONRES_AREAS`, the non-residential areas |
| `pipeline/build_transit.py` | `WALK_MINUTES`, `WALK_KMH`, `REACH_M`, `SNAP_M`, `INTERCHANGE_M`, `PERIODS`… |
| `index.html` | density classes (`BREAKS`, `RAMP`), line widths, basemaps |

## Files

```
index.html                  the map page (HTML, CSS and JS in one file)
data/amp.js                 the grid: cell columns (window.GRID_DATA)
data/amp_transit.js         lines, stations, walking areas, frequencies (window.TRANSIT_DATA)
data/raw/                   downloads and intermediate files (not published, not in git)
pipeline/regions.py         region and network configuration
pipeline/fetch_census.py    INE BGRI 2021 subsections
pipeline/extract_osm.py     OSM buildings and non-residential areas from the Geofabrik extract
pipeline/fetch_overture.py  Overture ML building footprints
pipeline/build_grid.py      the population grid
pipeline/build_transit.py   the rail layer
pipeline/fetch_lidar.py     DGT LiDAR tiles (not used yet)
```

## Known limitations

- **Census vintage:** counts are from the 2021 census; new buildings since are not reflected.
- **OSM completeness:** the building rules depend on what is mapped. A home inside a mapped
  campus or park loses its residents unless tagged as a home; an untagged office tower outside any
  mapped area can still receive residents.
- **Floors** are mapped in OSM for only some buildings; elsewhere GHSL heights (100 m, 2018) stand in.
- **Single cells** are estimates: use the error table above, and prefer patterns over individual
  cells, especially at the edge of large subsections.
- **Walking areas** ignore slopes, stairs and waiting at crossings, and depend on OSM footpaths.
- **Frequencies** are from a timetable, not real running, and only for the metro.
- **Rosa and Rubi:** colours are provisional and three Rubi stations are placed approximately.
