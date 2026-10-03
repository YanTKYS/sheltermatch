"""BODIKとGSIの指定緊急避難場所を比較するための、通信を行わない純粋な処理。

外部への通信・ファイル入出力は compare.py が担当し、ここには含めない（架空データで
test/test_gsi_shelter_compare.py から確認できるようにするため）。標準ライブラリと geopy だけを使う。

この比較は、どちらのデータが正しいかを判定するものではない。2つの公開データの差異を事実として整理する
だけで、自動的に同一と断定するのは「名称・住所・座標がそろって明らかに一致するもの」に限る。
"""
import math
import re
import statistics
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

from geopy.distance import geodesic

# 対象の自治体名（住所に含まれる市名の判定に使う）は、自治体ごとの設定ファイル（configs/<設定名>.json の
# municipality.name）から呼び出し側が渡す。このモジュールは特定の自治体に依存しない。

# 「明らかに同一」と扱う座標差の上限（m）。名称・住所が一致していても、これを超える場合は review_needed にする。
EXACT_MAX_DISTANCE_M = 30.0
# 名称が異なっていても「かなり近い位置」として review_needed にする距離（m）。
NEAR_DISTANCE_M = 100.0
# GSI内の複数レイヤーで同じ施設を統合するときの座標差の上限（m）。名称・住所が一致していても超えたら統合しない。
GSI_MERGE_MAX_DISTANCE_M = 5.0
# 住所が欠損しているGSIのFeatureを「糸満市付近」とみなす範囲（BODIKの座標範囲からの余白、度。約2km）。
ADDRESS_MISSING_MARGIN_DEG = 0.02

DISTANCE_THRESHOLDS_M = (10, 30, 100)

# GSI指定緊急避難場所のレイヤー（国土地理院 地理院タイル仕様）
GSI_LAYERS = {
    "skhb01": "洪水",
    "skhb02": "崖崩れ、土石流及び地滑り",
    "skhb03": "高潮",
    "skhb04": "地震",
    "skhb05": "津波",
    "skhb06": "大規模な火事",
    "skhb07": "内水氾濫",
    "skhb08": "火山現象",
}

# 一般的なハイフン類・ダッシュ類（長音記号「ー」は別の文字なので含めない）
_HYPHEN_CHARS = "‐‑‒–—―−－﹣⁃"
_HYPHEN_RE = re.compile(f"[{_HYPHEN_CHARS}]")
_SPACE_RE = re.compile(r"\s+")


def normalize_text(value):
    """比較用の最小限の正規化: NFKC、ハイフン類の統一、空白（全角含む）の整理、前後空白の除去。
    語句の削除や「○○小学校」と「○○小」を同じとみなすような意味的な変換は行わない。"""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = _HYPHEN_RE.sub("-", text)
    text = _SPACE_RE.sub(" ", text).strip()
    return text


def parse_coordinate(latitude, longitude):
    """緯度・経度を (lat, lon) の数値へ。欠損・不正・範囲外（日本の周辺を大きく外れるもの）は (None, None)。"""
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return None, None
    if math.isnan(lat) or math.isnan(lon) or not (20 <= lat <= 46 and 122 <= lon <= 154):
        return None, None
    return lat, lon


def distance_m(a, b):
    """2点の測地線距離（m）。どちらかの座標が欠損していれば None。"""
    if a is None or b is None or a.latitude is None or b.latitude is None:
        return None
    return geodesic((a.latitude, a.longitude), (b.latitude, b.longitude)).meters


@dataclass
class Facility:
    """BODIK・GSIいずれの施設にも使う比較用の形。original_* は元データのまま、normalized_* は比較用。"""
    source: str
    original_name: str
    original_address: str
    latitude: float = None
    longitude: float = None
    gsi_layers: tuple = ()
    gsi_feature_count: int = 0
    disasters: dict = field(default_factory=dict)
    remarks: str = ""
    scope: str = ""
    notes: list = field(default_factory=list)

    def __post_init__(self):
        self.original_name = "" if self.original_name is None else str(self.original_name)
        self.original_address = "" if self.original_address is None else str(self.original_address)
        self.normalized_name = normalize_text(self.original_name)
        self.normalized_address = normalize_text(self.original_address)


# ---------------------------------------------------------------------------
# タイル範囲
# ---------------------------------------------------------------------------

def tile_xy(latitude, longitude, zoom=10):
    """緯度・経度を含むXYZ（Webメルカトル）タイルの (x, y)。"""
    n = 2 ** zoom
    x = int((longitude + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(latitude))) / math.pi) / 2.0 * n)
    return x, y


def tiles_for_points(points, zoom=10, ring=1):
    """座標列から、必要なタイルの (x, y) を重複なしで返す。各点を含むタイルに加えて、周囲 ring 枚分の
    隣接タイルも含める（BODIKに無い近隣施設を取り逃さないため）。"""
    tiles = set()
    for lat, lon in points:
        x, y = tile_xy(lat, lon, zoom)
        for dx in range(-ring, ring + 1):
            for dy in range(-ring, ring + 1):
                tiles.add((x + dx, y + dy))
    return sorted(tiles)


# ---------------------------------------------------------------------------
# GSI: Feature の解釈・レイヤー間の重複整理・対象範囲の振り分け
# ---------------------------------------------------------------------------

def gsi_facility_from_feature(layer, feature):
    """GSIのGeoJSON Featureを Facility（1レイヤー分）へ。Point以外のジオメトリは None。"""
    geometry = feature.get("geometry") or {}
    if geometry.get("type") != "Point":
        return None
    coordinates = geometry.get("coordinates") or []
    if len(coordinates) < 2:
        return None
    properties = feature.get("properties") or {}
    lat, lon = parse_coordinate(coordinates[1], coordinates[0])
    facility = Facility(
        source="gsi",
        original_name=properties.get("name"),
        original_address=properties.get("address"),
        latitude=lat,
        longitude=lon,
        gsi_layers=(layer,),
        gsi_feature_count=1,
        disasters={f"disaster{i}": properties.get(f"disaster{i}") for i in range(1, 9)},
        remarks="" if properties.get("remarks") is None else str(properties.get("remarks")),
    )
    return facility


def merge_gsi_layers(features, max_distance_m=GSI_MERGE_MAX_DISTANCE_M):
    """災害種別ごとのレイヤーに分かれたGSIの Facility を、同じ施設とみなせるものだけ統合する。

    統合するのは、正規化後の名称と住所がともに一致し（名称が空のものは統合しない）、座標が
    max_distance_m 以内（または双方とも座標なし）のものだけ。条件を満たさないものは別の施設のまま残す。
    戻り値は (統合後の Facility のリスト, 名称・住所が同じなのに座標が離れていて統合しなかった件数)。
    """
    groups = defaultdict(list)
    standalone = []
    for feature in features:
        if feature.normalized_name:
            groups[(feature.normalized_name, feature.normalized_address)].append(feature)
        else:
            standalone.append(feature)

    merged = []
    split_by_distance = 0
    for group in groups.values():
        clusters = []
        for feature in group:
            for cluster in clusters:
                dist = distance_m(cluster[0], feature)
                same_place = (dist is None and cluster[0].latitude is None and feature.latitude is None) or (
                    dist is not None and dist <= max_distance_m)
                if same_place:
                    cluster.append(feature)
                    break
            else:
                clusters.append([feature])
        if len(clusters) > 1:
            split_by_distance += len(clusters) - 1
        merged.extend(_merge_cluster(cluster) for cluster in clusters)
    merged.extend(standalone)
    # 出力順を安定させる（名称・住所・座標順）
    merged.sort(key=lambda f: (f.normalized_name, f.normalized_address, f.latitude or 0, f.longitude or 0))
    return merged, split_by_distance


def _merge_cluster(cluster):
    first = cluster[0]
    layers = sorted({layer for f in cluster for layer in f.gsi_layers})
    disasters, conflicts = {}, []
    for key in (f"disaster{i}" for i in range(1, 9)):
        values = [f.disasters.get(key) for f in cluster if f.disasters.get(key) not in (None, "")]
        disasters[key] = values[0] if values else None
        if len(set(map(str, values))) > 1:
            conflicts.append(key)
    remarks = [r for r in dict.fromkeys(f.remarks for f in cluster) if r]
    original_names = list(dict.fromkeys(f.original_name for f in cluster))
    original_addresses = list(dict.fromkeys(f.original_address for f in cluster))
    merged = Facility(
        source="gsi",
        original_name=first.original_name,
        original_address=first.original_address,
        latitude=first.latitude,
        longitude=first.longitude,
        gsi_layers=tuple(layers),
        gsi_feature_count=sum(f.gsi_feature_count for f in cluster),
        disasters=disasters,
        remarks=" / ".join(remarks),
    )
    if len(cluster) > 1:
        merged.notes.append(f"{len(cluster)}件のFeatureを統合")
    if len(original_names) > 1 or len(original_addresses) > 1:
        merged.notes.append("統合したFeature間で名称・住所の元の表記が異なる（正規化後は一致）")
    if conflicts:
        merged.notes.append("統合したFeature間で値が異なる属性: " + ",".join(conflicts))
    return merged


def assign_gsi_scope(unique_gsi, bodik, city, margin_deg=ADDRESS_MISSING_MARGIN_DEG,
                     near_m=NEAR_DISTANCE_M):
    """GSIのユニーク施設ごとに、比較の対象にするか（scope）を決める。住所に市名が無いだけでは除外しない。

    - in_city: 住所に市名を含む（比較対象）
    - address_missing_near: 住所が空で、BODIKの座標範囲（余白つき）の中にある（比較対象。住所は比較できない）
    - other_address_near_bodik: 住所に市名は無いが、BODIKの施設と名称が一致する、またはBODIKの施設から
      near_m 以内にある（比較対象。住所が異なるため review_needed になる）
    - excluded_address_missing_far: 住所が空で、範囲の外にある（比較対象外）
    - excluded_other_address: 上のどれでもない（比較対象外）
    """
    city = normalize_text(city)
    coords = [(b.latitude, b.longitude) for b in bodik if b.latitude is not None]
    if coords:
        lat_min, lat_max = min(c[0] for c in coords) - margin_deg, max(c[0] for c in coords) + margin_deg
        lon_min, lon_max = min(c[1] for c in coords) - margin_deg, max(c[1] for c in coords) + margin_deg
    else:
        lat_min = lat_max = lon_min = lon_max = None
    bodik_names = {b.normalized_name for b in bodik if b.normalized_name}

    for g in unique_gsi:
        if city and city in g.normalized_address:
            g.scope = "in_city"
        elif not g.normalized_address:
            inside = (g.latitude is not None and lat_min is not None
                      and lat_min <= g.latitude <= lat_max and lon_min <= g.longitude <= lon_max)
            g.scope = "address_missing_near" if inside else "excluded_address_missing_far"
        else:
            distances = [distance_m(g, b) for b in bodik]
            near = g.normalized_name in bodik_names or any(d is not None and d <= near_m for d in distances)
            g.scope = "other_address_near_bodik" if near else "excluded_other_address"
    return unique_gsi


COMPARED_SCOPES = ("in_city", "address_missing_near", "other_address_near_bodik")


# ---------------------------------------------------------------------------
# BODIK と GSI の突き合わせ
# ---------------------------------------------------------------------------

@dataclass
class ComparisonRow:
    status: str                    # exact_match / bodik_only / gsi_only / review_needed
    bodik: Facility = None
    gsi: Facility = None
    distance_m: float = None
    notes: list = field(default_factory=list)
    reason: str = ""               # review_needed の分類（集計用）


def _address_state(b, g):
    if not b.normalized_address or not g.normalized_address:
        return "unknown"
    return "equal" if b.normalized_address == g.normalized_address else "differ"


def classify_pair(b, g, exact_max_m=EXACT_MAX_DISTANCE_M, near_m=NEAR_DISTANCE_M):
    """BODIKの1施設とGSIの1施設の関係を (tier, reason, distance_m) で返す。対応と見なせなければ tier=None。

    tier 1: 名称・住所が正規化後に一致し、座標差も exact_max_m 以内（exact_match 候補）
    tier 2: 名称は一致するが、住所の相違・欠損、または座標差が大きい（review_needed）
    tier 3: 住所は一致するが名称が異なる（review_needed）
    tier 4: 名称は異なるが near_m 以内にある（review_needed）
    """
    dist = distance_m(b, g)
    name_equal = bool(b.normalized_name) and b.normalized_name == g.normalized_name
    address = _address_state(b, g)
    if name_equal:
        if address == "equal":
            if dist is None:
                return 2, "座標を比較できない", dist
            if dist <= exact_max_m:
                return 1, "", dist
            return 2, f"名称・住所は一致するが座標差が{exact_max_m:g}mを超える", dist
        if address == "differ":
            return 2, "名称は一致するが住所が異なる", dist
        return 2, "名称は一致するが住所を比較できない（どちらかが欠損）", dist
    if address == "equal":
        return 3, "住所は一致するが名称が異なる", dist
    if dist is not None and dist <= near_m:
        return 4, f"名称は異なるが{near_m:g}m以内に近接している", dist
    return None, "", dist


def compare_facilities(bodik, gsi, exact_max_m=EXACT_MAX_DISTANCE_M, near_m=NEAR_DISTANCE_M):
    """BODIKとGSI（比較対象のユニーク施設）を突き合わせ、ComparisonRow のリストを返す。

    exact_match は常に1対1で、各施設は1つの行にだけ現れる。まず関係の強い順（tier→座標差）に1対1で対応付け、
    対応付けられなかった施設のうち、すでに別の施設と対応付けた相手との関係が残るものは、その相手を共有する
    review_needed の行にする（1対多。この行では相手側の施設が他の行と重複して現れる）。関係が全く無い施設だけが
    bodik_only / gsi_only になる。tier 1 に該当する相手が一方側に複数ある場合は、
    どれが正しい相手か断定できないため exact_match にせず review_needed にする。
    """
    pairs = []
    for bi, b in enumerate(bodik):
        for gi, g in enumerate(gsi):
            tier, reason, dist = classify_pair(b, g, exact_max_m, near_m)
            if tier is not None:
                pairs.append([tier, bi, gi, reason, dist])

    tier1_by_bodik, tier1_by_gsi = defaultdict(int), defaultdict(int)
    for tier, bi, gi, _, _ in pairs:
        if tier == 1:
            tier1_by_bodik[bi] += 1
            tier1_by_gsi[gi] += 1
    for pair in pairs:
        if pair[0] == 1 and (tier1_by_bodik[pair[1]] > 1 or tier1_by_gsi[pair[2]] > 1):
            pair[0], pair[3] = 2, "名称・住所・座標が一致する相手が複数あり、同一施設と断定できない"

    pairs.sort(key=lambda p: (p[0], math.inf if p[4] is None else p[4], p[1], p[2]))
    used_bodik, used_gsi, rows = set(), set(), []
    for tier, bi, gi, reason, dist in pairs:
        if bi in used_bodik or gi in used_gsi:
            continue
        used_bodik.add(bi)
        used_gsi.add(gi)
        b, g = bodik[bi], gsi[gi]
        notes = list(g.notes)
        if g.scope in _SCOPE_NOTES:
            notes.append(_SCOPE_NOTES[g.scope])
        if tier == 1:
            rows.append(ComparisonRow("exact_match", b, g, dist, notes))
        else:
            rows.append(ComparisonRow("review_needed", b, g, dist, [reason] + notes, reason))

    # 1対1で対応付けられなかった施設のうち、すでに別の施設と対応付けた相手と関係が残るもの
    # （例: 同じ住所に「○○小学校校舎」と「○○小学校グラウンド」があり、相手側は「○○小学校」1件）は、
    # bodik_only / gsi_only にせず、相手を共有する review_needed の行にする。
    first_pair_for_bodik, first_pair_for_gsi = {}, {}
    for pair in pairs:
        first_pair_for_bodik.setdefault(pair[1], pair)
        first_pair_for_gsi.setdefault(pair[2], pair)

    def shared_row(pair, b, g):
        _, _, _, reason, dist = pair
        notes = [reason, "同じ施設が他の行でも対応付けられている（1対多）"] + list(g.notes)
        if g.scope in _SCOPE_NOTES:
            notes.append(_SCOPE_NOTES[g.scope])
        return ComparisonRow("review_needed", b, g, dist, notes, reason)

    for bi, b in enumerate(bodik):
        if bi in used_bodik:
            continue
        if bi in first_pair_for_bodik:
            pair = first_pair_for_bodik[bi]
            rows.append(shared_row(pair, b, gsi[pair[2]]))
        else:
            rows.append(ComparisonRow("bodik_only", b, None, None, [_nearest_note(b, gsi, "GSI")]))
    for gi, g in enumerate(gsi):
        if gi in used_gsi:
            continue
        if gi in first_pair_for_gsi:
            pair = first_pair_for_gsi[gi]
            rows.append(shared_row(pair, bodik[pair[1]], g))
        else:
            notes = list(g.notes)
            if g.scope in _SCOPE_NOTES:
                notes.append(_SCOPE_NOTES[g.scope])
            notes.append(_nearest_note(g, bodik, "BODIK"))
            rows.append(ComparisonRow("gsi_only", None, g, None, notes))

    rows.sort(key=lambda r: (_STATUS_ORDER[r.status], (r.bodik or r.gsi).normalized_name))
    return rows


_STATUS_ORDER = {"exact_match": 0, "review_needed": 1, "bodik_only": 2, "gsi_only": 3}
_SCOPE_NOTES = {
    "address_missing_near": "GSI側の住所が空欄（糸満市付近の座標のため比較対象にした）",
    "other_address_near_bodik": "GSI側の住所に「糸満市」を含まない（BODIKと名称が同じ、または近接のため比較対象にした）",
}


def _nearest_note(facility, candidates, label):
    """対応付けられなかった施設について、参考として最寄りの相手側施設を示す（同一施設という意味ではない）。"""
    best, best_dist = None, None
    for c in candidates:
        d = distance_m(facility, c)
        if d is not None and (best_dist is None or d < best_dist):
            best, best_dist = c, d
    if best is None:
        return f"最寄りの{label}施設: 座標を比較できない"
    return f"参考: 最寄りの{label}施設は「{best.original_name}」（{best_dist:.0f}m。同一施設とは判断していない）"


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------

def distance_stats(values, thresholds=DISTANCE_THRESHOLDS_M):
    """座標間距離の最大・中央値・平均と、各閾値を超える件数。値が無ければ None。"""
    values = [v for v in values if v is not None]
    if not values:
        return None
    return {
        "count": len(values),
        "max": max(values),
        "median": statistics.median(values),
        "mean": statistics.fmean(values),
        "over": {t: sum(1 for v in values if v > t) for t in thresholds},
    }


def summarize(rows):
    """比較結果の件数・座標差・表記差を集計する（良し悪しの判定はしない）。"""
    counts = {s: 0 for s in _STATUS_ORDER}
    for r in rows:
        counts[r.status] += 1
    exact = [r for r in rows if r.status == "exact_match"]
    review = [r for r in rows if r.status == "review_needed"]
    paired = exact + review

    def surface_difference(pair_rows, attr):
        same_raw = sum(1 for r in pair_rows if getattr(r.bodik, f"original_{attr}") == getattr(r.gsi, f"original_{attr}"))
        same_norm_only = sum(
            1 for r in pair_rows
            if getattr(r.bodik, f"original_{attr}") != getattr(r.gsi, f"original_{attr}")
            and getattr(r.bodik, f"normalized_{attr}") == getattr(r.gsi, f"normalized_{attr}"))
        return {"identical": same_raw, "identical_after_normalization_only": same_norm_only,
                "different": len(pair_rows) - same_raw - same_norm_only}

    reasons = defaultdict(int)
    for r in review:
        reasons[r.reason] += 1
    return {
        "counts": counts,
        "exact_distance": distance_stats([r.distance_m for r in exact]),
        "paired_distance": distance_stats([r.distance_m for r in paired]),
        "exact_name": surface_difference(exact, "name"),
        "exact_address": surface_difference(exact, "address"),
        "paired_name": surface_difference(paired, "name"),
        "paired_address": surface_difference(paired, "address"),
        "review_reasons": dict(reasons),
    }
