"""テスト用の完全な架空データ（実在の要支援者・住所・避難所・ハザードデータではない）。

座標は、区域の位置関係を確認しやすいよう、平らな格子状に決めた架空の値。ハザード区域は
単純な長方形で、実際の浸水想定・警戒区域とは無関係。

    from fake_data import RESIDENTS, SHELTERS, hazard_geojson_uploads, residents_csv_bytes

件数・人数は、テストの期待値としてこのファイルの定義から導く（実データの件数は一切使わない）。
"""
import json

# ---- ハザード区域（長方形: 西, 南, 東, 北） ----
# 津波は、同じ大分類の2枚のポリゴン（浸水深の区分が違う）が重なる。
TSUNAMI_LOW = (127.660, 26.115, 127.670, 26.125)      # 津波:0.01m以上0.3m未満
TSUNAMI_HIGH = (127.662, 26.117, 127.668, 26.123)     # 津波:0.3m以上1.0m未満（上の内側）
STORM_SURGE = (127.665, 26.110, 127.675, 26.122)      # 高潮（津波と一部が重なる）
FLOOD = (127.640, 26.100, 127.652, 26.110)            # 洪水（その他の河川）
LANDSLIDE = (127.645, 26.104, 127.650, 26.108)        # 土砂災害（洪水の内側）

# ---- 要支援者（resident_id, address, latitude, longitude, geocode_status, note） ----
# 期待する判定の説明は、test_shelter_assignment.py の期待値の定義を参照。
RESIDENTS = [
    # 津波の2ポリゴン（内側）と高潮の重なる場所 → 津波1人（ポリゴンが2枚でも1人）・高潮1人
    ("R001", "架空町1-1", "26.1200", "127.6660", "matched", "津波2枚＋高潮"),
    # 洪水だけ
    ("R002", "架空町2-2", "26.1050", "127.6420", "matched", "洪水のみ"),
    # どのハザードにも該当しない
    ("R003", "架空町3-3", "26.2000", "127.7500", "matched", "区域外"),
    # 座標が空欄
    ("R004", "架空町4-4", "", "", "parcel_not_found", "座標なし"),
    # 座標が不正（数値として読めない）
    ("R005", "架空町5-5", "不明", "不明", "matched", "座標不正"),
    # 津波（外側のポリゴンだけ）
    ("R006", "架空町6-6", "26.1160", "127.6610", "matched", "津波の外側のみ"),
    # 洪水と土砂災害の両方（複数の大分類）
    ("R007", "架空町7-7", "26.1060", "127.6470", "matched", "洪水＋土砂災害"),
]

# ---- 避難所（BODIK Data API の records 形式。自治体標準ODSの列名） ----
# 災害種別_ の列は、今回の成果物では使わないことを確認するために含める。
SHELTERS = [
    {"名称": "架空避難所A", "緯度": "26.1200", "経度": "127.6660", "災害種別_洪水": "1", "災害種別_津波": ""},
    {"名称": "架空避難所B", "緯度": "26.1210", "経度": "127.6700", "災害種別_洪水": "", "災害種別_津波": "1"},
    {"名称": "架空避難所C", "緯度": "26.1050", "経度": "127.6450", "災害種別_洪水": "2", "災害種別_津波": "2"},
    {"名称": "架空避難所D", "緯度": "26.1500", "経度": "127.7000", "災害種別_洪水": "1", "災害種別_津波": "1"},
    {"名称": "架空避難所E", "緯度": "26.1800", "経度": "127.7200", "災害種別_洪水": "", "災害種別_津波": ""},
]


def residents_csv_bytes(extra_header="note"):
    """要支援者CSV（UTF-8）。resident_id,address,latitude,longitude,geocode_status と、独自の追加列 note。"""
    header = "resident_id,address,latitude,longitude,geocode_status"
    lines = [header + ("," + extra_header if extra_header else "")]
    for resident_id, address, latitude, longitude, status, note in RESIDENTS:
        line = f"{resident_id},{address},{latitude},{longitude},{status}"
        lines.append(line + ("," + note if extra_header else ""))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _rectangle(west, south, east, north):
    return {
        "type": "Polygon",
        "coordinates": [[[west, south], [east, south], [east, north], [west, north], [west, south]]],
    }


def _geojson(features):
    return json.dumps({
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": properties, "geometry": _rectangle(*box)}
            for box, properties in features
        ],
    }, ensure_ascii=False).encode("utf-8")


def hazard_geojson_uploads():
    """ファイル名から種別を自動判定できる、架空のハザードデータ（アップロード内容: {ファイル名: bytes}）。"""
    return {
        # 津波（沖縄県津波浸水想定のファイル名規則）。『分類』属性が詳細区分になる
        "level1_fake.geojson": _geojson([
            (TSUNAMI_LOW, {"分類": "0.01m以上0.3m未満"}),
            (TSUNAMI_HIGH, {"分類": "0.3m以上1.0m未満"}),
        ]),
        # 高潮（沖縄県高潮浸水想定のファイル名規則）
        "47007_takasiosinnsuisoutei_fake.geojson": _geojson([(STORM_SURGE, {})]),
        # 洪水・その他の河川（国土数値情報A31aのファイル名規則）。河川区分が詳細区分
        "A31a-25_47_20_GEOJSON.geojson": _geojson([(FLOOD, {})]),
        # 土砂災害（国土数値情報A33のファイル名規則）。現象の種類・区域区分が詳細区分
        "A33-25_47_GEOJSON.geojson": _geojson([(LANDSLIDE, {"A33_001": 1, "A33_002": 1})]),
    }
