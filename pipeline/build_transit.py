"""Build the rail layer for a region: lines, stations, each station's walking area and,
where a timetable is available, train frequencies.

Networks are listed under `transit` in regions.py (e.g. Metro do Porto, CP Porto trains).
Lines and stations come from each network's OSM route relations, grouped into lines by
their route master (so variants such as the B express join their line). Each line is
drawn along one direction of travel, so double track shows once, and lines sharing
track are drawn side by side. Only stations inside the region are kept, and only track
near it. Stations with the same name (ignoring a "Porto - " style prefix) whose stops
are within INTERCHANGE_M of each other are one interchange; same-named stations further
apart (Hospital São João's metro and train stops, 390 m) stay separate. Connected stations
with different names are listed under `interchanges` in regions.py.

Lines under construction (`planned`) come from their construction track and station
sites; stations OSM lacks are placed approximately on the track. Projected lines
(`projects`: projects.py) have their stations placed by hand from the plans, and their
track from OSM railway=proposed where mapped, else a smooth curve through the stations.

Walking area: everything reachable within WALK_MINUTES at WALK_KMH along OSM streets,
footpaths and stairs (no motorways), starting from the street points around the
station's stops. The reached paths are widened by REACH_M (front doors sit back
from the street centre line) and small enclosed gaps such as block interiors are
filled. Residents inside are summed from the grid in data/<region>.js, weighted by
the share of each cell that falls inside.

Driving area (networks with `drive_minutes`, e.g. trains): everywhere one can drive to
the station from within that time, along OSM roads at free-flow speeds (DRIVE_KMH, or
80% of a tagged maxspeed), one-way streets respected, plus SIGNAL_S at each traffic light and
JUNCTION_S at other junctions passed (estimates); no congestion.
The drive ends on the roads within SNAP_M of the station (not motorways), and homes
within DRIVE_REACH_M of a reached road count as reached.

Frequencies: trains per hour by period, counted from the operator's GTFS timetables
(data/raw/gtfs/<file>, per network, optional) at each station, matched by location.

Input:  data/raw/osm/portugal-latest.osm.pbf, data/raw/<region>/bgri2021.gpkg,
        data/<region>.js (run build_grid.py first), data/raw/gtfs/*.zip
Output: data/<region>_transit.js, data/<region>_drive.js (driving areas, loaded later)

Usage: uv run python pipeline/build_transit.py amp
"""

import csv
import heapq
import io
import json
import math
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import geopandas as gpd
import numpy as np
import osmium
import shapely
import shapely.ops
from pyproj import Transformer

from projects import PROJECTS
from regions import REGIONS

ROOT = Path(__file__).resolve().parent.parent
PBF = ROOT / "data/raw/osm/portugal-latest.osm.pbf"
WALK_MINUTES = 10
WALK_KMH = 4.8
WALK_M = WALK_KMH * 1000 / 60 * WALK_MINUTES  # 800 m
REACH_M = 50
FILL_HOLES_M2 = 100_000  # enclosed gaps smaller than 10 ha count as reached
SNAP_M = 80  # streets this close to a stop are where the walk starts
SIMPLIFY_M = 5
NAME_JOIN_M = 600  # an unnamed stop position takes the name of the nearest named one this close
INTERCHANGE_M = 250  # same-named stations whose stops are this close are one interchange
PART_M = 25  # an interchange's platforms closer than this share one marker
TRACK_MARGIN_M = 1500  # track drawn this far beyond the region
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
# Driving to a station (networks with `drive_minutes`): free-flow speeds in km/h by road
# class, OSRM's car profile; a tagged maxspeed counts at 80%. Plus time lost at junctions
# (estimates: no timings are mapped). No congestion.
DRIVE_KMH = {
    "motorway": 90, "motorway_link": 45, "trunk": 85, "trunk_link": 40, "primary": 65, "primary_link": 30,
    "secondary": 55, "secondary_link": 25, "tertiary": 40, "tertiary_link": 20, "unclassified": 25,
    "residential": 25, "living_street": 10, "service": 15, "road": 25,
}
MAXSPEED_SHARE = 0.8
MAXSPEED_PT = {"PT:urban": 50, "PT:rural": 90, "PT:trunk": 100, "PT:motorway": 120}
NO_PARKING = {"motorway", "motorway_link", "trunk", "trunk_link"}  # a drive can't start or end here
SIGNAL_S = 10  # average wait at traffic lights: about half of cars meet a red
JUNCTION_S = 3  # slowing, turning or giving way where three or more roads meet (service roads aside)
DRIVE_REACH_M = 50  # homes this far from a reached road count as reached
DRIVE_FILL_HOLES_M2 = 250_000
DRIVE_SIMPLIFY_M = 40

to_metric = Transformer.from_crs(4326, METRIC_CRS, always_xy=True)
to_wgs = Transformer.from_crs(METRIC_CRS, 4326, always_xy=True)
to_grid = Transformer.from_crs(METRIC_CRS, GRID_CRS, always_xy=True)


def metric(lonlat) -> np.ndarray:
    """(n, 2) lon/lat -> (n, 2) metres in METRIC_CRS."""
    lon, lat = np.asarray(lonlat, dtype=float).T
    return np.column_stack(to_metric.transform(lon, lat))


def walkable(tags) -> bool:
    if tags.get("foot") in ("yes", "designated", "permissive"):
        return True
    if tags.get("foot") == "no" or tags.get("access") in ("no", "private"):
        return False
    highway = tags.get("highway")
    if highway in ("trunk", "trunk_link"):  # expressways: only with mapped sidewalks
        return tags.get("sidewalk", "no") not in ("no", "none", "separate")
    return highway not in NOT_WALKABLE


def drive_speed(tags) -> float | None:
    """Car speed on a way in km/h, or None where cars can't go."""
    highway = tags.get("highway")
    if highway not in DRIVE_KMH or tags.get("area") == "yes":
        return None
    if any(tags.get(k) in ("no", "private") for k in ("access", "vehicle", "motor_vehicle", "motorcar")) \
            and tags.get("motorcar", tags.get("motor_vehicle")) not in ("yes", "destination", "permissive"):
        return None
    raw = tags.get("maxspeed", "")
    limit = MAXSPEED_PT.get(raw) or (float(m.group()) if (m := re.match(r"\d+", raw)) else None)
    return MAXSPEED_SHARE * limit if limit else DRIVE_KMH[highway]


def oneway(tags) -> int:
    """+1: cars only along the way's drawing order, -1: only against it, 0: both ways."""
    value = tags.get("oneway")
    if value in ("yes", "true", "1"):
        return 1
    if value == "-1":
        return -1
    if value == "no":
        return 0
    return 1 if tags.get("junction") in ("roundabout", "circular") or tags.get("highway") in ("motorway", "motorway_link") else 0


def read_routes(net: dict) -> list[dict]:
    """The network's route relations, each with its line: its route master's ref, else its own.

    Routes of other networks join when their route master is listed under `include`
    (only those), named as given there.
    """
    include = {(inc["network"], inc["master"]): inc["name"] for inc in net.get("include", [])}
    networks = {net["network"]} | {n for n, _ in include}
    routes, masters = [], {}
    fp = osmium.FileProcessor(str(PBF), osmium.osm.RELATION).with_filter(
        osmium.filter.TagFilter(("type", "route"), ("type", "route_master")))
    for r in fp:
        t = r.tags
        if t.get("network") not in networks or (t.get("route") or t.get("route_master")) not in net["routes"]:
            continue
        if t.get("type") == "route_master":
            for m in r.members:
                if m.type == "r":
                    masters[m.ref] = (t.get("ref"), t.get("name", ""), include.get((t.get("network"), t.get("name"))))
            continue
        routes.append({
            "id": r.id,
            "network": t.get("network"),
            "ref": t.get("ref", "?"),
            "name": t.get("name", ""),
            "colour": t.get("colour", "#888888"),
            "ways": [m.ref for m in r.members if m.type == "w" and m.role == ""],
            "stops": [m.ref for m in r.members if m.type == "n" and m.role.startswith("stop")],
        })
    kept = []
    for r in routes:
        ref, name, line_name = masters.get(r["id"], (None, None, None))
        if r["network"] != net["network"] and not line_name:
            continue  # another network's route that is not included
        r["line"] = ref or r["ref"]
        r["master_name"] = line_name or name or r["name"]
        r["line_name"] = line_name
        kept.append(r)
    found = {r["line_name"] for r in kept}
    for (network, master), name in include.items():
        if name not in found:
            print(f"  warning: no {network} route master named {master!r} ({name})")
    return kept


def read_osm(routes: list[dict], bbox, operators: set[str], proposers: set[str] = frozenset()) -> dict:
    """From one pass over the extract:

    stops: {node id: (name or None, lon, lat)}, tracks: {way id: [(node, lon, lat)]},
    walk: [nodes], drive: [(nodes, km/h, oneway, parking allowed, service road)], signals: {node ids}, and construction sites of the given operators: building:
    [(way id, name, opening, nodes)] for track, building_stations: [(name, nodes)];
    proposed: {name: [nodes]}, the railway=proposed track of the `proposers` operators.
    """
    stop_ids = {n for r in routes for n in r["stops"]}
    track_ids = {w for r in routes for w in r["ways"]}
    out = {"stops": {}, "tracks": {}, "walk": [], "drive": [], "building": [], "building_stations": [], "proposed": defaultdict(list),
           "signals": {n.id for n in osmium.FileProcessor(str(PBF), osmium.osm.NODE)
                       .with_filter(osmium.filter.TagFilter(("highway", "traffic_signals")))}}
    fp = (
        osmium.FileProcessor(str(PBF), osmium.osm.NODE | osmium.osm.WAY)
        .with_locations()
        .with_filter(osmium.filter.IdFilter(stop_ids).enable_for(osmium.osm.NODE))
        .with_filter(osmium.filter.KeyFilter("highway", "railway", "construction:railway").enable_for(osmium.osm.WAY))
    )
    route_types = {"light_rail", "subway", "train", "rail"}
    for o in fp:
        if o.is_node():
            out["stops"][o.id] = (o.tags.get("name"), o.location.lon, o.location.lat)
            continue
        t = o.tags
        nodes = lambda: [(n.ref, n.location.lon, n.location.lat) for n in o.nodes if n.location.valid()]
        if o.id in track_ids:
            out["tracks"][o.id] = nodes()
        if "highway" in t:
            way = nodes()
            if any(bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3] for _, x, y in way):
                if walkable(t):
                    out["walk"].append(way)
                if kmh := drive_speed(t):
                    out["drive"].append((way, kmh, oneway(t), t.get("highway") not in NO_PARKING,
                                         t.get("highway") == "service"))
        if t.get("railway") == "proposed" and t.get("operator") in proposers and "name" in t:
            out["proposed"][t["name"]].append(nodes())
        if t.get("operator") not in operators:
            continue
        if t.get("construction:railway") == "station" or t.get("construction:public_transport") == "station":
            out["building_stations"].append((t.get("name", "?"), nodes()))
        elif (t.get("railway") == "construction" and t.get("construction") in route_types
              or t.get("construction:railway") in route_types) and "service" not in t:
            out["building"].append((o.id, t.get("name"), t.get("opening_date"), nodes()))
    return out


def region_shape(region: str) -> shapely.Geometry:
    """The region's outline (METRIC_CRS), from its census subsections."""
    sub = gpd.read_file(ROOT / "data/raw" / region / "bgri2021.gpkg", columns=[]).to_crs(METRIC_CRS)
    return shapely.union_all(shapely.simplify(sub.geometry.values, 10)).buffer(20).simplify(20)


class Graph:
    """Streets as a graph. Walking: ways are (nodes), cost in metres, both directions.
    Driving: ways are (nodes, km/h, oneway, parking, service), cost in seconds, and edges are
    reversed, so a search from a station finds where one can drive *to* it from."""

    def __init__(self, ways: list, drive: bool = False, signals: set = frozenset()):
        index, lon, lat, edges, park = {}, [], [], [], set()
        for way in ways:
            nodes, kmh, way_dir, parking, service = way if drive else (way, None, 0, True, False)
            prev = None
            for node, x, y in nodes:
                i = index.get(node)
                if i is None:
                    i = index[node] = len(lon)
                    lon.append(x)
                    lat.append(y)
                if parking:
                    park.add(i)
                if prev is not None and prev != i:
                    edges.append((prev, i, kmh or 0, way_dir, service))
                prev = i
        self.x, self.y = (np.asarray(v) for v in to_metric.transform(lon, lat))
        a, b, kmh, way_dir, service = (np.array(v) for v in zip(*edges))
        cost = np.hypot(self.x[a] - self.x[b], self.y[a] - self.y[b])
        if drive:
            cost = cost / (kmh / 3.6)
        self.adj = [[] for _ in range(len(lon))]
        for i, j, c, d in zip(a.tolist(), b.tolist(), cost.tolist(), way_dir.tolist()):
            # adj[i]: the edges into i, which on foot (both ways) are also the edges out of it.
            if d != 1:  # one can go j -> i
                self.adj[i].append((j, c))
            if d != -1:  # i -> j
                self.adj[j].append((i, c))
        self.drive = drive
        self.park = np.zeros(len(lon), dtype=bool)
        self.park[list(park)] = True
        # Driving: time lost passing through a node, at lights and junctions (motorway
        # interchanges are grade-separated, so their nodes cost nothing).
        self.delay = np.zeros(len(lon))
        if drive:
            neighbours = [set() for _ in range(len(lon))]  # along roads, not driveways or car parks
            for i, j in zip(a[~service].tolist(), b[~service].tolist()):
                neighbours[i].add(j)
                neighbours[j].add(i)
            junction = np.array([len(n) >= 3 for n in neighbours]) & self.park
            self.delay[junction] = JUNCTION_S
            lit = [index[n] for n in signals if n in index]
            self.delay[lit] = SIGNAL_S
        self.delay = self.delay.tolist()  # read in the search's inner loop
        self.starts = np.array(sorted(park))
        self.tree = shapely.STRtree(shapely.points(self.x[self.starts], self.y[self.starts]))

    def sources(self, points) -> dict[int, float]:
        """Graph nodes near the given metric points (within SNAP_M, else the nearest) where
        a trip can start: walking, with the straight-line walk to them; driving, at 0."""
        start: dict[int, float] = {}
        for px, py in points:
            near = self.tree.query(shapely.Point(px, py), predicate="dwithin", distance=SNAP_M)
            if len(near) == 0:
                near = self.tree.query_nearest(shapely.Point(px, py))
            for i in self.starts[near].tolist():
                d = 0.0 if self.drive else math.hypot(self.x[i] - px, self.y[i] - py)
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
                nd = d + w + self.delay[j]
                if nd <= limit and nd < dist.get(j, math.inf):
                    dist[j] = nd
                    heapq.heappush(heap, (nd, j))
        return dist

    def area(self, dist: dict[int, float], limit: float, reach_m=REACH_M, holes_m2=FILL_HOLES_M2,
             simplify_m=SIMPLIFY_M, quad_segs=4) -> shapely.Geometry:
        """The reached stretch of every street, widened by reach_m, small gaps filled."""
        segments, whole = [], set()
        for i, d in dist.items():
            for j, w in self.adj[i]:
                if not (self.park[i] or self.park[j]):
                    continue  # motorways and trunk roads pass homes by without reaching them
                go = min(w, limit - d)
                if go == w:
                    if (min(i, j), max(i, j)) in whole:
                        continue  # whole edge already added from j
                    whole.add((min(i, j), max(i, j)))
                t = go / w if w > 0 else 0
                segments.append([(self.x[i], self.y[i]),
                                 (self.x[i] + t * (self.x[j] - self.x[i]), self.y[i] + t * (self.y[j] - self.y[i]))])
        # Buffering each piece and merging is far faster than buffering one huge MultiLineString.
        area = shapely.union_all(shapely.buffer(shapely.linestrings(segments), reach_m, quad_segs=quad_segs))
        polys = [shapely.Polygon(p.exterior, [h for h in p.interiors if shapely.Polygon(h).area >= holes_m2])
                 for p in getattr(area, "geoms", [area])]
        # Simplifying can leave a ring touching itself; repair it, keeping only the polygons.
        area = shapely.make_valid(shapely.MultiPolygon(polys).simplify(simplify_m))
        if area.geom_type == "GeometryCollection":
            area = shapely.MultiPolygon([g for part in area.geoms for g in getattr(part, "geoms", [part])
                                         if g.geom_type == "Polygon"])
        return area


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


def line_names(route: dict, stops: dict) -> tuple[str, str]:
    """('Linha A', 'Senhor de Matosinhos – Estádio do Dragão'): the line from the route's
    name, its ends from its first and last named stops (route names carry typos)."""
    head, _, ends = route["name"].partition(":")
    names = [bare_name(stops[n][0]) for n in route["stops"] if n in stops and stops[n][0]]
    if len(names) >= 2:
        ends = f"{names[0]} – {names[-1]}"
    return route["line_name"] or head.strip(), re.sub(r"\s*(⇒|=>|->)\s*", " – ", ends.strip())


def build_lines(routes: list[dict], tracks: dict, stops: dict, net: dict) -> tuple[list[dict], dict, dict]:
    """One direction of travel per line, oriented consistently where lines share track.

    Returns the lines, {way: [line refs]} and {way: +1/-1}.
    """
    by_line = defaultdict(list)
    for r in sorted(routes, key=lambda r: r["id"]):
        by_line[r["line"]].append(r)
    orientation: dict[int, int] = {}
    lines, on_way = [], defaultdict(list)
    for line in sorted(by_line):
        def fit(route):
            o = orient_ways(route, tracks)
            shared = [w for w in o if w in orientation]
            agree = sum(o[w] == orientation[w] for w in shared) - sum(o[w] != orientation[w] for w in shared)
            return agree, len(shared), route["ref"] == line  # the line's own routes over variants
        best = max(by_line[line], key=fit)
        for w, sign in orient_ways(best, tracks).items():
            orientation.setdefault(w, sign)
            if line not in on_way[w]:
                on_way[w].append(line)
        name, ends = line_names(best, stops)
        place = re.sub(r"^.*Linha (d[aeo]s? )?", "", best["master_name"])
        lines.append({"ref": line, "label": line if net["badge"] == "ref" else place, "name": name,
                      "ends": ends, "colour": best["colour"], "net": net["key"],
                      **({"network": best["network"]} if best["network"] != net["network"] else {})})
    return lines, on_way, orientation


def planned_lines(building: list, cfg: dict, net: dict) -> tuple[list[dict], dict[str, list]]:
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
    lines = [{"ref": known[name]["ref"], "label": known[name]["ref"], "name": name, "ends": known[name]["ends"],
              "colour": known[name]["colour"], "net": net["key"], "planned": True,
              "opening": opening[name].most_common(1)[0][0] if opening[name] else None}
             for name in known if name in track]
    return lines, track


def smooth(points: np.ndarray, steps: int = 12) -> np.ndarray:
    """A centripetal Catmull-Rom curve through the points (metric), for track drawn from a
    plan's stations alone."""
    if len(points) < 3:
        return points
    p = np.vstack([2 * points[0] - points[1], points, 2 * points[-1] - points[-2]])
    out = [points[0]]
    for k in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[k - 1:k + 3]
        t0 = 0.0
        t1 = t0 + np.linalg.norm(p1 - p0) ** 0.5
        t2 = t1 + np.linalg.norm(p2 - p1) ** 0.5
        t3 = t2 + np.linalg.norm(p3 - p2) ** 0.5
        for t in np.linspace(t1, t2, steps + 1)[1:]:
            a1 = (t1 - t) / (t1 - t0) * p0 + (t - t0) / (t1 - t0) * p1
            a2 = (t2 - t) / (t2 - t1) * p1 + (t - t1) / (t2 - t1) * p2
            a3 = (t3 - t) / (t3 - t2) * p2 + (t - t2) / (t3 - t2) * p3
            b1 = (t2 - t) / (t2 - t0) * a1 + (t - t0) / (t2 - t0) * a2
            b2 = (t3 - t) / (t3 - t1) * a2 + (t - t1) / (t3 - t1) * a3
            out.append((t2 - t) / (t2 - t1) * b1 + (t - t1) / (t2 - t1) * b2)
    return np.array(out)


def project_lines(net: dict, specs: list[dict], proposed: dict, links: dict) -> tuple[list[dict], list[dict]]:
    """A `projects` set's lines (projects.py) and their track features. Its new stations are
    added to `specs`; a station it starts from or passes (no position given) is the open or
    building one of that name, which then also lists the line."""
    lines, feats = [], []
    for line in PROJECTS[net["projects"]]:
        ref, pts = line["ref"], []
        for k, (name, pos) in enumerate(line["stations"]):
            if pos is None:
                known = next((s for s in specs if bare(s["name"]) == bare(name)), None)
                if known is None:
                    raise SystemExit(f"{line['name']}: no station {name!r} to join")
                known["planned"] = sorted(set(known["planned"]) | {ref})
                known["nets"] = sorted(set(known["nets"]) | {net["key"]})
                # The track meets it at its platform nearest the line's neighbouring station.
                other = line["stations"][1 if k == 0 else k - 1][1]
                towards = metric([other])[0] if other else known["points"].mean(axis=0)
                pts.append(known["points"][np.argmin(np.hypot(*(known["points"] - towards).T))])
            else:
                pts.append(metric([pos])[0])
                add_station(specs, {"name": name, "lines": [], "planned": [ref], "nets": [net["key"]],
                                    "points": pts[-1][None, :]}, links)
        pts = np.array(pts)
        ways = proposed.get(line.get("track"), [])
        if ways:
            track = shapely.ops.linemerge([shapely.LineString(metric([(x, y) for _, x, y in w])) for w in ways])
            track = max(getattr(track, "geoms", [track]), key=lambda g: g.length)
            a, b = (track.project(shapely.Point(p)) for p in (pts[0], pts[-1]))
            coords = np.array(shapely.ops.substring(track, a, b).coords)
            if a > b:
                coords = coords[::-1]
            # Join the stations the OSM track stops short of (ISMAI, the Dragão).
            if math.dist(coords[0], pts[0]) > 20:
                coords = np.vstack([pts[0], coords])
            if math.dist(coords[-1], pts[-1]) > 20:
                coords = np.vstack([coords, pts[-1]])
        else:
            if line.get("track"):
                print(f"  {line['name']}: no OSM track {line['track']!r}, drawn through its stations")
            coords = smooth(pts)
        lon, lat = to_wgs.transform(coords[:, 0], coords[:, 1])
        feats.append(feature(zip(lon, lat), ref=ref, c=line["colour"], k=0, net=net["key"], planned=True, proj=True))
        shown = lambda name: next((k for k, names in links.items() if name in names), bare_name(name))
        lines.append({"ref": ref, "label": ref, "name": line["name"],
                      "ends": f"{shown(line['stations'][0][0])} – {shown(line['stations'][-1][0])}",
                      "colour": line["colour"], "net": net["key"], "planned": True, "project": True,
                      "status": line["status"], **{k: line[k] for k in ("km", "note") if line.get(k)}})
    return lines, feats


def bare_name(name: str) -> str:
    """Station name without a city prefix: 'Porto - Campanhã' -> 'Campanhã'."""
    return re.sub(r"^Porto\s*[-–]\s*", "", name)


def bare(name: str) -> str:
    return bare_name(name).casefold()


def add_station(specs: list[dict], spec: dict, interchanges: dict) -> None:
    """Add a station, or fold it into a same-named one within INTERCHANGE_M, or one listed
    with it in `interchanges` ({shown name: [names]}): either way one interchange.

    An interchange keeps the place of each station it joins (`parts`: centre, future?),
    so the map can mark each platform and link them.
    """
    part = (spec["points"].mean(axis=0), not spec["lines"])
    listed = {bare(n): shown for shown, names in interchanges.items() for n in names}
    gap = lambda a, b: np.hypot(*(a[:, None, :] - b[None, :, :]).transpose(2, 0, 1)).min()
    key = lambda name: listed.get(bare(name), bare(name))
    same = next((s for s in specs if key(s["name"]) == key(spec["name"])
                 and (bare(spec["name"]) in listed or gap(s["points"], spec["points"]) < INTERCHANGE_M)), None)
    if same is None:
        specs.append({**spec, "parts": [part]})
        return
    if all(math.dist(part[0], p) >= PART_M for p, _ in same["parts"]):
        same["parts"].append(part)
    # The walk starts from the stops in service; a building site only places a future station.
    if spec["lines"]:
        same["points"] = np.vstack([same["points"], spec["points"]]) if same["lines"] else spec["points"]
    same["lines"] = sorted(set(same["lines"]) | set(spec["lines"]))
    same["planned"] = sorted(set(same["planned"]) | set(spec["planned"]))
    same["nets"] = sorted(set(same["nets"]) | set(spec["nets"]))
    if bare(spec["name"]) in listed:
        same["name"] = listed[bare(spec["name"])]
    elif len(spec["name"]) < len(same["name"]):
        same["name"] = spec["name"]


def network_stations(routes: list[dict], stops: dict, net: dict) -> list[dict]:
    """In-service stations: the routes' stop positions grouped by name. Unnamed stop
    positions join the nearest named one within NAME_JOIN_M, or are dropped."""
    served = defaultdict(set)
    for r in routes:
        for n in r["stops"]:
            if n in stops:
                served[n].add(r["line"])
    named = [n for n in served if stops[n][0]]
    tree = shapely.STRtree(shapely.points(metric([stops[n][1:] for n in named])))
    name_of = {n: stops[n][0] for n in named}
    for n in served:
        if not stops[n][0]:
            k = tree.query_nearest(shapely.Point(metric([stops[n][1:]])[0]), max_distance=NAME_JOIN_M)
            if len(k):
                name_of[n] = stops[named[int(k[0])]][0]
    groups = defaultdict(list)
    for n, name in name_of.items():
        groups[name].append(n)
    return [{"name": name, "lines": sorted({line for n in ns for line in served[n]}), "planned": [],
             "nets": [net["key"]], "points": metric(sorted({stops[n][1:] for n in ns}))}
            for name, ns in sorted(groups.items())]


def frequencies(path: Path, stations: list[dict], points: list[np.ndarray], key: str) -> dict:
    """Trains per hour at each of the network's stations in each period: per line and
    direction (the mean of the two directions), and all lines in both directions together.

    Sets station["freq"] = {line ref: [per period], "all": [per period]} and returns the
    timetable's periods and validity. GTFS stops are matched to the stations' own stop
    positions (`points`, metric), not their centres, which interchanges pull aside.
    """
    z = zipfile.ZipFile(path)
    read = lambda name: csv.DictReader(io.StringIO(z.read(name).decode("utf-8-sig")))
    routes = {r["route_id"]: r["route_short_name"] for r in read("routes.txt")}
    calendar = list(read("calendar.txt"))
    runs_on = {c["service_id"]: c for c in calendar}
    trips = {t["trip_id"]: (routes[t["route_id"]], runs_on.get(t["service_id"]), t["direction_id"])
             for t in read("trips.txt")}

    mine = [i for i, s in enumerate(stations) if key in s["nets"] and s["lines"] for _ in points[i]]
    tree = shapely.STRtree(shapely.points(np.vstack([points[i] for i in dict.fromkeys(mine)])))
    station_of = {}
    for stop in read("stops.txt"):
        p = shapely.Point(metric([(float(stop["stop_lon"]), float(stop["stop_lat"]))])[0])
        k = tree.query_nearest(p, max_distance=GTFS_MATCH_M)
        if len(k):
            station_of[stop["stop_id"]] = mine[int(k[0])]
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
    for station, c in counts.items():
        freq = stations[station].setdefault("freq", {})
        for route in sorted({r for r, _, _ in c}):
            freq[route] = [round(sum(c[route, d, k] for d in ("0", "1")) / 2 / hours[k], 1) for k in range(len(PERIODS))]
        total = [sum(n for (_, _, kk), n in c.items() if kk == k) / hours[k] for k in range(len(PERIODS))]
        freq["all"] = [round(a + b, 1) for a, b in zip(freq.get("all", [0] * len(PERIODS)), total)]
    fmt = lambda d: f"{d[:4]}-{d[4:6]}-{d[6:]}"
    hm = lambda h: f"{int(h)}:{round(h % 1 * 60):02d}"
    return {"periods": [{"label": label, "day": day, "hours": f"{hm(a)}–{hm(b)}"} for label, day, a, b in PERIODS],
            "from": fmt(min(c["start_date"] for c in calendar)), "to": fmt(max(c["end_date"] for c in calendar))}


def read_grid(region: str):
    text = (ROOT / "data" / f"{region}.js").read_text()
    grid = json.loads(text.split("=", 1)[1].rstrip().rstrip(";"))
    c, size = grid["columns"], grid["cell"]
    e, n = np.array(c["e"]) * size, np.array(c["n"]) * size
    cells = shapely.box(e, n, e + size, n + size)
    return cells, np.array(c["pop"], dtype=float), shapely.STRtree(cells)


def residents(area_metric: shapely.Geometry, cells, pop, tree) -> float:
    # Reprojecting can leave a thin sliver ring touching itself: repair before intersecting.
    area = shapely.make_valid(shapely.transform(area_metric, lambda xy: np.column_stack(to_grid.transform(xy[:, 0], xy[:, 1]))))
    hit = tree.query(area, predicate="intersects")
    share = shapely.area(shapely.intersection(cells[hit], area)) / shapely.area(cells[hit])
    return float((pop[hit] * share).sum())


def lonlat_rings(geom: shapely.Geometry, decimals: int = 5) -> list:
    """MultiPolygon as GeoJSON coordinates, exteriors counter-clockwise."""
    out = []
    for p in getattr(geom, "geoms", [geom]):
        p = shapely.orient_polygons(p)
        rings = [p.exterior, *p.interiors]
        out.append([[[round(x, decimals), round(y, decimals)] for x, y in zip(*to_wgs.transform(*np.asarray(r.coords).T))]
                    for r in rings])
    return out


def feature(coords, **props) -> dict:
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "LineString", "coordinates": [[round(x, 5), round(y, 5)] for x, y in coords]}}


def main(region: str) -> None:
    cfg = REGIONS[region]
    nets = cfg["transit"]
    routes = {net["key"]: read_routes(net) for net in nets if not net.get("projects")}
    for key, rs in routes.items():
        print(f"{key}: {len(rs)} routes, lines {sorted({r['line'] for r in rs})}")
    osm = read_osm([r for rs in routes.values() for r in rs], cfg["bbox"],
                   {net["network"] for net in nets if net.get("planned")},
                   {net["proposed_operator"] for net in nets if net.get("proposed_operator")})
    stops, tracks = osm["stops"], osm["tracks"]
    shape = region_shape(region)
    near_region = shape.buffer(TRACK_MARGIN_M)
    shapely.prepare(near_region)

    lines, tracks_geojson, specs = [], [], []
    links = cfg.get("interchanges", {})
    for net in nets:
        if net.get("projects"):
            new_lines, feats = project_lines(net, specs, osm["proposed"], links)
            lines += new_lines
            tracks_geojson += feats
            continue
        rs = routes[net["key"]]
        net_lines, on_way, orientation = build_lines(rs, tracks, stops, net)
        lines += net_lines
        colour = {line["ref"]: line["colour"] for line in net_lines}
        for w, refs in on_way.items():
            coords = [(x, y) for _, x, y in tracks[w]][:: orientation[w]]
            if not shapely.contains_xy(near_region, metric(coords)).any():
                continue  # track far outside the region
            if net.get("style") == "rail":  # one railway symbol per track, whatever runs on it
                tracks_geojson.append(feature(coords, ref=refs[0], c=colour[refs[0]], k=0, net=net["key"], rail=True))
                continue
            for k, ref in enumerate(sorted(refs)):
                tracks_geojson.append(feature(coords, ref=ref, c=colour[ref], k=k - (len(refs) - 1) / 2, net=net["key"]))
        for spec in network_stations(rs, stops, net):
            if shape.distance(shapely.Point(spec["points"].mean(axis=0))) < 200:
                add_station(specs, spec, links)

        # Lines under construction: their track, then their stations.
        planned = net.get("planned")
        if not planned:
            continue
        new_lines, new_track = planned_lines(osm["building"], planned, net)
        lines += new_lines
        ref_of = {name: planned["lines"][name]["ref"] for name in new_track}
        track_m = {}
        for name, ways in new_track.items():
            ref = ref_of[name]
            for way in ways:
                tracks_geojson.append(feature([(x, y) for _, x, y in way], ref=ref, c=planned["lines"][name]["colour"],
                                              k=0, net=net["key"], planned=True))
            track_m[name] = shapely.MultiLineString([metric([(x, y) for _, x, y in way]) for way in ways])
        for name, nodes in osm["building_stations"]:
            pts = metric([(x, y) for _, x, y in nodes])
            near = [line for line, g in track_m.items() if g.distance(shapely.MultiPoint(pts)) < 150]
            if near:
                add_station(specs, {"name": name, "lines": [], "planned": sorted(ref_of[line] for line in near),
                                    "nets": [net["key"]], "points": pts}, links)
        for name, (line, lon, lat) in planned.get("unmapped_stations", {}).items():
            if line not in track_m:
                continue
            given = shapely.Point(metric([(lon, lat)])[0])
            on_track = shapely.ops.nearest_points(track_m[line], given)[0]
            print(f"  {name}: placed on the {line} track, {on_track.distance(given):.0f} m from the given point")
            add_station(specs, {"name": name, "lines": [], "planned": [ref_of[line]], "nets": [net["key"]],
                                "points": np.array([[on_track.x, on_track.y]]), "approx": True}, links)

    # Walkable ways are only needed around the stations.
    allpts = np.vstack([s["points"] for s in specs])
    lon, lat = to_wgs.transform(allpts[:, 0], allpts[:, 1])
    pad = 0.03  # degrees, comfortably more than the walk
    box = (min(lon) - pad, min(lat) - pad, max(lon) + pad, max(lat) + pad)
    graph = Graph([w for w in osm["walk"] if any(box[0] <= x <= box[2] and box[1] <= y <= box[3] for _, x, y in w)])
    print(f"{len(stops)} stop positions, {len(tracks)} track ways, walking graph of {len(graph.adj)} nodes")
    # Driving areas, for the networks with `drive_minutes`, over every road in the region's box.
    drive_min = {net["key"]: net["drive_minutes"] for net in nets if net.get("drive_minutes")}
    roads = Graph(osm["drive"], drive=True, signals=osm["signals"]) if drive_min else None
    if roads:
        print(f"driving graph of {len(roads.adj)} nodes, {sum(d == SIGNAL_S for d in roads.delay)} with traffic lights, "
              f"{sum(d == JUNCTION_S for d in roads.delay)} other junctions")
    net_of = {line["ref"]: line["net"] for line in lines}

    cells, pop, tree = read_grid(region)
    stations, areas, points, drives = [], [], [], []
    for spec in sorted(specs, key=lambda s: s["name"]):
        points.append(spec["points"])
        x, y = spec["points"].T
        area = graph.area(graph.reach(graph.sources(zip(x, y)), WALK_M), WALK_M)
        areas.append(area)
        clon, clat = to_wgs.transform(x.mean(), y.mean())
        minutes = max((drive_min.get(net_of[r], 0) for r in spec["lines"]), default=0)
        drive = None
        if minutes:
            limit = minutes * 60
            drive = roads.area(roads.reach(roads.sources(zip(x, y)), limit), limit, DRIVE_REACH_M,
                               DRIVE_FILL_HOLES_M2, DRIVE_SIMPLIFY_M, quad_segs=2)
        drives.append(drive)
        stations.append({
            "name": spec["name"],
            "lines": spec["lines"],
            "planned": spec["planned"],
            "nets": spec["nets"],
            **({"approx": True} if spec.get("approx") else {}),
            "lon": round(clon, 5),
            "lat": round(clat, 5),
            "pop": round(residents(area, cells, pop, tree)),
            "km2": round(area.area / 1e6, 2),
            "walk": lonlat_rings(area),
            **({"parts": [[*(round(v, 5) for v in to_wgs.transform(*xy)), int(future)]
                          for xy, future in sorted(spec["parts"], key=lambda p: p[1])]}
               if len(spec["parts"]) > 1 else {}),
            **({"drive": {"minutes": minutes, "pop": round(residents(drive, cells, pop, tree)),
                          "km2": round(drive.area / 1e6, 1)}} if drive else {}),
        })

    networks = []
    building = {line["ref"] for line in lines if line.get("planned") and not line.get("project")}
    metro_soon = [a for s, a in zip(stations, areas) if s["lines"] and "metro" in s["nets"] or set(s["planned"]) & building]
    for net in nets:
        key = net["key"]
        if net.get("projects"):
            # Planned lines: their new stations, the residents near all their stations, and the
            # metro's reach with them (open and building lines too).
            mine = [(s, a) for s, a in zip(stations, areas) if any(net_of[r] == key for r in s["planned"])]
            info = {"key": key, "label": net["label"], "network": net["network"], "projects": True,
                    "stations": sum(not s["lines"] and not set(s["planned"]) & building for s, _ in mine),
                    "covered_pop": round(residents(shapely.union_all([a for _, a in mine]), cells, pop, tree)),
                    "covered_pop_metro": round(residents(shapely.union_all(metro_soon + [a for _, a in mine]), cells, pop, tree))}
            networks.append(info)
            print(f"{net['network']}: {sum(line['net'] == key for line in lines)} lines, {info['stations']} new stations, "
                  f"{info['covered_pop']} residents near their stations; metro with them: {info['covered_pop_metro']} "
                  f"({100 * info['covered_pop_metro'] / pop.sum():.1f}%)")
            continue
        open_ = [a for s, a in zip(stations, areas) if any(net_of[r] == key for r in s["lines"])]
        soon = [a for s, a in zip(stations, areas) if any(net_of[r] == key for r in s["lines"] + s["planned"])]
        info = {"key": key, "label": net["label"], "network": net["network"], "stations": len(open_),
                "covered_pop": round(residents(shapely.union_all(open_), cells, pop, tree))}
        if key in drive_min:
            by_car = [d for s, d in zip(stations, drives) if d and any(net_of[r] == key for r in s["lines"])]
            info["drive_minutes"] = drive_min[key]
            info["drive_covered_pop"] = round(residents(shapely.union_all(by_car), cells, pop, tree))
        if len(soon) > len(open_):
            info["covered_pop_planned"] = round(residents(shapely.union_all(soon), cells, pop, tree))
        gtfs = ROOT / "data/raw/gtfs" / net.get("gtfs", "-")
        if net.get("gtfs") and gtfs.exists():
            info["timetable"] = frequencies(gtfs, stations, points, key)
        elif net.get("gtfs"):
            print(f"no {gtfs.relative_to(ROOT)}: {net['network']} frequencies left out")
        networks.append(info)
        share = 100 * info["covered_pop"] / pop.sum()
        print(f"{net['network']}: {sum(line['net'] == key for line in lines)} lines, {len(open_)} stations open, "
              f"{info['covered_pop']} residents ({share:.1f}%) within {WALK_MINUTES} min"
              + (f", {info['covered_pop_planned']} with lines under construction" if "covered_pop_planned" in info else "")
              + (f"; {info['drive_covered_pop']} ({100 * info['drive_covered_pop'] / pop.sum():.1f}%) within a "
                 f"{info['drive_minutes']}-min drive" if "drive_minutes" in info else ""))
    every = residents(shapely.union_all([a for s, a in zip(stations, areas) if s["lines"]]), cells, pop, tree)

    out = ROOT / "data" / f"{region}_transit.js"
    out.write_text("window.TRANSIT_DATA = " + json.dumps({
        "walk": {"minutes": WALK_MINUTES, "kmh": WALK_KMH, "metres": round(WALK_M)},
        "drive": {"maxspeed_share": MAXSPEED_SHARE, "reach_m": DRIVE_REACH_M,
                  "signal_s": SIGNAL_S, "junction_s": JUNCTION_S},
        "total_pop": round(pop.sum()),
        "covered_pop": round(every),
        "networks": networks,
        "lines": lines,
        "tracks": {"type": "FeatureCollection", "features": tracks_geojson},
        "stations": stations,
    }, ensure_ascii=False, separators=(",", ":")) + ";\n")
    print(f"{len(stations)} stations; {every:.0f} residents ({100 * every / pop.sum():.1f}%) within "
          f"{WALK_MINUTES} min of any -> {out} ({out.stat().st_size / 1e6:.2f} MB)")
    # Driving areas are large, so they go in their own file, which the page loads after the rest.
    if roads:
        out = ROOT / "data" / f"{region}_drive.js"
        out.write_text("window.DRIVE_DATA = " + json.dumps(
            {s["name"]: lonlat_rings(d, 4) for s, d in zip(stations, drives) if d},
            ensure_ascii=False, separators=(",", ":")) + ";\n")
        print(f"{sum(d is not None for d in drives)} driving areas -> {out} ({out.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "amp")
