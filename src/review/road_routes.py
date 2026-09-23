"""道路に沿った参考経路（レビューHTMLの追加の参考情報）の算出処理。

OpenStreetMap（OSM）の徒歩用道路ネットワーク上で、要支援者地点と候補避難所を結ぶ最短の経路を求め、
レビューHTMLで表示するための経路座標を作ります。正式な成果物である `assigned_shelters.csv` の
列・値・候補順位・直線距離・ハザード判定には一切関与しません（ここで作るのはレビューHTMLにだけ
同梱する参考情報です）。

個人情報の扱い:
    外部へ通信するのは、道路データを取得するときの次の2回だけです。どちらも要支援者の座標・住所・
    ID を含みません（要支援者の座標から取得範囲を作ることもしません）。
      1. 糸満市の行政区域（OSMのリレーションID固定）を Nominatim から取得する
      2. その行政区域に固定の余裕範囲（約2km）を付けた範囲の徒歩用道路を Overpass API から取得する
    要支援者・避難所を道路へ接続する処理と経路計算は、取得した道路データを使って実行環境の中だけで
    行います。

経路の求め方:
    - 道路データは1回だけ取得し、全要支援者で使い回す
    - 要支援者地点・避難所地点は、最も近い道路（線）上の点へ接続する。ほぼ同じ近さ
      （CONNECTION_ALTERNATIVE_M 以内の差）に別の道路がある場合は、その中から経路全体が最短になる
      ものを選ぶ。道路まで MAX_CONNECTION_M より遠い場合は、遠くの道路へ無理に接続せず
      「経路を算出できません」とする
    - 最短経路は避難所ごとに1回だけ計算し（避難所側の道路の両端からの最短距離）、その避難所を候補に
      持つ全員の経路をそこから取り出す
    - 経路の形は道路データの線の形（OSMの道路の折れ点）をそのまま使う。道路の分岐点どうしを直線で
      結んだだけの形にはしない
    - 道路上でつながっていない場合は「経路を算出できません」とし、直線で代用しない
    - 地点が糸満市の行政区域から AREA_ROUTABLE_M より外にある場合は、道路データの取得範囲の端で道路が
      切れていて実際より大回りの経路になり得るため、経路を算出しない
    - 表示する「道路上の距離」は、本人側の道路への接続点から避難所側の道路への接続点までの、道路に
      沿った距離。地点と道路の間（接続部分。道路ではない直線）の長さは含めず、別に表示する
"""
import time

import numpy as np
import shapely
from pyproj import Transformer
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from shapely.geometry import LineString, Point
from shapely.ops import substring, transform
from shapely.strtree import STRtree

# Notebookとこのモジュールの受け渡し方（関数の引数・戻り値）を変えたら上げる。
ROAD_ROUTES_API_VERSION = 1


# =============================================================================
# 定数
# =============================================================================

# 道路データの取得範囲。要支援者の座標には依存させず、公開されている行政区域から決める。
# OSMの「糸満市」の行政区域（boundary=administrative）のリレーションID。名称で検索すると別の
# 地物が返る恐れがあるため、IDで固定する。
AREA_NAME = "糸満市"
AREA_OSM_RELATION_ID = 4559181
# 取得した行政区域が糸満市のものであることの確認に使う地点（糸満市役所付近。経度, 緯度）。
AREA_CHECK_POINT_LONLAT = (127.6658, 26.1236)
# 行政区域の周囲に付ける固定の余裕範囲（メートル）。市境付近の要支援者・避難所の経路が、
# 隣接市町の道路を通る場合にも途切れないようにするため。
AREA_BUFFER_M = 2000
# 経路を算出する地点の範囲（行政区域からの距離。メートル）。道路データは AREA_BUFFER_M まで
# 取得するが、取得範囲の端の近くでは範囲外の道路が切れていて、実際より大回りの経路になり得る。
# そのため、地点がこの範囲（行政区域＋1km）の外にある場合は経路を算出しない（道路データには
# さらに外側1km分の余裕が残る）。
AREA_ROUTABLE_M = 1000
# 取得範囲の輪郭を間引く許容差（メートル）。Overpass APIへ送る範囲の頂点数を減らすため。
# 余裕範囲（2km）に比べて十分小さいので、行政区域そのものが範囲から外れることはない。
AREA_SIMPLIFY_M = 100

# 距離計算に使う平面直角座標系（JGD2011 / 平面直角座標系 第XV系。沖縄本島を含む系）。
METRIC_CRS = "EPSG:6683"

# 地点（要支援者・避難所）から道路までの接続距離の上限（メートル）。これより遠い道路へは
# 接続しない（道路から離れた地点を、遠くの道路へ無理につないだ経路を作らないため）。
MAX_CONNECTION_M = 150
# 最も近い道路に加えて接続の候補にする道路の範囲（最も近い道路との距離の差。メートル）と、候補の上限数。
# 歩道・上下線のように、ほぼ同じ近さに別々の線として描かれた道路のうち、経路全体が最短になるものを
# 選ぶため。差をこれ以上大きくすると、道路でない部分（直線）を長くとって近道する経路になり得る。
CONNECTION_ALTERNATIVE_M = 20
MAX_CONNECTION_CANDIDATES = 8

# 経路の線を間引く許容差（メートル）。レビューZIPの容量を抑えるためで、道路の形からのずれは
# この値以内に収まる。
PATH_SIMPLIFY_M = 0.5
# 経路座標の小数桁数（緯度経度。6桁で約0.1m）。
COORD_DECIMALS = 6

# 道路データの出典。背景地図（国土地理院）とは別のデータのため、表示でも混同しないこと。
OSM_ATTRIBUTION = "© OpenStreetMap contributors"
OSM_LICENSE = "Open Database License (ODbL) 1.0"
OSM_COPYRIGHT_URL = "https://www.openstreetmap.org/copyright"

# 経路を算出できなかった理由（レビューHTMLの表示文言はテンプレート側で持つ）。
REASON_RESIDENT_TOO_FAR = "resident_too_far"
REASON_SHELTER_TOO_FAR = "shelter_too_far"
REASON_NOT_CONNECTED = "not_connected"
REASON_OUTSIDE_AREA = "outside_area"
FAILURE_REASONS = (REASON_RESIDENT_TOO_FAR, REASON_SHELTER_TOO_FAR, REASON_NOT_CONNECTED,
                   REASON_OUTSIDE_AREA)


# =============================================================================
# 道路データの取得（OSMnx。外部通信はここだけ）
# =============================================================================

def fetch_area_boundary(ox):
    """糸満市の行政区域（OSM）を取得し、(ポリゴン, 表示名) を返す。

    送るのは固定のリレーションIDだけで、要支援者の情報は含まない。取得結果が想定と違う場合
    （別の地物が返った等）は、誤った範囲の道路で経路を作らないよう例外を送出する。"""
    boundary = ox.geocode_to_gdf(f"R{AREA_OSM_RELATION_ID}", by_osmid=True)
    if len(boundary) != 1:
        raise RuntimeError(f"{AREA_NAME}の行政区域を1件に特定できませんでした（{len(boundary)}件）。")
    geometry = boundary.geometry.iloc[0]
    if geometry is None or geometry.geom_type not in ("Polygon", "MultiPolygon"):
        raise RuntimeError(f"{AREA_NAME}の行政区域が面として取得できませんでした。")
    if not geometry.contains(Point(*AREA_CHECK_POINT_LONLAT)):
        raise RuntimeError(f"取得した行政区域に{AREA_NAME}役所付近が含まれていません。取得結果を確認してください。")
    display_name = boundary["display_name"].iloc[0] if "display_name" in boundary.columns else AREA_NAME
    return geometry, str(display_name)


def area_with_margin(boundary, margin_m=AREA_BUFFER_M):
    """行政区域に固定の余裕範囲（既定は AREA_BUFFER_M）を付けた範囲（経緯度のポリゴン）を返す。"""
    to_metric = Transformer.from_crs("EPSG:4326", METRIC_CRS, always_xy=True)
    to_lonlat = Transformer.from_crs(METRIC_CRS, "EPSG:4326", always_xy=True)
    metric = transform(to_metric.transform, boundary)
    area = metric.buffer(margin_m).simplify(AREA_SIMPLIFY_M)
    return transform(to_lonlat.transform, area)


def load_road_network(ox, log=print):
    """糸満市の行政区域＋固定の余裕範囲の徒歩用道路ネットワークをOSMから取得する。

    戻り値はレビュー用の経路計算に使う RoadNetwork。取得できない場合は例外を送出する
    （呼び出し側で「道路経路を算出できなかった」扱いにし、結果CSVには影響させない）。"""
    started = time.perf_counter()
    log(f"{AREA_NAME}の行政区域（OpenStreetMap）を取得しています…")
    boundary, display_name = fetch_area_boundary(ox)
    area = area_with_margin(boundary)
    log(f"徒歩用の道路データ（OpenStreetMap）を取得しています（{AREA_NAME}と周囲約{AREA_BUFFER_M // 1000}km）…")
    graph = ox.graph_from_polygon(
        area, network_type="walk", simplify=True, retain_all=False, truncate_by_edge=True
    )
    source = {
        "area": f"{AREA_NAME}の行政区域（OpenStreetMap relation {AREA_OSM_RELATION_ID}）と周囲約"
                f"{AREA_BUFFER_M // 1000}km",
        "area_display_name": display_name,
        "network_type": "walk",
        "retrieved_at": time.strftime("%Y-%m-%d %H:%M"),
        "fetch_seconds": round(time.perf_counter() - started, 1),
    }
    return RoadNetwork(graph, source, routable_area=area_with_margin(boundary, AREA_ROUTABLE_M))


# =============================================================================
# 道路ネットワーク（接続・最短経路）
# =============================================================================

class Connection:
    """地点を道路へ接続した結果（どの道路の、端から何mの位置に、何m離れて接続したか）。"""

    __slots__ = ("edge", "position", "distance")

    def __init__(self, edge, position, distance):
        self.edge = edge
        self.position = position
        self.distance = distance


class RoadNetwork:
    """OSMnxのグラフ（経緯度。ノードに x/y、エッジに geometry（任意））から、経路計算用の
    データを1回だけ作って保持する。

    - 道路の線は平面直角座標（メートル）へ変換し、同じ道路の往復（向きだけ違う重複）は1本にまとめる
    - 線の向きは、番号の小さいノードから大きいノードへそろえる
    - 最短経路の計算には、ノード間の最も短い道路だけを使う"""

    def __init__(self, graph, source=None, routable_area=None):
        self.source = dict(source or {})
        self._to_metric = Transformer.from_crs("EPSG:4326", METRIC_CRS, always_xy=True)
        self._to_lonlat = Transformer.from_crs(METRIC_CRS, "EPSG:4326", always_xy=True)
        # 経路を算出する地点の範囲（経緯度）。Noneなら範囲で絞らない。
        self.routable_area = routable_area
        if routable_area is not None:
            shapely.prepare(routable_area)

        node_ids = list(graph.nodes)
        if not node_ids or graph.number_of_edges() == 0:
            raise RuntimeError("道路データに道路が含まれていません。")
        index = {node_id: position for position, node_id in enumerate(node_ids)}
        lons = np.array([float(graph.nodes[n]["x"]) for n in node_ids])
        lats = np.array([float(graph.nodes[n]["y"]) for n in node_ids])
        node_x, node_y = self._to_metric.transform(lons, lats)
        self.node_xy = np.column_stack([node_x, node_y])

        lines = {}
        for u, v, data in graph.edges(data=True):
            a, b = index[u], index[v]
            geometry = data.get("geometry")
            if geometry is None:
                lonlat = [(lons[a], lats[a]), (lons[b], lats[b])]
            else:
                lonlat = list(geometry.coords)
            xs, ys = self._to_metric.transform([c[0] for c in lonlat], [c[1] for c in lonlat])
            coords = list(zip(xs, ys))
            if a > b:
                a, b = b, a
            # 線の始点がノードaの側になるようにそろえる（OSMnxのgeometryは辺の向きに沿うが、
            # 念のため端点の位置で確認する）
            if a != b and (_distance(coords[0], self.node_xy[a]) > _distance(coords[0], self.node_xy[b])):
                coords.reverse()
            line = LineString(coords)
            if line.length <= 0:
                continue
            # 同じ2ノード間の同じ長さの線は、往復の重複として1本にまとめる
            key = (a, b, round(line.length, 1))
            lines.setdefault(key, (a, b, line))

        ordered = sorted(lines.values(), key=lambda item: (item[0], item[1], item[2].length))
        self.edge_a = np.array([item[0] for item in ordered])
        self.edge_b = np.array([item[1] for item in ordered])
        self.edge_lines = [item[2] for item in ordered]
        self.edge_length = np.array([line.length for line in self.edge_lines])
        self._tree = STRtree(self.edge_lines)

        # 最短経路用: 2ノード間で最も短い道路の番号
        self._pair_edge = {}
        for edge in range(len(self.edge_lines)):
            a, b = int(self.edge_a[edge]), int(self.edge_b[edge])
            if a == b:
                continue  # 行き止まりの輪のような線は、最短経路の途中には使わない（接続先としては使う）
            best = self._pair_edge.get((a, b))
            if best is None or self.edge_length[edge] < self.edge_length[best]:
                self._pair_edge[(a, b)] = edge
        pairs = list(self._pair_edge.items())
        rows = [a for (a, b), _ in pairs] + [b for (a, b), _ in pairs]
        cols = [b for (a, b), _ in pairs] + [a for (a, b), _ in pairs]
        weights = [self.edge_length[edge] for _, edge in pairs] * 2
        node_count = len(node_ids)
        self._matrix = csr_matrix((weights, (rows, cols)), shape=(node_count, node_count))
        self.node_count = node_count
        self.edge_count = len(self.edge_lines)

    # ---- 接続 ----
    def is_routable(self, lat, lon):
        """地点が経路を算出する範囲（行政区域＋AREA_ROUTABLE_M）の中にあるか。"""
        return self.routable_area is None or self.routable_area.covers(Point(lon, lat))

    def connect(self, lat, lon, max_distance=MAX_CONNECTION_M):
        """地点を道路へ接続する候補を、近い順に返す。max_distance以内に道路が無ければ空の一覧
        （遠くの道路へ無理に接続しない）。

        候補は、最も近い道路と、それより CONNECTION_ALTERNATIVE_M 以内しか遠くない道路。OSMでは
        歩道や上下線が別々の線として描かれ、最も近い線だけがほかの道路と遠くでしかつながって
        いないことがあるため、ほぼ同じ近さの道路の中から、経路全体が最短になるものを選べるようにする。"""
        x, y = self._to_metric.transform(lon, lat)
        point = Point(x, y)
        hits, distances = self._tree.query_nearest(
            point, max_distance=max_distance, return_distance=True, all_matches=True
        )
        if len(hits) == 0:
            return []
        limit = min(float(distances.min()) + CONNECTION_ALTERNATIVE_M, max_distance)
        connections = []
        for edge in self._tree.query(point, predicate="dwithin", distance=limit):
            line = self.edge_lines[edge]
            distance = float(point.distance(line))
            if distance <= limit:
                connections.append(Connection(int(edge), float(line.project(point)), distance))
        # 同じ距離の道路が複数ある場合も、結果が実行ごとに変わらないように並べる
        connections.sort(key=lambda connection: (connection.distance, connection.edge))
        return connections[:MAX_CONNECTION_CANDIDATES]

    # ---- 最短経路 ----
    def shortest_from(self, connections):
        """避難所側の接続候補から全ノードへの最短距離を求める（避難所ごとに1回だけ呼ぶ）。
        接続候補の道路の両端それぞれを起点に計算し、起点までの距離（接続部分＋道路の端まで）を
        足したうえで、ノードごとに最も近い起点を選んでおく。"""
        roots, offsets, ends = [], [], []
        for number, connection in enumerate(connections):
            length = self.edge_length[connection.edge]
            for node, along, end in (
                (self.edge_a[connection.edge], connection.position, 0.0),
                (self.edge_b[connection.edge], length - connection.position, length),
            ):
                roots.append(int(node))
                offsets.append(connection.distance + along)
                ends.append((number, end))
        distances, predecessors = dijkstra(
            self._matrix, directed=True, indices=roots, return_predecessors=True
        )
        return ShortestPaths(self, connections, roots, ends, distances + np.array(offsets)[:, None],
                             predecessors)

    def lonlat_path(self, coords):
        """平面直角座標の点列を、間引いたうえで [[緯度, 経度], ...] にする。
        本人側と避難所側が道路上の同じ点に接続した場合（道路上の距離0m）は、その点2つにする。"""
        if len(coords) >= 2 and LineString(coords).length > 0 and PATH_SIMPLIFY_M > 0:
            coords = list(LineString(coords).simplify(PATH_SIMPLIFY_M, preserve_topology=False).coords)
        if len(coords) == 1:
            coords = coords * 2
        xs, ys = self._to_lonlat.transform([c[0] for c in coords], [c[1] for c in coords])
        return [[round(float(lat), COORD_DECIMALS), round(float(lon), COORD_DECIMALS)]
                for lon, lat in zip(xs, ys)]


class ShortestPaths:
    """1つの避難所（道路への接続候補）を起点にした最短経路の計算結果。"""

    def __init__(self, network, connections, roots, ends, totals, predecessors):
        self.network = network
        self.connections = connections
        self.roots = roots
        self.ends = ends  # 起点ごとの (避難所側の接続候補の番号, その道路上の端の位置)
        self.predecessors = predecessors
        # ノードごとの、避難所の地点までの最短距離（接続部分を含む）と、そのとき使う起点
        self.best_row = np.argmin(totals, axis=0)
        self.best_cost = totals[self.best_row, np.arange(totals.shape[1])]
        self._node_paths = {}  # (起点, ノード) → ノード列。同じノードからの経路を何度も辿らない

    def _node_path(self, row, node):
        """ノードから起点（避難所側の道路の端）までのノード列（本人側→避難所側の順）。"""
        key = (row, node)
        if key not in self._node_paths:
            predecessors = self.predecessors[row]
            root = self.roots[row]
            path = [node]
            while path[-1] != root:
                path.append(int(predecessors[path[-1]]))
            self._node_paths[key] = path
        return self._node_paths[key]

    def route_to(self, resident_connections):
        """本人側の接続候補から避難所側の接続候補までの経路のうち、接続部分を含めた合計が最短のものを
        求める。戻り値は (道路上の距離m, 平面直角座標の点列, 本人側の接続, 避難所側の接続)。
        道路がつながっていなければNone。"""
        network = self.network
        best = None  # (合計の距離, 種類, 補助情報)
        for resident in resident_connections:
            length = network.edge_length[resident.edge]
            for node, along, end in (
                (int(network.edge_a[resident.edge]), resident.position, 0.0),
                (int(network.edge_b[resident.edge]), length - resident.position, length),
            ):
                total = resident.distance + along + self.best_cost[node]
                if np.isfinite(total) and (best is None or total < best[0]):
                    best = (total, "via_node", (resident, node, end))
            for shelter in self.connections:
                if shelter.edge == resident.edge:
                    total = resident.distance + abs(resident.position - shelter.position) + shelter.distance
                    if best is None or total <= best[0]:
                        best = (total, "same_edge", (resident, shelter))
        if best is None:
            return None

        total, kind, detail = best
        if kind == "same_edge":
            resident, shelter = detail
            line = network.edge_lines[resident.edge]
            coords = _line_coords(substring(line, resident.position, shelter.position))
        else:
            resident, node, end = detail
            row = int(self.best_row[node])
            number, shelter_end = self.ends[row]
            shelter = self.connections[number]
            coords = _line_coords(substring(network.edge_lines[resident.edge], resident.position, end))
            node_path = self._node_path(row, node)
            for start_node, end_node in zip(node_path, node_path[1:]):
                coords = _join(coords, self._edge_coords(start_node, end_node))
            shelter_line = network.edge_lines[shelter.edge]
            coords = _join(coords, _line_coords(substring(shelter_line, shelter_end, shelter.position)))
        road_distance = float(total) - resident.distance - shelter.distance
        return road_distance, coords, resident, shelter

    def _edge_coords(self, start_node, end_node):
        network = self.network
        a, b = (start_node, end_node) if start_node < end_node else (end_node, start_node)
        coords = list(network.edge_lines[network._pair_edge[(a, b)]].coords)
        return coords if start_node == a else coords[::-1]


def _distance(p, q):
    return float(np.hypot(p[0] - q[0], p[1] - q[1]))


def _line_coords(geometry):
    """substringの結果（線、または長さ0のときは点）を点列にする。"""
    return list(geometry.coords)


def _join(coords, more):
    """点列をつなぐ（つなぎ目の同じ点は1つにする）。"""
    if coords and more and _distance(coords[-1], more[0]) < 1e-6:
        return coords + more[1:]
    return coords + more


# =============================================================================
# 全要支援者・候補の経路計算
# =============================================================================

def compute_road_routes(network, review_rows, max_connection_m=MAX_CONNECTION_M):
    """全要支援者・全候補について、道路に沿った参考経路を求める。

    review_rows は候補算出ループで作った要支援者ごとの表示用データ（CSVと同じ候補順位）。
    戻り値の routes は review_rows と同じ並びで、要支援者ごとに候補と同じ順の経路情報を持つ。
    座標が無い要支援者は候補自体が無いため、経路も作らない（空の一覧）。"""
    started = time.perf_counter()

    # 避難所ごとに、その避難所を候補に持つ（要支援者, 候補の位置）をまとめる
    tasks_by_shelter = {}
    routes = []
    for resident_index, row in enumerate(review_rows):
        routes.append([None] * len(row["candidates"]))
        for candidate_index, candidate in enumerate(row["candidates"]):
            key = (float(candidate["latitude"]), float(candidate["longitude"]))
            tasks_by_shelter.setdefault(key, []).append((resident_index, candidate_index))

    resident_connections = {}

    def resident_connection(resident_index):
        if resident_index not in resident_connections:
            row = review_rows[resident_index]
            resident_connections[resident_index] = network.connect(
                float(row["latitude"]), float(row["longitude"]), max_connection_m
            )
        return resident_connections[resident_index]

    shelter_connected = 0
    for (shelter_lat, shelter_lon), tasks in sorted(tasks_by_shelter.items()):
        shelter_routable = network.is_routable(shelter_lat, shelter_lon)
        shelter = network.connect(shelter_lat, shelter_lon, max_connection_m) if shelter_routable else []
        paths = None
        if shelter:
            shelter_connected += 1
            paths = network.shortest_from(shelter)
        for resident_index, candidate_index in tasks:
            rank = review_rows[resident_index]["candidates"][candidate_index]["rank"]
            row = review_rows[resident_index]
            if not shelter_routable or not network.is_routable(float(row["latitude"]),
                                                               float(row["longitude"])):
                routes[resident_index][candidate_index] = _unavailable(rank, REASON_OUTSIDE_AREA)
                continue
            resident = resident_connection(resident_index)
            if not resident:
                routes[resident_index][candidate_index] = _unavailable(rank, REASON_RESIDENT_TOO_FAR)
                continue
            if not shelter:
                routes[resident_index][candidate_index] = _unavailable(rank, REASON_SHELTER_TOO_FAR)
                continue
            found = paths.route_to(resident)
            if found is None:
                routes[resident_index][candidate_index] = _unavailable(rank, REASON_NOT_CONNECTED)
                continue
            road_distance, coords, resident_used, shelter_used = found
            routes[resident_index][candidate_index] = {
                "rank": rank,
                "status": "ok",
                "road_distance_m": round(road_distance, 1),
                "resident_connection_m": round(resident_used.distance, 1),
                "shelter_connection_m": round(shelter_used.distance, 1),
                "path": network.lonlat_path(coords),
            }

    counts = {"ok": 0}
    counts.update({reason: 0 for reason in FAILURE_REASONS})
    for resident_routes in routes:
        for route in resident_routes:
            counts["ok" if route["status"] == "ok" else route["reason"]] += 1

    return {
        "status": "ok",
        "message": None,
        "max_connection_m": max_connection_m,
        "area_name": AREA_NAME,
        "routable_margin_m": AREA_ROUTABLE_M,
        "source": _source_info(network.source),
        "routes": routes,
        "stats": {
            "routes": sum(len(r) for r in routes),
            **counts,
            "shelters": len(tasks_by_shelter),
            "shelters_connected": shelter_connected,
            "road_nodes": network.node_count,
            "road_edges": network.edge_count,
        },
        "seconds": round(time.perf_counter() - started, 2),
    }


def _unavailable(rank, reason):
    return {"rank": rank, "status": "unavailable", "reason": reason}


def _source_info(source):
    info = {
        "name": "OpenStreetMap",
        "attribution": OSM_ATTRIBUTION,
        "license": OSM_LICENSE,
        "license_short": "ODbL 1.0",
        "url": OSM_COPYRIGHT_URL,
    }
    info.update(source)
    return info


def print_summary(result, log=print):
    """経路の算出結果（成功・失敗の件数と処理時間）を表示する。"""
    stats = result["stats"]
    log(f"道路に沿った参考経路: 候補 延べ{stats['routes']}件中 算出できた {stats['ok']}件 / "
        f"算出できなかった {stats['routes'] - stats['ok']}件")
    log(f"  本人の地点から{result['max_connection_m']}m以内に道路が無い: {stats[REASON_RESIDENT_TOO_FAR]}件")
    log(f"  避難所の地点から{result['max_connection_m']}m以内に道路が無い: {stats[REASON_SHELTER_TOO_FAR]}件")
    log(f"  道路データ上でつながっていない: {stats[REASON_NOT_CONNECTED]}件")
    log(f"  地点が{AREA_NAME}から{AREA_ROUTABLE_M}mより外（道路データの取得範囲の端に近い）: "
        f"{stats[REASON_OUTSIDE_AREA]}件")
    log(f"  対象の避難所 {stats['shelters']}か所（道路へ接続できた {stats['shelters_connected']}か所）"
        f" / 道路データ ノード{stats['road_nodes']}・道路{stats['road_edges']}本")
    log(f"  経路計算の処理時間: {result['seconds']:.2f}秒")
