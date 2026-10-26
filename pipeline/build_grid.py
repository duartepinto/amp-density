"""Build the 250 m x 250 m grid for a region and write the web map data file.

Method
------
Census 2021 counts (INE BGRI) are published per statistical subsection. Those
rarely line up with the grid (EPSG:3035, the INSPIRE/Eurostat grid), so each
subsection's counts are spread over the cells it touches through the buildings
inside it:

* buildings: OSM footprints, complemented by Overture's machine-learned footprints
  (Microsoft / Esri) where OSM has no building mapped yet
* buildings inside non-residential OSM areas (campuses, hospitals, parks, cemeteries,
  industrial land...) are skipped unless tagged as homes, as long as their subsection
  has other buildings to hold its residents
* floors: OSM `building:levels`, else GHSL GHS-BUILT-H net height / 3 m
* residents, dwellings, households -> proportional to residential floor area
  (footprint x floors)
* residential buildings -> one per footprint; the subsection's apartment buildings
  (built for 3+ dwellings) go to the footprints most likely to be apartments
  (OSM apartment tags, then the largest floor area), the single-family ones
  (built for 1-2 dwellings) to the rest. Buildings with 3+ floors are placed the
  same way, ranked by mapped OSM floors first.
* average floors of apartment buildings: only where OSM maps `building:levels`
* subsections without any matching building fall back to the GHSL GHS-POP
  100 m population surface (or plain area when GHSL sees nobody there)

Median cell errors, measured by rebuilding the census from coarser sections, are
in ERRORS below and in the README.

Usage: uv run python pipeline/build_grid.py amp
"""

import json
import re
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import shapely
from pyproj import Transformer

from regions import REGIONS

ROOT = Path(__file__).resolve().parent.parent
CELL = 250  # metres
# Median per-cell error by cell size. "pop" also holds for dwellings and households.
ERRORS = {
    100: {"pop": "20–40%", "res_bldg": "12–30%", "apt_share": "11 pts"},
    250: {"pop": "14%", "res_bldg": "8%", "apt_share": "4 pts", "rise_share": "9 pts", "hh_size": "3%"},
    500: {"pop": "7%", "res_bldg": "4%", "apt_share": "3 pts"},
}
FINE = 100  # metres; resolution of the GHSL fallback surface
CRS = 3035
FLOOR_HEIGHT_M = 3.0
GHSL_HEIGHT = ROOT / (
    "data/raw/ghsl/GHS_BUILT_H_ANBH_E2018_GLOBE_R2023A_54009_100_V1_0_R5_C18/"
    "GHS_BUILT_H_ANBH_E2018_GLOBE_R2023A_54009_100_V1_0_R5_C18.tif"
)
GHSL_POP = ROOT / (
    "data/raw/ghsl/GHS_POP_E2020_GLOBE_R2023A_54009_100_V1_0_R5_C18/"
    "GHS_POP_E2020_GLOBE_R2023A_54009_100_V1_0_R5_C18.tif"
)

# OSM building=* values that do not house residents. Anything not listed
# (mostly building=yes) is treated as possibly residential.
NOT_A_DWELLING = {
    "garage", "garages", "shed", "roof", "carport", "greenhouse", "transformer_tower",
    "ruins", "service", "hut", "cabin", "construction", "farm_auxiliary", "barn",
    "stable", "parking", "bunker", "toilets", "container", "storage_tank", "silo",
    "water_tower", "tower", "bridge", "collapsed", "abandoned", "kiosk", "pavilion",
}
COMMERCIAL = {"commercial", "retail", "office", "supermarket", "hotel", "mall", "shop", "store"}
INDUSTRIAL = {"industrial", "warehouse", "factory", "manufacture", "depot", "hangar"}
PUBLIC = {
    "school", "church", "chapel", "hospital", "university", "college", "public", "civic",
    "government", "sports_hall", "stadium", "train_station", "transportation", "fire_station",
    "kindergarten", "cathedral", "monastery", "convent", "religious", "museum", "sports_centre",
    "grandstand", "clinic", "townhall", "police", "military",
}
# Untagged (building=yes) buildings whose name starts like this are not homes: a sports
# hall, church, school, shop... Names of homes (Casa, Edifício, Bloco, Torre, Moradia,
# Residência, Lar - care homes count their residents) are not listed.
NOT_A_HOME_NAME = re.compile(
    r"^(?:centro|junta|pavilh[aã]o|caf[eé]|capela|restaurante|igreja|associa[cç][aã]o|pingo doce|continente|"
    r"escola|sal[aã]o|lidl|piscinas?|museu|bombeiros|mercado|hotel|sede|coreto|mercadona|lavadouro|bar|"
    r"c[aâ]mara|posto|padaria|burger|clube|moinho|esta[cç][aã]o|biblioteca|tribunal|gnr|psp|usf|"
    r"unidade de sa[uú]de|mcdonald|garagem|aldi|mosteiro|quiosque|oficina|auchan|intermarch[eé]|adega|"
    r"teatro|audit[oó]rio|f[aá]brica|armaz[eé]m|supermercado|hipermercado|hospital|cl[ií]nica|farm[aá]cia|"
    r"instituto|faculdade|universidade|col[eé]gio|jardim de inf[aâ]ncia|infant[aá]rio|creche|gin[aá]sio|"
    r"est[aá]dio|complexo|pol[ií]cia|quartel|cemit[eé]rio|cinema|banco|ctt|correios|loja|minipre[cç]o|"
    r"ermida|santu[aá]rio|convento|semin[aá]rio|cruzeiro|fonte|reservat[oó]rio)\b"
)
# Status and use tags that rule out a home, on any building.
NOT_A_HOME_TAGS = ["ruins", "abandoned", "disused", "leisure", "sport", "man_made", "healthcare"]
HISTORIC_HOMES = {"building", "manor", "house"}
# amenity/tourism values that still describe homes (care homes count their residents)
HOME_AMENITIES = {"social_facility", "nursing_home", "retirement_home", "shelter"}
HOME_TOURISM = {"apartment", "chalet"}
APARTMENT_TAGS = {"apartments", "residential", "dormitory"}
HOUSE_TAGS = {"house", "detached", "semidetached_house", "terrace", "bungalow", "farm"}
MIN_FOOTPRINT_M2 = 20

AREA_FIELDS = ["pop", "dwellings", "households"]
COUNTS = ["res_bldg", "houses", "apt_bldg", "low_rise", "mid_rise"]
FIELDS = [*AREA_FIELDS, *COUNTS]


def is_residential(b: gpd.GeoDataFrame) -> pd.Series:
    tag = b["building"].fillna("yes")
    use = b["building_use"].fillna("")
    business = (tag == "yes") & (
        b["shop"].notna() | b["office"].notna()
        | (b["tourism"].notna() & ~b["tourism"].isin(HOME_TOURISM))
        | (b["amenity"].notna() & ~b["amenity"].isin(HOME_AMENITIES))
    )
    named = (tag == "yes") & b["name"].fillna("").str.lower().str.strip().str.contains(NOT_A_HOME_NAME)
    status = b[NOT_A_HOME_TAGS].notna().any(axis=1) | (b["historic"].notna() & ~b["historic"].isin(HISTORIC_HOMES))
    return ~(
        tag.isin(NOT_A_DWELLING | COMMERCIAL | INDUSTRIAL | PUBLIC)
        | use.isin(COMMERCIAL | INDUSTRIAL)
        | b["amenity"].isin(PUBLIC)
        | business
        | named
        | status
        | (b.area < MIN_FOOTPRINT_M2)
    )


def apartment_score(b: gpd.GeoDataFrame) -> pd.Series:
    """Higher = more likely an apartment building: OSM tags first, then floor area."""
    tag = b["building"].fillna("yes")
    score = np.log1p(b.area * b["levels"])
    return score + np.where(tag.isin(APARTMENT_TAGS), 100, 0) - np.where(tag.isin(HOUSE_TAGS), 100, 0)


def tall_score(b: gpd.GeoDataFrame) -> pd.Series:
    """Higher = more likely to have 3+ floors: mapped OSM floors decide, else as apartments."""
    known = b["osm_levels"]
    return b["score"] + np.where(known >= 3, 200, 0) - np.where(known <= 2, 200, 0)


def sample_ghsl(raster: Path, points: gpd.GeoSeries) -> np.ndarray:
    """Value of the GHSL 100 m cell under each point (NaN where empty)."""
    with rasterio.open(raster) as src:
        pts = points.to_crs(src.crs)
        coords = np.column_stack([pts.x, pts.y])
        vals = np.array([v[0] for v in src.sample(coords)], dtype=float)
        vals[vals == src.nodata] = np.nan
    vals[vals <= 0] = np.nan
    return vals


def cell_index(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return np.floor(x / CELL).astype(np.int64), np.floor(y / CELL).astype(np.int64)


def load_buildings(raw: Path) -> gpd.GeoDataFrame:
    """Residential-candidate footprints (OSM + ML) with floors and apartment score."""
    osm = gpd.read_file(raw / "osm_buildings.gpkg").to_crs(CRS)
    ml = gpd.read_file(raw / "overture_buildings.gpkg").to_crs(CRS)
    print(f"{len(osm)} OSM + {len(ml)} ML buildings")

    # ML footprints carry no tags: they behave like an OSM building=yes.
    ml = ml.rename(columns={"num_floors": "building_levels"})[["building_levels", "geometry"]]
    b = pd.concat([osm.assign(src="osm"), ml.assign(src="ml")], ignore_index=True)
    for col in ["building", "building_use", "shop", "office", "amenity", "tourism", "name", "historic", *NOT_A_HOME_TAGS]:
        b[col] = b[col].astype(object)
    b = b[is_residential(b)].copy()

    levels = pd.to_numeric(b["building_levels"], errors="coerce")
    levels = levels.where((levels >= 1) & (levels <= 60))
    b["osm_levels"] = levels.where(b.src == "osm")
    ghsl_levels = sample_ghsl(GHSL_HEIGHT, b.geometry.centroid) / FLOOR_HEIGHT_M
    b["levels"] = levels.fillna(pd.Series(np.clip(ghsl_levels, 1, None), index=b.index)).fillna(1)
    b["full_area"] = b.area
    b["score"] = apartment_score(b)
    b["tall"] = tall_score(b)
    b["apartment_like"] = b["building"].isin(APARTMENT_TAGS) | (b.osm_levels >= 3)

    # Untagged buildings on a campus, in a park, a hospital... are not homes.
    areas = gpd.read_file(raw / "osm_nonres_areas.gpkg").to_crs(CRS)
    inside = gpd.sjoin(gpd.GeoDataFrame(geometry=b.geometry.representative_point(), crs=CRS),
                       areas[["geometry"]], predicate="within", how="inner").index.unique()
    homes = b["building"].isin(APARTMENT_TAGS | HOUSE_TAGS)
    b["nonres_area"] = b.index.isin(inside) & ~homes
    print(f"{b.nonres_area.sum()} candidate buildings stand in non-residential areas")
    return b[["levels", "osm_levels", "apartment_like", "full_area", "score", "tall", "nonres_area", "geometry"]]


def allocate(pieces: pd.DataFrame, totals: pd.DataFrame, unit: str) -> pd.DataFrame:
    """Spread each unit's census totals over its building pieces.

    `pieces` are footprints already cut along unit borders, with columns
    unit, area, levels, full_area, score. `totals` is indexed by unit.
    """
    p = pieces.join(totals[FIELDS], on=unit)
    g = p.groupby(unit)

    fa = p.area * p.levels
    fa_share = fa / fa.groupby(p[unit]).transform("sum")
    for field in AREA_FIELDS:
        p[field] = p[field] * fa_share

    # A building cut in two counts as half a building on each side.
    cw = p.area / p.full_area
    p["res_bldg"] = p.res_bldg * cw / cw.groupby(p[unit]).transform("sum")

    p = p.assign(cw=cw)
    p = ranked_split(p, unit, "score", top="apt_bldg", rest="houses")
    return ranked_split(p, unit, "tall", top="mid_rise", rest="low_rise")


def ranked_split(p: pd.DataFrame, unit: str, rank: str, top: str, rest: str) -> pd.DataFrame:
    """Split a unit's `top` + `rest` counts over its footprints: walk them from highest to
    lowest `rank` and give `top` to the first ones until its census share is reached."""
    p = p.sort_values([unit, rank], ascending=[True, False])
    g = p.groupby(unit)
    total_cw = g.cw.transform("sum")
    before = g.cw.cumsum() - p.cw
    known = (p[top] + p[rest]).replace(0, np.nan)
    target = (p[top] / known).fillna(0) * total_cw
    top_frac = ((target - before) / p.cw).clip(0, 1)
    for field, w in [(top, p.cw * top_frac), (rest, p.cw * (1 - top_frac))]:
        wsum = w.groupby(p[unit]).transform("sum")
        share = (w / wsum).where(wsum > 0, p.cw / total_cw)
        p[field] = p[field] * share
    return p


def fallback_allocation(subsections: gpd.GeoDataFrame) -> pd.DataFrame:
    """Spread counts of subsections by GHSL population x area share, on a 100 m subgrid."""
    rows = []
    for sub in subsections.itertuples():
        minx, miny, maxx, maxy = sub.geometry.bounds
        ex = np.arange(np.floor(minx / FINE), np.ceil(maxx / FINE))
        ny = np.arange(np.floor(miny / FINE), np.ceil(maxy / FINE))
        gx, gy = np.meshgrid(ex, ny)
        gx, gy = gx.ravel(), gy.ravel()
        boxes = shapely.box(gx * FINE, gy * FINE, (gx + 1) * FINE, (gy + 1) * FINE)
        share = shapely.area(shapely.intersection(boxes, sub.geometry)) / sub.geometry.area
        cx, cy = (gx + 0.5) * FINE, (gy + 0.5) * FINE
        ghsl = np.nan_to_num(sample_ghsl(GHSL_POP, gpd.GeoSeries(gpd.points_from_xy(cx, cy), crs=CRS))) * share
        if ghsl.sum() > 0:
            share = ghsl / ghsl.sum()
        keep = share > 0
        e, n = cell_index(cx[keep], cy[keep])
        rows.append(pd.DataFrame({"e": e, "n": n, **{f: getattr(sub, f) * share[keep] for f in FIELDS}}))
    return pd.concat(rows) if rows else pd.DataFrame(columns=["e", "n", *FIELDS])


def load_census(raw: Path) -> gpd.GeoDataFrame:
    census = gpd.read_file(raw / "bgri2021.gpkg").to_crs(CRS)
    census["geometry"] = census.geometry.make_valid()
    census = census.rename(columns={
        "N_INDIVIDUOS": "pop",
        "N_ALOJAMENTOS_TOTAL": "dwellings",
        "N_AGREGADOS_DOMESTICOS_PRIVADO": "households",
        "N_EDIFICIOS_CLASSICOS": "res_bldg",
        "N_EDIFICIOS_CLASS_CONST_1_OU_2": "houses",
        "N_EDIFICIOS_CLASS_CONST_3_OU_M": "apt_bldg",
        "N_EDIFICIOS_1_OU_2_PISOS": "low_rise",
        "N_EDIFICIOS_3_OU_MAIS_PISOS": "mid_rise",
    })
    census[FIELDS] = census[FIELDS].fillna(0)
    return census


def cut_by(buildings: gpd.GeoDataFrame, census: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Cut buildings along subsection borders: terraced rows are often mapped as one
    polygon spanning several census blocks."""
    pieces = gpd.overlay(buildings, census[["BGRI2021", "geometry"]], how="intersection", keep_geom_type=True)
    pieces = pieces[pieces.area >= 5].copy()
    pieces["area"] = pieces.area
    return pieces


def main(region: str) -> None:
    raw = ROOT / "data/raw" / region
    census = load_census(raw)
    print(f"{len(census)} subsections")
    buildings = load_buildings(raw)
    pieces = cut_by(buildings, census)

    # Buildings in non-residential areas only hold a subsection's residents when it has no other.
    elsewhere = ~pieces.nonres_area
    has_other = elsewhere.groupby(pieces.BGRI2021).transform("any")
    kept_in_area = pieces[pieces.nonres_area & ~has_other]
    print(f"{pieces.nonres_area.sum() - len(kept_in_area)} building pieces in non-residential areas skipped; "
          f"{kept_in_area.BGRI2021.nunique()} subsections have no other buildings and keep theirs")
    pieces = pieces[elsewhere | ~has_other]

    res = allocate(pieces, census.set_index("BGRI2021"), "BGRI2021")
    centre = res.geometry.centroid
    res["e"], res["n"] = cell_index(centre.x.values, centre.y.values)

    orphan = census[~census.BGRI2021.isin(set(res.BGRI2021)) & ((census["pop"] > 0) | (census.res_bldg > 0))]
    print(f"{len(orphan)} populated subsections without any building "
          f"({orphan['pop'].sum():.0f} residents) -> GHSL population weighting")

    cells = pd.concat([res[["e", "n", *FIELDS]], fallback_allocation(orphan)]).groupby(["e", "n"]).sum().reset_index()
    cells = cells[(cells["pop"] >= 0.5) | (cells.res_bldg >= 0.5)]
    cells = cells.join(apartment_floors(buildings), on=["e", "n"])
    cells[["apt_floors", "apt_floors_n"]] = cells[["apt_floors", "apt_floors_n"]].fillna(0)
    for f in FIELDS:
        print(f"  {f}: census {census[f].sum():.0f} -> grid {cells[f].sum():.0f}")
    write_output(region, cells)


def apartment_floors(buildings: gpd.GeoDataFrame) -> pd.DataFrame:
    """Mean OSM-mapped floors of apartment-like buildings per cell, and how many were mapped."""
    apt = buildings[buildings.apartment_like & buildings.osm_levels.notna()]
    centre = apt.geometry.centroid
    e, n = cell_index(centre.x.values, centre.y.values)
    g = apt.osm_levels.groupby([e, n])
    out = pd.DataFrame({"apt_floors": g.mean(), "apt_floors_n": g.size()})
    out.index.names = ["e", "n"]
    return out


def write_output(region: str, cells: pd.DataFrame) -> None:
    to_wgs = Transformer.from_crs(CRS, 4326, always_xy=True)
    lon0, lat0 = to_wgs.transform(cells.e.min() * CELL, cells.n.min() * CELL)
    lon1, lat1 = to_wgs.transform((cells.e.max() + 1) * CELL, (cells.n.max() + 1) * CELL)
    r1 = lambda s: np.round(s.to_numpy(), 1).tolist()

    # Written as a script rather than JSON so index.html can load it from file://.
    out = ROOT / "data" / f"{region}.js"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("window.GRID_DATA = " + json.dumps({
        "region": REGIONS[region]["name"],
        "crs": f"EPSG:{CRS}",
        "cell": CELL,
        "errors": ERRORS.get(CELL, {}),
        "bounds": [lon0, lat0, lon1, lat1],
        "count": len(cells),
        "columns": {
            "e": cells.e.astype(int).tolist(),
            "n": cells.n.astype(int).tolist(),
            **{f: np.round(cells[f].to_numpy()).astype(int).tolist() for f in AREA_FIELDS},
            "res_bldg": r1(cells.res_bldg),
            "houses": r1(cells.houses),
            "apt_bldg": r1(cells.apt_bldg),
            "low_rise": r1(cells.low_rise),
            "mid_rise": r1(cells.mid_rise),
            "apt_floors": r1(cells.apt_floors),
            "apt_floors_n": cells.apt_floors_n.astype(int).tolist(),
        },
    }, ensure_ascii=False, separators=(",", ":")) + ";\n")
    print(f"wrote {len(cells)} cells, {cells['pop'].sum():.0f} residents -> {out} ({out.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "amp")
