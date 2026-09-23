"""検証用: sheltermatch.ipynb のセルを**そのまま**順に実行し、結果CSVとレビューZIPを作る。

Google Colab を使えない環境で、Notebook本体（src/ 配下のモジュールを含む）を書き換えずに通しで
動かすためのもの。Colab固有の部分と、この環境から接続できない外部サービスだけを差し替える。

差し替えるもの（Notebook・src/ のコードは変更しない）:
    google.colab.files       アップロード＝指定したファイルを渡す / ダウンロード＝記録のみ
    %pip 行                  実行しない（必要なライブラリはあらかじめ導入しておく）
    GitHub raw（src/）       --code で指定した版（作業ツリー、または git の参照）のファイルを返す
    BODIK Data API           架空の避難所データ（CKAN datastore_search 形式）を返す
    unpkg（Leaflet 1.9.4）   npm レジストリの公式パッケージ（leaflet-1.9.4.tgz）の中身を返す
                             （SHA-256 の照合は review_builder がそのまま行う）
    地理院タイル             ※検証用の代替。道路データの線を描いたタイルを返す（国土地理院の地図ではない）
    OSMnx の取得関数         --road-source overture のとき、Nominatim / Overpass の代わりに、Overture Maps
                             から作った OSM XML（overture_to_osm_xml.py）を返す。渡された取得範囲・
                             パラメータは記録し、取得範囲で切り出してから返す
                             --road-source network のときは差し替えない（実際の通信を試みる）

外部へのリクエストはすべて記録し、要支援者の座標が含まれていないことを確認できるようにする。

使い方の例:
    python3 experiments/road_routes/run_notebook_check.py --out /tmp/run_a \\
        --residents experiments/performance/residents_1000.csv --shelters /tmp/shelters.json \\
        --road-data /tmp/road_data --road-routes on
"""
import argparse
import builtins
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import time
import types
import zipfile
from pathlib import Path
from unittest import mock

import requests

REPO = Path(__file__).resolve().parents[2]
SRC_FILES = ["hazard/hazard_loader.py", "review/review_builder.py", "review/review_template.html",
             "review/road_routes.py"]


class FakeResponse:
    def __init__(self, content, status_code=200):
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return json.loads(self.content.decode("utf-8"))


class FakeFiles:
    """google.colab.files の代わり。upload() は順番に指定のファイルを返す。"""

    def __init__(self, uploads):
        self.uploads = list(uploads)
        self.downloads = []

    def upload(self):
        if not self.uploads:
            raise RuntimeError("検証ハーネス: アップロードするファイルが指定されていません")
        return self.uploads.pop(0)

    def download(self, name):
        self.downloads.append(str(name))


def code_files(code_ref):
    """--code で指定した版の Notebook と src/ ファイルを返す。"""
    if code_ref == "worktree":
        notebook = (REPO / "sheltermatch.ipynb").read_text(encoding="utf-8")
        files = {p: (REPO / "src" / p).read_bytes() for p in SRC_FILES if (REPO / "src" / p).exists()}
        return notebook, files

    def show(path):
        result = subprocess.run(["git", "show", f"{code_ref}:{path}"], cwd=REPO, capture_output=True)
        return result.stdout if result.returncode == 0 else None

    notebook = show("sheltermatch.ipynb").decode("utf-8")
    files = {p: data for p in SRC_FILES if (data := show(f"src/{p}")) is not None}
    return notebook, files


def leaflet_files(cache_dir):
    """npm レジストリの leaflet-1.9.4.tgz から、unpkg と同じパスでファイルを取り出す。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    tgz = cache_dir / "leaflet-1.9.4.tgz"
    if not tgz.exists():
        response = requests.get("https://registry.npmjs.org/leaflet/-/leaflet-1.9.4.tgz", timeout=60)
        response.raise_for_status()
        tgz.write_bytes(response.content)
    files = {}
    with tarfile.open(tgz) as archive:
        for member in archive.getmembers():
            if member.isfile() and member.name.startswith("package/"):
                files[member.name[len("package/"):]] = archive.extractfile(member).read()
    return files


class TileRenderer:
    """検証用の背景タイル（道路データの線を描く）。国土地理院の地図ではない。"""

    def __init__(self, osm_xml):
        import osmnx as ox
        from shapely.strtree import STRtree
        graph = ox.graph_from_xml(osm_xml, bidirectional=False, simplify=True, retain_all=True)
        edges = ox.convert.graph_to_gdfs(graph, nodes=False)
        self.lines = list(edges.geometry)
        self.tree = STRtree(self.lines)

    def tile(self, z, x, y):
        import math
        from PIL import Image, ImageDraw
        from shapely.geometry import box

        def lonlat(px, py):
            n = 2.0 ** z
            lon = px / n * 360.0 - 180.0
            lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * py / n))))
            return lon, lat

        west, north = lonlat(x, y)
        east, south = lonlat(x + 1, y + 1)
        image = Image.new("RGB", (256, 256), (238, 240, 236))
        draw = ImageDraw.Draw(image)

        def pixel(lon, lat):
            n = 2.0 ** z
            px = (lon + 180.0) / 360.0 * n
            lat_rad = math.radians(lat)
            py = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n
            return ((px - x) * 256, (py - y) * 256)

        for index in self.tree.query(box(west, south, east, north)):
            coords = [pixel(lon, lat) for lon, lat in self.lines[index].coords]
            draw.line(coords, fill=(160, 166, 175), width=2)
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        return buffer.getvalue()


class Network:
    """requests.get の差し替え。すべてのリクエストを記録する。"""

    def __init__(self, src_files, shelters_json, leaflet, tiles, pass_through_osm=False):
        self.src_files = src_files
        self.pass_through_osm = pass_through_osm
        self.real_get = requests.get
        self.shelters_json = shelters_json
        self.leaflet = leaflet
        self.tiles = tiles
        self.log = []

    def get(self, url, params=None, timeout=None, **kwargs):
        self.log.append({"url": url, "params": params})
        match = re.match(r"https://raw\.githubusercontent\.com/YanTKYS/sheltermatch/main/src/(.+)$", url)
        if match:
            data = self.src_files.get(match.group(1))
            return FakeResponse(data or b"", 200 if data is not None else 404)
        if url.startswith("https://data.bodik.jp/api/action/datastore_search"):
            records = self.shelters_json
            offset = int((params or {}).get("offset", 0))
            limit = int((params or {}).get("limit", 1000))
            body = {"success": True, "result": {"records": records[offset:offset + limit],
                                                "total": len(records)}}
            return FakeResponse(json.dumps(body, ensure_ascii=False).encode("utf-8"))
        match = re.match(r"https://unpkg\.com/leaflet@1\.9\.4/(.+)$", url)
        if match:
            return FakeResponse(self.leaflet[match.group(1)])
        match = re.match(r"https://cyberjapandata\.gsi\.go\.jp/xyz/pale/(\d+)/(\d+)/(\d+)\.png$", url)
        if match:
            return FakeResponse(self.tiles.tile(*map(int, match.groups())))
        if self.pass_through_osm and re.match(
                r"https://(nominatim\.openstreetmap\.org|overpass-api\.de)/", url):
            self.log[-1]["passed_through"] = True
            return self.real_get(url, params=params, timeout=timeout, **kwargs)
        raise requests.ConnectionError(f"検証ハーネス: 想定外の外部通信を遮断しました: {url}")


def patch_osmnx(road_data, inject_island, calls):
    """OSMnx の取得関数を、Overture 由来の OSM XML を返すものに差し替える。"""
    import geopandas as gpd
    import osmnx as ox

    boundary_path = Path(road_data) / "itoman_boundary.geojson"
    xml_path = Path(road_data) / "itoman_walk.osm"

    def geocode_to_gdf(query, *, which_result=None, by_osmid=False):
        calls.append({"function": "geocode_to_gdf", "query": query, "by_osmid": by_osmid})
        gdf = gpd.read_file(boundary_path)
        gdf["display_name"] = "糸満市, 沖縄県, 日本（検証用: Overture divisions / OSM relation 4559181）"
        return gdf

    def graph_from_polygon(polygon, *, network_type="all", simplify=True, retain_all=False,
                           truncate_by_edge=False, custom_filter=None):
        calls.append({"function": "graph_from_polygon", "network_type": network_type,
                      "simplify": simplify, "retain_all": retain_all,
                      "truncate_by_edge": truncate_by_edge,
                      "polygon_sha256": hashlib.sha256(polygon.wkb).hexdigest(),
                      "polygon_bounds": list(polygon.bounds)})
        graph = ox.graph_from_xml(xml_path, bidirectional=network_type == "walk", simplify=simplify,
                                  retain_all=True)
        graph = ox.truncate.truncate_graph_polygon(graph, polygon, truncate_by_edge=truncate_by_edge)
        if not retain_all:
            graph = ox.truncate.largest_component(graph)
        if inject_island:
            add_synthetic_island(graph)
        return graph

    return mock.patch.multiple(ox, geocode_to_gdf=geocode_to_gdf, graph_from_polygon=graph_from_polygon)


# 検証用に追加する、ほかの道路とつながっていない架空の道路（海上。実在しない）。
# 「道路データ上でつながっていない」場合の表示を確認するためだけに使う。位置は make_test_inputs.py と共通。
ISLAND = [(127.6440, 26.1000), (127.6446, 26.1003), (127.6452, 26.1000)]


def add_synthetic_island(graph):
    ids = [9_000_000_001, 9_000_000_002, 9_000_000_003]
    for node_id, (lon, lat) in zip(ids, ISLAND):
        graph.add_node(node_id, x=lon, y=lat, street_count=1)
    for u, v in zip(ids, ids[1:]):
        for a, b in ((u, v), (v, u)):
            graph.add_edge(a, b, key=0, length=65.0, highway="footway", oneway=False, reversed=False,
                           osmid=0)


def run(args):
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    notebook_text, src_files = code_files(args.code)
    notebook = json.loads(notebook_text)

    uploads = [{Path(args.residents).name: Path(args.residents).read_bytes()}]
    if args.hazard:
        uploads.append({Path(args.hazard).name: Path(args.hazard).read_bytes()})
    fake_files = FakeFiles(uploads)
    colab = types.ModuleType("google.colab")
    colab.files = fake_files
    google = types.ModuleType("google")
    google.colab = colab

    shelters = json.loads(Path(args.shelters).read_text(encoding="utf-8"))
    tiles = TileRenderer(Path(args.road_data) / "itoman_walk.osm")
    network = Network(src_files, shelters, leaflet_files(Path(args.cache)), tiles,
                      pass_through_osm=args.road_source == "network")
    osmnx_calls = []

    namespace = {"display": lambda *a, **k: None, "__name__": "__main__"}
    stdout = io.StringIO()
    cell_times = []
    patches = [
        mock.patch.dict(sys.modules, {"google": google, "google.colab": colab}),
        mock.patch("requests.get", network.get),
        mock.patch.object(builtins, "input", lambda prompt="": args.hazard_type),
    ]
    if args.road_source == "overture":
        patches.append(patch_osmnx(args.road_data, args.inject_island, osmnx_calls))

    previous_cwd = os.getcwd()
    os.chdir(out)
    error = None
    try:
        for patch in patches:
            patch.start()
        code_cells = [c for c in notebook["cells"] if c["cell_type"] == "code"]
        for number, cell in enumerate(code_cells):
            source = cell["source"] if isinstance(cell["source"], str) else "".join(cell["source"])
            source = "\n".join(line for line in source.split("\n") if not line.lstrip().startswith("%pip"))
            if number == 0:  # 利用者設定セル
                source = re.sub(r"^ENABLE_HAZARD_CHECK = .*$", f"ENABLE_HAZARD_CHECK = {bool(args.hazard)}",
                                source, flags=re.M)
                source = re.sub(r"^ENABLE_ROAD_ROUTES = .*$",
                                f"ENABLE_ROAD_ROUTES = {args.road_routes == 'on'}", source, flags=re.M)
            started = time.perf_counter()
            real_stdout = sys.stdout
            sys.stdout = Tee(real_stdout, stdout)
            try:
                exec(compile(source, f"<cell {number}>", "exec"), namespace)
            finally:
                sys.stdout = real_stdout
            cell_times.append(round(time.perf_counter() - started, 2))
    except Exception as exc:  # noqa: BLE001 （検証結果として記録する）
        error = f"{type(exc).__name__}: {exc}"
        print("セルの実行が止まりました:", error)
    finally:
        for patch in reversed(patches):
            patch.stop()
        os.chdir(previous_cwd)

    result = namespace.get("road_routes_result")
    report = {
        "code": args.code,
        "error": error,
        "cell_seconds": cell_times,
        "timings": {k: round(v, 2) for k, v in (namespace.get("TIMINGS") or {}).items()},
        "downloads": fake_files.downloads,
        "requests": network.log,
        "osmnx_calls": osmnx_calls,
        "road_routes_status": result.get("status") if result else None,
        "road_routes_stats": result.get("stats") if result else None,
        "road_routes_seconds": result.get("seconds") if result else None,
        "csv_sha256": file_hash(out / "assigned_shelters.csv"),
        "zip_bytes": (out / "sheltermatch_review.zip").stat().st_size
        if (out / "sheltermatch_review.zip").exists() else None,
    }
    if (out / "sheltermatch_review.zip").exists():
        with zipfile.ZipFile(out / "sheltermatch_review.zip") as archive:
            report["zip_entries"] = {i.filename: i.file_size for i in archive.infolist()}
    (out / "stdout.txt").write_text(stdout.getvalue(), encoding="utf-8")
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("requests", "zip_entries")},
                     ensure_ascii=False, indent=1))


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


class Tee(io.TextIOBase):
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for stream in self.streams:
            stream.write(text)
        return len(text)

    def flush(self):
        for stream in self.streams:
            stream.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="作業・出力ディレクトリ（リポジトリの外を推奨）")
    parser.add_argument("--code", default="worktree", help="worktree または git の参照（例: origin/main）")
    parser.add_argument("--residents", required=True, help="要支援者CSV（架空データ）")
    parser.add_argument("--shelters", required=True, help="架空の避難所データ（BODIKのrecords形式のJSON）")
    parser.add_argument("--hazard", help="ハザードデータ（任意。指定するとENABLE_HAZARD_CHECK=True）")
    parser.add_argument("--hazard-type", default="津波", help="ハザード種別の入力に答える値")
    parser.add_argument("--road-data", required=True, help="overture_to_osm_xml.py の出力ディレクトリ")
    parser.add_argument("--road-routes", choices=["on", "off"], default="off")
    parser.add_argument("--road-source", choices=["overture", "network"], default="overture")
    parser.add_argument("--inject-island", action="store_true",
                        help="つながっていない架空の道路を追加する（経路なしの表示確認用）")
    parser.add_argument("--cache", default=str(Path.home() / ".cache" / "sheltermatch-check"))
    run(parser.parse_args())


if __name__ == "__main__":
    main()
