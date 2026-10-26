"""Build the 250 m x 250 m population grid for a region and write the web map data file.

Method
------
Residents (INE Census 2021, BGRI) are published per statistical subsection. Those
rarely line up with the grid (EPSG:3035, the INSPIRE/Eurostat grid), so each
subsection's residents are spread over the cells it touches in proportion to the
residential floor area (footprint x floors) of the buildings inside it:

* buildings: OSM footprints, complemented by Overture's machine-learned footprints
  (Microsoft / Esri) where OSM has no building mapped yet
* floors: OSM `building:levels`, else GHSL GHS-BUILT-H net height / 3 m
* subsections without any matching building fall back to the GHSL GHS-POP
  100 m population surface (or plain area when GHSL sees nobody there)

Median cell error of this allocation, measured by rebuilding the census from
coarser sections (see README): ~14% at 250 m, ~7% at 500 m, 20-40% at 100 m.

Usage: uv run python pipeline/build_grid.py amp
"""

import json
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
TYPICAL_ERROR = {100: "20–40%", 200: "15%", 250: "14%", 500: "7%", 1000: "5%"}  # median, per cell
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
MIN_FOOTPRINT_M2 = 20


def is_residential(b: gpd.GeoDataFrame) -> pd.Series:
    tag = b["building"].fillna("yes")
    use = b["building_use"].fillna("")
    business = (tag == "yes") & (b["shop"].notna() | b["office"].notna() | b["tourism"].isin({"hotel", "hostel"}))
    return ~(
        tag.isin(NOT_A_DWELLING | COMMERCIAL | INDUSTRIAL | PUBLIC)
        | use.isin(COMMERCIAL | INDUSTRIAL)
        | b["amenity"].isin(PUBLIC)
        | business
        | (b.area < MIN_FOOTPRINT_M2)
    )


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


def fallback_allocation(subsections: gpd.GeoDataFrame) -> pd.DataFrame:
    """Spread residents of subsections by GHSL population x area share, on a 100 m subgrid."""
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
        rows.append(pd.DataFrame({"e": e, "n": n, "pop": sub.pop * share[keep]}))
    return pd.concat(rows) if rows else pd.DataFrame(columns=["e", "n", "pop"])


def main(region: str) -> None:
    raw = ROOT / "data/raw" / region
    census = gpd.read_file(raw / "bgri2021.gpkg").to_crs(CRS)
    census["geometry"] = census.geometry.make_valid()
    census = census.rename(columns={"N_INDIVIDUOS": "pop"})
    census["pop"] = census["pop"].fillna(0)
    osm = gpd.read_file(raw / "osm_buildings.gpkg").to_crs(CRS)
    ml = gpd.read_file(raw / "overture_buildings.gpkg").to_crs(CRS)
    print(f"{len(census)} subsections, {len(osm)} OSM + {len(ml)} ML buildings")

    # ML footprints carry no tags: they behave like an OSM building=yes.
    ml = ml.rename(columns={"num_floors": "building_levels"})[["building_levels", "geometry"]]
    buildings = pd.concat([osm, ml], ignore_index=True)
    for col in ["building", "building_use", "shop", "office", "amenity", "tourism"]:
        buildings[col] = buildings[col].astype(object)
    buildings = buildings[is_residential(buildings)]

    levels = pd.to_numeric(buildings["building_levels"], errors="coerce")
    levels = levels.where((levels >= 1) & (levels <= 60))
    ghsl_levels = sample_ghsl(GHSL_HEIGHT, buildings.geometry.centroid) / FLOOR_HEIGHT_M
    buildings["levels"] = levels.fillna(pd.Series(np.clip(ghsl_levels, 1, None), index=buildings.index)).fillna(1)

    # Cut buildings along subsection borders: terraced rows are often mapped as one
    # polygon spanning several census blocks.
    res = gpd.overlay(buildings[["levels", "geometry"]], census[["BGRI2021", "geometry"]],
                      how="intersection", keep_geom_type=True)
    res = res[res.area >= 5]
    piece = res.geometry.centroid
    res["e"], res["n"] = cell_index(piece.x.values, piece.y.values)
    res["floor_area"] = res.area * res.levels
    res["w"] = res.floor_area / res.groupby("BGRI2021").floor_area.transform("sum")
    res = res.merge(census[["BGRI2021", "pop"]], on="BGRI2021")
    res["pop"] = res["pop"] * res.w

    orphan = census[~census.BGRI2021.isin(set(res.BGRI2021)) & (census["pop"] > 0)]
    print(f"{len(orphan)} populated subsections without any building "
          f"({orphan['pop'].sum():.0f} residents) -> GHSL population weighting")

    cells = pd.concat([res[["e", "n", "pop"]], fallback_allocation(orphan)]).groupby(["e", "n"]).sum().reset_index()
    cells = cells[cells["pop"] >= 0.5]
    write_output(region, cells)


def write_output(region: str, cells: pd.DataFrame) -> None:
    to_wgs = Transformer.from_crs(CRS, 4326, always_xy=True)
    lon0, lat0 = to_wgs.transform(cells.e.min() * CELL, cells.n.min() * CELL)
    lon1, lat1 = to_wgs.transform((cells.e.max() + 1) * CELL, (cells.n.max() + 1) * CELL)

    # Written as a script rather than JSON so index.html can load it from file://.
    out = ROOT / "data" / f"{region}.js"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("window.GRID_DATA = " + json.dumps({
        "region": REGIONS[region]["name"],
        "crs": f"EPSG:{CRS}",
        "cell": CELL,
        "typical_error": TYPICAL_ERROR.get(CELL),
        "bounds": [lon0, lat0, lon1, lat1],
        "count": len(cells),
        "columns": {
            "e": cells.e.astype(int).tolist(),
            "n": cells.n.astype(int).tolist(),
            "pop": np.round(cells["pop"].to_numpy()).astype(int).tolist(),
        },
    }, separators=(",", ":")) + ";\n")
    print(f"wrote {len(cells)} cells, {cells['pop'].sum():.0f} residents -> {out} ({out.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "amp")
