"""Download INE Census 2021 statistical subsections (BGRI 2021) for a region.

Source: INE ArcGIS feature service (CC-BY 4.0)
https://gisserver.ine.pt/arcgis/rest/services/BGRI2021/BGRI/FeatureServer/0
When INE's server is unreachable, an ArcGIS Online republication of the same
layer (identical schema, all 210 170 subsections) is used instead.

Usage: uv run python pipeline/fetch_census.py amp
"""

import json
import sys
import time
from pathlib import Path

import geopandas as gpd
import requests

from regions import REGIONS

SERVICES = [
    "https://gisserver.ine.pt/arcgis/rest/services/BGRI2021/BGRI/FeatureServer/0/query",
    "https://services.arcgis.com/CCZiGSEQbAxxFVh3/arcgis/rest/services/BGRI_Censos_2021/FeatureServer/3/query",
]
PAGE_SIZE = 2000
ROOT = Path(__file__).resolve().parent.parent

FIELDS = ["BGRI2021", "DTMN21", "N_INDIVIDUOS"]


def fetch_page(service: str, where: str, offset: int) -> dict:
    params = {
        "where": where,
        "outFields": ",".join(FIELDS),
        "outSR": 3763,
        "orderByFields": "OBJECTID",
        "resultOffset": offset,
        "resultRecordCount": PAGE_SIZE,
        "f": "geojson",
    }
    for attempt in range(3):
        try:
            r = requests.get(service, params=params, timeout=60)
            r.raise_for_status()
            page = r.json()
            if "error" in page:
                raise requests.RequestException(page["error"])
            return page
        except (requests.RequestException, json.JSONDecodeError) as e:
            print(f"  retry {attempt + 1} after error: {e}", file=sys.stderr)
            time.sleep(2**attempt)
    raise RuntimeError(f"failed to fetch offset {offset}")


def main(region: str) -> None:
    where = REGIONS[region]["where"]
    out = ROOT / "data/raw" / region / "bgri2021.gpkg"
    out.parent.mkdir(parents=True, exist_ok=True)

    for service in SERVICES:
        try:
            features = fetch_all(service, where)
            break
        except RuntimeError as e:
            print(f"{service} unavailable ({e}), trying next source", file=sys.stderr)
    else:
        raise SystemExit("no census source reachable")

    # The service emits GeoJSON in the requested outSR despite the spec's WGS84 default.
    gdf = gpd.GeoDataFrame.from_features(features, crs=3763)
    gdf.to_file(out, driver="GPKG")
    print(f"wrote {len(gdf)} subsections, {int(gdf.N_INDIVIDUOS.sum())} residents -> {out}")


def fetch_all(service: str, where: str) -> list[dict]:
    print(f"fetching from {service}")
    features = []
    offset = 0
    while True:
        page = fetch_page(service, where, offset)
        batch = page.get("features", [])
        features.extend(batch)
        print(f"  fetched {len(features)} subsections")
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return features


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "amp")
