"""道路に沿った参考経路（src/review/road_routes.py）と、そのレビューHTMLへの受け渡し
（src/review/review_builder.py）の回帰テスト。

外部通信は行わない。道路は、OSMnxが返すグラフと同じ形（ノードに x/y、辺に length と任意の
geometry を持つ networkx.MultiDiGraph）の小さな架空の道路網で確認する。実際の道路データでの確認は
experiments/road_routes/ の手順で行う（docs/road-routes-check.md を参照）。

    python3 -m unittest discover -s test
"""
import importlib.util
import unittest
from pathlib import Path
from unittest import mock

try:
    import networkx as nx
    import numpy as np
    import pandas as pd
    from pyproj import Transformer
    from shapely.geometry import LineString, box
    DEPENDENCIES = True
except ImportError:  # pragma: no cover
    DEPENDENCIES = False

REVIEW_DIR = Path(__file__).resolve().parents[1] / "src" / "review"


def load(name):
    spec = importlib.util.spec_from_file_location(name, REVIEW_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# 糸満市役所付近を原点にした、メートル単位の架空の道路網を作る
ORIGIN_LON, ORIGIN_LAT = 127.6658, 26.1236


def lonlat(x, y):
    """原点からの東西x・南北y（メートル）を経緯度にする。"""
    to_metric = Transformer.from_crs("EPSG:4326", "EPSG:6683", always_xy=True)
    to_lonlat = Transformer.from_crs("EPSG:6683", "EPSG:4326", always_xy=True)
    ox_, oy_ = to_metric.transform(ORIGIN_LON, ORIGIN_LAT)
    return to_lonlat.transform(ox_ + x, oy_ + y)


def latlon(x, y):
    lon, lat = lonlat(x, y)
    return lat, lon


def make_graph(nodes, edges):
    """nodes: {id: (x, y)}、edges: [(u, v, [(x, y), ...] or None)]。往復の辺を作る（徒歩用と同じ）。"""
    graph = nx.MultiDiGraph()
    for node_id, (x, y) in nodes.items():
        lon, lat = lonlat(x, y)
        graph.add_node(node_id, x=lon, y=lat)
    for u, v, shape in edges:
        points = shape if shape is not None else [nodes[u], nodes[v]]
        line = LineString([lonlat(x, y) for x, y in points])
        length = sum(np.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))
        data = {"length": length}
        if shape is not None:
            data["geometry"] = line
        graph.add_edge(u, v, **data)
        reverse = dict(data)
        if shape is not None:
            reverse["geometry"] = LineString(list(line.coords)[::-1])
        graph.add_edge(v, u, **reverse)
    return graph


def review_rows(residents):
    """residents: [((lat, lon) or None, [(lat, lon), ...])]"""
    rows = []
    for point, shelters in residents:
        rows.append({
            "latitude": point[0] if point else None,
            "longitude": point[1] if point else None,
            "candidates": [] if point is None else [
                {"rank": rank, "latitude": lat, "longitude": lon}
                for rank, (lat, lon) in enumerate(shelters, start=1)
            ],
        })
    return rows


@unittest.skipUnless(DEPENDENCIES, "networkx / shapely / pyproj が必要です")
class RoadRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rr = load("road_routes")

    def network(self, graph, **kwargs):
        return self.rr.RoadNetwork(graph, {"retrieved_at": "test"}, **kwargs)

    def metric(self, lat, lon):
        to_metric = Transformer.from_crs("EPSG:4326", "EPSG:6683", always_xy=True)
        ox_, oy_ = to_metric.transform(ORIGIN_LON, ORIGIN_LAT)
        x, y = to_metric.transform(lon, lat)
        return x - ox_, y - oy_

    def test_route_follows_curved_road_shape(self):
        # 0 → 1 の道路は北へ200m膨らむ曲線。直線（または分岐点どうしを直線で結んだ形）ではなく、
        # 道路の線の形に沿うこと。
        shape = [(0, 0), (100, 200), (200, 200), (300, 0)]
        graph = make_graph({0: (0, 0), 1: (300, 0)}, [(0, 1, shape)])
        result = self.rr.compute_road_routes(self.network(graph), review_rows(
            [(latlon(0, -10), [latlon(300, -20)])]))
        route = result["routes"][0][0]
        self.assertEqual(route["status"], "ok")
        expected = 2 * np.hypot(100, 200) + 100
        self.assertAlmostEqual(route["road_distance_m"], expected, delta=0.5)
        self.assertAlmostEqual(route["resident_connection_m"], 10, delta=0.1)
        self.assertAlmostEqual(route["shelter_connection_m"], 20, delta=0.1)
        path = [self.metric(lat, lon) for lat, lon in route["path"]]
        self.assertGreaterEqual(len(path), 4)
        self.assertTrue(any(y > 150 for _, y in path), "道路の曲がった形が経路に含まれていない")
        # 始点・終点は道路上の接続点（地点そのものではない）
        self.assertAlmostEqual(path[0][1], 0, delta=0.2)
        self.assertAlmostEqual(path[-1][0], 300, delta=0.2)

    def test_shortest_path_matches_networkx_on_grid(self):
        # 5x5 の格子で、辺ごとに長さの違う（曲がった）道路にし、networkx の最短経路と一致すること
        rng = np.random.default_rng(1)
        nodes = {(i, j): (i * 100.0, j * 100.0) for i in range(5) for j in range(5)}
        ids = {key: n for n, key in enumerate(nodes)}
        edges = []
        for (i, j) in nodes:
            for di, dj in ((1, 0), (0, 1)):
                if (i + di, j + dj) in nodes:
                    a, b = nodes[(i, j)], nodes[(i + di, j + dj)]
                    bend = rng.uniform(0, 40)
                    mid = ((a[0] + b[0]) / 2 + dj * bend, (a[1] + b[1]) / 2 + di * bend)
                    edges.append((ids[(i, j)], ids[(i + di, j + dj)], [a, mid, b]))
        graph = make_graph({ids[k]: v for k, v in nodes.items()}, edges)
        network = self.network(graph)
        reference = nx.Graph()
        for u, v, data in graph.edges(data=True):
            reference.add_edge(u, v, w=data["length"])

        # 地点は分岐点のすぐ脇に置き、接続点＝分岐点になるようにする
        for start, goal in (((0, 0), (4, 4)), ((0, 4), (4, 0)), ((2, 1), (3, 4))):
            sx, sy = nodes[start]
            gx, gy = nodes[goal]
            result = self.rr.compute_road_routes(network, review_rows(
                [(latlon(sx - 0.01, sy - 0.01), [latlon(gx + 0.01, gy + 0.01)])]))
            route = result["routes"][0][0]
            expected = nx.dijkstra_path_length(reference, ids[start], ids[goal], weight="w")
            self.assertAlmostEqual(route["road_distance_m"], expected, delta=0.5)

    def test_uses_shorter_of_parallel_roads(self):
        long_way = [(0, 0), (50, 300), (100, 0)]
        graph = make_graph({0: (0, 0), 1: (100, 0), 2: (-100, 0), 3: (200, 0)},
                           [(2, 0, None), (0, 1, long_way), (0, 1, None), (1, 3, None)])
        result = self.rr.compute_road_routes(self.network(graph), review_rows(
            [(latlon(-100, -5), [latlon(200, -5)])]))
        self.assertAlmostEqual(result["routes"][0][0]["road_distance_m"], 300, delta=0.5)

    def sidewalk_graph(self, road_y):
        """車道（y=road_y）と、その脇の歩道（y=5。西端でしか車道とつながっていない）。"""
        nodes = {13: (-500, road_y), 14: (1000, road_y), 12: (-500, 5), 11: (200, 5)}
        return make_graph(nodes, [(13, 14, None), (12, 11, None), (12, 13, None)])

    def test_nearly_as_close_road_is_used_when_nearest_line_is_poorly_connected(self):
        # 本人は歩道まで2m・車道まで13m。歩道は西端でしか車道とつながっていないため、歩道だけに
        # 接続すると大回り（約1.4km）になる。ほぼ同じ近さの車道へ接続した経路（200m）を選ぶこと。
        result = self.rr.compute_road_routes(self.network(self.sidewalk_graph(-10)), review_rows(
            [(latlon(100, 3), [latlon(300, -15)])]))
        route = result["routes"][0][0]
        self.assertAlmostEqual(route["road_distance_m"], 200, delta=0.5)
        self.assertAlmostEqual(route["resident_connection_m"], 13, delta=0.1)

    def test_clearly_farther_road_is_not_used_to_shortcut(self):
        # 車道が43m先（最も近い歩道より20m以上遠い）なら、道路でない部分を長くとる近道はしない
        result = self.rr.compute_road_routes(self.network(self.sidewalk_graph(-40)), review_rows(
            [(latlon(100, 3), [latlon(300, -45)])]))
        route = result["routes"][0][0]
        self.assertAlmostEqual(route["resident_connection_m"], 2, delta=0.1)
        self.assertAlmostEqual(route["road_distance_m"], 600 + 45 + 800, delta=0.5)

    def test_both_points_connecting_to_same_road_point(self):
        # 本人と避難所が交差点の両側にいて、どちらも交差点（道路上の同じ点）に接続する場合
        graph = make_graph({0: (0, 0), 1: (0, 300), 2: (0, -300)}, [(0, 1, None), (0, 2, None)])
        result = self.rr.compute_road_routes(self.network(graph), review_rows(
            [(latlon(-8, 0), [latlon(9, 0)])]))
        route = result["routes"][0][0]
        self.assertEqual(route["status"], "ok")
        self.assertAlmostEqual(route["road_distance_m"], 0, delta=0.01)
        self.assertEqual(len(route["path"]), 2)
        self.assertEqual(route["path"][0], route["path"][1])

    def test_same_road_uses_distance_along_road(self):
        graph = make_graph({0: (0, 0), 1: (1000, 0)}, [(0, 1, None)])
        result = self.rr.compute_road_routes(self.network(graph), review_rows(
            [(latlon(400, 20), [latlon(700, -30)])]))
        route = result["routes"][0][0]
        self.assertAlmostEqual(route["road_distance_m"], 300, delta=0.5)

    def test_too_far_from_road_is_not_connected_to_distant_road(self):
        graph = make_graph({0: (0, 0), 1: (500, 0)}, [(0, 1, None)])
        rows = review_rows([
            (latlon(100, 400), [latlon(400, 10)]),   # 本人が道路から400m
            (latlon(100, 10), [latlon(400, 400)]),   # 避難所が道路から400m
        ])
        result = self.rr.compute_road_routes(self.network(graph), rows)
        self.assertEqual(result["routes"][0][0], {"rank": 1, "status": "unavailable",
                                                  "reason": "resident_too_far"})
        self.assertEqual(result["routes"][1][0], {"rank": 1, "status": "unavailable",
                                                  "reason": "shelter_too_far"})
        self.assertEqual(result["stats"]["ok"], 0)

    def test_disconnected_roads_give_no_route(self):
        graph = make_graph({0: (0, 0), 1: (100, 0), 2: (1000, 0), 3: (1100, 0)},
                           [(0, 1, None), (2, 3, None)])
        result = self.rr.compute_road_routes(self.network(graph), review_rows(
            [(latlon(50, 5), [latlon(1050, 5)])]))
        route = result["routes"][0][0]
        self.assertEqual(route["reason"], "not_connected")
        self.assertNotIn("path", route)
        self.assertEqual(result["stats"]["not_connected"], 1)

    def test_resident_without_coordinates_has_no_routes(self):
        graph = make_graph({0: (0, 0), 1: (100, 0)}, [(0, 1, None)])
        result = self.rr.compute_road_routes(self.network(graph), review_rows(
            [(None, []), (latlon(10, 5), [latlon(90, 5)])]))
        self.assertEqual(result["routes"][0], [])
        self.assertEqual(result["routes"][1][0]["status"], "ok")

    def test_outside_routable_area_is_not_routed(self):
        graph = make_graph({0: (0, 0), 1: (3000, 0)}, [(0, 1, None)])
        west, south = lonlat(-100, -100)
        east, north = lonlat(1000, 100)
        network = self.network(graph, routable_area=box(west, south, east, north))
        result = self.rr.compute_road_routes(network, review_rows([
            (latlon(100, 5), [latlon(900, 5), latlon(2500, 5)]),
            (latlon(2000, 5), [latlon(900, 5)]),
        ]))
        self.assertEqual(result["routes"][0][0]["status"], "ok")
        self.assertEqual(result["routes"][0][1]["reason"], "outside_area")
        self.assertEqual(result["routes"][1][0]["reason"], "outside_area")

    def test_shortest_paths_are_computed_once_per_shelter(self):
        graph = make_graph({0: (0, 0), 1: (100, 0), 2: (200, 0)}, [(0, 1, None), (1, 2, None)])
        shelters = [latlon(20, 5), latlon(180, 5)]
        rows = review_rows([(latlon(x, 5), shelters) for x in (40, 60, 90, 120, 150)])
        network = self.network(graph)
        with mock.patch.object(self.rr, "dijkstra", wraps=self.rr.dijkstra) as counted:
            result = self.rr.compute_road_routes(network, rows)
        self.assertEqual(counted.call_count, 2)  # 避難所2か所 × 1回（要支援者の人数によらない）
        self.assertEqual(result["stats"]["ok"], 10)

    def test_fetch_uses_fixed_public_area_only(self):
        """道路データの取得範囲は、固定の行政区域IDと固定の余裕範囲だけから決まる
        （要支援者の情報は取得関数へ渡らない）。"""
        boundary = box(*lonlat(-3000, -3000), *lonlat(3000, 3000))
        graph = make_graph({0: (0, 0), 1: (100, 0)}, [(0, 1, None)])
        fake_ox = mock.Mock()
        fake_ox.geocode_to_gdf.return_value = _GeoFrame(boundary)
        fake_ox.graph_from_polygon.return_value = graph
        network = self.rr.load_road_network(fake_ox, log=lambda *a: None)

        fake_ox.geocode_to_gdf.assert_called_once_with("R4559181", by_osmid=True)
        (area,), kwargs = fake_ox.graph_from_polygon.call_args
        self.assertEqual(kwargs["network_type"], "walk")
        self.assertTrue(area.contains(boundary))
        # 余裕範囲はおおむね2km（輪郭の間引き分だけ小さくなり得る）
        to_metric = Transformer.from_crs("EPSG:4326", "EPSG:6683", always_xy=True)
        from shapely.ops import transform
        margin = transform(to_metric.transform, area).bounds[2] - transform(to_metric.transform, boundary).bounds[2]
        self.assertAlmostEqual(margin, 2000, delta=110)
        self.assertEqual(network.edge_count, 1)
        self.assertTrue(network.is_routable(*latlon(3800, 0)))
        self.assertFalse(network.is_routable(*latlon(4200, 0)))

    def test_fetch_rejects_unexpected_boundary(self):
        fake_ox = mock.Mock()
        fake_ox.geocode_to_gdf.return_value = _GeoFrame(box(*lonlat(5000, 5000), *lonlat(6000, 6000)))
        with self.assertRaises(RuntimeError):
            self.rr.load_road_network(fake_ox, log=lambda *a: None)
        fake_ox.graph_from_polygon.assert_not_called()


class _GeoFrame:
    """geocode_to_gdf の戻り値の代わり（テストで使う属性だけ）。"""

    def __init__(self, geometry):
        self.geometry = pd.Series([geometry])
        self.columns = ["geometry", "display_name"]
        self._display = pd.Series(["糸満市, 沖縄県, 日本"])

    def __len__(self):
        return 1

    def __getitem__(self, key):
        return self._display


@unittest.skipUnless(DEPENDENCIES, "pandas / shapely が必要です")
class ReviewBuilderRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.rb = load("review_builder")
        except ImportError as error:  # pragma: no cover
            raise unittest.SkipTest(f"review_builder を読み込めません: {error}")

    def sample(self):
        final_df = pd.DataFrame({
            "resident_id": ["A", "B"], "address": ["架空1", "架空2"],
            "match_status": ["ok", "no_coordinates"],
            "candidate_1": ["避難所X", np.nan], "distance_1_m": [123.4, np.nan],
        })
        rows = [
            {"latitude": 26.12, "longitude": 127.66, "candidates": [{
                "rank": 1, "name": "避難所X", "latitude": 26.121, "longitude": 127.661,
                "distance_m": 123.4, "disaster_support": np.nan, "shelter_in_hazard": np.nan,
                "shelter_hazard_types": np.nan, "straight_line_intersects_hazard": np.nan,
                "straight_line_hazard_types": np.nan}]},
            {"latitude": None, "longitude": None, "candidates": []},
        ]
        routes = {"status": "ok", "message": None, "max_connection_m": 150, "area_name": "糸満市",
                  "routable_margin_m": 1000, "source": {"attribution": "© OpenStreetMap contributors"},
                  "stats": {"routes": 1, "ok": 1},
                  "routes": [[{"rank": 1, "status": "ok", "road_distance_m": 200.0,
                               "resident_connection_m": 5.0, "shelter_connection_m": 6.0,
                               "path": [[26.1201, 127.6601], [26.1209, 127.6609]]}], []]}
        return final_df, rows, routes

    def test_routes_are_attached_to_candidates(self):
        final_df, rows, routes = self.sample()
        data = self.rb.build_review_data(final_df, rows, [], {}, 1, [], routes)
        self.assertEqual(data["road_routes"]["status"], "ok")
        self.assertNotIn("routes", data["road_routes"])
        route = data["residents"][0]["candidates"][0]["road_route"]
        self.assertEqual(route["road_distance_m"], 200.0)
        self.assertNotIn("rank", route)
        self.rb.verify_review_data(data, final_df, 1)

    def test_routes_off_keeps_previous_data_shape(self):
        final_df, rows, _ = self.sample()
        data = self.rb.build_review_data(final_df, rows, [], {}, 1, [])
        self.assertIsNone(data["road_routes"])
        self.assertNotIn("road_route", data["residents"][0]["candidates"][0])

    def test_failed_routes_are_reported_without_route_data(self):
        final_df, rows, _ = self.sample()
        failed = self.rb.road_routes_unavailable("道路データ（OpenStreetMap）の取得に失敗しました")
        self.rb.verify_road_routes(failed, rows)
        data = self.rb.build_review_data(final_df, rows, [], {}, 1, [], failed)
        self.assertEqual(data["road_routes"]["status"], "failed")
        self.assertNotIn("road_route", data["residents"][0]["candidates"][0])

    def test_mismatched_routes_stop_before_html(self):
        _, rows, routes = self.sample()
        routes["routes"] = [[], []]
        with self.assertRaises(RuntimeError):
            self.rb.verify_road_routes(routes, rows)
        _, rows, routes = self.sample()
        routes["routes"][0][0]["path"] = [[26.12, 127.66]]
        with self.assertRaises(RuntimeError):
            self.rb.verify_road_routes(routes, rows)

    def test_osm_notice_is_separate_from_basemap_notice(self):
        import tempfile
        _, _, routes = self.sample()
        with tempfile.TemporaryDirectory() as directory:
            self.rb.write_osm_notice(routes, Path(directory))
            text = (Path(directory) / "osm-roads-NOTICE.txt").read_text(encoding="utf-8")
        self.assertIn("© OpenStreetMap contributors", text)
        self.assertIn("背景地図（国土地理院）とは別のデータ", text)
        self.assertIn("安全かは確認していません", text)


if __name__ == "__main__":
    unittest.main()
