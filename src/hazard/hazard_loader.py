"""sheltermatch ハザードデータ（GeoJSON / Shapefile / 公式配布ZIP）の読込処理。

アップロードされたファイルを解析・正規化し、EPSG:4326のPolygon / MultiPolygonだけを集めた
1つのGeoDataFrame（`hazard_type` と `geometry` の2列）を返します。sheltermatch.ipynb は
Google Colabでの実行時にこのファイルをGitHubから取得し、`load_uploaded_hazards()` を
呼び出すだけにしています。Notebookのグローバル変数には依存しません。

このモジュールは読み込みと正規化だけを行います。要支援者・候補避難所がハザード区域内かどうかの
判定そのものは、従来どおりNotebook側（距離計算・ハザード判定セル）で行います。

区域判定に使えるのはPolygon / MultiPolygonだけなので、次のように扱います。

* 有効なPolygon / MultiPolygon: 一切変更せずそのまま使う
* is_valid=FalseのPolygon / MultiPolygon: shapely.make_valid() で直してから使う
  （直せなかったものは除外する）
* 空・欠損のジオメトリ、Polygon / MultiPolygon以外: 区域判定対象外として除外する

選択したファイルのうち1つでも読み込めなかった場合は、判定結果が不完全なまま
「区域外」と読める結果を作らないよう、例外を送出して処理を止めます（fail-closed）。
"""
import io
import re
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import make_valid, union_all

# Notebookとこのモジュールの受け渡し方（load_uploaded_hazardsの引数・戻り値）を変えたら上げる。
# Notebook側は読み込んだ直後にこの値を確認し、互換性のない組み合わせのまま処理を続けない。
HAZARD_LOADER_API_VERSION = 1


def _decode_zip_entry_name(member):
    """ZIPエントリのファイル名を復号する。UTF-8フラグ（汎用目的ビットフラグのbit 11）が
    立っていないエントリは、Pythonのzipfileが既定でCP437として解釈するため、CP932
    (Shift-JIS系)の日本語ファイル名を含む配布ZIP（沖縄県公式データ等、UTF-8フラグを立てずに
    作成されたZIP）では文字化けする。その場合は元のバイト列をCP932として再解釈する
    （変換できない場合は元の文字列のまま扱う）。"""
    if member.flag_bits & 0x800:
        return member.filename
    try:
        return member.filename.encode("cp437").decode("cp932")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return member.filename


def _normalize_zip_entry_separators(name):
    """ZIPエントリ名のパス区切りを '/'（ZIP標準の区切り）に正規化する。国土数値情報A31a等、
    一部の公式配布ZIPはディレクトリ区切りに '\\'（バックスラッシュ）を使って格納されているが、
    '\\' はPOSIX環境のPathlibでは区切り文字として扱われずサブフォルダを認識できないため、
    展開前に '/' へ揃える。'/' はWindows・POSIXどちらのPathlibでも区切り文字として扱われるため、
    実行環境（Colab/Linux・Windows）に関わらず同じディレクトリ構造になる。"""
    return name.replace("\\", "/")


def safe_extract_zip(zip_bytes, extract_dir):
    """ZIPをextract_dirへ安全に展開する。各エントリ名は、文字コード復元
    （_decode_zip_entry_name）→パス区切り正規化（_normalize_zip_entry_separators）の順で
    処理してから扱う。絶対パスや'..'を含むなど、正規化後のパスが展開先ディレクトリの外に出る
    エントリが1件でもあれば、展開を一切行わずに例外を送出する（パストラバーサル対策。
    全エントリを先に検査してから展開する）。"""
    extract_dir_abs = Path(extract_dir).resolve()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zip_file:
        resolved_members = []
        for member in zip_file.infolist():
            name = _decode_zip_entry_name(member)
            name = _normalize_zip_entry_separators(name)
            is_dir_entry = name.endswith("/")
            member_path = (extract_dir_abs / name).resolve()
            if member_path != extract_dir_abs and extract_dir_abs not in member_path.parents:
                raise RuntimeError(
                    "ZIP内に不正なパスが含まれているため展開を中止しました"
                    f"（パストラバーサルの可能性）: {name}"
                )
            resolved_members.append((member, member_path, is_dir_entry))

        for member, member_path, is_dir_entry in resolved_members:
            if is_dir_entry:
                member_path.mkdir(parents=True, exist_ok=True)
                continue
            member_path.parent.mkdir(parents=True, exist_ok=True)
            with zip_file.open(member) as source, open(member_path, "wb") as target:
                target.write(source.read())


def find_files_by_extension(extract_dir, extension):
    """extract_dir配下から、指定した拡張子のファイルを再帰的に探す。公式配布ZIPには
    '.SHP' / '.GEOJSON' のように拡張子が大文字で格納されているものがあり、Colab(Linux)の
    パターン照合は大文字・小文字を区別するため、ここでは区別せずに探す。"""
    return sorted(p for p in extract_dir.rglob("*") if p.is_file() and p.suffix.lower() == extension)


def find_shapefile_layers(extract_dir):
    """extract_dir配下の.shpを再帰的に探索し、同じディレクトリに.shx/.dbfが揃っているものだけを
    有効なShapefileレイヤーとして返す（.prjが無くても読み込みは試みる。CRSが無いものとして扱う）。
    戻り値は (有効な.shpパスのリスト, .shx/.dbfが揃っていないため除外した件数, 発見した.shp総数)。"""
    shp_paths = find_files_by_extension(extract_dir, ".shp")
    valid_paths = []
    missing_companion_count = 0
    for shp_path in shp_paths:
        siblings = {p.name.lower() for p in shp_path.parent.iterdir()}
        stem_lower = shp_path.stem.lower()
        if f"{stem_lower}.shx" in siblings and f"{stem_lower}.dbf" in siblings:
            valid_paths.append(shp_path)
        else:
            missing_companion_count += 1
    return valid_paths, missing_companion_count, len(shp_paths)


def normalize_code_value(value):
    """公式データのコード値を、対応表を引くための文字列へ揃える。同じコードでも読込方法によって
    1 / "1" / 1.0 のように型や表現が変わるため、整数として解釈できる場合は整数の文字列表現
    （"1"）に統一する。欠損値はNoneを返す。1.5のような整数でない値を丸めると既知コードへ誤分類
    される恐れがあるため丸めず、元の値の文字列表現のまま返す（未知のコード値も消さずに残す）。
    ハザードデータのコード属性（国土数値情報A33の A33_001 / A33_002 等）から使う。"""
    if pd.isna(value):
        return None

    text = str(value).strip()
    try:
        number = float(text)
        if number.is_integer():
            return str(int(number))
    except (TypeError, ValueError):
        pass
    return text


# 国土数値情報A33（土砂災害警戒区域データ）の公式コードリスト。
# A33_001=現象の種類、A33_002=区域区分。名称は公式コードリストのままとし、独自の呼称（イエロー/
# レッド等）へは置き換えない。
A33_PHENOMENON_LABELS = {
    "1": "急傾斜地の崩壊",
    "2": "土石流",
    "3": "地滑り",
}
A33_ZONE_LABELS = {
    "1": "土砂災害警戒区域(指定済)",
    "2": "土砂災害特別警戒区域(指定済)",
    "3": "土砂災害警戒区域(指定前)",
    "4": "土砂災害特別警戒区域(指定前)",
}


def derive_feature_hazard_types(gdf, hazard_type):
    """レイヤーの属性から、行（ポリゴン）ごとのhazard_typeを組み立てる。国土数値情報A33
    （土砂災害警戒区域データ）の 'A33_001'（現象の種類）/'A33_002'（区域区分）属性がある場合は
    公式コードリストの名称をそのまま用いて '<hazard_type>:<現象の種類>:<区域区分>' とする
    （未知のコード値は削除せず、コード値自体をそのまま使う）。津波浸水想定データ等の『分類』属性が
    ある場合は、従来どおり '<hazard_type>:<分類の値>' とする。どちらも無い場合はhazard_typeを
    全行へそのまま適用する。データセットが増えてもこの関数の中だけで判定し、呼び出し側を
    巨大なif文にしない。"""
    if "A33_001" in gdf.columns and "A33_002" in gdf.columns:
        def build_a33_hazard_type(row):
            phenomenon_code = normalize_code_value(row["A33_001"])
            zone_code = normalize_code_value(row["A33_002"])
            phenomenon = A33_PHENOMENON_LABELS.get(phenomenon_code, phenomenon_code)
            zone = A33_ZONE_LABELS.get(zone_code, zone_code)
            parts = [part for part in (phenomenon, zone) if part]
            return f"{hazard_type}:{':'.join(parts)}" if parts else hazard_type

        return gdf.apply(build_a33_hazard_type, axis=1)

    if "分類" in gdf.columns:
        return gdf["分類"].apply(
            lambda value: f"{hazard_type}:{value}" if pd.notna(value) and str(value).strip() else hazard_type
        )

    return hazard_type


# 区域判定に使えるジオメトリ種別。
POLYGON_TYPES = ["Polygon", "MultiPolygon"]


def empty_hazard_layer():
    """有効なハザード区域が1件も無い場合に返す、空のレイヤー。"""
    return gpd.GeoDataFrame({"hazard_type": [], "geometry": []}, crs="EPSG:4326")


def repair_invalid_polygon(geometry):
    """自己交差等で is_valid=False になっているPolygon/MultiPolygonを、shapely.make_valid()で
    区域判定に使える形へ直す。公式配布データには自己交差を含むポリゴンが実際に含まれており
    （国土数値情報A31a・沖縄県津波浸水想定で確認）、そのまま除外するとその区域が判定から
    抜け落ちるため、ここで直してから使う。

    make_valid()の結果がGeometryCollection（面と線が混ざったもの）になる場合は、
    Polygon/MultiPolygon成分だけを取り出す（線・点の成分は区域ではないため使わない）。
    区域として使える形にならなかった場合はNoneを返し、呼び出し側で従来どおり除外する。

    戻り値は (直したジオメトリ or None, GeometryCollectionから成分を取り出したか)。
    既に有効なジオメトリへは適用しないこと（この関数は呼び出し側でis_valid=Falseの行にだけ使う）。"""
    try:
        repaired = make_valid(geometry)
    except Exception:
        # make_valid自体が失敗した場合も、処理を止めずに従来どおり除外扱いにする
        return None, False

    if repaired is None or repaired.is_empty:
        return None, False

    from_collection = False
    if repaired.geom_type == "GeometryCollection":
        polygon_parts = [
            part for part in repaired.geoms
            if part.geom_type in POLYGON_TYPES and not part.is_empty
        ]
        if not polygon_parts:
            return None, False
        repaired = union_all(polygon_parts)
        from_collection = True

    if repaired.geom_type not in POLYGON_TYPES or repaired.is_empty or not repaired.is_valid:
        return None, False
    return repaired, from_collection


def load_hazard_layer(source, hazard_type, assume_wgs84_without_crs):
    """1件の空間データ（GeoJSONまたはShapefile）を読み込み、hazard_type/geometryの2列に正規化し、
    EPSG:4326へ統一する。区域判定はPolygon/MultiPolygonのみを対象とするため、次のように扱う。

    * 空・欠損のジオメトリ: 区域判定対象外として除外する
    * 有効なPolygon/MultiPolygon: 一切変更せずそのまま使う（make_valid()もかけない）
    * is_valid=FalseのPolygon/MultiPolygon: repair_invalid_polygonで直してから使う
      （直せなかったものは従来どおり除外する）
    * Polygon/MultiPolygon以外（LineString等）: 独自に面へ変換したりはせず除外する

    行ごとのhazard_typeはderive_feature_hazard_typesで組み立てる（『分類』属性やA33の公式属性が
    あれば詳細区分を反映し、無ければhazard_typeをそのまま使う）。ジオメトリを直した行も、
    元のhazard_typeをそのまま引き継ぐ（種別・カテゴリの再解釈はしない）。

    CRSが取得できる場合はEPSG:4326へ変換する。CRSが取得できない場合、assume_wgs84_without_crsが
    Trueなら（GeoJSON等、既定でWGS84として扱われる形式）そのままEPSG:4326とみなす。Falseの場合
    （Shapefile等）は無条件にEPSG:4326と決めつけず、geometry全体のboundsが経緯度として妥当な範囲
    （経度-180~180・緯度-90~90）に収まる場合のみEPSG:4326と推定し、収まらない場合は座標系を判断
    できないとみなして空のGeoDataFrameを返す（呼び出し側で除外扱いにする）。

    戻り値は (GeoDataFrame, crs_note, counts)。crs_noteは 'assumed_by_bounds' / 'unresolved' /
    None。countsは件数の内訳（null_or_empty / non_polygon / repaired /
    repaired_from_collection / unrepairable）で、利用者への集約表示に使う。"""
    counts = Counter()
    gdf = gpd.read_file(source)

    crs_note = None
    if gdf.crs is not None:
        if gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(epsg=4326)
    elif assume_wgs84_without_crs or len(gdf) == 0:
        gdf = gdf.set_crs(epsg=4326)
    else:
        minx, miny, maxx, maxy = gdf.total_bounds
        if -180 <= minx and maxx <= 180 and -90 <= miny and maxy <= 90:
            gdf = gdf.set_crs(epsg=4326)
            crs_note = "assumed_by_bounds"
        else:
            return empty_hazard_layer(), "unresolved", counts

    geometry = gdf.geometry
    is_null_or_empty = geometry.isna() | geometry.is_empty
    is_polygonal = geometry.geom_type.isin(POLYGON_TYPES)

    usable_mask = ((~is_null_or_empty) & is_polygonal & geometry.is_valid).to_numpy()
    repair_mask = ((~is_null_or_empty) & is_polygonal & (~geometry.is_valid)).to_numpy()

    counts["null_or_empty"] = int(is_null_or_empty.sum())
    counts["non_polygon"] = int(((~is_null_or_empty) & (~is_polygonal)).sum())

    # 有効なジオメトリはそのまま、is_valid=Falseのものだけ直して追加する。
    kept_positions = list(np.flatnonzero(usable_mask))
    kept_geometries = list(geometry.to_numpy()[usable_mask])

    for position in np.flatnonzero(repair_mask):
        repaired, from_collection = repair_invalid_polygon(geometry.iat[position])
        if repaired is None:
            counts["unrepairable"] += 1
            continue
        kept_positions.append(position)
        kept_geometries.append(repaired)
        counts["repaired"] += 1
        if from_collection:
            counts["repaired_from_collection"] += 1

    if not kept_positions:
        return empty_hazard_layer(), crs_note, counts

    kept_rows = gdf.iloc[kept_positions].reset_index(drop=True)
    layer = gpd.GeoDataFrame(
        {
            "hazard_type": derive_feature_hazard_types(kept_rows, hazard_type),
            "geometry": kept_geometries,
        },
        crs="EPSG:4326",
    )
    return layer, crs_note, counts


def _takashio_category_from_stem(stem):
    """高潮データ（例: 最大浸水深_糸満市.shp）のファイル名から、末尾の市町村名部分を除いた
    カテゴリ名を返す（例: '最大浸水深_糸満市' → '最大浸水深'）。区切りが無ければそのまま返す。"""
    prefix, sep, _rest = stem.rpartition("_")
    return prefix if sep else stem


def resolve_layer_hazard_type(base_hazard_type, vector_path, extract_dir):
    """ハザードのカテゴリをhazard_typeへ反映する。国土数値情報等では洪水の中でも「計画規模」
    「想定最大規模」等がZIP展開先直下のサブフォルダで分かれて配布されるため、そのサブフォルダ名を
    そのままカテゴリ名として使う（フォルダ名の意味は独自解釈せず、特定の配布元のディレクトリ名には
    依存しない）。ただし高潮（沖縄県高潮浸水想定データ）は複数のShapefileレイヤーが1つのラッパー
    フォルダにまとめて配布され、フォルダ名だけではレイヤーを区別できないため、フォルダの有無に
    関わらずファイル名から市町村名部分を除いたカテゴリ（例: 最大浸水深/浸水継続時間）を優先して
    用いる。サブフォルダも高潮の命名規則も無い場合はカテゴリなし（基本種別名のみ）とする。"""
    if base_hazard_type == "高潮":
        category = _takashio_category_from_stem(vector_path.stem)
    else:
        rel_parts = vector_path.relative_to(extract_dir).parts
        category = rel_parts[0] if len(rel_parts) > 1 else None
    return f"{base_hazard_type}:{category}" if category else base_hazard_type


def _detect_a31a_hazard_type(filename):
    """国土数値情報A31a（洪水浸水想定区域データ）のファイル名規則
    （例: A31a-25_47_10_GEOJSON.zip）から、年度・都道府県コードには依存せず、
    河川区分（10/20）のみで洪水の種別名を判定する。一致しなければNoneを返す。"""
    match = re.match(r"^A31a-\d+_\d+_(10|20)(?=[_.])", filename)
    if not match:
        return None
    river_classification_labels = {
        "10": "洪水（洪水予報河川・水位周知河川）",
        "20": "洪水（その他の河川）",
    }
    return river_classification_labels[match.group(1)]


def _detect_a33_hazard_type(filename):
    """国土数値情報A33（土砂災害警戒区域データ）のファイル名規則
    （例: A33-25_47_GEOJSON.zip）から、年度・都道府県コードには依存せず土砂災害と判定する。
    ファイル名の先頭部分だけを見るため、Colab等がファイル名重複を避けて末尾に付与する
    '(1)' 等の連番があっても判定できる。一致しなければNoneを返す。"""
    if re.match(r"^A33-\d+_\d+_GEOJSON", filename):
        return "土砂災害"
    return None


def _detect_tsunami_level_hazard_type(filename):
    """沖縄県津波浸水想定データのファイル名規則（例: level1_1cm-30cm.zip、level1～level7）から
    津波と判定する。浸水深の分類はファイル名のlevel番号からは推測せず、実データのShapefile内
    『分類』属性（load_hazard_layerで反映）を優先する。"""
    if re.match(r"^level[1-7](?=[_.])", filename, re.IGNORECASE):
        return "津波"
    return None


def _detect_takashio_hazard_type(filename):
    """沖縄県高潮浸水想定データのファイル名規則
    （例: 47007_takasiosinnsuisoutei_22itoman.zip）から高潮と判定する。"""
    if re.match(r"^\d+_takasiosinnsuisoutei_", filename):
        return "高潮"
    return None


# 既知の公式データ配布ファイル名パターンの判定関数一覧。今後、新しい公式データにも対応する場合は
# ここに判定関数を追加すればよく、巨大なif文へは積み上げない（現時点ではA31a・A33・
# 津波(level1~7)・高潮(takasiosinnsuisoutei)のみ、実データで確認できたファイル名規則として
# 実装している）。
KNOWN_HAZARD_FILENAME_DETECTORS = [
    _detect_a31a_hazard_type,
    _detect_a33_hazard_type,
    _detect_tsunami_level_hazard_type,
    _detect_takashio_hazard_type,
]


def detect_hazard_type(filename):
    """既知の公式データ配布ファイル名パターンからハザード種別名を自動判定する。ZIP・GeoJSON単体の
    どちらのファイル名にも同じ規則を適用する（形式ごとに判定ロジックを二重実装しない）。曖昧な推測は
    せず、既知のパターンのいずれにも一致しない場合はNoneを返す（呼び出し側で利用者入力へフォールバック
    する）。"""
    for detector in KNOWN_HAZARD_FILENAME_DETECTORS:
        hazard_type = detector(filename)
        if hazard_type is not None:
            return hazard_type
    return None


def load_hazard_upload(filename, file_bytes, hazard_type):
    """1つのアップロード（.geojson または国・県等の公式配布ZIP）から、GeoDataFrameを組み立てる。
    ZIPの場合は安全に展開し、内部のディレクトリ構造に関わらず配下の.geojsonと.shp（Shapefile。
    同名の.shx/.dbfが揃っているものに限る）を再帰的に探索する（特定の配布元のファイル名・
    ディレクトリ名には依存しない）。サブフォルダがあればカテゴリとしてhazard_typeに反映し、
    サブフォルダがなければ渡された基本種別名をそのままhazard_typeとする（高潮のみファイル名から
    カテゴリを補う。resolve_layer_hazard_type参照）。利用者には集約したサマリのみ表示し、
    ファイルごとの詳細ログは出さない。"""
    suffix = Path(filename).suffix.lower()
    if suffix not in (".geojson", ".zip"):
        raise ValueError(f"'{filename}' は対応していない形式です。.geojson または .zip を選択してください。")

    layers = []
    empty_file_count = 0
    unresolved_crs_count = 0
    assumed_by_bounds_count = 0
    missing_shapefile_companion_count = 0
    geometry_counts = Counter()

    if suffix == ".geojson":
        layer, crs_note, layer_counts = load_hazard_layer(
            io.BytesIO(file_bytes), hazard_type, assume_wgs84_without_crs=True
        )
        geometry_counts.update(layer_counts)
        if len(layer) == 0:
            empty_file_count += 1
        else:
            layers.append(layer)
    else:
        with tempfile.TemporaryDirectory(prefix="hazard_zip_") as extract_dir_str:
            extract_dir = Path(extract_dir_str)
            safe_extract_zip(file_bytes, extract_dir)
            print(f"'{filename}' を展開しました。")

            geojson_paths = find_files_by_extension(extract_dir, ".geojson")
            shp_paths, missing_shapefile_companion_count, total_shp_found = find_shapefile_layers(extract_dir)

            if not geojson_paths and total_shp_found == 0:
                raise RuntimeError(
                    f"'{filename}' 内に.geojsonまたはShapefile(.shp)が見つかりませんでした。"
                )

            print(f"Shapefileを {len(shp_paths)}レイヤー検出しました。")
            print(f"GeoJSONを {len(geojson_paths)}ファイル検出しました。")

            all_vector_paths = [(p, True) for p in geojson_paths] + [(p, False) for p in shp_paths]

            categories = sorted(
                {
                    p.relative_to(extract_dir).parts[0]
                    for p, _ in all_vector_paths
                    if len(p.relative_to(extract_dir).parts) > 1
                }
            )
            if categories:
                print(f"{len(categories)}カテゴリ（サブフォルダ）を検出しました。")

            for vector_path, is_geojson in all_vector_paths:
                file_hazard_type = resolve_layer_hazard_type(hazard_type, vector_path, extract_dir)
                layer, crs_note, layer_counts = load_hazard_layer(
                    vector_path, file_hazard_type, assume_wgs84_without_crs=is_geojson
                )
                geometry_counts.update(layer_counts)
                if crs_note == "unresolved":
                    unresolved_crs_count += 1
                    continue
                if crs_note == "assumed_by_bounds":
                    assumed_by_bounds_count += 1
                if len(layer) == 0:
                    empty_file_count += 1
                else:
                    layers.append(layer)

    if missing_shapefile_companion_count:
        print(
            f"{missing_shapefile_companion_count}件のShapefileは.shx/.dbfが揃っていないため"
            "読み込めませんでした。"
        )
    if assumed_by_bounds_count:
        print(
            f"{assumed_by_bounds_count}件はCRS情報が無いため、座標値から経緯度データと判断して"
            "EPSG:4326として読み込みました。"
        )
    invalid_polygon_count = geometry_counts["repaired"] + geometry_counts["unrepairable"]
    if invalid_polygon_count:
        print(f"不正なジオメトリ（自己交差等）: {invalid_polygon_count}件")
        print(f"  make_validで修復して採用: {geometry_counts['repaired']}件")
        if geometry_counts["repaired_from_collection"]:
            print(
                "    うちGeometryCollectionからPolygon成分を採用: "
                f"{geometry_counts['repaired_from_collection']}件"
            )
        print(f"  修復できず除外: {geometry_counts['unrepairable']}件")
    if geometry_counts["null_or_empty"]:
        print(
            f"{geometry_counts['null_or_empty']}件は空・欠損のジオメトリのため区域判定対象外です。"
        )
    if geometry_counts["non_polygon"]:
        print(
            f"{geometry_counts['non_polygon']}件はPolygon/MultiPolygon以外のため除外しました"
            "（この区域はハザード判定に含まれません）。"
        )
    if empty_file_count:
        print(f"{empty_file_count}ファイルは有効なPolygon/MultiPolygonを含まないため除外しました。")
    if unresolved_crs_count:
        print(
            f"{unresolved_crs_count}件は座標系を特定できないため除外しました"
            "（CRS情報が無く、座標値も経緯度として妥当な範囲ではありませんでした）。"
        )

    if not layers:
        raise RuntimeError(
            f"'{filename}' から有効なハザード区域(Polygon/MultiPolygon)を1件も読み込めませんでした。"
        )

    combined = gpd.GeoDataFrame(pd.concat(layers, ignore_index=True), crs="EPSG:4326")
    print(f"有効なハザードポリゴンを {len(combined)}件読み込みました。")
    combined_hazard_types = sorted(combined["hazard_type"].unique())
    if len(combined_hazard_types) == 1:
        print(f"hazard_type='{combined_hazard_types[0]}'")
    else:
        print(f"hazard_type: {', '.join(combined_hazard_types)}")
    return combined


# =============================================================================
# Notebookから呼び出す入口
# =============================================================================


def load_uploaded_hazards(uploaded_hazards, ask_hazard_type=None):
    """アップロードされたハザードデータをすべて読み込み、1つのGeoDataFrameへ統合して返す。

    uploaded_hazards  {ファイル名: ファイルのバイト列} （google.colab.files.upload() の戻り値）
    ask_hazard_type   ファイル名からハザード種別を自動判定できなかったときに呼ぶ関数。
                      引数はファイル名で、戻り値が種別名になる。省略した場合や、空の文字列が
                      返った場合は、ファイル名をそのまま種別名として使う（従来と同じ挙動）。

    戻り値は hazard_type / geometry の2列を持つEPSG:4326のGeoDataFrame。
    有効なハザード区域を1件も読み込めなかった場合はRuntimeErrorを送出する。
    """
    layers = []
    load_errors = []

    for filename, file_bytes in uploaded_hazards.items():
        detected_hazard_type = detect_hazard_type(filename)
        if detected_hazard_type is not None:
            hazard_type = detected_hazard_type
            print(f"'{filename}'")
            print(f"→ ハザード種別を自動判定しました: {hazard_type}")
        else:
            answer = ask_hazard_type(filename) if ask_hazard_type is not None else ""
            hazard_type = str(answer).strip() or filename

        # 選択した複数ファイルのうち一部だけ読込失敗した状態のまま候補・ハザード判定へ進めると、
        # 判定結果のFalse（有効な座標で判定した結果、区域外）が「選択した全ハザードデータで
        # 判定済み」と誤認されかねない。そのため1ファイルの読込失敗（形式非対応・ZIP内に
        # geojson/shpが無い・有効なポリゴンが無い等）で即座に生のtracebackで止めはしないが、
        # ここでは握りつぶさず内容を集約し、全ファイルの検査後に1件でも失敗があれば処理を
        # 停止する（5種類全部を必須にはしないが、選択したファイルは全て正常に読めることを
        # 結果生成の条件にする。fail-closed）。
        try:
            layers.append(load_hazard_upload(filename, file_bytes, hazard_type))
        except (RuntimeError, ValueError) as error:
            load_errors.append((filename, str(error)))

    if load_errors:
        error_list = "\n".join(f"  - {name}: {reason}" for name, reason in load_errors)
        raise RuntimeError(
            "ハザードデータの一部を読み込めなかったため、判定結果が不完全になることを防ぐため"
            "処理を停止しました。\n"
            "\n読み込めなかったファイル:\n"
            f"{error_list}\n"
            "\nファイルを確認するか、今回判定に使用しないファイルは選択から外して、"
            "「ハザードデータ読込」セルを再実行してください。"
        )

    hazard_gdf = None
    if layers:
        hazard_gdf = gpd.GeoDataFrame(pd.concat(layers, ignore_index=True), crs="EPSG:4326")

    if hazard_gdf is None or len(hazard_gdf) == 0:
        raise RuntimeError(
            "ENABLE_HAZARD_CHECK=True ですが、有効なハザード区域(Polygon/MultiPolygon)を1件も読み込めませんでした。"
            "GeoJSON/ZIPファイルが正しくアップロードされているか、ジオメトリ形式を確認してください。"
            "ハザード判定を行わない場合は、上の「利用者設定」で ENABLE_HAZARD_CHECK=False にしてください。"
        )

    print(f"ハザードデータを合計 {len(hazard_gdf)}件読み込みました。")
    return hazard_gdf
