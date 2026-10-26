"""Build the metro layer for a region: lines, stations and each station's walking area.

Lines and stations come from the OSM route relations of the region's metro network
(`metro` in regions.py). Each line is drawn along one direction of travel, so double
track shows once, and lines sharing track are drawn side by side. Lines under
construction (`metro_planned`) come from their construction track and station sites;
stations OSM lacks are placed approximately on the track.

Walking area: everything reachable within WALK_MINUTES at WALK_KMH along OSM streets,
footpaths and stairs (no motorways), starting from the street points around the
station's stops. The reached paths are widened by REACH_M (front doors sit back
from the street centre line) and small enclosed gaps such as block interiors are
filled. Residents inside are summed from the grid in data/<region>.js, weighted by
the share of each cell that falls inside.

Frequencies: trains per hour by period, counted from the operator's GTFS timetables
(data/raw/gtfs/<region>_metro.zip, optional) at each station, matched by location.

Input:  data/raw/osm/portugal-latest.osm.pbf, data/<region>.js (run build_grid.py first),
        data/raw/gtfs/<region>_metro.zip
Output: data/<region>_metro.js

Usage: uv run python pipeline/build_metro.py amp
"""

import csv
import heapq
import io
import json
import math
import sys
import zipfile
from collections import Counter
from collections import defaultdict
from pathlib import Path

import numpy as np
import osmium
import shapely
import shapely.ops
from pyproj import Transformer

from regions import REGIONS

ROOT = Path(__file__).resolve().parent.parent
PBF = ROOT / "data/raw/osm/portugal-latest.osm.pbf"
ROUTE_TYPES = {"light_rail", "subway"}
WALK_MINUTES = 10
WALK_KMH = 4.8
WALK_M = WALK_KMH * 1000 / 60 * WALK_MINUTES  # 800 m
REACH_M = 50
FILL_HOLES_M2 = 100_000  # enclosed gaps smaller than 10 ha count as reached
SNAP_M = 80  # streets this close to a stop are where the walk starts
SIMPLIFY_M = 5
METRIC_CRS, GRID_CRS = 3763, 3035
# Timetable periods: (short label, calendar.txt day column, start, end in hours)
PERIODS = [
    ("Peak", "monday", 7.5, 9.5),
    ("Midday", "monday", 11, 16),
    ("Evening", "monday", 21, 24),
    ("Sat", "saturday", 10, 19),
    ("Sun", "sunday", 10, 19),
]
GTFS_MATCH_M = 300  # a GTFS stop belongs to the station within this distance
NOT_WALKABLE = {"motorway", "motorway_link", "construction", "proposed", "raceway", "bus_guideway", "abandoned"}

to_metric = Transformer.from_crs(4326, METRIC_CRS, always_xy=True)
to_wgs = Transformer.from_crs(METRIC_CRS, 4326, always_xy=True)
to_grid = Transformer.from_crs(METRIC_CRS, GRID_CRS, always_xy=True)


def walkable(tags) -> bool:
    if tags.get("foot") in ("yes", "designated", "permissive"):
        return True
    if tags.get("foot") == "no" or tags.get("access") in ("no", "private"):
        return False
    highway = tags.get("highway")
    if highway in ("trunk", "trunk_link"):  # expressways: only with mapped sidewalks
        return tags.get("sidewalk", "no") not in ("no", "none", "separate")
    return highway not in NOT_WALKABLE


def read_routes(network: str) -> list[dict]:
    routes = []
    fp = osmium.FileProcessor(str(PBF), osmium.osm.RELATION).with_filter(osmium.filter.TagFilter(("type", "route")))
    for r in fp:
        if r.tags.get("route") not in ROUTE_TYPES or r.tags.get("network") != network:
            continue
        routes.append({
            "id": r.id,
            "ref": r.tags.get("ref", "?"),
            "name": r.tags.get("name", ""),
            "colour": r.tags.get("colour", "#888888"),
            "ways": [m.ref for m in r.members if m.type == "w" and m.role == ""],
            "stops": [m.ref for m in r.members if m.type == "n" and m.role.startswith("stop")],
        })
    return routes


def read_osm(routes: list[dict], bbox, operator: str) -> dict:
    """From one pass over the extract:

    stops: {node id: (name, lon, lat)}, tracks: {way id: [(node, lon, lat)]}, walk: [nodes],
    and the network's construction sites: building: [(way id, name, opening, nodes)] for
    track, building_stations: [(name, nodes)] for stations.
    """
    stop_ids = {n for r in routes for n in r["stops"]}
    track_ids = {w for r in routes for w in r["ways"]}
    out = {"stops": {}, "tracks": {}, "walk": [], "building": [], "building_stations": []}
    fp = (
        osmium.FileProcessor(str(PBF), osmium.osm.NODE | osmium.osm.WAY)
        .with_locations()
        .with_filter(osmium.filter.IdFilter(stop_ids).enable_for(osmium.osm.NODE))
        .with_filter(osmium.filter.KeyFilter("highway", "railway", "construction:railway").enable_for(osmium.osm.WAY))
    )
    for o in fp:
        if o.is_node():
            out["stops"][o.id] = (o.tags.get("name", "?"), o.location.lon, o.location.lat)
            continue
        t = o.tags
        nodes = lambda: [(n.ref, n.location.lon, n.location.lat) for n in o.nodes if n.location.valid()]
        if o.id in track_ids:
            out["tracks"][o.id] = nodes()
        if "highway" in t and walkable(t):
            way = nodes()
            if any(bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3] for _, x, y in way):
                out["walk"].append(way)
        if t.get("operator") != operator:
            continue
        if t.get("construction:railway") == "station" or t.get("construction:public_transport") == "station":
            out["building_stations"].append((t.get("name", "?"), nodes()))
        elif (t.get("railway") == "construction" and t.get("construction") in ROUTE_TYPES
              or t.get("construction:railway") in ROUTE_TYPES) and "service" not in t:
            out["building"].append((o.id, t.get("name"), t.get("opening_date"), nodes()))
    return out


class WalkGraph:
    def __init__(self, ways: list):
        index, lon, lat, edges = {}, [], [], []
        for way in ways:
            prev = None
            for node, x, y in way:
                i = index.get(node)
                if i is None:
                    i = index[node] = len(lon)
                    lon.append(x)
                    lat.append(y)
                if prev is not None and prev != i:
                    edges.append((prev, i))
                prev = i
        self.x, self.y = (np.asarray(v) for v in to_metric.transform(lon, lat))
        a, b = np.array(edges).T
        length = np.hypot(self.x[a] - self.x[b], self.y[a] - self.y[b])
        self.adj = [[] for _ in range(len(lon))]
        for i, j, d in zip(a.tolist(), b.tolist(), length.tolist()):
            self.adj[i].append((j, d))
            self.adj[j].append((i, d))
        self.tree = shapely.STRtree(shapely.points(self.x, self.y))

    def sources(self, points: list[tuple[float, float]]) -> dict[int, float]:
        """Graph nodes near the given metric points, with the straight-line walk to them."""
        start: dict[int, float] = {}
        for px, py in points:
            near = self.tree.query(shapely.Point(px, py), predicate="dwithin", distance=SNAP_M)
            if len(near) == 0:
                near = self.tree.query_nearest(shapely.Point(px, py))
            for i in near.tolist():
                d = math.hypot(self.x[i] - px, self.y[i] - py)
                start[i] = min(start.get(i, math.inf), d)
        return start

    def reach(self, start: dict[int, float], limit: float) -> dict[int, float]:
        dist = {i: d for i, d in start.items() if d <= limit}
        heap = [(d, i) for i, d in dist.items()]
        heapq.heapify(heap)
        while heap:
            d, i = heapq.heappop(heap)
            if d > dist[i]:
                continue
            for j, w in self.adj[i]:
                nd = d + w
                if nd <= limit and nd < dist.get(j, math.inf):
                    dist[j] = nd
                    heapq.heappush(heap, (nd, j))
        return dist

    def area(self, dist: dict[int, float], limit: float) -> shapely.Geometry:
        """The reached stretch of every path, widened by REACH_M, small gaps filled."""
        segments = []
        for i, d in dist.items():
            for j, w in self.adj[i]:
                go = min(w, limit - d)
                if go == w and j in dist and j < i:
                    continue  # whole edge already added from j
                t = go / w if w > 0 else 0
                segments.append([(self.x[i], self.y[i]),
                                 (self.x[i] + t * (self.x[j] - self.x[i]), self.y[i] + t * (self.y[j] - self.y[i]))])
        # Buffering each piece and merging is far faster than buffering one huge MultiLineString.
        area = shapely.union_all(shapely.buffer(shapely.linestrings(segments), REACH_M, quad_segs=4))
        polys = [shapely.Polygon(p.exterior, [h for h in p.interiors if shapely.Polygon(h).area >= FILL_HOLES_M2])
                 for p in getattr(area, "geoms", [area])]
        return shapely.MultiPolygon(polys).simplify(SIMPLIFY_M)


def orient_ways(route: dict, tracks: dict) -> dict[int, int]:
    """+1 if the route runs along the way in its drawing order, -1 if against it."""
    ways = [w for w in route["ways"] if w in tracks]
    ends = [(tracks[w][0][0], tracks[w][-1][0]) for w in ways]
    out = {}
    for k, w in enumerate(ways):
        first, last = ends[k]
        if k + 1 < len(ways):
            out[w] = 1 if last in ends[k + 1] else -1 if first in ends[k + 1] else 1
        elif k > 0:
            out[w] = 1 if first in ends[k - 1] else -1 if last in ends[k - 1] else 1
        else:
            out[w] = 1
    return out


def build_lines(routes: list[dict], tracks: dict) -> tuple[list[dict], dict, dict, dict]:
    """One direction of travel per line, oriented consistently where lines share track.

    Returns the lines, {way: [line refs]}, {way: +1/-1} and {variant: line}. Variants that run
    on a subset of another same-coloured line's track (e.g. B express) are folded in.
    """
    by_ref = defaultdict(list)
    for r in sorted(routes, key=lambda r: r["id"]):
        by_ref[r["ref"]].append(r)
    ways_of = {ref: {w for r in rs for w in r["ways"]} for ref, rs in by_ref.items()}
    # Longest track first, then the shorter ref (B before Bx on identical track), so a
    # variant is only ever folded into a line that is not itself a variant.
    parent, kept = {}, []
    for ref in sorted(by_ref, key=lambda r: (-len(ways_of[r]), len(r), r)):
        colour = by_ref[ref][0]["colour"]
        main = next((o for o in kept if by_ref[o][0]["colour"] == colour and ways_of[ref] <= ways_of[o]), None)
        if main:
            parent[ref] = main
        else:
            kept.append(ref)

    orientation: dict[int, int] = {}
    lines, on_way = [], defaultdict(list)
    for ref in sorted(r for r in by_ref if r not in parent):
        def fit(route):
            o = orient_ways(route, tracks)
            shared = [w for w in o if w in orientation]
            return (sum(o[w] == orientation[w] for w in shared) - sum(o[w] != orientation[w] for w in shared), len(shared))
        best = max(by_ref[ref], key=fit)
        for w, sign in orient_ways(best, tracks).items():
            orientation.setdefault(w, sign)
            if ref not in on_way[w]:
                on_way[w].append(ref)
        lines.append({"ref": ref, "colour": best["colour"],
                      "name": best["name"].replace("⇒", "–").replace(" =>", " –")})
    return lines, on_way, orientation, parent


def planned_lines(building: list, cfg: dict) -> tuple[list[dict], dict[str, list]]:
    """Lines under construction and their track {OSM line name: [ways' (node, lon, lat)]}.

    Track named after a line belongs to it; differently named pieces in between (a
    bridge with its own name) join the line whose track they touch.
    """
    known = cfg["lines"]
    line_of = {w: name for w, name, _, _ in building if name in known}
    nodes_of = {w: {n for n, _, _ in nodes} for w, _, _, nodes in building}
    while True:
        line_at = {n: line_of[w] for w in line_of for n in nodes_of[w]}
        joined = {w: line_at[n] for w, _, _, _ in building if w not in line_of
                  for n in nodes_of[w] if n in line_at}
        if not joined:
            break
        line_of.update(joined)
    track = defaultdict(list)
    opening = defaultdict(Counter)
    for w, _, date, nodes in building:
        if w in line_of:
            track[line_of[w]].append(nodes)
            if date:
                opening[line_of[w]][date] += 1
    lines = [{**{k: known[name][k] for k in ("ref", "colour", "name")}, "planned": True,
              "opening": opening[name].most_common(1)[0][0] if opening[name] else None}
             for name in known if name in track]
    return lines, track


def frequencies(path: Path, stations: list[dict]) -> tuple[list[dict], dict]:
    """Trains per hour at each station in each period: per line and direction (the mean
    of the two directions), and all lines in both directions together.

    Returns one {line ref: [per period], "all": [per period]} per station (empty for
    stations not in the timetable) and the feed's validity.
    """
    z = zipfile.ZipFile(path)
    read = lambda name: csv.DictReader(io.StringIO(z.read(name).decode("utf-8-sig")))
    routes = {r["route_id"]: r["route_short_name"] for r in read("routes.txt")}
    calendar = list(read("calendar.txt"))
    runs_on = {c["service_id"]: c for c in calendar}
    trips = {t["trip_id"]: (routes[t["route_id"]], runs_on.get(t["service_id"]), t["direction_id"])
             for t in read("trips.txt")}

    open_ = [i for i, s in enumerate(stations) if s["lines"]]
    tree = shapely.STRtree(shapely.points([to_metric.transform(stations[i]["lon"], stations[i]["lat"]) for i in open_]))
    station_of = {}
    for stop in read("stops.txt"):
        p = shapely.Point(to_metric.transform(float(stop["stop_lon"]), float(stop["stop_lat"])))
        k = tree.query_nearest(p, max_distance=GTFS_MATCH_M)
        if len(k):
            station_of[stop["stop_id"]] = open_[int(k[0])]
        else:
            print(f"  GTFS stop {stop['stop_name']} matches no station")

    counts = defaultdict(Counter)  # station -> (line, direction, period) -> trains
    for st in read("stop_times.txt"):
        route, service, direction = trips[st["trip_id"]]
        station = station_of.get(st["stop_id"])
        if service is None or station is None:
            continue  # holiday-only services (calendar_dates.txt) are left out
        h, m, *_ = (int(v) for v in st["departure_time"].split(":"))
        t = h + m / 60
        for k, (_, day, start, end) in enumerate(PERIODS):
            if service[day] == "1" and start <= t < end:
                counts[station][route, direction, k] += 1

    hours = [end - start for _, _, start, end in PERIODS]
    out = [{} for _ in stations]
    for station, c in counts.items():
        for route in sorted({r for r, _, _ in c}):
            out[station][route] = [round(sum(c[route, d, k] for d in ("0", "1")) / 2 / hours[k], 1)
                                   for k in range(len(PERIODS))]
        out[station]["all"] = [round(sum(n for (_, _, kk), n in c.items() if kk == k) / hours[k], 1)
                               for k in range(len(PERIODS))]
    fmt = lambda d: f"{d[:4]}-{d[4:6]}-{d[6:]}"
    valid = {"from": fmt(min(c["start_date"] for c in calendar)), "to": fmt(max(c["end_date"] for c in calendar))}
    return out, valid


def read_grid(region: str):
    text = (ROOT / "data" / f"{region}.js").read_text()
    grid = json.loads(text.split("=", 1)[1].rstrip().rstrip(";"))
    c, size = grid["columns"], grid["cell"]
    e, n = np.array(c["e"]) * size, np.array(c["n"]) * size
    cells = shapely.box(e, n, e + size, n + size)
    return cells, np.array(c["pop"], dtype=float), shapely.STRtree(cells)


def residents(area_metric: shapely.Geometry, cells, pop, tree) -> float:
    area = shapely.transform(area_metric, lambda xy: np.column_stack(to_grid.transform(xy[:, 0], xy[:, 1])))
    hit = tree.query(area, predicate="intersects")
    share = shapely.area(shapely.intersection(cells[hit], area)) / shapely.area(cells[hit])
    return float((pop[hit] * share).sum())


def lonlat_rings(geom: shapely.Geometry) -> list:
    """MultiPolygon as GeoJSON coordinates, exteriors counter-clockwise."""
    out = []
    for p in getattr(geom, "geoms", [geom]):
        p = shapely.orient_polygons(p)
        rings = [p.exterior, *p.interiors]
        out.append([[[round(x, 5), round(y, 5)] for x, y in zip(*to_wgs.transform(*np.asarray(r.coords).T))]
                    for r in rings])
    return out


def main(region: str) -> None:
    cfg = REGIONS[region]
    routes = read_routes(cfg["metro"])
    print(f"{len(routes)} routes: {sorted({r['ref'] for r in routes})}")

    # Walkable ways are only needed around the stations: first find the stops' extent.
    pad = 0.03  # degrees, comfortably more than the walk
    osm = read_osm(routes, cfg["bbox"], cfg["metro"])
    stops, tracks, walk = osm["stops"], osm["tracks"], osm["walk"]
    lons = [s[1] for s in stops.values()]
    lats = [s[2] for s in stops.values()]
    box = (min(lons) - pad, min(lats) - pad, max(lons) + pad, max(lats) + pad)
    walk = [w for w in walk if any(box[0] <= x <= box[2] and box[1] <= y <= box[3] for _, x, y in w)]
    graph = WalkGraph(walk)
    print(f"{len(stops)} stop positions, {len(tracks)} track ways, walking graph of {len(graph.adj)} nodes")

    lines, on_way, orientation, parent = build_lines(routes, tracks)
    tracks_geojson = []
    for w, refs in on_way.items():
        coords = [(x, y) for _, x, y in tracks[w]][:: orientation[w]]
        coords = [[round(x, 5), round(y, 5)] for x, y in coords]
        for k, ref in enumerate(sorted(refs)):
            colour = next(line["colour"] for line in lines if line["ref"] == ref)
            tracks_geojson.append({"type": "Feature", "properties": {"ref": ref, "c": colour, "k": k - (len(refs) - 1) / 2},
                                   "geometry": {"type": "LineString", "coordinates": coords}})

    # Stations in service: a route's stop positions grouped by name.
    served = defaultdict(set)
    for r in routes:
        for n in r["stops"]:
            if n in stops:
                served[stops[n][0]].add(parent.get(r["ref"], r["ref"]))
    points = defaultdict(set)
    for n in {n for r in routes for n in r["stops"] if n in stops}:
        name, lon, lat = stops[n]
        points[name].add((lon, lat))
    specs = [{"name": name, "lines": sorted(served[name]), "planned": [],
              "points": np.array(to_metric.transform(*np.array(sorted(points[name])).T)).T}
             for name in sorted(points)]

    # Lines under construction: their track, then their stations. A new station sharing
    # a name with one in service (an interchange) is added to it.
    planned = cfg.get("metro_planned")
    if planned:
        new_lines, new_track = planned_lines(osm["building"], planned)
        lines += new_lines
        ref_of = {name: planned["lines"][name]["ref"] for name in new_track}
        colour = {line["ref"]: line["colour"] for line in new_lines}
        track_m = {}
        for name, ways in new_track.items():
            for way in ways:
                tracks_geojson.append({"type": "Feature",
                                       "properties": {"ref": ref_of[name], "c": colour[ref_of[name]], "k": 0, "planned": True},
                                       "geometry": {"type": "LineString",
                                                    "coordinates": [[round(x, 5), round(y, 5)] for _, x, y in way]}})
            track_m[name] = shapely.MultiLineString(
                [np.array(to_metric.transform(*np.array([(x, y) for _, x, y in way]).T)).T for way in ways])

        sites = []  # (name, metric points, OSM line names, approximate?)
        for name, nodes in osm["building_stations"]:
            pts = np.array(to_metric.transform(*np.array([(x, y) for _, x, y in nodes]).T)).T
            site = shapely.MultiPoint(pts)
            near = [line for line, g in track_m.items() if g.distance(site) < 150]
            if near:
                sites.append((name, pts, near, False))
        for name, (line, lon, lat) in planned.get("unmapped_stations", {}).items():
            if line not in track_m:
                continue
            given = shapely.Point(*to_metric.transform(lon, lat))
            on_track = shapely.ops.nearest_points(track_m[line], given)[0]
            print(f"  {name}: placed on the {line} track, {on_track.distance(given):.0f} m from the given point")
            sites.append((name, np.array([[on_track.x, on_track.y]]), [line], True))

        for name, pts, near, approx in sites:
            refs = sorted(ref_of[line] for line in near)
            centre = pts.mean(axis=0)
            same = next((s for s in specs if s["name"] == name
                         and math.dist(s["points"].mean(axis=0), centre) < 600), None)
            if same:
                same["planned"] = sorted(set(same["planned"]) | set(refs))
            else:
                specs.append({"name": name, "lines": [], "planned": refs, "points": pts, "approx": approx})

    cells, pop, tree = read_grid(region)
    stations, areas = [], []
    for spec in sorted(specs, key=lambda s: s["name"]):
        x, y = spec["points"].T
        dist = graph.reach(graph.sources(list(zip(x, y))), WALK_M)
        area = graph.area(dist, WALK_M)
        areas.append((area, bool(spec["lines"])))
        clon, clat = to_wgs.transform(x.mean(), y.mean())
        stations.append({
            "name": spec["name"],
            "lines": spec["lines"],
            "planned": spec["planned"],
            **({"approx": True} if spec.get("approx") else {}),
            "lon": round(clon, 5),
            "lat": round(clat, 5),
            "pop": round(residents(area, cells, pop, tree)),
            "km2": round(area.area / 1e6, 2),
            "walk": lonlat_rings(area),
        })
    gtfs = ROOT / "data/raw/gtfs" / f"{region}_metro.zip"
    timetable = None
    if gtfs.exists():
        freq, valid = frequencies(gtfs, stations)
        for s, f in zip(stations, freq):
            s["freq"] = f
        hm = lambda h: f"{int(h)}:{round(h % 1 * 60):02d}"
        timetable = {"periods": [{"label": label, "day": day, "hours": f"{hm(a)}–{hm(b)}"} for label, day, a, b in PERIODS],
                     **valid}
        busiest = max(stations, key=lambda s: s["freq"].get("all", [0])[0])
        print(f"timetable {valid['from']} to {valid['to']}: busiest at weekday peak is "
              f"{busiest['name']}, {busiest['freq']['all'][0]} trains/h")
    else:
        print(f"no {gtfs.relative_to(ROOT)}: frequencies left out")
    covered = residents(shapely.union_all([a for a, open_ in areas if open_]), cells, pop, tree)
    covered_all = residents(shapely.union_all([a for a, _ in areas]), cells, pop, tree)

    out = ROOT / "data" / f"{region}_metro.js"
    out.write_text("window.METRO_DATA = " + json.dumps({
        "network": cfg["metro"],
        "walk": {"minutes": WALK_MINUTES, "kmh": WALK_KMH, "metres": round(WALK_M)},
        "covered_pop": round(covered),
        "covered_pop_planned": round(covered_all),
        "timetable": timetable,
        "total_pop": round(pop.sum()),
        "lines": lines,
        "tracks": {"type": "FeatureCollection", "features": tracks_geojson},
        "stations": stations,
    }, ensure_ascii=False, separators=(",", ":")) + ";\n")
    print(f"{len(lines)} lines, {len(stations)} stations; {covered:.0f} of {pop.sum():.0f} residents "
          f"({100 * covered / pop.sum():.1f}%) within {WALK_MINUTES} min, {covered_all:.0f} "
          f"({100 * covered_all / pop.sum():.1f}%) with the lines under construction -> {out} ({out.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "amp")
