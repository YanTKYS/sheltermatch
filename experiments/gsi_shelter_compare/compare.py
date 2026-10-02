"""BODIKと国土地理院（GSI）の指定緊急避難場所データを比較する実験。

本番の sheltermatch.ipynb / src/ からは独立しており、どこからも import されない。比較・評価だけが目的で、
データソースの切り替えや、どちらかのデータを正解とみなす判定は行わない。詳しくは README.md を参照。

使い方:
    python3 -m pip install requests geopy        # 未導入の場合のみ
    python3 experiments/gsi_shelter_compare/compare.py [--output-dir DIR]

公開データ（BODIK Data API・地理院タイル）を読み取るだけで、APIキーは不要。
"""
import argparse
import csv
import sys
import time
from pathlib import Path

from compare_logic import (
    COMPARED_SCOPES, EXACT_MAX_DISTANCE_M, GSI_LAYERS, NEAR_DISTANCE_M, TARGET_CITY,
    Facility, assign_gsi_scope, compare_facilities, gsi_facility_from_feature, merge_gsi_layers,
    parse_coordinate, summarize, tiles_for_points,
)

# sheltermatch.ipynb（避難所取得・正規化セル）と同じ取得先。糸満市 指定緊急避難場所データセット。
BODIK_BASE_URL = "https://data.bodik.jp"
BODIK_RESOURCE_ID = "3132a0a4-f522-4b2d-bf18-f106d8b3a5ae"

GSI_TILE_URL = "https://cyberjapandata.gsi.go.jp/xyz/{layer}/{z}/{x}/{y}.geojson"
GSI_ZOOM = 10
USER_AGENT = "sheltermatch-gsi-shelter-compare (experiment; read-only)"

# BODIK（自治体標準ODS）の日本語列名 → 比較用の列。本番Notebookが使う name/latitude/longitude に加えて、住所も使う。
BODIK_COLUMNS = {
    "name": ("名称", "name"),
    "address": ("住所", "address"),
    "latitude": ("緯度", "latitude"),
    "longitude": ("経度", "longitude"),
}


# ---------------------------------------------------------------------------
# 取得
# ---------------------------------------------------------------------------

def fetch_bodik_records(session, base_url=BODIK_BASE_URL, resource_id=BODIK_RESOURCE_ID, page_size=1000):
    """BODIKのCKAN Data API（datastore_search）から全件取得する（sheltermatch.ipynb と同じ取得方法）。"""
    endpoint = f"{base_url}/api/action/datastore_search"
    records, offset, total = [], 0, None
    while True:
        response = session.get(endpoint, params={"resource_id": resource_id, "limit": page_size, "offset": offset},
                               timeout=30)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success"):
            raise RuntimeError("CKAN APIレスポンスが success=false を返しました。")
        result = payload.get("result")
        if result is None or "records" not in result:
            raise RuntimeError("CKAN APIレスポンスに result.records が含まれていません。")
        records.extend(result["records"])
        if total is None:
            total = result.get("total", len(result["records"]))
        offset += len(result["records"])
        if not result["records"] or offset >= total:
            break
    if not records:
        raise RuntimeError("BODIK APIの取得結果が0件でした。")
    return records


def bodik_facilities(records):
    """BODIKのレコードを Facility へ。座標が不正な施設も、比較から落とさず座標なしで残す。"""
    def pick(record, key):
        for column in BODIK_COLUMNS[key]:
            if column in record:
                return record[column]
        return None

    facilities = []
    for record in records:
        lat, lon = parse_coordinate(pick(record, "latitude"), pick(record, "longitude"))
        facilities.append(Facility("bodik", pick(record, "name"), pick(record, "address"), lat, lon))
    return facilities


def fetch_gsi_tile(session, layer, x, y, retries=3):
    """地理院タイル1枚のFeatureを返す。データの無いタイルは404が返るため、空として扱う。
    それ以外の失敗は、取りこぼしに気付けるよう例外にする。"""
    url = GSI_TILE_URL.format(layer=layer, z=GSI_ZOOM, x=x, y=y)
    last_error = None
    for attempt in range(retries):
        try:
            response = session.get(url, timeout=30)
            if response.status_code == 404:
                return []
            response.raise_for_status()
            return response.json().get("features", [])
        except Exception as error:  # 通信エラー・JSON不正など
            last_error = error
            time.sleep(1 + attempt)
    raise RuntimeError(f"タイルを取得できませんでした: {url} ({last_error})")


def fetch_gsi_features(session, tiles, layers=tuple(GSI_LAYERS), pause_s=0.2):
    """指定タイル×指定レイヤーのFeatureを (layer, feature) のリストで返す。"""
    fetched, tile_hits = [], 0
    for layer in layers:
        for x, y in tiles:
            features = fetch_gsi_tile(session, layer, x, y)
            if features:
                tile_hits += 1
            fetched.extend((layer, f) for f in features)
            time.sleep(pause_s)
    return fetched, tile_hits


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------

def fmt(value, digits=None):
    if value is None:
        return ""
    return f"{value:.{digits}f}" if digits is not None else value


COMPARISON_COLUMNS = [
    "status", "bodik_name", "bodik_address", "bodik_latitude", "bodik_longitude",
    "gsi_name", "gsi_address", "gsi_latitude", "gsi_longitude", "distance_m", "gsi_layers", "notes",
    "bodik_normalized_name", "bodik_normalized_address", "gsi_normalized_name", "gsi_normalized_address", "gsi_scope",
]


def write_comparison_csv(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, COMPARISON_COLUMNS)
        writer.writeheader()
        for r in rows:
            b, g = r.bodik, r.gsi
            writer.writerow({
                "status": r.status,
                "bodik_name": b.original_name if b else "", "bodik_address": b.original_address if b else "",
                "bodik_latitude": fmt(b.latitude if b else None), "bodik_longitude": fmt(b.longitude if b else None),
                "gsi_name": g.original_name if g else "", "gsi_address": g.original_address if g else "",
                "gsi_latitude": fmt(g.latitude if g else None), "gsi_longitude": fmt(g.longitude if g else None),
                "distance_m": fmt(r.distance_m, 1),
                "gsi_layers": ";".join(g.gsi_layers) if g else "",
                "notes": " | ".join(r.notes),
                "bodik_normalized_name": b.normalized_name if b else "",
                "bodik_normalized_address": b.normalized_address if b else "",
                "gsi_normalized_name": g.normalized_name if g else "",
                "gsi_normalized_address": g.normalized_address if g else "",
                "gsi_scope": g.scope if g else "",
            })


GSI_UNIQUE_COLUMNS = (["scope", "original_name", "normalized_name", "original_address", "normalized_address",
                       "latitude", "longitude", "gsi_layers", "gsi_feature_count"]
                      + [f"disaster{i}" for i in range(1, 9)] + ["remarks", "notes"])


def write_gsi_unique_csv(path, unique_gsi):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, GSI_UNIQUE_COLUMNS)
        writer.writeheader()
        for g in unique_gsi:
            row = {
                "scope": g.scope, "original_name": g.original_name, "normalized_name": g.normalized_name,
                "original_address": g.original_address, "normalized_address": g.normalized_address,
                "latitude": fmt(g.latitude), "longitude": fmt(g.longitude),
                "gsi_layers": ";".join(g.gsi_layers), "gsi_feature_count": g.gsi_feature_count,
                "remarks": g.remarks, "notes": " | ".join(g.notes),
            }
            row.update({k: fmt(v) for k, v in g.disasters.items()})
            writer.writerow(row)


def print_distance_stats(label, stats):
    if stats is None:
        print(f"  {label}: 対象なし")
        return
    over = " / ".join(f"{t}m超 {n}件" for t, n in stats["over"].items())
    print(f"  {label}（{stats['count']}件）: 最大 {stats['max']:.1f}m / 中央値 {stats['median']:.1f}m / "
          f"平均 {stats['mean']:.1f}m")
    print(f"    {over}")


def print_summary(summary, bodik, compared_gsi, rows, exact_max_m, near_m):
    c = summary["counts"]
    n_b, n_g = len(bodik), len(compared_gsi)
    print()
    print("=" * 70)
    print("比較結果の要約（事実の整理です。どちらのデータが正しいか・どちらを使うべきかの判断はしません）")
    print("=" * 70)
    print(f"施設数: BODIK {n_b}件 / GSI（比較対象のユニーク施設）{n_g}件（差 {n_g - n_b:+d}件）")
    print(f"対応付け: exact_match {c['exact_match']}件 / review_needed {c['review_needed']}件 / "
          f"bodik_only {c['bodik_only']}件 / gsi_only {c['gsi_only']}件")
    print(f"  BODIKのうち exact_match は {c['exact_match']}/{n_b}件、GSIのうち exact_match は {c['exact_match']}/{n_g}件")

    for status, label in (("bodik_only", "BODIKだけにある施設"), ("gsi_only", "GSIだけにある施設")):
        items = [r for r in rows if r.status == status]
        print(f"\n{label}: {len(items)}件")
        for r in items:
            f = r.bodik or r.gsi
            print(f"  - {f.original_name}（{f.original_address or '住所なし'}）")

    print(f"\nreview_needed: {c['review_needed']}件")
    for reason, count in sorted(summary["review_reasons"].items()):
        print(f"  - {reason}: {count}件")
    for r in rows:
        if r.status == "review_needed":
            shared = "［1対多］" if any("1対多" in n for n in r.notes) else ""
            print(f"    · BODIK「{r.bodik.original_name}」 ⇔ GSI「{r.gsi.original_name}」"
                  f"（距離 {fmt(r.distance_m, 1) or '不明'}m）{shared}")
    print("  ※ ［1対多］は、同じGSI施設（またはBODIK施設）が他の行とも対応している行です。")

    print("\n座標差（BODIK座標とGSI座標の測地線距離）")
    print_distance_stats("exact_match", summary["exact_distance"])
    print_distance_stats("対応付けできた全組（exact_match + review_needed）", summary["paired_distance"])
    print(f"  ※ exact_match は座標差 {exact_max_m:g}m 以内のものに限る。閾値は目視確認の目安で、正誤の判定ではありません。")

    print("\n名称・住所の表記差（対応付けできた組。元の文字列を比べた結果）")
    for key, label in (("exact_name", "exact_match の名称"), ("exact_address", "exact_match の住所"),
                       ("paired_name", "全組の名称"), ("paired_address", "全組の住所")):
        s = summary[key]
        print(f"  {label}: 元の表記が同一 {s['identical']}件 / 正規化後にだけ一致 "
              f"{s['identical_after_normalization_only']}件 / 異なる {s['different']}件")
    print(f"  ※ 正規化は NFKC・ハイフン類の統一・空白の整理のみ。語句の削除や意味的な変換はしていません。"
          f"（名称が異なる組の近接判定は {near_m:g}m 以内）")
    print()
    print("注意: GSIのデータは市町村が登録した情報で、最新でない・未掲載の場合があります。最新かつ詳細な状況は")
    print("      当該市町村に確認してください。この比較はGSIを正解とするものではありません。")


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output-dir", default=str(Path(__file__).resolve().parent / "output"),
                        help="結果CSVの出力先（既定: このフォルダ内の output/。リポジトリにはコミットしない）")
    parser.add_argument("--ring", type=int, default=1, help="BODIKの座標を含むタイルの周囲に追加で取得するタイル数（既定: 1）")
    parser.add_argument("--exact-max-m", type=float, default=EXACT_MAX_DISTANCE_M,
                        help=f"exact_match とする座標差の上限（m、既定: {EXACT_MAX_DISTANCE_M:g}）")
    parser.add_argument("--near-m", type=float, default=NEAR_DISTANCE_M,
                        help=f"名称が異なっても review_needed にする近接距離（m、既定: {NEAR_DISTANCE_M:g}）")
    args = parser.parse_args(argv)

    import requests  # 通信を行うのはこのファイルだけ（compare_logic.py は通信しない）

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    print("BODIK Data API から指定緊急避難場所を取得します…")
    bodik = bodik_facilities(fetch_bodik_records(session))
    bodik_with_coords = [b for b in bodik if b.latitude is not None]
    print(f"BODIK施設数: {len(bodik)}件（座標が不正・欠損: {len(bodik) - len(bodik_with_coords)}件）")

    tiles = tiles_for_points([(b.latitude, b.longitude) for b in bodik_with_coords], GSI_ZOOM, args.ring)
    print(f"GSIのz={GSI_ZOOM}タイル: {len(tiles)}枚×{len(GSI_LAYERS)}レイヤー（BODIKの座標を含むタイルと周囲{args.ring}枚）")
    fetched, tile_hits = fetch_gsi_features(session, tiles)
    print(f"GSI取得Feature数（災害種別レイヤー重複込み・住所での絞り込み前）: {len(fetched)}件（データのあったタイル {tile_hits}枚）")

    features, skipped = [], 0
    for layer, feature in fetched:
        facility = gsi_facility_from_feature(layer, feature)
        if facility is None:
            skipped += 1
        else:
            features.append(facility)
    if skipped:
        print(f"  Point以外のため読み飛ばしたFeature: {skipped}件")

    unique_gsi, split_count = merge_gsi_layers(features)
    assign_gsi_scope(unique_gsi, bodik, TARGET_CITY, near_m=args.near_m)
    compared_gsi = [g for g in unique_gsi if g.scope in COMPARED_SCOPES]
    print(f"GSIユニーク施設数（レイヤー間の重複を統合後・絞り込み前）: {len(unique_gsi)}件")
    for scope in (*COMPARED_SCOPES, "excluded_address_missing_far", "excluded_other_address"):
        n = sum(1 for g in unique_gsi if g.scope == scope)
        mark = "比較対象" if scope in COMPARED_SCOPES else "比較対象外"
        print(f"  {scope}: {n}件（{mark}）")
    print(f"GSI比較対象のユニーク施設数: {len(compared_gsi)}件"
          f"（Featureとしては{sum(g.gsi_feature_count for g in compared_gsi)}件）")
    if split_count:
        print(f"  名称・住所が同じでも座標が離れていたため統合しなかった施設: {split_count}件")

    rows = compare_facilities(bodik, compared_gsi, args.exact_max_m, args.near_m)
    summary = summarize(rows)
    c = summary["counts"]
    print(f"\nexact_match: {c['exact_match']}  bodik_only: {c['bodik_only']}  "
          f"gsi_only: {c['gsi_only']}  review_needed: {c['review_needed']}")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_comparison_csv(output_dir / "gsi_bodik_comparison.csv", rows)
    write_gsi_unique_csv(output_dir / "gsi_unique_shelters.csv", unique_gsi)
    print(f"出力: {output_dir / 'gsi_bodik_comparison.csv'}")
    print(f"出力: {output_dir / 'gsi_unique_shelters.csv'}")

    print_summary(summary, bodik, compared_gsi, rows, args.exact_max_m, args.near_m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
