"""sheltermatch レビュー用HTML（補助成果物）の生成処理。

`assigned_shelters.csv` が正式なデータ成果物で、このモジュールが作るHTMLは、その結果を職員が
地図上で目視確認するための補助成果物です（避難所の割り当て結果ではありません）。

sheltermatch.ipynb はGoogle Colabでの実行時に、このファイルと review_template.html をGitHubから
取得して `build_review_package()` を呼び出すだけにしています。Notebookのグローバル変数には
依存せず、必要なデータはすべて引数で受け取ります。

生成される `sheltermatch_review.zip` は、地図ライブラリ（Leaflet）・背景地図PNG・ハザードPNGを
すべて同梱するため、展開したフォルダだけで外部通信なしに `review.html` を開けます。外部
（GitHub・unpkg・地理院タイル）への通信が発生するのは、このモジュールが成果物を生成するときだけです。

出力するZIPの中身:
    sheltermatch_review/review.html                         … 全要支援者のデータを埋め込んだ1ファイル
    sheltermatch_review/assets/hazard_*.png                 … ハザード区域の表示用画像（大分類ごとに1枚）
    sheltermatch_review/assets/basemap_itoman.png           … 表示範囲の背景地図（詳細）
    sheltermatch_review/assets/basemap_okinawa_mainland.png … 沖縄本島概要図の背景地図
    sheltermatch_review/assets/js, css, images              … Leaflet本体（地図ライブラリ）
    sheltermatch_review/assets/leaflet-LICENSE.txt          … Leafletの公式LICENSE本文
"""
import hashlib
import io
import json
import math
import shutil
import time
import zipfile
from pathlib import Path

import geopandas as gpd
import matplotlib
import numpy as np
import pandas as pd
import requests
from PIL import Image
from shapely.geometry import box

matplotlib.use("Agg")  # 画面を持たない環境でもPNGを書き出せるようにする
import matplotlib.pyplot as plt  # noqa: E402  （バックエンドを指定してから読み込む必要がある）

# Notebookとこのモジュールの受け渡し方（build_review_packageの引数・戻り値）を変えたら上げる。
# Notebook側は読み込んだ直後にこの値を確認し、互換性のない組み合わせのまま処理を続けない。
REVIEW_BUILDER_API_VERSION = 1


# =============================================================================
# 定数
# =============================================================================

# 同梱する地図ライブラリ。バージョンを固定し、取得したファイルの内容をSHA-256で確認してから
# 同梱する（レビュー成果物だけで完結させるため）。
LEAFLET_VERSION = "1.9.4"
# unpkgはnpmパッケージの内容をそのまま配信する。dist/配下がJS・CSS・画像、
# パッケージ直下にライセンス本文があるため、配布元のパスをそれぞれ指定する。
LEAFLET_BASE_URL = f"https://unpkg.com/leaflet@{LEAFLET_VERSION}"
LEAFLET_FILES = [
    # (配布元のパス, 成果物内のパス, SHA-256)
    ("dist/leaflet.js", "js/leaflet.js",
     "db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a"),
    ("dist/leaflet.css", "css/leaflet.css",
     "a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6"),
    ("dist/images/layers.png", "images/layers.png",
     "1dbbe9d028e292f36fcba8f8b3a28d5e8932754fc2215b9ac69e4cdecf5107c6"),
    ("dist/images/layers-2x.png", "images/layers-2x.png",
     "066daca850d8ffbef007af00b06eac0015728dee279c51f3cb6c716df7c42edf"),
    ("dist/images/marker-icon.png", "images/marker-icon.png",
     "574c3a5cca85f4114085b6841596d62f00d7c892c7b03f28cbfa301deb1dc437"),
    ("dist/images/marker-icon-2x.png", "images/marker-icon-2x.png",
     "00179c4c1ee830d3a108412ae0d294f55776cfeb085c60129a39aa6fc4ae2528"),
    ("dist/images/marker-shadow.png", "images/marker-shadow.png",
     "264f5c640339f042dd729062cfc04c17f8ea0f29882b538e3848ed8f10edb4da"),
    # Leaflet 1.9.4はBSD 2-Clause Licenseで配布されており、再配布時は著作権表示・条件・
    # 免責条項の保持が必須のため、公式LICENSE本文をそのまま同梱する。
    ("LICENSE", "leaflet-LICENSE.txt",
     "53e8dc25862014e4324741ca18fbe3611e11d42ef69f59f86ea8c5389647d4cb"),
]

# ---- 背景地図（オフライン用に地理院タイルをPNGへモザイクして同梱する） ----
# 取得できるのはNotebookを実行している環境（Google Colab等、インターネット接続あり）だけで
# よい。展開したreview.htmlは、この節で作るPNGを含む成果物フォルダだけで外部通信なしに開ける。
#
# 地理院タイルの利用条件・出典表記は、実装時点の国土地理院公式情報を確認すること。
# https://www.gsi.go.jp/kikakuchousei/kikakuchousei40182.html
GSI_TILE_URL_TEMPLATE = "https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png"
GSI_ATTRIBUTION_TEXT = "国土地理院"
GSI_TILE_SIZE = 256

# 沖縄本島全体が収まる固定範囲（min_lat, min_lon, max_lat, max_lon）。厳密な行政区域の抽出や
# 本島形状のマスクは行わず、本島内の大まかな位置関係が分かれば十分という前提の概略値。
OKINAWA_MAINLAND_BOUNDS = (25.85, 127.50, 27.05, 128.35)

# 背景地図PNGの生成設定。zoom_candidatesは解像度が高い順に並べ、先頭から試して
# タイル数がmax_tiles以下になった最初のズームを使う（安全策）。候補のどのズームでも
# 収まらない場合は、黙って大量取得せず処理を中止する。
# 初版のレビュー用途（沖縄本島概要＋糸満市詳細）に対して十分余裕のある値として、
# 詳細背景は最大200枚（zoom15で概ね市街地1つ分の範囲を想定）、概要背景は最大120枚
# （zoom10で本島全体）を上限とする。
BASEMAP_SPECS = [
    {
        "key": "detail",
        "filename": "basemap_itoman.png",
        "label": "詳細背景（表示範囲）",
        "zoom_candidates": [15, 14, 13, 12, 11],
        "max_tiles": 200,
    },
    {
        "key": "overview",
        "filename": "basemap_okinawa_mainland.png",
        "label": "沖縄本島概要背景",
        "zoom_candidates": [10, 9, 8, 7],
        "max_tiles": 120,
    },
]

REVIEW_PACKAGE_NAME = "sheltermatch_review"
REVIEW_ZIP_FILENAME = f"{REVIEW_PACKAGE_NAME}.zip"
REVIEW_IMAGE_WIDTH = 1600  # ハザード表示用PNGの横幅（px）

# 表示用のハザード大分類。解析結果として保持している詳細なhazard_typeは変更しない。
HAZARD_DISPLAY_GROUPS = [
    ("tsunami", "津波", "津波", "#0891b2"),
    ("flood", "洪水", "洪水", "#2563eb"),
    ("storm_surge", "高潮", "高潮", "#7c3aed"),
    ("landslide", "土砂災害", "土砂災害", "#b45309"),
    ("other", "その他", None, "#6b7280"),
]


# =============================================================================
# 地図素材の準備（地図ライブラリ・背景地図PNG）
# =============================================================================

def bundle_leaflet(assets_dir):
    """地図ライブラリ（Leaflet）を取得して成果物へ同梱する。

    取得できるのはNotebookを実行している環境（Google Colab等）だけで構わない。同梱した後の
    review.html は、展開したフォルダだけで外部通信なしに開ける。
    取得したファイルは、内容がバージョン固定のものと同じかSHA-256で確認してから書き込む。

    CSSは画像を 'images/...' という相対パスで参照するため、CSSを css/ へ置くとパスがずれる。
    利用者が中身を追いやすい assets/images/ へ画像をまとめたうえで、CSS側の参照を
    '../images/...' へ書き換える。"""
    for source_path, package_path, expected_digest in LEAFLET_FILES:
        url = f"{LEAFLET_BASE_URL}/{source_path}"
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
        except Exception as error:
            raise RuntimeError(
                f"地図ライブラリ（Leaflet {LEAFLET_VERSION}）を取得できませんでした: {url}\n"
                f"（詳細: {error}）\n"
                "結果CSVは出力済みです。レビュー用HTMLだけを作り直す場合は、"
                "インターネットに接続できる状態でこのセルを再実行してください。"
            ) from error

        content = response.content
        actual_digest = hashlib.sha256(content).hexdigest()
        if actual_digest != expected_digest:
            raise RuntimeError(
                f"地図ライブラリの内容が想定と異なります: {url}\n"
                f"  期待 {expected_digest}\n  実際 {actual_digest}"
            )

        if package_path.endswith(".css"):
            content = content.decode("utf-8").replace("url(images/", "url(../images/").encode("utf-8")

        target = assets_dir / package_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    print(f"地図ライブラリ（Leaflet {LEAFLET_VERSION}）を成果物へ同梱しました。")


def _lonlat_to_tile_xy(lon, lat, zoom):
    """経緯度を、指定ズームでのXYZタイル座標（小数）へ変換する（Web Mercator）。
    小数のまま返すことで、タイルの中の位置（切り出し位置）もこの値から計算できる。"""
    lat = max(min(lat, 85.05112878), -85.05112878)  # Web Mercatorの有効な緯度範囲に収める
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def _tile_range_for_bounds(bounds, zoom):
    """表示範囲（min_lat, min_lon, max_lat, max_lon）を覆うタイルの整数範囲
    （tile_x0, tile_y0, tile_x1, tile_y1）と、範囲の四隅のタイル座標（小数。
    モザイク後の切り出し位置の計算に使う）を返す。"""
    min_lat, min_lon, max_lat, max_lon = bounds
    x0, y0 = _lonlat_to_tile_xy(min_lon, max_lat, zoom)  # 北西（左上）
    x1, y1 = _lonlat_to_tile_xy(max_lon, min_lat, zoom)  # 南東（右下）
    tile_x0, tile_x1 = int(math.floor(x0)), int(math.floor(x1))
    tile_y0, tile_y1 = int(math.floor(y0)), int(math.floor(y1))
    return (tile_x0, tile_y0, tile_x1, tile_y1), (x0, y0, x1, y1)


def _select_basemap_zoom(bounds, zoom_candidates, max_tiles):
    """タイル数がmax_tiles以下になる、最も解像度の高いズームを選ぶ。
    候補のどのズームでもタイル数が収まらない場合は (None, None, None) を返す
    （呼び出し側で、大量ダウンロードをせずに処理を中止する）。"""
    for zoom in zoom_candidates:
        tile_range, _ = _tile_range_for_bounds(bounds, zoom)
        tile_x0, tile_y0, tile_x1, tile_y1 = tile_range
        tile_count = (tile_x1 - tile_x0 + 1) * (tile_y1 - tile_y0 + 1)
        if tile_count <= max_tiles:
            return zoom, tile_range, tile_count
    return None, None, None


def _fetch_gsi_tile(zoom, x, y, tile_cache):
    """1枚の地理院タイルを取得する。同じNotebookセッション内で同じタイルを何度も取得しない
    よう、メモリ上の簡易キャッシュ（tile_cache）を使う（永続的なキャッシュは行わない）。
    失敗した場合は、どのURLで失敗したかが分かる例外を送出する。"""
    key = (zoom, x, y)
    if key in tile_cache:
        return tile_cache[key]

    url = GSI_TILE_URL_TEMPLATE.format(z=zoom, x=x, y=y)
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
    except Exception as error:
        raise RuntimeError(
            f"背景地図タイルを取得できませんでした: {url}\n（詳細: {error}）"
        ) from error

    try:
        tile_image = Image.open(io.BytesIO(response.content)).convert("RGB")
    except Exception as error:
        raise RuntimeError(
            f"背景地図タイルの画像を読み込めませんでした: {url}\n（詳細: {error}）"
        ) from error

    tile_cache[key] = tile_image
    return tile_image


def render_basemap_png(bounds, spec, assets_dir, tile_cache):
    """1件の背景地図PNGを生成する。

    ハザードPNGと同じ考え方で、表示範囲の四隅をタイル座標（Web Mercator）へ変換し、
    その位置でモザイク画像を正確に切り出す。これにより、Leaflet ImageOverlayへ渡す
    緯度経度のboundsと画像の地理範囲が一致し、ハザードPNGとも位置がずれない。"""
    zoom, tile_range, tile_count = _select_basemap_zoom(
        bounds, spec["zoom_candidates"], spec["max_tiles"]
    )
    if zoom is None:
        min_lat, min_lon, max_lat, max_lon = bounds
        raise RuntimeError(
            f"{spec['label']}の生成を中止しました。表示範囲が広すぎるため、"
            f"候補のズーム{spec['zoom_candidates']}のどれを使ってもタイル数が"
            f"上限（{spec['max_tiles']}枚）を超えます"
            f"（範囲: 緯度{min_lat:.4f}〜{max_lat:.4f} / 経度{min_lon:.4f}〜{max_lon:.4f}）。"
            "黙って大量のタイルを取得することは行いません。"
        )

    tile_x0, tile_y0, tile_x1, tile_y1 = tile_range
    columns = tile_x1 - tile_x0 + 1
    rows = tile_y1 - tile_y0 + 1

    _, (x0, y0, x1, y1) = _tile_range_for_bounds(bounds, zoom)
    crop_left = (x0 - tile_x0) * GSI_TILE_SIZE
    crop_top = (y0 - tile_y0) * GSI_TILE_SIZE
    crop_right = (x1 - tile_x0) * GSI_TILE_SIZE
    crop_bottom = (y1 - tile_y0) * GSI_TILE_SIZE
    output_width = max(1, round(crop_right - crop_left))
    output_height = max(1, round(crop_bottom - crop_top))

    # 安全策: 取得前に必要タイル数・想定出力サイズ・使用ズームを表示する。
    print(
        f"[{spec['label']}] zoom={zoom} / タイル数={tile_count}枚（{columns}x{rows}）"
        f" / 想定出力サイズ={output_width}x{output_height}px"
    )

    mosaic = Image.new("RGB", (columns * GSI_TILE_SIZE, rows * GSI_TILE_SIZE))
    for tile_x in range(tile_x0, tile_x1 + 1):
        for tile_y in range(tile_y0, tile_y1 + 1):
            tile_image = _fetch_gsi_tile(zoom, tile_x, tile_y, tile_cache)
            mosaic.paste(
                tile_image,
                ((tile_x - tile_x0) * GSI_TILE_SIZE, (tile_y - tile_y0) * GSI_TILE_SIZE),
            )

    cropped = mosaic.crop((
        round(crop_left), round(crop_top),
        round(crop_left) + output_width, round(crop_top) + output_height,
    ))
    output_path = assets_dir / spec["filename"]
    cropped.save(output_path, "PNG")

    return {
        "key": spec["key"],
        "image": f"assets/{spec['filename']}",
        # ImageOverlayへ渡す緯度経度の矩形。ハザードPNGと同じ表示範囲を使う場合は
        # display_boundsそのもの。
        "bounds": [[bounds[0], bounds[1]], [bounds[2], bounds[3]]],
        "zoom": zoom,
        "tile_count": tile_count,
        "width": output_width,
        "height": output_height,
    }


def render_offline_basemaps(display_bounds, assets_dir):
    """糸満市周辺の詳細背景（表示範囲）と、沖縄本島の概要背景の2枚のPNGを生成する。

    背景地図はレビュー補助情報だが、中途半端な（一部だけ生成できた）背景地図を含む
    レビューZIPは作らない方針のため、1件でも取得・生成に失敗した場合は例外を送出して
    処理を止める。結果CSV（assigned_shelters.csv）はこの関数の呼び出し前に出力済みのため、
    この関数が失敗しても結果CSVには影響しない。"""
    tile_cache = {}
    basemaps = {}

    print("背景地図（地理院タイル）を取得しています（インターネット接続が必要です）…")
    for spec in BASEMAP_SPECS:
        bounds = display_bounds if spec["key"] == "detail" else OKINAWA_MAINLAND_BOUNDS
        basemaps[spec["key"]] = render_basemap_png(bounds, spec, assets_dir, tile_cache)

    notice_path = assets_dir / "gsi-basemap-NOTICE.txt"
    notice_path.write_text(
        "背景地図について\n"
        "\n"
        "このフォルダの basemap_itoman.png / basemap_okinawa_mainland.png は、"
        "国土地理院の地理院タイル（淡色地図）を、このレビュー成果物を作成した時点で"
        "取得し、画像として保存したものです。\n"
        "\n"
        f"出典: {GSI_ATTRIBUTION_TEXT}（地理院タイル, https://maps.gsi.go.jp/）\n"
        "利用条件は国土地理院の公式情報を確認してください。\n"
        "https://www.gsi.go.jp/kikakuchousei/kikakuchousei40182.html\n"
        "\n"
        "review.html は展開後、これらの画像も含め外部通信なしで利用できます。\n",
        encoding="utf-8",
    )

    print(f"背景地図PNGを作成しました（出典: {GSI_ATTRIBUTION_TEXT}）。")
    return basemaps


# =============================================================================
# ハザード区域の表示用PNG
# =============================================================================

def hazard_display_group(hazard_type):
    """詳細なhazard_typeを、表示用の大分類キーへ寄せる（元のhazard_typeは変更しない）。"""
    text = str(hazard_type)
    for key, _label, prefix, _color in HAZARD_DISPLAY_GROUPS:
        if prefix is not None and text.startswith(prefix):
            return key
    return "other"


def split_hazard_types(value):
    """';'区切りのhazard_typeを一覧へ分解する（判定していない・該当なしは空の一覧）。"""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return []
    return [part for part in str(value).split(";") if part]


def review_display_bounds(latitudes, longitudes):
    """有効な要支援者地点・避難所地点の全体範囲に余白を付けた共通表示範囲を返す
    （min_lat, min_lon, max_lat, max_lon）。すべてのハザードPNG・詳細背景PNGでこの範囲を使う。"""
    min_lat, max_lat = float(np.min(latitudes)), float(np.max(latitudes))
    min_lon, max_lon = float(np.min(longitudes)), float(np.max(longitudes))
    lat_margin = max((max_lat - min_lat) * 0.08, 0.005)
    lon_margin = max((max_lon - min_lon) * 0.08, 0.005)
    return (min_lat - lat_margin, min_lon - lon_margin, max_lat + lat_margin, max_lon + lon_margin)


def render_hazard_images(hazard_area, display_bounds, assets_dir):
    """ハザード区域を大分類ごとの透過PNGへ描き出し、HTMLへ渡すレイヤー情報を返す。

    位置合わせのため、表示範囲をEPSG:3857へ変換した矩形の中で描く（緯度経度のまま描いた画像を
    引き伸ばすと、Web地図上でずれるため）。同じ種別のポリゴンが重なった場所だけ濃くならないよう、
    PNGは不透明な単色で描き、半透明表示はHTML側のレイヤー不透明度で行う。"""
    min_lat, min_lon, max_lat, max_lon = display_bounds
    display_box = box(min_lon, min_lat, max_lon, max_lat)

    # 表示範囲と交差するポリゴンだけに絞る（画像に写らないポリゴンは描かない）
    visible_rows = hazard_area.sindex.query(display_box, predicate="intersects")
    visible = hazard_area.iloc[visible_rows]
    if len(visible) == 0:
        return []

    mercator_bounds = gpd.GeoSeries([display_box], crs="EPSG:4326").to_crs(epsg=3857).total_bounds
    minx, miny, maxx, maxy = mercator_bounds
    image_height = max(1, int(round(REVIEW_IMAGE_WIDTH * (maxy - miny) / (maxx - minx))))

    visible = visible.to_crs(epsg=3857)
    display_groups = visible["hazard_type"].map(hazard_display_group)

    layers = []
    for key, label, _prefix, color in HAZARD_DISPLAY_GROUPS:
        group = visible[display_groups == key]
        if len(group) == 0:
            continue

        figure = plt.figure(figsize=(REVIEW_IMAGE_WIDTH / 100, image_height / 100), dpi=100)
        axes = figure.add_axes([0, 0, 1, 1])
        axes.set_xlim(minx, maxx)
        axes.set_ylim(miny, maxy)
        axes.set_axis_off()
        group.plot(ax=axes, color=color, linewidth=0, antialiased=False)
        image_name = f"hazard_{key}.png"
        figure.savefig(assets_dir / image_name, dpi=100, transparent=True)
        plt.close(figure)

        layers.append({
            "key": key,
            "label": label,
            "color": color,
            "image": f"assets/{image_name}",
            # ImageOverlayへ渡す緯度経度の矩形。上でEPSG:3857へ変換したのと同じ矩形。
            "bounds": [[min_lat, min_lon], [max_lat, max_lon]],
            "polygon_count": int(len(group)),
        })
    return layers


# =============================================================================
# 埋め込みデータの組み立てと結果CSVとの照合
# =============================================================================

def json_value(value):
    """NaN等をそのままJSONにできないため、Pythonの標準的な値へ整える。"""
    if value is None:
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, str):
        return value
    if pd.isna(value):
        return None
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value)
    return str(value)


def build_review_data(final_df, review_rows, hazard_layers, basemaps, top_n):
    """結果CSVと同じ実行結果から、レビューHTMLへ埋め込むデータを組み立てる。
    候補の順位・距離は候補算出ループで得たものをそのまま使い、ここで計算し直さない
    （CSVとHTMLで候補順位がずれないようにするため）。"""
    residents = []
    for position in range(len(final_df)):
        row = final_df.iloc[position]
        review_row = review_rows[position]

        candidates = []
        hazard_groups = set()
        for candidate in review_row["candidates"]:
            shelter_types = split_hazard_types(candidate["shelter_hazard_types"])
            line_types = split_hazard_types(candidate["straight_line_hazard_types"])
            hazard_groups.update(hazard_display_group(t) for t in shelter_types + line_types)
            candidates.append({
                "rank": candidate["rank"],
                "name": json_value(candidate["name"]),
                "latitude": json_value(candidate["latitude"]),
                "longitude": json_value(candidate["longitude"]),
                "distance_m": json_value(candidate["distance_m"]),
                "disaster_support": json_value(candidate["disaster_support"]),
                "shelter_in_hazard": json_value(candidate["shelter_in_hazard"]),
                "shelter_hazard_types": shelter_types,
                "straight_line_intersects_hazard": json_value(
                    candidate["straight_line_intersects_hazard"]
                ),
                "straight_line_hazard_types": line_types,
            })

        resident_types = split_hazard_types(row.get("resident_hazard_types"))
        hazard_groups.update(hazard_display_group(t) for t in resident_types)

        latitude = json_value(review_row["latitude"])
        longitude = json_value(review_row["longitude"])
        residents.append({
            "resident_id": json_value(row["resident_id"]),
            "address": json_value(row.get("address")),
            "latitude": latitude,
            "longitude": longitude,
            "has_coordinates": latitude is not None and longitude is not None,
            "match_status": json_value(row["match_status"]),
            "resident_in_hazard": json_value(row.get("resident_in_hazard")),
            "resident_hazard_types": resident_types,
            "hazard_groups": sorted(hazard_groups),
            "candidates": candidates,
        })

    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "top_n": top_n,
        "hazard_layers": hazard_layers,
        "basemap": basemaps,
        "basemap_attribution": GSI_ATTRIBUTION_TEXT,
        "residents": residents,
    }


def verify_review_data(review_data, final_df, top_n):
    """HTMLへ埋め込むデータが結果CSVと一致していることを確認する。
    1件でも食い違えばHTMLを作らずに処理を止める（CSVと見比べて判断できない資料を出さないため）。"""
    problems = []
    residents = review_data["residents"]

    if len(residents) != len(final_df):
        problems.append(f"要支援者件数が一致しません（HTML {len(residents)}件 / CSV {len(final_df)}件）")
    else:
        for position, resident in enumerate(residents):
            row = final_df.iloc[position]
            label = f"CSV{position + 2}行目"

            if str(resident["resident_id"]) != str(row["resident_id"]):
                problems.append(
                    f"{label}: resident_idが一致しません"
                    f"（HTML '{resident['resident_id']}' / CSV '{row['resident_id']}'）"
                )

            by_rank = {candidate["rank"]: candidate for candidate in resident["candidates"]}
            for rank in range(1, top_n + 1):
                candidate = by_rank.get(rank)
                csv_name = row.get(f"candidate_{rank}")

                if pd.isna(csv_name):
                    if candidate is not None:
                        problems.append(f"{label}: CSVに候補{rank}が無いのにHTMLにはあります")
                    continue
                if candidate is None:
                    problems.append(f"{label}: CSVの候補{rank}がHTMLにありません")
                    continue

                for field, csv_column in (
                    ("name", f"candidate_{rank}"),
                    ("distance_m", f"distance_{rank}_m"),
                    ("shelter_in_hazard", f"candidate_{rank}_shelter_in_hazard"),
                    ("straight_line_intersects_hazard",
                     f"candidate_{rank}_straight_line_intersects_hazard"),
                ):
                    if csv_column not in final_df.columns:
                        continue
                    if candidate[field] != json_value(row[csv_column]):
                        problems.append(
                            f"{label}: 候補{rank}の{csv_column}が一致しません"
                            f"（HTML {candidate[field]!r} / CSV {json_value(row[csv_column])!r}）"
                        )

                for field, csv_column in (
                    ("shelter_hazard_types", f"candidate_{rank}_shelter_hazard_types"),
                    ("straight_line_hazard_types", f"candidate_{rank}_straight_line_hazard_types"),
                ):
                    if csv_column not in final_df.columns:
                        continue
                    if candidate[field] != split_hazard_types(row[csv_column]):
                        problems.append(f"{label}: 候補{rank}の{csv_column}が一致しません")

            if "resident_in_hazard" in final_df.columns:
                if resident["resident_in_hazard"] != json_value(row["resident_in_hazard"]):
                    problems.append(f"{label}: resident_in_hazardが一致しません")
                if resident["resident_hazard_types"] != split_hazard_types(row["resident_hazard_types"]):
                    problems.append(f"{label}: resident_hazard_typesが一致しません")

    if problems:
        raise RuntimeError(
            "レビューHTMLの内容が結果CSVと一致しないため、HTMLを作らずに処理を停止しました。\n"
            + "\n".join(f"  - {problem}" for problem in problems[:20])
            + (f"\n  ほか{len(problems) - 20}件" if len(problems) > 20 else "")
        )


def embed_review_json(review_data):
    """HTMLへ安全に埋め込める形のJSON文字列を返す。住所等の利用者データがそのまま
    HTMLとして解釈されないよう、'<' '>' '&' をJSONのエスケープ表現へ置き換える
    （'</script>' でscriptタグを壊せないようにするため）。"""
    text = json.dumps(review_data, ensure_ascii=False, allow_nan=False)
    for character, escaped in (
        ("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"),
        # 行区切り扱いの文字はJavaScriptの文字列を壊すため、これも置き換える
        (chr(0x2028), "\\u2028"), (chr(0x2029), "\\u2029"),
    ):
        text = text.replace(character, escaped)
    return text


# =============================================================================
# 成果物（sheltermatch_review.zip）の生成
# =============================================================================

def build_review_package(final_df, review_rows, hazard_area, shelters_df, top_n, output_dir,
                         template_path):
    """レビュー用のHTML・PNGを作り、ZIPへまとめてそのパスを返す。

    Notebook側のグローバル変数は参照せず、必要なものはすべて引数で受け取る。

    final_df      結果CSV（assigned_shelters.csv）と同じ内容のDataFrame
    review_rows   候補算出ループで作った要支援者ごとの表示用データ（候補の順位・距離を含む）
    hazard_area   ハザード区域のGeoDataFrame（ハザード判定を行っていない場合はNone）
    shelters_df   距離計算に使った有効な避難所のDataFrame（表示範囲の算出に使う）
    top_n         候補の件数（TOP_N）
    output_dir    ZIPと作業用フォルダの出力先
    template_path review_template.html のパス
    """
    package_dir = Path(output_dir) / REVIEW_PACKAGE_NAME
    assets_dir = package_dir / "assets"
    if package_dir.exists():
        shutil.rmtree(package_dir)
    assets_dir.mkdir(parents=True)

    # 表示範囲（ハザードPNG・詳細背景PNGで共通）は、有効な要支援者地点と避難所地点から決める。
    # 有効な避難所が0件の場合は避難所取得セルで既に処理を止めているため、ここでは必ず1件以上ある。
    # 背景地図の生成はハザード判定の有無（ENABLE_HAZARD_CHECK）に依存させない。
    latitudes = [row["latitude"] for row in review_rows if row["latitude"] is not None]
    longitudes = [row["longitude"] for row in review_rows if row["longitude"] is not None]
    latitudes += list(shelters_df["latitude"])
    longitudes += list(shelters_df["longitude"])

    bundle_leaflet(assets_dir)
    display_bounds = review_display_bounds(latitudes, longitudes)

    hazard_layers = []
    if hazard_area is not None and len(hazard_area) > 0:
        hazard_layers = render_hazard_images(hazard_area, display_bounds, assets_dir)

    basemaps = render_offline_basemaps(display_bounds, assets_dir)

    review_data = build_review_data(final_df, review_rows, hazard_layers, basemaps, top_n)
    verify_review_data(review_data, final_df, top_n)

    # 画面（HTML/CSS/JavaScript）はPython文字列として持たず、review_template.html から読み込む。
    template = Path(template_path).read_text(encoding="utf-8")
    html = template.replace("__REVIEW_DATA_JSON__", embed_review_json(review_data))
    (package_dir / "review.html").write_text(html, encoding="utf-8")

    zip_path = Path(output_dir) / REVIEW_ZIP_FILENAME
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for path in sorted(package_dir.rglob("*")):
            if path.is_file():
                zip_file.write(path, path.relative_to(output_dir))

    print(f"レビュー用HTMLを作成しました（要支援者 {len(review_data['residents'])}件）。")
    print("ZIPを展開して review.html を開けば、外部通信なしで背景地図・ハザード区域・"
          "要支援者・候補避難所を確認できます。")
    print("背景地図PNG:")
    for spec in BASEMAP_SPECS:
        info = basemaps[spec["key"]]
        print(f"  {info['image']}（zoom={info['zoom']}, {info['width']}x{info['height']}px）")
    if hazard_layers:
        print("ハザード表示用PNG:")
        for layer in hazard_layers:
            print(f"  {layer['label']}: {layer['polygon_count']}ポリゴン → {layer['image']}")
    else:
        print("ハザード判定を行っていないため、ハザード表示用PNGは作成していません。")
    return zip_path
