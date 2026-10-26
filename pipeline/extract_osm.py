"""Extract building footprints from an OSM extract.

Input:  data/raw/osm/portugal-latest.osm.pbf  (https://download.geofabrik.de/europe/portugal.html)
Output: data/raw/<region>/osm_buildings.gpkg     (polygons, EPSG:3763)
        data/raw/<region>/osm_nonres_areas.gpkg  (polygons, EPSG:3763)

The tags kept are the ones needed to tell homes apart from shops, offices,
public and industrial buildings when placing residents. The non-residential
areas (campuses, hospitals, parks, cemeteries, industrial land...) catch the
many buildings inside them that are only tagged building=yes.

Usage: uv run python pipeline/extract_osm.py amp
"""

import sys
from pathlib import Path

import geopandas as gpd
import osmium
import shapely

from regions import REGIONS

ROOT = Path(__file__).resolve().parent.parent
PBF = ROOT / "data/raw/osm/portugal-latest.osm.pbf"

BUILDING_TAGS = [
    "building", "building:levels", "building:use", "shop", "office", "amenity", "tourism",
    "name", "historic", "ruins", "abandoned", "disused", "leisure", "sport", "man_made", "healthcare",
]

# Areas where nobody lives, whatever their buildings are tagged.
NONRES_AREAS = {
    "amenity": ["university", "college", "school", "hospital", "grave_yard", "parking"],  # not prisons: inmates are census residents
    "landuse": ["education", "cemetery", "industrial", "military", "railway", "religious", "landfill",
                "quarry", "retail", "garages", "recreation_ground", "port"],
    "leisure": ["park", "stadium", "sports_centre", "golf_course", "pitch", "track", "water_park"],
    "aeroway": ["aerodrome"],
    "tourism": ["zoo", "theme_park"],
    "power": ["plant", "substation"],
    "man_made": ["wastewater_plant", "works"],
}


def in_bbox(lon: float, lat: float, bbox) -> bool:
    return bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]


def extract_buildings(bbox) -> gpd.GeoDataFrame:
    wkb = osmium.geom.WKBFactory()
    rows = []
    fp = (
        osmium.FileProcessor(str(PBF))
        .with_areas(osmium.filter.KeyFilter("building"))
        .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA))
        .with_filter(osmium.filter.KeyFilter("building"))
    )
    for area in fp:
        try:
            geom = shapely.from_wkb(wkb.create_multipolygon(area))
        except RuntimeError:
            continue  # broken multipolygon
        c = geom.centroid
        if not in_bbox(c.x, c.y, bbox):
            continue
        row = {"osm_id": area.orig_id(), "osm_type": "w" if area.from_way() else "r"}
        for tag in BUILDING_TAGS:
            row[tag.replace(":", "_")] = area.tags.get(tag)
        row["geometry"] = geom
        rows.append(row)
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=4326).to_crs(3763)



def extract_nonres_areas(bbox) -> gpd.GeoDataFrame:
    wkb = osmium.geom.WKBFactory()
    pairs = [(k, v) for k, vs in NONRES_AREAS.items() for v in vs]
    rows = []
    fp = (
        osmium.FileProcessor(str(PBF))
        .with_areas(osmium.filter.TagFilter(*pairs))
        .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA))
        .with_filter(osmium.filter.TagFilter(*pairs))
    )
    for area in fp:
        try:
            geom = shapely.from_wkb(wkb.create_multipolygon(area))
        except RuntimeError:
            continue
        c = geom.centroid
        if not in_bbox(c.x, c.y, bbox):
            continue
        kind = next(f"{k}={area.tags[k]}" for k, v in pairs if area.tags.get(k) == v)
        rows.append({"kind": kind, "name": area.tags.get("name"), "geometry": geom})
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=4326).to_crs(3763)


def main(region: str) -> None:
    bbox = REGIONS[region]["bbox"]
    out_dir = ROOT / "data/raw" / region
    out_dir.mkdir(parents=True, exist_ok=True)

    buildings = extract_buildings(bbox)
    buildings.to_file(out_dir / "osm_buildings.gpkg", driver="GPKG")
    print(f"wrote {len(buildings)} buildings")

    areas = extract_nonres_areas(bbox)
    areas.to_file(out_dir / "osm_nonres_areas.gpkg", driver="GPKG")
    print(f"wrote {len(areas)} non-residential areas")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "amp")
