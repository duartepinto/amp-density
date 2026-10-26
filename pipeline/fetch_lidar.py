"""Download DGT LiDAR elevation tiles (2 m surface + terrain models) for a region.

Building height = surface model (MDS, includes roofs) - terrain model (MDT, bare ground).
Source: Direção-Geral do Território, Centro de Dados (https://cdd.dgterritorio.gov.pt/),
national LiDAR survey 2024-25. Tiles are 1 km x 1 km GeoTIFFs in EPSG:3763.

Searching the catalogue is public; downloading needs a free CDD account. Put its
credentials in ~/.dgt (keep it private: chmod 600 ~/.dgt):

    login your@email.pt
    password your_password

Only tiles that contain residential grid cells (data/<region>.js) are fetched, and
tiles already on disk are skipped, so the script can be re-run to resume.

Output: data/raw/lidar/<collection>/<tile>.tif

Usage: uv run python pipeline/fetch_lidar.py amp [--dry-run]
"""

import json
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import requests
import shapely
from pyproj import Transformer

ROOT = Path(__file__).resolve().parent.parent
SEARCH = "https://cdd.dgterritorio.gov.pt/dgt-be/v1/search"
SITE = "https://cdd.dgterritorio.gov.pt"
AUTH = "https://auth.cdd.dgterritorio.gov.pt/realms/dgterritorio/protocol/openid-connect/auth"
COLLECTIONS = ["MDS-2m", "MDT-2m"]
QUERY_STEP_DEG = 0.1  # search in ~8 x 11 km boxes, well under the 1000-item page limit
DELAY_S = 0.5


class LoginForm(HTMLParser):
    """Finds the Keycloak login form's action URL and hidden fields."""

    def __init__(self):
        super().__init__()
        self.action, self.fields, self._inside = None, {}, False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and a.get("id") == "kc-form-login":
            self.action, self._inside = a.get("action"), True
        elif tag == "input" and self._inside and a.get("name"):
            self.fields[a["name"]] = a.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self._inside = False


def residential_cells(region: str) -> shapely.Geometry:
    """Union of the region's grid cells that have residential buildings, in lon/lat."""
    text = (ROOT / "data" / f"{region}.js").read_text()
    grid = json.loads(text.split("=", 1)[1].rstrip().rstrip(";"))
    cols, size = grid["columns"], grid["cell"]
    keep = np.array(cols["res_bldg"]) > 0
    e, n = np.array(cols["e"])[keep] * size, np.array(cols["n"])[keep] * size
    to_wgs = Transformer.from_crs(3035, 4326, always_xy=True)
    lon0, lat0 = to_wgs.transform(e, n)
    lon1, lat1 = to_wgs.transform(e + size, n + size)
    return shapely.union_all(shapely.box(lon0, lat0, lon1, lat1))


def find_tiles(area: shapely.Geometry) -> dict[str, dict]:
    """Latest version of every tile intersecting `area`, per collection: {item id: item}."""
    minx, miny, maxx, maxy = area.bounds
    tiles: dict[tuple[str, str], dict] = {}
    for lon in np.arange(minx, maxx, QUERY_STEP_DEG):
        for lat in np.arange(miny, maxy, QUERY_STEP_DEG):
            box = shapely.box(lon, lat, lon + QUERY_STEP_DEG, lat + QUERY_STEP_DEG)
            if not box.intersects(area):
                continue
            r = requests.post(SEARCH, json={"bbox": list(box.bounds), "limit": 1000, "collections": COLLECTIONS}, timeout=60)
            r.raise_for_status()
            for item in r.json()["features"]:
                if not shapely.geometry.shape(item["geometry"]).intersects(area):
                    continue
                key = (item["collection"], item["id"].rsplit("-", 2)[0])  # same tile, any date/version
                best = tiles.get(key)
                if best is None or item["properties"].get("version", "") > best["properties"].get("version", ""):
                    tiles[key] = item
            time.sleep(DELAY_S)
    return {item["id"]: item for item in tiles.values()}


def read_credentials() -> tuple[str, str]:
    path = Path.home() / ".dgt"
    if not path.exists():
        sys.exit(f"Missing {path}. Create it with two lines:\n  login your@email.pt\n  password your_password")
    creds = dict(line.split(" ", 1) for line in path.read_text().splitlines() if " " in line)
    return creds["login"].strip(), creds["password"].strip()


def login() -> requests.Session:
    user, password = read_credentials()
    s = requests.Session()
    s.headers["User-Agent"] = "portugal-density/0.1 (research; python-requests)"
    s.get(SITE, timeout=30)
    params = {"client_id": "aai-oidc-dgt", "response_type": "code", "scope": "openid profile email",
              "redirect_uri": f"{SITE}/auth/callback"}
    page = s.get(f"{AUTH}?{urlencode(params)}", timeout=30)
    form = LoginForm()
    form.feed(page.text)
    if not form.action:
        sys.exit("Could not find the DGT login form; the site may have changed.")
    data = {**form.fields, "username": user, "password": password}
    done = s.post(form.action, data=data, timeout=30, allow_redirects=True)
    if not done.url.startswith(SITE):
        sys.exit("DGT login failed: check the credentials in ~/.dgt.")
    return s


def download(session: requests.Session, item: dict, out: Path) -> int:
    r = session.get(item["assets"]["data"]["href"], stream=True, timeout=120)
    r.raise_for_status()
    if "html" in r.headers.get("Content-Type", ""):
        raise RuntimeError("got a web page instead of a GeoTIFF (session expired?)")
    tmp = out.with_suffix(".part")
    with tmp.open("wb") as f:
        for chunk in r.iter_content(1 << 20):
            f.write(chunk)
    tmp.rename(out)
    return out.stat().st_size


def main(region: str, dry_run: bool) -> None:
    tiles = find_tiles(residential_cells(region))
    total = sum(t["properties"].get("file:size", 0) for t in tiles.values())
    by_coll = {c: sum(t["collection"] == c for t in tiles.values()) for c in COLLECTIONS}
    print(f"{len(tiles)} tiles ({by_coll}), about {total / 1e9:.1f} GB")
    if dry_run:
        return

    session, done = login(), 0
    for i, (tile_id, item) in enumerate(sorted(tiles.items()), 1):
        out = ROOT / "data/raw/lidar" / item["collection"] / f"{tile_id}.tif"
        if out.exists():
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(3):
            try:
                download(session, item, out)
                break
            except (requests.RequestException, RuntimeError) as e:
                print(f"  {tile_id}: {e}; logging in again", file=sys.stderr)
                time.sleep(2 ** attempt)
                session = login()
        else:
            sys.exit(f"giving up on {tile_id}; re-run to resume")
        done += 1
        if done % 25 == 0:
            print(f"  {i}/{len(tiles)} tiles")
        time.sleep(DELAY_S)
    print(f"done: {done} downloaded, {len(tiles) - done} already on disk")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0] if args else "amp", "--dry-run" in sys.argv)
