"""Figures per line for the networks with `line_stats` in regions.py (the metro): the
residents a line could serve, per station and per km, how many of them live in the core
municipality (Porto) and outside it, and the points of interest within a walk of its stations.

- Stations of a line: those it serves; for a line under construction, those it will serve.
- Residents served: those within WALK_MINUTES of any of the line's stations (the union of
  their walking areas, so nobody counts twice), from the grid as in build_transit.py.
- New (planned lines): residents served who live beyond a walk of every open metro station
  and, for a projected line, of every one being built too: those it would bring within reach.
- Per station: the mean of each station's own walking-area residents (areas overlap, so
  these add up to more than the line's total). Inside/outside the core: by where the
  station stands.
- Per km: residents served ÷ the line's length, along one direction of travel (for a line
  under construction, whose track OSM maps for both directions, the parallel track is dropped;
  for a projected line, its official length where the plan gives one).
- Points of interest from OpenStreetMap, ranked by the trips they draw (POI rules below).
  One counts for a line when it reaches into the walking area of one of its stations.

Input:  data/<region>_transit.js (build_transit.py), data/<region>.js, data/raw/<region>/bgri2021.gpkg,
        data/raw/osm/portugal-latest.osm.pbf
Output: data/<region>_lines.js

Usage: uv run python pipeline/build_lines.py amp
"""

import json
import sys
from collections import defaultdict

import geopandas as gpd
import numpy as np
import osmium
import shapely

from build_transit import (METRIC_CRS, PBF, ROOT, bare_name, lonlat_rings, metric, read_grid, region_shape,
                           residents)
from regions import REGIONS

PARALLEL_M = 15  # track this close to a longer piece is its other direction
AREA_SIMPLIFY_M = 10  # the line's walking area, drawn on the map

# Points of interest, by the trips they draw:
#   high    regional draws that many thousands use every day: large hospitals, the airport,
#           stations with long-distance trains
#   medium  a few thousand a day: faculties and other higher education, other hospitals,
#           shopping centres, stadiums, exhibition and concert venues
#   low     leisure and culture: museums, theatres, notable sights, large parks, zoos
# Small things tagged like these are left out (a hospital under MIN_HOSPITAL_HA, a mall
# under MALL_HA, a park under PARK_HA); for the rest, culture and sights only count when
# notable enough to have a Wikidata entry (sights: a Wikipedia article, which leaves out
# the many listed fountains, trees and pillories), as do stadiums and venues.
HIGH, MEDIUM, LOW = 0, 1, 2
LARGE_HOSPITAL_HA = 3
MIN_HOSPITAL_HA = 0.1
MALL_HA = 1
PARK_HA = 5
LONG_DISTANCE = {"CP Longo Curso"}  # OSM train networks whose stops are high-ranked
POI_TAGS = [
    ("amenity", "hospital"), ("aeroway", "aerodrome"), ("amenity", "university"), ("shop", "mall"),
    ("leisure", "stadium"), ("amenity", "exhibition_centre"), ("amenity", "conference_centre"),
    ("amenity", "concert_hall"), ("tourism", "museum"), ("tourism", "gallery"), ("tourism", "attraction"),
    ("amenity", "theatre"), ("amenity", "arts_centre"), ("tourism", "zoo"), ("tourism", "theme_park"),
    ("amenity", "marketplace"), ("leisure", "park"),
]


def classify(tags, area_ha: float, is_point: bool) -> tuple[int, str] | None:
    """(tier, kind) of a point of interest, or None when it doesn't count."""
    get, notable = tags.get, "wikidata" in tags
    if get("amenity") == "hospital":
        if area_ha >= LARGE_HOSPITAL_HA:
            return HIGH, "hospital"
        return (MEDIUM, "hospital") if is_point or area_ha >= MIN_HOSPITAL_HA else None
    if get("aeroway") == "aerodrome":
        return (HIGH, "airport") if "iata" in tags else None
    if get("amenity") == "university":
        return MEDIUM, "education"
    if get("shop") == "mall":
        return (MEDIUM, "mall") if area_ha >= MALL_HA else None
    if get("leisure") == "stadium":
        return (MEDIUM, "stadium") if notable else None
    if get("amenity") in ("exhibition_centre", "conference_centre", "concert_hall"):
        return (MEDIUM, "venue") if notable else None
    if get("leisure") == "park":
        return (LOW, "park") if area_ha >= PARK_HA else None
    if not notable:
        return None
    if get("tourism") in ("museum", "gallery"):
        return LOW, "museum"
    if get("amenity") in ("theatre", "arts_centre"):
        return LOW, "theatre"
    if get("tourism") in ("zoo", "theme_park"):
        return LOW, "park"
    if get("amenity") == "marketplace":
        return LOW, "market"
    if get("tourism") == "attraction":
        return (LOW, "sight") if "wikipedia" in tags else None
    return None


def read_pois(shape: shapely.Geometry) -> list[dict]:
    """Points of interest inside the region (METRIC_CRS geometries), one per place."""
    wkb = osmium.geom.WKBFactory()
    found = []
    fp = (osmium.FileProcessor(str(PBF)).with_areas(osmium.filter.TagFilter(*POI_TAGS)).with_locations()
          .with_filter(osmium.filter.TagFilter(*POI_TAGS)))
    for o in fp:
        if o.is_area():
            try:
                geom = shapely.from_wkb(wkb.create_multipolygon(o))
            except RuntimeError:
                continue  # broken multipolygon
        elif o.is_node():
            geom = shapely.Point(o.location.lon, o.location.lat)
        else:
            continue
        name = o.tags.get("name")
        if not name:
            continue
        geom = shapely.transform(geom, lambda xy: metric(xy))
        if not shape.contains(geom.centroid):
            continue
        kind = classify(o.tags, geom.area / 1e4, o.is_node())
        if kind:
            found.append({"name": name, "tier": kind[0], "kind": kind[1], "geom": geom})
    found += long_distance_stations(shape)
    return dedupe(found)


def long_distance_stations(shape: shapely.Geometry) -> list[dict]:
    """Stations where long-distance trains stop (high), from those networks' OSM routes."""
    stops = set()
    for r in osmium.FileProcessor(str(PBF), osmium.osm.RELATION).with_filter(osmium.filter.TagFilter(("route", "train"))):
        if r.tags.get("network") in LONG_DISTANCE:
            stops |= {m.ref for m in r.members if m.type == "n" and m.role.startswith("stop")}
    by_name = defaultdict(list)
    for n in osmium.FileProcessor(str(PBF), osmium.osm.NODE).with_filter(osmium.filter.IdFilter(stops)):
        if n.tags.get("name"):
            by_name[bare_name(n.tags["name"])].append((n.location.lon, n.location.lat))
    out = []
    for name, lonlat in sorted(by_name.items()):
        geom = shapely.MultiPoint(metric(lonlat))
        if shape.contains(geom.centroid):
            out.append({"name": name, "tier": HIGH, "kind": "rail", "geom": geom})
    return out


def dedupe(pois: list[dict]) -> list[dict]:
    """One entry per place. A university's outline that holds several faculties is dropped
    (the faculties count, not the umbrella). Otherwise, going from the most important and
    largest down, a place is dropped when it shares its name with one kept within 500 m, or
    stands inside one kept of the same kind; a low-ranked one inside any kept outline joins
    it (Serralves: the museum, in its park)."""
    same = lambda a, b: a.startswith(b) or b.startswith(a)  # "ESAP" and "ESAP, Porto"
    polys = [p for p in pois if p["geom"].area > 0]
    tree = shapely.STRtree([p["geom"].centroid for p in pois])
    umbrella = set()
    for p in polys:
        inside = [pois[i] for i in tree.query(p["geom"], predicate="contains")]
        if sum(q is not p and q["kind"] == p["kind"] and q["geom"].area < p["geom"].area for q in inside) >= 2:
            umbrella.add(id(p))
    kept = []
    for p in sorted(pois, key=lambda p: (p["tier"], -p["geom"].area)):
        if id(p) in umbrella:
            continue
        c = p["geom"].centroid
        if any(same(k["name"].casefold(), p["name"].casefold()) and k["geom"].distance(c) < 500
               or k["geom"].area > 0 and (k["kind"] == p["kind"] or p["tier"] == LOW) and k["geom"].contains(c)
               for k in kept):
            continue
        kept.append(p)
    return kept


def core_shape(region: str, code: str) -> shapely.Geometry:
    sub = gpd.read_file(ROOT / "data/raw" / region / "bgri2021.gpkg", columns=["DTMN21"]).to_crs(METRIC_CRS)
    return shapely.make_valid(shapely.union_all(sub[sub["DTMN21"] == code].geometry.values))


def walk_area(station: dict) -> shapely.Geometry:
    """A station's walking area (lon/lat rings in the transit file) in METRIC_CRS."""
    return shapely.make_valid(shapely.MultiPolygon([(metric(p[0]), [metric(h) for h in p[1:]]) for p in station["walk"]]))


def track_km(features: list[dict], parallel: bool = False) -> float:
    """Length of track pieces, each counted once (the map draws shared track once per line);
    with `parallel`, also without the other direction's track alongside."""
    unique = {tuple(map(tuple, f["geometry"]["coordinates"])) for f in features}
    pieces = sorted((shapely.LineString(metric(c)) for c in unique), key=lambda g: -g.length)
    kept = []
    for g in pieces:
        near = shapely.union_all(kept).buffer(PARALLEL_M) if kept and parallel else None
        if near is None or g.intersection(near).length < g.length / 2:
            kept.append(g)
    return sum(g.length for g in kept) / 1000


def figures(stations: list[dict], areas: list, km: float, core: shapely.Geometry, grid, pois: list[dict],
            reached: shapely.Geometry | None = None) -> dict:
    """A line's figures; with `reached` (the area the network already reaches), also the
    residents it would newly reach."""
    cells, pop, tree = grid
    union = shapely.union_all(areas)
    in_core = [bool(core.contains(shapely.Point(metric([(s["lon"], s["lat"])])[0]))) for s in stations]
    pops = np.array([s["pop"] for s in stations], dtype=float)
    mean = lambda v: round(float(v.mean())) if len(v) else None
    served = residents(union, cells, pop, tree)
    served_core = residents(union.intersection(core), cells, pop, tree)
    found = []
    for p in pois:
        if not union.intersects(p["geom"]):
            continue
        centre = p["geom"].centroid  # the station nearest its middle, among those it reaches
        near = min((k for k, a in enumerate(areas) if a.intersects(p["geom"])),
                   key=lambda k: centre.distance(shapely.Point(metric([(stations[k]["lon"], stations[k]["lat"])])[0])))
        found.append([p["name"], p["tier"], p["kind"], stations[near]["name"]])
    found.sort(key=lambda f: (f[1], f[0].casefold()))
    return {
        "stations": len(stations), "stations_core": sum(in_core), "km": round(km, 1),
        "pop": round(served), "pop_core": round(served_core),
        "avg": mean(pops), "avg_core": mean(pops[in_core]), "avg_out": mean(pops[~np.array(in_core)]),
        **({"pop_new": round(residents(union.difference(reached), cells, pop, tree))} if reached is not None else {}),
        "pois": found,
    }


def main(region: str) -> None:
    cfg = REGIONS[region]
    text = (ROOT / "data" / f"{region}_transit.js").read_text()
    transit = json.loads(text.split("=", 1)[1].rstrip().rstrip(";"))
    keys = [net["key"] for net in cfg["transit"] if net.get("line_stats")]
    shape = region_shape(region)
    core = core_shape(region, cfg["core"]["code"])
    grid = read_grid(region)
    pois = read_pois(shape)
    print(f"{len(pois)} points of interest: " + ", ".join(
        f"{sum(p['tier'] == t for p in pois)} {label}" for t, label in enumerate(["high", "medium", "low"])))

    areas = [walk_area(s) for s in transit["stations"]]
    out = {"core": cfg["core"]["name"], "walk_minutes": transit["walk"]["minutes"],
           "rules": {"large_hospital_ha": LARGE_HOSPITAL_HA, "mall_ha": MALL_HA, "park_ha": PARK_HA},
           "networks": {}, "lines": {}}
    # What the metro reaches today, and once the lines being built open: planned lines are
    # measured by the residents they add to these.
    lines_by_ref = {l["ref"]: l for l in transit["lines"]}
    building = {r for r, l in lines_by_ref.items() if l.get("planned") and not l.get("project")}
    stations = transit["stations"]
    on_metro = [i for i, s in enumerate(stations) if any(lines_by_ref[r]["net"] == "metro" for r in s["lines"])]
    reach_open = shapely.union_all([areas[i] for i in on_metro])
    reach_building = shapely.union_all([areas[i] for i, s in enumerate(stations) if i in on_metro or set(s["planned"]) & building])
    for key in keys:
        open_ = set()
        for line in (l for l in transit["lines"] if l["net"] == key):
            ref, planned = line["ref"], bool(line.get("planned"))
            idx = [i for i, s in enumerate(transit["stations"]) if ref in (s["planned"] if planned else s["lines"])]
            feats = [f for f in transit["tracks"]["features"]
                     if f["properties"]["net"] == key and f["properties"]["ref"] == ref
                     and bool(f["properties"].get("planned")) == planned]
            km = line.get("km") or track_km(feats, parallel=planned)
            reached = None if not planned else reach_building if line.get("project") else reach_open
            f = figures([transit["stations"][i] for i in idx], [areas[i] for i in idx], km, core, grid, pois, reached)
            f["area"] = lonlat_rings(shapely.union_all([areas[i] for i in idx]).simplify(AREA_SIMPLIFY_M))
            out["lines"][ref] = f
            if not planned:
                open_.update(idx)
            print(f"{line['name']:<11} {f['stations']:>3} stations {f['km']:>5} km {f['pop']:>7} residents "
                  f"({f['pop_core']} in {cfg['core']['name']}{', %d new' % f['pop_new'] if 'pop_new' in f else ''}), "
                  f"{f['pop'] / km:>6.0f}/km, {f['avg']}/station, "
                  f"POIs {[sum(p[1] == t for p in f['pois']) for t in range(3)]}")
        if not open_:
            continue  # projected lines only: no network yet
        idx = sorted(open_)
        feats = [f for f in transit["tracks"]["features"] if f["properties"]["net"] == key and not f["properties"].get("planned")]
        f = figures([transit["stations"][i] for i in idx], [areas[i] for i in idx], track_km(feats), core, grid, pois)
        out["networks"][key] = f
        print(f"all open   {f['stations']:>3} stations {f['km']:>5} km {f['pop']:>7} residents ({f['pop_core']} in core)")

    path = ROOT / "data" / f"{region}_lines.js"
    path.write_text("window.LINES_DATA = " + json.dumps(out, ensure_ascii=False, separators=(",", ":")) + ";\n")
    print(f"-> {path} ({path.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "amp")
