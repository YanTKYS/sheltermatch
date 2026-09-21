"""性能検証用の架空共通住民CSV（100 / 500 / 1000件）を再現可能に生成するスクリプト。

sheltermatch.ipynb の実運用規模での処理時間・出力内容を確認するためのものであり、
address_geocode.ipynb は通さない（住所→座標変換の正確性は目的外のため）。
共通住民CSVの正式5列 (resident_id,address,latitude,longitude,geocode_status) で出力し、
実在の住所・住民データは一切使用しない。

使い方:
    python3 experiments/generate_performance_samples.py

experiments/performance/residents_100.csv
experiments/performance/residents_500.csv
experiments/performance/residents_1000.csv
を生成する（固定シードのため、実行するたびに同じ内容になる）。
"""
import csv
from pathlib import Path

import numpy as np

OUTPUT_DIR = Path(__file__).resolve().parent / "performance"

# 固定シード。実行するたびに同じデータが生成されることを保証する。
SEED = 20240921

# 糸満市周辺として妥当な緯度・経度の範囲（境界ポリゴン取得等の新たな外部依存は追加せず、
# 市域をおおむね包含する矩形で代用する）。
LAT_RANGE = (26.08, 26.16)
LON_RANGE = (127.63, 127.75)

SIZES = [100, 500, 1000]

# 各件数に対する異常系（座標欠損・範囲外）の件数。全体件数に対して影響しすぎない
# 少数（1〜4%程度）に固定する。resident_idの空欄・重複など入力契約そのものを破る
# データはここには含めない（address_geocode.ipynb側で別途検証済みのため）。
ANOMALY_COUNTS = {
    100: {"blank": 2, "lat_out_of_range": 1, "lon_out_of_range": 1},
    500: {"blank": 4, "lat_out_of_range": 2, "lon_out_of_range": 2},
    1000: {"blank": 6, "lat_out_of_range": 3, "lon_out_of_range": 3},
}

FIELDNAMES = ["resident_id", "address", "latitude", "longitude", "geocode_status"]


def generate_rows(n):
    """n件分の行を生成する。緯度経度は固定シードで一定範囲に分散させ、末尾側の
    固定件数を異常系（座標欠損・範囲外）に置き換える（行番号は件数ごとに固定）。"""
    rng = np.random.default_rng(SEED)
    lats = rng.uniform(LAT_RANGE[0], LAT_RANGE[1], size=n)
    lons = rng.uniform(LON_RANGE[0], LON_RANGE[1], size=n)

    rows = []
    for i in range(n):
        idx = i + 1
        rows.append({
            "resident_id": f"P{idx:04d}",
            "address": f"架空住所{idx:04d}",
            "latitude": f"{lats[i]:.6f}",
            "longitude": f"{lons[i]:.6f}",
            "geocode_status": "matched",
        })

    counts = ANOMALY_COUNTS[n]
    cursor = n  # 末尾側から固定件数を異常系へ置き換える（呼び出すたびに同じ行になる）

    def next_index():
        nonlocal cursor
        cursor -= 1
        return cursor

    # 座標欠損: 住所が解決できなかった想定（geocode_statusも未解決として扱う）
    for _ in range(counts["blank"]):
        row = rows[next_index()]
        row["latitude"] = ""
        row["longitude"] = ""
        row["geocode_status"] = "town_not_found"

    # 緯度が範囲外: 変換自体はmatchedだったが、値が壊れているケース（手入力・編集ミス等）を模す
    for _ in range(counts["lat_out_of_range"]):
        row = rows[next_index()]
        row["latitude"] = "91.000000"

    # 経度が範囲外: 同上
    for _ in range(counts["lon_out_of_range"]):
        row = rows[next_index()]
        row["longitude"] = "181.000000"

    return rows


def write_csv(n):
    rows = generate_rows(n)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"residents_{n}.csv"
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    print(f"'{output_path}' を{len(rows)}件で生成した。")


def main():
    for n in SIZES:
        write_csv(n)


if __name__ == "__main__":
    main()
