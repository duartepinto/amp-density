"""Download building footprints that OSM is missing, from Overture Maps.

Overture's building layer merges OSM with machine-learned footprints (Microsoft,
Google, Esri). OSM coverage outside the larger cities is patchy, so the
non-OSM footprints fill the gaps used to place census residents.

Output: data/raw/<region>/overture_buildings.gpkg (EPSG:3763)

Usage: uv run python pipeline/fetch_overture.py amp
"""

import sys
from pathlib import Path

import duckdb
import geopandas as gpd
import shapely

from regions import REGIONS

ROOT = Path(__file__).resolve().parent.parent
RELEASE = "2026-10-21.0"
SOURCE = f"s3://overturemaps-us-west-2/release/{RELEASE}/theme=buildings/type=building/*"


def main(region: str) -> None:
    xmin, ymin, xmax, ymax = REGIONS[region]["bbox"]
    out = ROOT / "data/raw" / region / "overture_buildings.gpkg"

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
    con.execute("SET s3_region='us-west-2';")
    df = con.execute(f"""
        SELECT id, sources[1].dataset AS source, height, num_floors, ST_AsWKB(geometry) AS wkb
        FROM read_parquet('{SOURCE}', hive_partitioning = 1)
        WHERE bbox.xmin BETWEEN {xmin} AND {xmax}
          AND bbox.ymin BETWEEN {ymin} AND {ymax}
          AND sources[1].dataset != 'OpenStreetMap'
    """).df()

    gdf = gpd.GeoDataFrame(
        df.drop(columns="wkb"), geometry=shapely.from_wkb(df.wkb.map(bytes)), crs=4326
    ).to_crs(3763)
    gdf.to_file(out, driver="GPKG")
    print(f"wrote {len(gdf)} non-OSM footprints -> {out}")
    print(gdf.source.value_counts().to_string())


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "amp")
