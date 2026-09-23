"""検証用: Overture Maps の道路データ（OpenStreetMap由来, ODbL）から、糸満市周辺の徒歩用道路を
OSM XML へ変換する。

sheltermatch.ipynb 本体は OSMnx で OpenStreetMap（Overpass API）から直接取得する。このスクリプトは、
Overpass API・Nominatim へ接続できない検証環境（2026-09-23 のサンドボックス。外向き通信ポリシーで
遮断）で、**実際のOSMの道路形状**を使って経路計算・レビューHTMLを確認するための代替手段。

- Overture Maps Foundation の transportation テーマ（segment）は OSM の way を分割・再構成した
  データで、線の形・道路の接続（connector = OSMのノード）は OSM のものを保っている
- 取得範囲は本体と同じ考え方（糸満市の行政区域 = OSM relation 4559181 に約2kmの余裕範囲）。
  要支援者の座標は使わない
- 出力した OSM XML を `osmnx.graph_from_xml()` で読み込むと、本体（`graph_from_polygon`）と同じ
  形式のグラフになる（ノードに x/y、簡略化した辺に geometry）
- 徒歩で通れない道路の除外は、OSMnx の network_type="walk" の条件に合わせる
  （motorway・cycleway・foot=no 相当・access=private 相当を除外）

使い方（S3 の公開バケットへ匿名で接続できる環境で実行する）:
    python3 experiments/road_routes/overture_to_osm_xml.py <出力先ディレクトリ>

出力（リポジトリへはコミットしない。道路データ本体は必要なときに取得し直す）:
    <出力先>/itoman_walk.osm           徒歩用道路のOSM XML
    <出力先>/itoman_boundary.geojson   糸満市の行政区域（Overture divisions。OSM relation 4559181 由来）
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from xml.sax.saxutils import quoteattr

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.fs as pafs
import pyarrow.parquet as pq
import shapely
from pyproj import Geod

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "review"))
import road_routes  # noqa: E402

RELEASE = "2026-08-19.0"
BUCKET = "overturemaps-us-west-2"
# 行グループを絞り込むための粗い矩形（糸満市＋余裕範囲を含む）。最終的な範囲は行政区域から決める。
SCAN_BOUNDS = (127.55, 25.98, 127.85, 26.25)

EXCLUDED_CLASSES = {"motorway", "cycleway"}
GEOD = Geod(ellps="WGS84")


def s3_filesystem():
    return pafs.S3FileSystem(anonymous=True, region="us-west-2",
                             proxy_options=os.environ.get("HTTPS_PROXY") or None)


def read_bbox(s3, theme, kind, columns=None):
    """Overtureの指定テーマのうち、SCAN_BOUNDSと重なる行グループだけを読み出す。"""
    xmin, ymin, xmax, ymax = SCAN_BOUNDS
    base = f"{BUCKET}/release/{RELEASE}/theme={theme}/type={kind}/"
    paths = [i.path for i in s3.get_file_info(pafs.FileSelector(base)) if i.path.endswith(".parquet")]

    def stat(meta, group, name):
        row_group = meta.row_group(group)
        for i in range(row_group.num_columns):
            column = row_group.column(i)
            if column.path_in_schema == name:
                return column.statistics.min, column.statistics.max
        raise KeyError(name)

    def matching_groups(path):
        meta = pq.ParquetFile(path, filesystem=s3).metadata
        groups = []
        for g in range(meta.num_row_groups):
            if (stat(meta, g, "bbox.xmin")[0] <= xmax and stat(meta, g, "bbox.xmax")[1] >= xmin
                    and stat(meta, g, "bbox.ymin")[0] <= ymax and stat(meta, g, "bbox.ymax")[1] >= ymin):
                groups.append(g)
        return path, groups

    with ThreadPoolExecutor(16) as executor:
        hits = [(p, g) for p, g in executor.map(matching_groups, paths) if g]
    tables = []
    for path, groups in hits:
        table = pq.ParquetFile(path, filesystem=s3).read_row_groups(groups, columns=columns)
        bbox = table.column("bbox")
        mask = pc.and_(
            pc.and_(pc.less_equal(pc.struct_field(bbox, "xmin"), xmax),
                    pc.greater_equal(pc.struct_field(bbox, "xmax"), xmin)),
            pc.and_(pc.less_equal(pc.struct_field(bbox, "ymin"), ymax),
                    pc.greater_equal(pc.struct_field(bbox, "ymax"), ymin)),
        )
        tables.append(table.filter(mask))
    return pa.concat_tables(tables, promote_options="default").to_pylist()


def itoman_boundary(s3):
    rows = read_bbox(s3, "divisions", "division_area")
    for row in rows:
        names = row.get("names") or {}
        records = [s.get("record_id", "") for s in row.get("sources") or []]
        if names.get("primary") == road_routes.AREA_NAME and any(
                r.startswith(f"r{road_routes.AREA_OSM_RELATION_ID}@") for r in records):
            return shapely.from_wkb(row["geometry"]), records[0]
    raise RuntimeError("Overture divisions に糸満市（OSM relation 4559181）の区域が見つかりません")


def walkable(row):
    if row["subtype"] != "road" or row["class"] in EXCLUDED_CLASSES:
        return False
    for restriction in row.get("access_restrictions") or []:
        when = restriction.get("when") or {}
        access = restriction.get("access_type")
        modes = when.get("mode") or []
        if when.get("heading") or when.get("during") or when.get("vehicle"):
            continue  # 向き・時間帯・車両条件付きの規制は、徒歩の通行可否には使わない
        if access == "denied" and (not modes or "foot" in modes):
            return False  # access=no / foot=no 相当
        if access == "allowed" and "as_private" in (when.get("recognized") or []):
            return False  # access=private 相当（OSMnxの徒歩ネットワークでも除外される）
    return True


def main(output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    s3 = s3_filesystem()

    boundary, boundary_source = itoman_boundary(s3)
    (output_dir / "itoman_boundary.geojson").write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {"source": f"Overture {RELEASE} / OSM {boundary_source}"},
                      "geometry": shapely.geometry.mapping(boundary)}],
    }), encoding="utf-8")
    area = road_routes.area_with_margin(boundary)

    segments = read_bbox(s3, "transportation", "segment",
                         columns=["id", "geometry", "bbox", "subtype", "class", "connectors",
                                  "access_restrictions", "sources"])
    geometries = shapely.from_wkb([row["geometry"] for row in segments])
    inside = shapely.intersects(geometries, area)

    node_ids = {}
    nodes = {}
    ways = []
    next_id = [1]

    def new_node(lon, lat, key=None):
        if key is not None and key in node_ids:
            return node_ids[key]
        node_id = next_id[0]
        next_id[0] += 1
        nodes[node_id] = (lon, lat)
        if key is not None:
            node_ids[key] = node_id
        return node_id

    kept = 0
    snapped_errors = []
    for row, geometry, is_inside in zip(segments, geometries, inside):
        if not is_inside or not walkable(row):
            continue
        coords = list(geometry.coords)
        lons = np.array([c[0] for c in coords])
        lats = np.array([c[1] for c in coords])
        _, _, steps = GEOD.inv(lons[:-1], lats[:-1], lons[1:], lats[1:])
        cumulative = np.concatenate([[0.0], np.cumsum(steps)])
        total = cumulative[-1] if cumulative[-1] > 0 else 1.0
        fractions = cumulative / total

        vertex_keys = [None] * len(coords)
        for connector in row.get("connectors") or []:
            vertex = int(np.argmin(np.abs(fractions - connector["at"])))
            snapped_errors.append(abs(fractions[vertex] - connector["at"]) * total)
            vertex_keys[vertex] = connector["connector_id"]
        refs = [new_node(lon, lat, key) for (lon, lat), key in zip(coords, vertex_keys)]
        osm_way = next((s["record_id"] for s in row.get("sources") or []
                        if (s.get("dataset") == "OpenStreetMap")), "")
        ways.append((refs, row["class"] if row["class"] not in (None, "unknown") else "road", osm_way))
        kept += 1

    path = output_dir / "itoman_walk.osm"
    with open(path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6" generator="sheltermatch-verification">\n')
        for node_id, (lon, lat) in nodes.items():
            f.write(f'  <node id="{node_id}" lat="{lat:.7f}" lon="{lon:.7f}"/>\n')
        for number, (refs, highway, osm_way) in enumerate(ways, start=1):
            f.write(f'  <way id="{number}">\n')
            for ref in refs:
                f.write(f'    <nd ref="{ref}"/>\n')
            f.write(f'    <tag k="highway" v={quoteattr(highway)}/>\n')
            f.write(f'    <tag k="source:osm_way" v={quoteattr(osm_way)}/>\n')
            f.write('  </way>\n')
        f.write("</osm>\n")

    errors = np.array(snapped_errors)
    print(f"Overture {RELEASE}: 範囲内のsegment {int(inside.sum())}件のうち徒歩用 {kept}件 → {path}")
    print(f"  ノード {len(nodes)} / 分岐点(connector) {len(node_ids)}"
          f" / connector位置と頂点のずれ 最大{errors.max():.2f}m・99%点{np.percentile(errors, 99):.2f}m")
    print(f"  処理時間 {time.perf_counter() - started:.1f}秒")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "road_data")
