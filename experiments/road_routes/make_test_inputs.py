"""検証用: 道路に沿った参考経路の確認に使う**架空**の入力データを作る（固定シード）。

実在の要支援者・住所は一切使わない。避難所も架空（名称は「架空避難所NN」）で、位置は検証用の
道路データ上の点から数十m ずらした点に置く（実際の避難所は道路沿いの施設のため、それに近い条件にする）。

出力（指定したディレクトリへ。リポジトリへはコミットしない）:
    shelters.json         BODIK Data API の records 形式（名称・緯度・経度・災害種別_*）
    residents_cases.csv   確認したい場合を1人ずつ並べた要支援者CSV（共通住民CSVの5列）
    residents_itoman_1000.csv  糸満市の区域内の道路の近くに置いた架空の1,000人（全員に経路ができる
                          条件での処理時間・レビューZIP容量の測定用）
    hazard_cases.geojson  架空のハザード区域（ENABLE_HAZARD_CHECK=True の確認用。実在の区域ではない）

使い方:
    python3 experiments/road_routes/make_test_inputs.py <overture_to_osm_xml.py の出力> <出力先>
"""
import csv
import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import osmnx as ox
from pyproj import Transformer
from shapely.geometry import Point, box

SEED = 20260923
SHELTER_COUNT = 55
DISASTER_TYPES = ["洪水", "崖崩れ、土石流及び地滑り", "高潮", "地震", "津波", "大規模な火事", "内水氾濫"]

# 検証用の架空の道路（run_notebook_check.py --inject-island で追加する、ほかの道路とつながっていない
# 道路）。糸満市の区域内の海上（実際の道路から1km以上離れた地点）に置く。(経度, 緯度)
ISLAND = [(127.6440, 26.1000), (127.6446, 26.1003), (127.6452, 26.1000)]
ISLAND_RESIDENT = (26.10015, 127.6446)
# 糸満市の区域内の海上で、道路から1km以上離れた地点（本人側で道路へ接続できない場合の確認用）
FAR_FROM_ROAD_RESIDENT = (26.1050, 127.6350)
# 糸満市の区域内の海上で、道路から約450m離れた地点（避難所側で道路へ接続できない場合の確認用）
FAR_FROM_ROAD_SHELTER = (26.0950, 127.6550)


def main(road_data, output):
    road_data, output = Path(road_data), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)
    to_metric = Transformer.from_crs("EPSG:4326", "EPSG:6683", always_xy=True)
    to_lonlat = Transformer.from_crs("EPSG:6683", "EPSG:4326", always_xy=True)

    graph = ox.graph_from_xml(road_data / "itoman_walk.osm", bidirectional=True, retain_all=False)
    nodes = ox.convert.graph_to_gdfs(graph, edges=False)
    boundary = gpd.read_file(road_data / "itoman_boundary.geojson").geometry.iloc[0]
    # 糸満市の区域内にある道路の点から選ぶ（実際の避難所・要支援者は市内にいるため）
    inside = nodes[nodes.geometry.within(boundary)]
    chosen = inside.sample(SHELTER_COUNT, random_state=SEED)

    def offset(lon, lat, max_m):
        x, y = to_metric.transform(lon, lat)
        angle = rng.uniform(0, 2 * np.pi)
        distance = rng.uniform(0, max_m)
        return to_lonlat.transform(x + distance * np.cos(angle), y + distance * np.sin(angle))

    shelters = []
    for number, (_, node) in enumerate(chosen.iterrows(), start=1):
        lon, lat = offset(node.x, node.y, 40)
        record = {"名称": f"架空避難所{number:02d}", "緯度": round(lat, 6), "経度": round(lon, 6)}
        for disaster in DISASTER_TYPES:
            record[f"災害種別_{disaster}"] = str(rng.choice(["1", "1", "1", "2", ""]))
        shelters.append(record)

    # 道路から離れた避難所（避難所側で道路へ接続できない場合の確認用）と、その近くの道路沿いの要支援者
    far_lat, far_lon = FAR_FROM_ROAD_SHELTER
    record = {"名称": "架空避難所（道路から離れた地点）", "緯度": far_lat, "経度": far_lon}
    record.update({f"災害種別_{d}": "1" for d in DISASTER_TYPES})
    shelters.append(record)
    near = inside.loc[inside.geometry.distance(Point(far_lon, far_lat)).idxmin()]
    nx_, ny_ = to_metric.transform(near.x, near.y)
    near_far_lon, near_far_lat = to_lonlat.transform(nx_ + 10, ny_ + 10)

    (output / "shelters.json").write_text(json.dumps(shelters, ensure_ascii=False, indent=1),
                                          encoding="utf-8")

    cases = [
        ("C01", "架空住所（市街地）", 26.1245, 127.6671, "matched"),
        ("C02", "架空住所（市街地・北）", 26.1398, 127.6822, "matched"),
        ("C03", "架空住所（郊外）", 26.1003, 127.7195, "matched"),
        ("C04", "架空住所（海上・道路から遠い）", *FAR_FROM_ROAD_RESIDENT, "matched"),
        ("C05", "架空住所（座標なし）", "", "", "town_not_found"),
        ("C06", "架空住所（座標が範囲外）", "91.000000", "181.000000", "matched"),
        ("C07", "架空住所（つながっていない架空の道路の近く）", *ISLAND_RESIDENT, "matched"),
        ("C08", "架空住所（道路から離れた避難所の近く）", round(near_far_lat, 6), round(near_far_lon, 6), "matched"),
        ("C09", "架空住所（糸満市外・道路データの取得範囲の端）", 26.1900, 127.6950, "matched"),
    ]
    with open(output / "residents_cases.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["resident_id", "address", "latitude", "longitude", "geocode_status"])
        writer.writerows(cases)

    # 全員が市内の道路の近く（0〜60m）にいる架空の1,000人
    people = inside.sample(1000, random_state=SEED + 1, replace=len(inside) < 1000)
    with open(output / "residents_itoman_1000.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["resident_id", "address", "latitude", "longitude", "geocode_status"])
        for number, (_, node) in enumerate(people.iterrows(), start=1):
            lon, lat = offset(node.x, node.y, 60)
            writer.writerow([f"Q{number:04d}", f"架空住所{number:04d}", f"{lat:.6f}", f"{lon:.6f}", "matched"])

    # 架空のハザード区域: C01 の周辺と、C01 から候補へ向かう直線にかかりそうな矩形
    hazard = gpd.GeoDataFrame(
        {"name": ["架空区域A", "架空区域B"]},
        geometry=[box(127.6640, 26.1215, 127.6690, 26.1260), box(127.6720, 26.1300, 127.6760, 26.1350)],
        crs="EPSG:4326",
    )
    hazard.to_file(output / "hazard_cases.geojson", driver="GeoJSON")
    print(f"架空避難所 {len(shelters)}件・確認用の要支援者 {len(cases)}件・市内の架空の1,000人・"
          f"架空ハザード区域 2件を {output} へ出力")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
