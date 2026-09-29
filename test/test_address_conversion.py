"""address_geocode.ipynb の住所変換の回帰テスト。

実データ（要支援者名簿）で実際に起こり得る住所表記が、同じ地番・同じ住居・同じ座標へ変換されることと、
別の住所を同一視していないことを確認する。Notebookのセル（設定 → ABRマスター準備 → 住所変換ロジック
定義 → 住所CSV変換・出力）をそのまま順に実行し、Colab固有の操作（ファイルのアップロード・ダウンロード・
表示）だけを差し替える。Notebook側を直した場合もこのテストがそのまま効く。

ABRの公式データ（町字・地番・地番位置参照・住居表示の街区／住居と位置参照）はコミットできないため、
実データと同じ列構成の**完全な架空データ**をテスト内で作り、ZIPにしてアップロードする。住居表示データは
実際の配布と同じく「県単位」を想定し、対象自治体（472107）以外の行も混ぜている。

実行方法:
    python3 test/test_address_conversion.py
    python3 -m unittest discover -s test
"""
import contextlib
import csv
import io
import json
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path

import pandas as pd

NOTEBOOK_PATH = Path(__file__).resolve().parent.parent / "address_geocode.ipynb"
SETTINGS_CELL, ABR_PREP_CELL, LOGIC_CELL, CONVERT_CELL = 1, 2, 3, 4

LG_CODE = "472107"        # 対象自治体（Notebookの既定設定と同じ）
OTHER_LG_CODE = "472018"  # 県単位のデータに混ざる別の自治体（行の内容はすべて架空）


# =============================================================================
# 架空のABRデータ（列構成だけを実データに合わせる）
# =============================================================================

TOWN_HEADER = ["lg_code", "machiaza_id", "pref", "city", "oaza_cho", "chome", "koaza",
               "machiaza_dist", "rsdt_addr_flg"]
# (lg_code, machiaza_id, oaza_cho, chome, rsdt_addr_flg)
TOWNS = [
    (LG_CODE, "0001001", "字糸満", "", "0"),
    (LG_CODE, "0002001", "字真栄里", "", "0"),
    (LG_CODE, "0003001", "字座波", "", "0"),
    (LG_CODE, "0004001", "字糸満町", "", "0"),      # 「字糸満」より長い別の町字
    (LG_CODE, "0005001", "西崎町", "１丁目", "1"),   # 住居表示（ABR側の丁目は全角数字）
    (LG_CODE, "0006001", "字国吉", "１丁目", "0"),   # 地番のまま丁目を持つ町字
    (LG_CODE, "0007001", "字上里", "", "0"),        # 「大字上里」と別名が衝突する
    (LG_CODE, "0008001", "大字上里", "", "0"),
    (LG_CODE, "0009001", "架空町", "", "1"),        # 住居表示（丁目なし）
    (LG_CODE, "0010002", "架空台", "２丁目", "1"),   # 住居表示（丁目あり）
    (LG_CODE, "0010003", "架空台", "３丁目", "1"),
    # 別の自治体に、同じmachiaza_id・同じ町字名がある
    (OTHER_LG_CODE, "0001001", "字糸満", "", "0"),
    (OTHER_LG_CODE, "0009001", "架空町", "", "1"),
]
TOWN_BY_ID = {(lg, mid): (oaza, chome) for lg, mid, oaza, chome, _ in TOWNS}

PARCEL_HEADER = ["lg_code", "machiaza_id", "prc_id", "prc_num1", "prc_num2", "prc_num3"]
PARCEL_POS_HEADER = ["lg_code", "machiaza_id", "prc_id", "rep_lon", "rep_lat", "rep_srid"]
# (lg_code, machiaza_id, prc_id, prc_num1, prc_num2, prc_num3, rep_lon, rep_lat)。
# rep_lon が None の地番は、位置参照の行を作らない。
PARCELS = [
    (LG_CODE, "0001001", "P01", "673", "", "", "127.6801", "26.1201"),
    (LG_CODE, "0001001", "P02", "673", "2", "", "127.6802", "26.1202"),
    (LG_CODE, "0001001", "P03", "6732", "", "", "127.6900", "26.1300"),
    (LG_CODE, "0001001", "P04", "1", "2", "3", "127.6803", "26.1203"),
    (LG_CODE, "0001001", "P05", "673", "2101", "", "127.6950", "26.1350"),  # 673-2 と 101 をつなげた番号
    (LG_CODE, "0001001", "P06", "927", "2", "", "127.6804", "26.1204"),
    (LG_CODE, "0002001", "P07", "1448", "", "", "127.6700", "26.1400"),
    (LG_CODE, "0002001", "P08", "99", "", "", None, None),                   # 位置参照が無い地番
    (LG_CODE, "0003001", "P09", "1444", "1", "", "127.6600", "26.1500"),
    (LG_CODE, "0003001", "P10", "50", "", "", "127.6610", "26.1510"),        # 同じ地番が2件（曖昧）
    (LG_CODE, "0003001", "P11", "50", "", "", "127.6620", "26.1520"),
    (LG_CODE, "0004001", "P12", "1", "", "", "127.6000", "26.1000"),
    (LG_CODE, "0006001", "P13", "12", "3", "", "127.6400", "26.1700"),
    (LG_CODE, "0007001", "P14", "1", "", "", "127.6100", "26.1100"),
    (LG_CODE, "0008001", "P15", "1", "", "", "127.6200", "26.1200"),
    # 別の自治体（同じmachiaza_id・prc_idの行を含む）
    (OTHER_LG_CODE, "0001001", "P02", "673", "2", "", "127.9000", "26.3000"),
    (OTHER_LG_CODE, "0001001", "P20", "674", "", "", "127.9001", "26.3001"),
]

BLK_HEADER = ["lg_code", "machiaza_id", "blk_id", "oaza_cho", "chome", "koaza", "machiaza_dist",
              "blk_num", "rsdt_addr_flg"]
BLK_POS_HEADER = ["lg_code", "machiaza_id", "blk_id", "rsdt_addr_flg", "rep_lon", "rep_lat", "rep_srid"]
RSDT_HEADER = ["lg_code", "machiaza_id", "blk_id", "rsdt_id", "rsdt2_id", "oaza_cho", "chome", "koaza",
               "machiaza_dist", "blk_num", "rsdt_num", "rsdt_num2", "rsdt_addr_flg"]
RSDT_POS_HEADER = ["lg_code", "machiaza_id", "blk_id", "rsdt_id", "rsdt2_id", "rsdt_addr_flg",
                   "rep_lon", "rep_lat", "rep_srid"]

# 住居表示-街区 (lg_code, machiaza_id, blk_id, blk_num)
BLOCKS = [
    (LG_CODE, "0009001", "001", "37"),
    (LG_CODE, "0009001", "002", "38"),
    (LG_CODE, "0009001", "003", "40"),   # 住居の無い街区（街区位置参照だけがある）
    (LG_CODE, "0009001", "004", "41"),
    (LG_CODE, "0010002", "001", "32"),
    (LG_CODE, "0010003", "001", "5"),
    (LG_CODE, "0005001", "001", "1"),
    (OTHER_LG_CODE, "0009001", "001", "37"),
    (OTHER_LG_CODE, "0009001", "009", "50"),  # 別の自治体にだけある街区
]
# 住居表示-街区位置参照 (lg_code, machiaza_id, blk_id, rep_lon, rep_lat)。
# 同じ街区（架空町37）に位置が2つある（実データでも同一街区キーの複数行がある）。
BLOCK_POSITIONS = [
    (LG_CODE, "0009001", "001", "127.7101", "26.1101"),
    (LG_CODE, "0009001", "001", "127.7102", "26.1102"),
    (LG_CODE, "0009001", "003", "127.7103", "26.1103"),
    (LG_CODE, "0010002", "001", "127.7104", "26.1104"),
    (OTHER_LG_CODE, "0009001", "001", "127.8100", "26.2100"),
]
# 住居表示-住居 (lg_code, machiaza_id, blk_id, rsdt_id, rsdt2_id, blk_num, rsdt_num, rsdt_num2)
RESIDENCES = [
    (LG_CODE, "0009001", "001", "001", "", "37", "14", ""),
    (LG_CODE, "0009001", "001", "002", "", "37", "15", ""),
    (LG_CODE, "0009001", "001", "003", "", "37", "16", ""),
    (LG_CODE, "0009001", "001", "003", "001", "37", "16", "1"),  # 住居番号2を持つ住居
    (LG_CODE, "0009001", "002", "001", "", "38", "1", ""),        # 住居位置参照が無い
    (LG_CODE, "0009001", "002", "002", "", "38", "2", ""),        # 住居位置参照の座標が空欄
    (LG_CODE, "0009001", "004", "001", "", "41", "7", ""),        # 同じ番号の住居が2件（曖昧）
    (LG_CODE, "0009001", "004", "002", "", "41", "7", ""),
    (LG_CODE, "0010002", "001", "002", "", "32", "2", ""),
    (LG_CODE, "0010003", "001", "001", "", "5", "1", ""),
    (LG_CODE, "0005001", "001", "001", "", "1", "1", ""),
    (OTHER_LG_CODE, "0009001", "001", "001", "", "37", "14", ""),  # 同じ町字・番号が別の自治体にある
    (OTHER_LG_CODE, "0009001", "009", "001", "", "50", "1", ""),
]
# 住居表示-住居位置参照 (lg_code, machiaza_id, blk_id, rsdt_id, rsdt2_id, rep_lon, rep_lat)
RESIDENCE_POSITIONS = [
    (LG_CODE, "0009001", "001", "001", "", "127.7001", "26.1001"),
    (LG_CODE, "0009001", "001", "002", "", "127.7002", "26.1002"),
    (LG_CODE, "0009001", "001", "003", "", "127.7003", "26.1003"),
    (LG_CODE, "0009001", "001", "003", "001", "127.7004", "26.1004"),
    (LG_CODE, "0009001", "002", "002", "", "", ""),
    (LG_CODE, "0009001", "004", "001", "", "127.7007", "26.1007"),
    (LG_CODE, "0009001", "004", "002", "", "127.7008", "26.1008"),
    (LG_CODE, "0010002", "001", "002", "", "127.7009", "26.1009"),
    (LG_CODE, "0010003", "001", "001", "", "127.7010", "26.1010"),
    (LG_CODE, "0005001", "001", "001", "", "127.7011", "26.1011"),
    (LG_CODE, "0009001", "001", "099", "", "127.7099", "26.1099"),  # 住居マスターに無い位置参照
    (OTHER_LG_CODE, "0009001", "001", "001", "", "127.8001", "26.2001"),
    (OTHER_LG_CODE, "0009001", "009", "001", "", "127.8002", "26.2002"),
]

# 架空データの座標（期待値の確認に使う）
COORD_673_2 = (26.1202, 127.6802)
COORD_927_2 = (26.1204, 127.6804)
COORD_KAKU_37_14 = (26.1001, 127.7001)
COORD_KAKU_37_16 = (26.1003, 127.7003)
COORD_KAKU_37_16_1 = (26.1004, 127.7004)
COORD_KAKUDAI2_32_2 = (26.1009, 127.7009)
COORD_NISHIZAKI1_1_1 = (26.1011, 127.7011)


def csv_bytes(header, rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def zip_bytes(member_name, header, rows):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zip_file:
        zip_file.writestr(member_name, csv_bytes(header, rows))
    return buffer.getvalue()


def town_rows():
    return [(lg, mid, "沖縄県", "架空市", oaza, chome, "", "", flag) for lg, mid, oaza, chome, flag in TOWNS]


def blk_rows(blocks):
    rows = []
    for lg, mid, blk_id, blk_num in blocks:
        oaza, chome = TOWN_BY_ID[(lg, mid)]
        rows.append((lg, mid, blk_id, oaza, chome, "", "", blk_num, "1"))
    return rows


def rsdt_rows(residences):
    rows = []
    for lg, mid, blk_id, rsdt_id, rsdt2_id, blk_num, rsdt_num, rsdt_num2 in residences:
        oaza, chome = TOWN_BY_ID[(lg, mid)]
        rows.append((lg, mid, blk_id, rsdt_id, rsdt2_id, oaza, chome, "", "", blk_num, rsdt_num,
                     rsdt_num2, "1"))
    return rows


def chiban_zips():
    return {
        "mt_town_all.csv.zip": zip_bytes("mt_town_all.csv", TOWN_HEADER, town_rows()),
        f"mt_parcel_city{LG_CODE}.csv.zip": zip_bytes(
            f"mt_parcel_city{LG_CODE}.csv", PARCEL_HEADER, [p[:6] for p in PARCELS]),
        f"mt_parcel_pos_city{LG_CODE}.csv.zip": zip_bytes(
            f"mt_parcel_pos_city{LG_CODE}.csv", PARCEL_POS_HEADER,
            [p[:3] + (p[6], p[7], "6668") for p in PARCELS if p[6] is not None]),
    }


def residential_zips(blocks=BLOCKS, block_positions=BLOCK_POSITIONS, residences=RESIDENCES,
                     residence_positions=RESIDENCE_POSITIONS, include=("blk", "blk_pos", "rsdt", "rsdt_pos")):
    """住居表示の4データ（県単位を想定）のZIP。include で一部だけを作れる。"""
    zips = {
        "blk": ("mt_rsdtdsp_blk_pref47.csv", BLK_HEADER, blk_rows(blocks)),
        "blk_pos": ("mt_rsdtdsp_blk_pos_pref47.csv", BLK_POS_HEADER,
                    [(lg, mid, blk_id, "1", lon, lat, "6668") for lg, mid, blk_id, lon, lat in block_positions]),
        "rsdt": ("mt_rsdtdsp_rsdt_pref47.csv", RSDT_HEADER, rsdt_rows(residences)),
        "rsdt_pos": ("mt_rsdtdsp_rsdt_pos_pref47.csv", RSDT_POS_HEADER,
                     [(lg, mid, blk_id, rsdt_id, rsdt2_id, "1", lon, lat, "6668")
                      for lg, mid, blk_id, rsdt_id, rsdt2_id, lon, lat in residence_positions]),
    }
    return {f"{member}.zip": zip_bytes(member, header, rows)
            for kind, (member, header, rows) in zips.items() if kind in include}


# =============================================================================
# Notebookの実行（Colab固有の操作だけを差し替える）
# =============================================================================

class FakeFiles:
    """google.colab.files の代わり。upload() は指定したファイルを順に返し、download() は記録だけする。"""

    def __init__(self):
        self.pending = []
        self.downloaded = []

    def upload(self):
        return self.pending.pop(0) if self.pending else {}

    def download(self, name):
        self.downloaded.append(str(name))


@contextlib.contextmanager
def fake_google_colab(fake_files):
    saved = {name: sys.modules.get(name) for name in ("google", "google.colab")}
    google = types.ModuleType("google")
    colab = types.ModuleType("google.colab")
    colab.files = fake_files
    google.colab = colab
    sys.modules["google"] = google
    sys.modules["google.colab"] = colab
    try:
        yield
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


class NotebookRun:
    """address_geocode.ipynb のセルを順に実行する。prepare=True なら設定→ABRマスター準備→住所変換ロジック定義
    まで実行する（ABRマスター準備で止まる場合を確かめるときは prepare=False にして run_cell を使う）。"""

    def __init__(self, abr_zips, prepare=True, notebook_path=NOTEBOOK_PATH):
        notebook = json.loads(Path(notebook_path).read_text(encoding="utf-8"))
        self.cells = ["".join(cell["source"]) for cell in notebook["cells"]]
        self.path = str(notebook_path)
        self.files = FakeFiles()
        self.files.pending.append(dict(abr_zips))
        self.printed = []
        self.displayed = []
        self.env = {"__name__": "__main__", "print": self._print, "display": self.displayed.append}
        self.run_cell(SETTINGS_CELL)
        if prepare:
            self.run_cell(ABR_PREP_CELL)
            self.run_cell(LOGIC_CELL)

    def _print(self, *args, **kwargs):
        self.printed.append(" ".join(str(arg) for arg in args))

    def printed_text(self):
        return "\n".join(self.printed)

    def run_cell(self, index):
        with fake_google_colab(self.files):
            exec(compile(self.cells[index], f"{self.path}#cell{index}", "exec"), self.env)

    def geocode(self, address):
        return self.env["geocode_one_address"](address)

    def convert(self, residents_csv, filename="residents.csv"):
        """「住所CSV変換・出力」セルを実行し、ダウンロードされた出力CSVの中身を返す（出力が無ければNone）。"""
        self.files.pending.append({filename: residents_csv.encode("utf-8")})
        downloaded_before = len(self.files.downloaded)
        with tempfile.TemporaryDirectory() as directory, contextlib.chdir(directory):
            self.run_cell(CONVERT_CELL)
            if len(self.files.downloaded) == downloaded_before:
                return None
            return Path(self.files.downloaded[-1]).read_text(encoding="utf-8-sig")


# 地番用3ZIP＋住居表示用4ZIP（通常の運用）
FULL = NotebookRun({**chiban_zips(), **residential_zips()})
# 地番用3ZIPだけ（住居表示データをアップロードしない運用）
CHIBAN_ONLY = NotebookRun(chiban_zips())
LOGIC = FULL.env


def geocode(address):
    return FULL.geocode(address)


def coordinates(result):
    return (result["latitude"], result["longitude"])


# =============================================================================
# ABRデータの種別判定・県単位データからの抽出・住居表示データの整合性
# =============================================================================

class ClassifyCsvTest(unittest.TestCase):
    """ファイル名ではなく列構成で種別を判定し、包含関係で誤判定しないこと。"""

    def test_列構成から種別を判定する(self):
        classify = LOGIC["classify_csv"]
        cases = [
            (TOWN_HEADER, "town"),
            (PARCEL_HEADER, "parcel"),
            (PARCEL_POS_HEADER, "parcel_pos"),
            (BLK_HEADER, "blk"),            # 町字の列も含むが、町字マスタとは判定しない
            (BLK_POS_HEADER, "blk_pos"),
            (RSDT_HEADER, "rsdt"),          # 町字・街区の列も含むが、住居マスターとして判定する
            (RSDT_POS_HEADER, "rsdt_pos"),  # 街区位置参照の列も含むが、住居位置参照として判定する
            (["post_code", "lg_code"], "post_code"),
            (["foo", "bar"], None),
        ]
        for header, expected in cases:
            with self.subTest(expected):
                self.assertEqual(classify(header), expected)

    def test_ファイル名に関係なく列構成で判定する(self):
        """住居マスターのCSVを町字マスタのようなファイル名で渡しても、住居表示-住居として読む。"""
        zips = {**chiban_zips(), **residential_zips(include=("blk", "blk_pos", "rsdt_pos"))}
        zips["mt_town_all_copy.csv.zip"] = zip_bytes("mt_town_all_copy.csv", RSDT_HEADER, rsdt_rows(RESIDENCES))
        run = NotebookRun(zips)
        self.assertEqual(run.env["master_status"]["residential"], "loaded")
        self.assertEqual(run.geocode("糸満市架空町37番14号")["status"], "matched")
        # 町字マスタは本物の町字マスタのまま（住居マスターで上書きされていない）
        self.assertEqual(len(run.env["town_lg"]), len([t for t in TOWNS if t[0] == LG_CODE]))


class AbrPreparationTest(unittest.TestCase):
    """県単位の住居表示データから対象自治体だけを抽出し、住居と住居位置参照を結合すること。"""

    def test_対象自治体の行だけを抽出して件数を表示する(self):
        printed = FULL.printed_text()
        itoman = {"blk": 7, "blk_pos": 4, "rsdt": 11, "rsdt_pos": 11}
        whole = {"blk": len(BLOCKS), "blk_pos": len(BLOCK_POSITIONS), "rsdt": len(RESIDENCES),
                 "rsdt_pos": len(RESIDENCE_POSITIONS)}
        labels = {"blk": "住居表示-街区", "blk_pos": "住居表示-街区位置参照", "rsdt": "住居表示-住居",
                  "rsdt_pos": "住居表示-住居位置参照"}
        for kind, count in itoman.items():
            with self.subTest(kind):
                self.assertIn(f"{labels[kind]}: {count}件（ファイル全体 {whole[kind]}件）", printed)
                self.assertEqual(set(FULL.env["residential_lg"][kind]["lg_code"]), {LG_CODE})

    def test_住居と住居位置参照の結合と確認用の件数(self):
        printed = FULL.printed_text()
        for line in ("住居マスターのキー重複: 0件", "住居位置参照のキー重複: 0件",
                     "住居マスターと住居位置参照の結合: 10件（住居マスター 11件中）",
                     "住居マスターに対して位置参照が見つからない: 1件",
                     "住居マスターに対応しない住居位置参照（照合には使わない）: 1件",
                     "座標欠損（住居位置参照はあるが緯度・経度が無い）: 1件"):
            with self.subTest(line):
                self.assertIn(line, printed)
        master = FULL.env["RESIDENTIAL_MASTER"]
        self.assertEqual(len(master), 11)            # 住居マスターの行を減らしも増やしもしない
        self.assertEqual(set(master["lg_code"]), {LG_CODE})

    def test_住居番号2が空欄の住居も位置参照と対応付ける(self):
        master = FULL.env["RESIDENTIAL_MASTER"]
        row = master[(master["machiaza_id"] == "0009001") & (master["rsdt_id"] == "001")
                     & (master["blk_id"] == "001")]
        self.assertEqual(len(row), 1)
        self.assertEqual(row.iloc[0]["rsdt2_id"], "")
        self.assertEqual((row.iloc[0]["rep_lat"], row.iloc[0]["rep_lon"]), COORD_KAKU_37_14)

    def test_IDの先頭ゼロと空欄を文字列のまま扱う(self):
        master = FULL.env["RESIDENTIAL_MASTER"]
        self.assertIn("0009001", set(master["machiaza_id"]))
        self.assertIn("001", set(master["blk_id"]))
        self.assertFalse(master["rsdt_num2"].isna().any())

    def test_街区位置参照の重複は異常扱いせず1件へ潰さない(self):
        self.assertEqual(FULL.env["master_status"]["residential"], "loaded")
        blk_pos = FULL.env["residential_lg"]["blk_pos"]
        self.assertEqual(len(blk_pos), 4)  # 同じ街区（架空町37）の2行もそのまま残る
        same_block = blk_pos[(blk_pos["machiaza_id"] == "0009001") & (blk_pos["blk_id"] == "001")]
        self.assertEqual(len(same_block), 2)
        self.assertIn("同じ街区に複数の位置がある街区: 1街区（2行）", FULL.printed_text())

    def test_住居表示データが無い場合は地番住所だけを変換する(self):
        self.assertEqual(CHIBAN_ONLY.env["master_status"]["residential"], "not_uploaded")
        self.assertIsNone(CHIBAN_ONLY.env["RESIDENTIAL_MASTER"])
        for address in ("沖縄県糸満市西崎町1丁目1番1号", "糸満市架空町37番14号", "糸満市架空町37番14号 架空荘"):
            with self.subTest(address):
                result = CHIBAN_ONLY.geocode(address)
                self.assertEqual(result["status"], "residential_display_area")
                self.assertIsNone(result["latitude"])
        self.assertEqual(CHIBAN_ONLY.geocode("糸満市字糸満673番地2")["status"], "matched")


class ResidentialDataIntegrityTest(unittest.TestCase):
    """住居表示データが不完全・不整合な場合は、曖昧な座標を作らずに止めること。"""

    def assert_preparation_stops(self, zips, message):
        run = NotebookRun(zips, prepare=False)
        with self.assertRaises(RuntimeError) as raised:
            run.run_cell(ABR_PREP_CELL)
        self.assertIn(message, str(raised.exception))
        self.assertEqual(run.env["master_status"]["residential"], "error")
        # 止まったあとに先へ進んでも、住所CSV変換セルは実行しない
        run.run_cell(LOGIC_CELL)
        self.assertIsNone(run.convert("resident_id,address,latitude,longitude,geocode_status\nT1,架空町37番14号,,,\n"))
        self.assertIn("住居表示データを正しく準備できなかったため", run.printed_text())
        return run

    def test_住居位置参照が無い(self):
        self.assert_preparation_stops(
            {**chiban_zips(), **residential_zips(include=("blk", "blk_pos", "rsdt"))}, "住居表示-住居位置参照")

    def test_住居マスターが無い(self):
        self.assert_preparation_stops(
            {**chiban_zips(), **residential_zips(include=("blk", "blk_pos", "rsdt_pos"))}, "住居表示-住居 ")

    def test_街区マスターが無い(self):
        self.assert_preparation_stops(
            {**chiban_zips(), **residential_zips(include=("blk_pos", "rsdt", "rsdt_pos"))}, "住居表示-街区 ")

    def test_住居マスターのキーが一意でない(self):
        residences = RESIDENCES + [RESIDENCES[0]]
        self.assert_preparation_stops({**chiban_zips(), **residential_zips(residences=residences)}, "一意でない")

    def test_住居位置参照のキーが一意でない(self):
        positions = RESIDENCE_POSITIONS + [(LG_CODE, "0009001", "001", "001", "", "127.7500", "26.1500")]
        self.assert_preparation_stops(
            {**chiban_zips(), **residential_zips(residence_positions=positions)}, "一意でない")

    def test_対象自治体の行が無い(self):
        residences = [r for r in RESIDENCES if r[0] != LG_CODE]
        self.assert_preparation_stops(
            {**chiban_zips(), **residential_zips(residences=residences)}, "の行がありません")

    def test_ほかの自治体だけの重複では止めない(self):
        """県単位のデータは対象自治体の行を抽出してから確認する（ほかの自治体の重複で止めない）。"""
        other = [r for r in RESIDENCES if r[0] == OTHER_LG_CODE]
        run = NotebookRun({**chiban_zips(), **residential_zips(residences=RESIDENCES + other)})
        self.assertEqual(run.env["master_status"]["residential"], "loaded")
        self.assertEqual(run.geocode("糸満市架空町37番14号")["status"], "matched")


# =============================================================================
# 地番住所（従来どおり）
# =============================================================================

# 変更前（住居表示・方書対応の前）の Notebook で、同じ架空データから得た結果。方書を含まない地番住所は、
# 住居表示データを読み込んだ場合も読み込まない場合も、状態・緯度・経度がこれと変わらないこと。
REGRESSION_CASES = [
    ("沖縄県糸満市字糸満673番地2", "matched", 26.1202, 127.6802),
    ("糸満市字糸満673番地2", "matched", 26.1202, 127.6802),
    ("字糸満673番地2", "matched", 26.1202, 127.6802),
    ("糸満市糸満673番地2", "matched", 26.1202, 127.6802),
    ("沖縄県糸満市大字糸満673番地2", "matched", 26.1202, 127.6802),
    ("沖縄県糸満市字糸満６７３番地２", "matched", 26.1202, 127.6802),
    ("沖縄県糸満市字糸満673-2", "matched", 26.1202, 127.6802),
    ("沖縄県糸満市字糸満673－2", "matched", 26.1202, 127.6802),
    ("沖縄県糸満市字糸満673ー2", "matched", 26.1202, 127.6802),
    ("沖縄県糸満市字糸満673‐2", "matched", 26.1202, 127.6802),
    ("沖縄県糸満市字糸満673―2", "matched", 26.1202, 127.6802),
    ("沖縄県糸満市字糸満673−2", "matched", 26.1202, 127.6802),
    ("沖縄県糸満市字糸満673番地の2", "matched", 26.1202, 127.6802),
    ("沖縄県糸満市字糸満673番の2", "matched", 26.1202, 127.6802),
    ("沖縄県糸満市字糸満673番2", "matched", 26.1202, 127.6802),
    ("沖縄県糸満市字糸満673の2", "matched", 26.1202, 127.6802),
    ("沖縄県糸満市字糸満673番地2号", "matched", 26.1202, 127.6802),
    ("  沖縄県糸満市字糸満673番地2  ", "matched", 26.1202, 127.6802),
    ("沖縄県糸満市 字糸満 673番地2", "matched", 26.1202, 127.6802),
    ("沖縄県糸満市　字糸満　673番地2", "matched", 26.1202, 127.6802),
    ("沖縄県 糸満市 字糸満 673番地", "matched", 26.1201, 127.6801),
    ("沖縄県糸満市字糸満673", "matched", 26.1201, 127.6801),
    ("沖縄県糸満市字糸満673番地", "matched", 26.1201, 127.6801),
    ("糸満市糸満673番", "matched", 26.1201, 127.6801),
    ("沖縄県糸満市字糸満1-2-3", "matched", 26.1203, 127.6803),
    ("沖縄県糸満市字糸満1番地2号3", "matched", 26.1203, 127.6803),
    ("沖縄県糸満市字糸満1の2の3", "matched", 26.1203, 127.6803),
    ("沖縄県糸満市字国吉1丁目12番地3", "matched", 26.17, 127.64),
    ("沖縄県糸満市字国吉１丁目12番地3", "matched", 26.17, 127.64),
    ("沖縄県糸満市字国吉一丁目12番地3", "matched", 26.17, 127.64),
    ("沖縄県糸満市字糸満6732", "matched", 26.13, 127.69),
    ("糸満市字糸満673-2101", "matched", 26.135, 127.695),
    ("糸満市字糸満927-2", "matched", 26.1204, 127.6804),
    ("糸満市字真栄里1448", "matched", 26.14, 127.67),
    ("糸満市字座波1444-1", "matched", 26.15, 127.66),
    ("糸満市字糸満町1番地", "matched", 26.1, 127.6),
    ("糸満市字上里1番地", "matched", 26.11, 127.61),
    ("糸満市大字上里1番地", "matched", 26.12, 127.62),
    ("糸満市上里1番地", "ambiguous_town", None, None),
    ("糸満市字座波50", "ambiguous_parcel", None, None),
    ("糸満市字真栄里99", "coordinates_missing", None, None),
    ("糸満市字糸満674", "parcel_not_found", None, None),
    ("糸満市字糸満六七三番地", "parcel_not_found", None, None),
    ("糸満市字糸満673番地2号室", "parcel_not_found", None, None),
    ("糸満市字糸満673-2-101", "parcel_not_found", None, None),
    ("糸満市字糸満9999番地", "parcel_not_found", None, None),
    ("糸満市字糸満673番地99", "parcel_not_found", None, None),
    ("沖縄県那覇市おもろまち1丁目1番地", "town_not_found", None, None),
    ("沖縄県糸満市字西崎町1丁目1番地", "town_not_found", None, None),
    ("沖縄県糸満市大字西崎町1丁目1番地", "town_not_found", None, None),
    ("", "blank_address", None, None),
    ("   ", "blank_address", None, None),
    (None, "blank_address", None, None),
]


class ChibanRegressionTest(unittest.TestCase):
    """方書の無い地番住所の状態・緯度・経度が、変更前と変わらないこと。"""

    def test_変更前と同じ結果になる(self):
        for run_name, run in (("住居表示データあり", FULL), ("地番データのみ", CHIBAN_ONLY)):
            for address, status, latitude, longitude in REGRESSION_CASES:
                with self.subTest(run=run_name, address=address):
                    result = run.geocode(address)
                    self.assertEqual(result["status"], status)
                    self.assertEqual(coordinates(result), (latitude, longitude))


class SameAddressTest(unittest.TestCase):
    """同じ住所として扱うべき表記が、同じ地番・同じ座標になること。"""

    # 「沖縄県糸満市字糸満673番地2」と同じ場所を指す表記
    SAME_AS_673_2 = [
        ("基本形", "沖縄県糸満市字糸満673番地2"),
        ("都道府県省略", "糸満市字糸満673番地2"),
        ("都道府県・市省略", "字糸満673番地2"),
        ("「字」省略", "糸満市糸満673番地2"),
        ("「大字」表記", "沖縄県糸満市大字糸満673番地2"),
        ("全角数字", "沖縄県糸満市字糸満６７３番地２"),
        ("半角ハイフン", "沖縄県糸満市字糸満673-2"),
        ("全角ハイフン", "沖縄県糸満市字糸満673－2"),
        ("長音", "沖縄県糸満市字糸満673ー2"),
        ("ハイフン U+2010", "沖縄県糸満市字糸満673‐2"),
        ("ダッシュ", "沖縄県糸満市字糸満673―2"),
        ("マイナス U+2212", "沖縄県糸満市字糸満673−2"),
        ("「番地の」", "沖縄県糸満市字糸満673番地の2"),
        ("「番の」", "沖縄県糸満市字糸満673番の2"),
        ("「番」", "沖縄県糸満市字糸満673番2"),
        ("「の」", "沖縄県糸満市字糸満673の2"),
        ("末尾に「号」", "沖縄県糸満市字糸満673番地2号"),
        ("前後の半角スペース", "  沖縄県糸満市字糸満673番地2  "),
        ("途中の半角スペース", "沖縄県糸満市 字糸満 673番地2"),
        ("全角スペース", "沖縄県糸満市　字糸満　673番地2"),
    ]

    def test_同じ住所の表記揺れが同じ地番と座標になる(self):
        expected = geocode("沖縄県糸満市字糸満673番地2")
        self.assertEqual(expected["status"], "matched")
        for label, address in self.SAME_AS_673_2:
            with self.subTest(label):
                result = geocode(address)
                self.assertEqual(result["status"], "matched")
                self.assertEqual(result["address_type"], "地番")
                self.assertEqual(result["parcel_number"], "673-2")
                self.assertEqual(result["prc_id"], expected["prc_id"])
                self.assertEqual(coordinates(result), coordinates(expected))
                self.assertEqual(result["ignored_suffix"], "")  # 方書の除外はしていない

    def test_字を含む正式住所と字の省略(self):
        """ABRの町字名が「字糸満」の場合、「字」を省略した住所も同じ町字として一致する。"""
        for address in ("糸満市字糸満673番地", "糸満市糸満673番地", "糸満市大字糸満673番地"):
            with self.subTest(address):
                result = geocode(address)
                self.assertEqual(result["status"], "matched")
                self.assertEqual(result["matched_town"], "字糸満")
                self.assertEqual(result["prc_id"], "P01")

    def test_1段の地番(self):
        for address in ("沖縄県糸満市字糸満673", "沖縄県糸満市字糸満673番地", "糸満市糸満673番"):
            with self.subTest(address):
                result = geocode(address)
                self.assertEqual(result["status"], "matched")
                self.assertEqual(result["parcel_number"], "673")

    def test_3段の地番(self):
        for address in ("沖縄県糸満市字糸満1-2-3", "沖縄県糸満市字糸満1番地2号3",
                        "沖縄県糸満市字糸満1の2の3"):
            with self.subTest(address):
                result = geocode(address)
                self.assertEqual(result["status"], "matched")
                self.assertEqual(result["parcel_number"], "1-2-3")

    def test_丁目の表記揺れ(self):
        """ABR側が全角数字（１丁目）でも、算用数字・漢数字の入力が同じ町字に当たること。"""
        for address in ("沖縄県糸満市字国吉1丁目12番地3", "沖縄県糸満市字国吉１丁目12番地3",
                        "沖縄県糸満市字国吉一丁目12番地3"):
            with self.subTest(address):
                result = geocode(address)
                self.assertEqual(result["status"], "matched")
                self.assertEqual(result["matched_town"], "字国吉１丁目")
                self.assertEqual(result["parcel_number"], "12-3")


# =============================================================================
# 住居表示住所
# =============================================================================

class ResidentialAddressTest(unittest.TestCase):
    """住居表示地域の住所を、町字＋街区符号＋住居番号（＋住居番号2）の完全一致で照合すること。"""

    def test_街区符号と住居番号が一致すれば住居位置参照の座標になる(self):
        result = geocode("沖縄県糸満市架空町37番14号")
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["address_type"], "住居表示")
        self.assertEqual((result["blk_num"], result["rsdt_num"], result["rsdt_num2"]), ("37", "14", ""))
        self.assertEqual(coordinates(result), COORD_KAKU_37_14)

    def test_丁目のある町字(self):
        """「2丁目」は町字側で判定し、残りの「32番2号」を街区32・住居番号2として照合する。"""
        for address in ("沖縄県糸満市架空台2丁目32番2号", "糸満市架空台２丁目32番2号", "糸満市架空台二丁目32-2"):
            with self.subTest(address):
                result = geocode(address)
                self.assertEqual(result["status"], "matched")
                self.assertEqual(result["matched_town"], "架空台２丁目")
                self.assertEqual((result["blk_num"], result["rsdt_num"]), ("32", "2"))
                self.assertEqual(coordinates(result), COORD_KAKUDAI2_32_2)
        # 3丁目の同じ番号は無い（別の丁目の住居へ寄せない）
        self.assertEqual(geocode("糸満市架空台3丁目32番2号")["status"], "residential_block_not_found")

    def test_番号の表記揺れが同じ住居になる(self):
        for address in ("糸満市架空町37番14号", "糸満市架空町37-14", "糸満市架空町37番14", "糸満市架空町３７－１４",
                        "糸満市架空町37の14", "架空町37番地14号", "沖縄県糸満市 架空町 37番14号"):
            with self.subTest(address):
                result = geocode(address)
                self.assertEqual(result["status"], "matched")
                self.assertEqual(coordinates(result), COORD_KAKU_37_14)

    def test_住居番号2(self):
        for address in ("糸満市架空町37-16-1", "糸満市架空町37番16号の1", "糸満市架空町37番16号1"):
            with self.subTest(address):
                result = geocode(address)
                self.assertEqual(result["status"], "matched")
                self.assertEqual(result["rsdt_num2"], "1")
                self.assertEqual(coordinates(result), COORD_KAKU_37_16_1)
        # 住居番号2の無い「37番16号」は、住居番号2を持つ住居と区別する
        self.assertEqual(coordinates(geocode("糸満市架空町37番16号")), COORD_KAKU_37_16)

    def test_既存の住居表示の町字(self):
        result = geocode("沖縄県糸満市西崎町一丁目1番1号")
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["matched_town"], "西崎町１丁目")
        self.assertEqual(coordinates(result), COORD_NISHIZAKI1_1_1)

    def test_街区だけ一致しても座標を付けない(self):
        for address in ("糸満市架空町37番13号", "糸満市架空町37番", "糸満市架空町37番99号"):
            with self.subTest(address):
                result = geocode(address)
                self.assertEqual(result["status"], "residential_number_not_found")
                self.assertEqual(result["blk_num"], "37")
                self.assertEqual(coordinates(result), (None, None))

    def test_街区が見つからない(self):
        for address in ("糸満市架空町39番1号", "糸満市架空町番地不明"):
            with self.subTest(address):
                result = geocode(address)
                self.assertEqual(result["status"], "residential_block_not_found")
                self.assertEqual(coordinates(result), (None, None))

    def test_住居番号が複数候補なら推測しない(self):
        result = geocode("糸満市架空町41番7号")
        self.assertEqual(result["status"], "ambiguous_residential")
        self.assertEqual(coordinates(result), (None, None))

    def test_住居位置参照が無い住居は座標を付けない(self):
        for address in ("糸満市架空町38番1号", "糸満市架空町38番2号"):  # 位置参照なし／座標が空欄
            with self.subTest(address):
                result = geocode(address)
                self.assertEqual(result["status"], "coordinates_missing")
                self.assertEqual(coordinates(result), (None, None))

    def test_街区位置参照の座標で代用しない(self):
        """街区40は街区位置参照だけがあり住居が無い。街区の代表点で matched にしない。"""
        for address in ("糸満市架空町40番1号", "糸満市架空町40番"):
            with self.subTest(address):
                result = geocode(address)
                self.assertEqual(result["status"], "residential_number_not_found")
                self.assertEqual(coordinates(result), (None, None))

    def test_同じ街区に複数の街区位置参照があっても住居の座標を使う(self):
        result = geocode("糸満市架空町37番14号")
        block_positions = {(float(lat), float(lon)) for lg, mid, blk_id, lon, lat in BLOCK_POSITIONS
                           if (lg, mid, blk_id) == (LG_CODE, "0009001", "001")}
        self.assertEqual(len(block_positions), 2)
        self.assertEqual(coordinates(result), COORD_KAKU_37_14)
        self.assertNotIn(coordinates(result), block_positions)

    def test_地番の書き方の住所を地番として照合しない(self):
        """住居表示地域の住所は住居表示データだけで照合する（地番マスタで似た番号を探さない）。"""
        self.assertEqual(geocode("糸満市架空町673番地")["address_type"], "住居表示")
        self.assertNotEqual(geocode("糸満市架空町673番地")["status"], "matched")


class PrefectureDataTest(unittest.TestCase):
    """県単位のデータに別の自治体の行が混ざっていても、対象自治体の行だけを使うこと。"""

    def test_別の自治体の同じ町字と番号を使わない(self):
        result = geocode("糸満市架空町37番14号")
        self.assertEqual(coordinates(result), COORD_KAKU_37_14)  # 別の自治体は (26.2001, 127.8001)

    def test_別の自治体にだけある住所は見つからない(self):
        self.assertEqual(geocode("糸満市架空町50番1号")["status"], "residential_block_not_found")
        self.assertEqual(geocode("糸満市字糸満674")["status"], "parcel_not_found")

    def test_同じmachiaza_idの地番も対象自治体のものを使う(self):
        self.assertEqual(coordinates(geocode("糸満市字糸満673番地2")), COORD_673_2)


# =============================================================================
# 方書
# =============================================================================

class SuffixTest(unittest.TestCase):
    """住所の後ろの方書（施設名・建物名・部屋番号等）を、元の住所の空白の位置で外して照合すること。"""

    def assert_matched_ignoring(self, address, expected_coordinates, used, suffix):
        result = geocode(address)
        self.assertEqual(result["status"], "matched")
        self.assertEqual(coordinates(result), expected_coordinates)
        self.assertEqual(result["matched_address"], used)
        self.assertEqual(result["ignored_suffix"], suffix)
        self.assertEqual(result["input_address"], address)

    def test_地番住所と施設名(self):
        self.assert_matched_ignoring("沖縄県糸満市字糸満927番地の2 架空特別養護老人ホーム", COORD_927_2,
                                     "沖縄県糸満市字糸満927番地の2", "架空特別養護老人ホーム")
        self.assert_matched_ignoring("沖縄県糸満市字糸満927番地の2 架空特別養護老人ホーム 東棟", COORD_927_2,
                                     "沖縄県糸満市字糸満927番地の2", "架空特別養護老人ホーム 東棟")

    def test_地番住所と建物名と部屋番号(self):
        self.assert_matched_ignoring("糸満市字糸満673番地2 架空ハイツ 101号室", COORD_673_2,
                                     "糸満市字糸満673番地2", "架空ハイツ 101号室")
        self.assert_matched_ignoring("糸満市字糸満673番地2　架空ハイツ２０１", COORD_673_2,
                                     "糸満市字糸満673番地2", "架空ハイツ２０１")

    def test_住居表示住所と施設名(self):
        self.assert_matched_ignoring("沖縄県糸満市架空町37番14号 架空デイサービスセンター", COORD_KAKU_37_14,
                                     "沖縄県糸満市架空町37番14号", "架空デイサービスセンター")

    def test_住居表示住所と建物名と部屋番号(self):
        self.assert_matched_ignoring("糸満市架空町37番14号 架空マンション 101号室", COORD_KAKU_37_14,
                                     "糸満市架空町37番14号", "架空マンション 101号室")
        self.assert_matched_ignoring("糸満市架空台2丁目32番2号 架空コーポ202", COORD_KAKUDAI2_32_2,
                                     "糸満市架空台2丁目32番2号", "架空コーポ202")

    def test_住所の途中の空白と方書(self):
        self.assert_matched_ignoring("沖縄県 糸満市 字糸満 673番地2 架空荘", COORD_673_2,
                                     "沖縄県 糸満市 字糸満 673番地2", "架空荘")

    def test_方書を外しても見つからなければ未変換のまま(self):
        for address, status in (("糸満市字糸満9999番地 架空荘", "parcel_not_found"),
                                ("糸満市架空町37番13号 架空荘", "residential_number_not_found"),
                                ("糸満市架空町39番1号 架空荘", "residential_block_not_found"),
                                ("沖縄県那覇市おもろまち1丁目1番地 架空ビル", "town_not_found")):
            with self.subTest(address):
                result = geocode(address)
                self.assertEqual(result["status"], status)
                self.assertEqual(coordinates(result), (None, None))

    def test_方書を外して曖昧さを解消しない(self):
        self.assertEqual(geocode("糸満市字座波50 架空荘")["status"], "ambiguous_parcel")
        self.assertEqual(geocode("糸満市架空町41番7号 架空荘")["status"], "ambiguous_residential")
        self.assertEqual(geocode("糸満市上里1番地 架空荘")["status"], "ambiguous_town")

    def test_住所全文で一致すれば方書の除外はしない(self):
        result = geocode("糸満市字糸満673番地2")
        self.assertEqual(result["ignored_suffix"], "")
        self.assertEqual(result["matched_address"], "糸満市字糸満673番地2")

    def test_数字の間の空白で番号をつなげない(self):
        """「673-2 101」を「673-2101」（別の地番）として照合しない。空白の後が数字だけの場合は、番号の続きか
        部屋番号か判断できないため、「673-2」にも切り詰めずに未変換とする。"""
        self.assertEqual(geocode("糸満市字糸満673-2101")["status"], "matched")  # この地番は実在する
        self.assertEqual(geocode("糸満市字糸満6732")["status"], "matched")      # この地番も実在する
        for address in ("糸満市字糸満673-2 101", "糸満市字糸満673-2　１０１", "糸満市字糸満673 2",
                        "糸満市字糸満673-2 101 架空荘"):
            with self.subTest(address):
                result = geocode(address)
                self.assertEqual(result["status"], "parcel_not_found")
                self.assertEqual(coordinates(result), (None, None))
                self.assertEqual(result["ignored_suffix"], "")
        for address in ("糸満市架空町37-14 101", "糸満市架空町37-14 2"):
            with self.subTest(address):
                self.assertNotEqual(geocode(address)["status"], "matched")

    def test_方書と分かる場合は数字の後の空白でも外す(self):
        self.assert_matched_ignoring("糸満市字糸満673番地2 101号室", COORD_673_2, "糸満市字糸満673番地2", "101号室")
        # 「号」で番号が終わっている住居表示住所の後ろの数字は方書として外す
        self.assert_matched_ignoring("糸満市架空町37番14号 101", COORD_KAKU_37_14, "糸満市架空町37番14号", "101")


# =============================================================================
# 誤一致の防止
# =============================================================================

class DifferentAddressTest(unittest.TestCase):
    """別の住所を同一視していないこと（正規化を強くしすぎていないこと）。"""

    def test_673番地2と6732は別の地番(self):
        branch = geocode("沖縄県糸満市字糸満673番地2")
        merged = geocode("沖縄県糸満市字糸満6732")
        self.assertEqual(branch["parcel_number"], "673-2")
        self.assertEqual(merged["parcel_number"], "6732")
        self.assertNotEqual(branch["prc_id"], merged["prc_id"])
        self.assertNotEqual(branch["latitude"], merged["latitude"])

    def test_枝番の有無で別の座標になる(self):
        self.assertNotEqual(geocode("糸満市字糸満673")["prc_id"],
                            geocode("糸満市字糸満673-2")["prc_id"])

    def test_長い町字名が優先される(self):
        self.assertEqual(geocode("糸満市字糸満町1番地")["matched_town"], "字糸満町")
        self.assertEqual(geocode("糸満市字糸満673")["matched_town"], "字糸満")

    def test_正式名称に一致する入力は別名より優先する(self):
        """「字上里」と「大字上里」が別の町字として存在する場合、正式名称そのものの入力は
        その町字へ一致させる（別名として当たるもう一方では曖昧扱いにしない）。"""
        self.assertEqual(geocode("糸満市字上里1番地")["matched_town"], "字上里")
        self.assertEqual(geocode("糸満市大字上里1番地")["matched_town"], "大字上里")

    def test_接頭辞の無い町字に字大字の別名を作らない(self):
        """正式名称が「字」「大字」で始まらない町字（西崎町1丁目）に、存在しない別名を作らない。"""
        self.assertEqual(geocode("沖縄県糸満市西崎町1丁目1番1号")["matched_town"], "西崎町１丁目")
        for address in ("沖縄県糸満市字西崎町1丁目1番1号", "沖縄県糸満市大字西崎町1丁目1番1号"):
            with self.subTest(address):
                self.assertEqual(geocode(address)["status"], "town_not_found")

    def test_町字の検索キー(self):
        """検索キーの作られ方を直接確認する。"""
        town_search_keys = LOGIC["town_search_keys"]
        self.assertEqual(town_search_keys("字糸満"), ["字糸満", "糸満", "大字糸満"])
        self.assertEqual(town_search_keys("大字上里"), ["大字上里", "上里", "字上里"])
        self.assertEqual(town_search_keys("西崎町1丁目"), ["西崎町1丁目"])

    def test_地番の漢数字は変換しない(self):
        """丁目以外の漢数字は桁の解釈が一意に決まらないため、推測せずparcel_not_foundとする。"""
        result = geocode("糸満市字糸満六七三番地")
        self.assertEqual(result["status"], "parcel_not_found")
        self.assertEqual(result["matched_town"], "字糸満")

    def test_空白の無い3段の番号を切り詰めない(self):
        """「673-2-101」を「673-2」に短縮しない（末尾を部屋番号と推測しない）。"""
        result = geocode("糸満市字糸満673-2-101")
        self.assertEqual(result["status"], "parcel_not_found")
        self.assertEqual(result["parcel_number"], "673-2-101")
        self.assertIsNone(result["prc_id"])
        self.assertEqual(geocode("糸満市字糸満673番地2号室")["status"], "parcel_not_found")

    def test_住居表示の番号末尾を部屋番号扱いしない(self):
        for address in ("糸満市架空町37-14-101", "糸満市架空町37番14号101"):
            with self.subTest(address):
                result = geocode(address)
                self.assertEqual(result["status"], "residential_number_not_found")
                self.assertEqual((result["blk_num"], result["rsdt_num"], result["rsdt_num2"]), ("37", "14", "101"))
                self.assertEqual(coordinates(result), (None, None))

    def test_近い地番や住居番号へ寄せない(self):
        for address, status in (("糸満市字糸満674", "parcel_not_found"),
                                ("糸満市字糸満673番地3", "parcel_not_found"),
                                ("糸満市架空町37番13号", "residential_number_not_found"),
                                ("糸満市架空町37番17号", "residential_number_not_found"),
                                ("糸満市架空町36番14号", "residential_block_not_found")):
            with self.subTest(address):
                result = geocode(address)
                self.assertEqual(result["status"], status)
                self.assertEqual(coordinates(result), (None, None))

    def test_曖昧な町字地番住居を1件に決めない(self):
        for address, status in (("糸満市上里1番地", "ambiguous_town"),
                                ("糸満市字座波50", "ambiguous_parcel"),
                                ("糸満市架空町41番7号", "ambiguous_residential")):
            with self.subTest(address):
                result = geocode(address)
                self.assertEqual(result["status"], status)
                self.assertEqual(coordinates(result), (None, None))

    def test_存在しない地番は確定させない(self):
        for address in ("糸満市字糸満9999番地", "糸満市字糸満673番地99"):
            with self.subTest(address):
                self.assertEqual(geocode(address)["status"], "parcel_not_found")

    def test_市外や空欄の住所(self):
        self.assertEqual(geocode("沖縄県那覇市おもろまち1丁目1番地")["status"], "town_not_found")
        self.assertEqual(geocode("")["status"], "blank_address")
        self.assertEqual(geocode("   ")["status"], "blank_address")
        self.assertEqual(geocode(None)["status"], "blank_address")


# =============================================================================
# 出力CSV（「住所CSV変換・出力」セル）
# =============================================================================

RESIDENTS_CSV = """resident_id,address,latitude,longitude,geocode_status
T001,沖縄県糸満市字糸満673番地2,,,
T002,沖縄県糸満市字糸満927番地の2 架空特別養護老人ホーム,,,
T003,沖縄県糸満市架空町37番14号 架空マンション 101号室,,,
T004,沖縄県糸満市架空町37番13号,,,
T005,,,,
T006,糸満市字座波50,26.5,127.5,matched
0007,N/A,,,
"""


class OutputCsvTest(unittest.TestCase):
    """正式CSVが従来の5列のままで、addressが入力のまま出力されること。"""

    @classmethod
    def setUpClass(cls):
        cls.printed_before = len(FULL.printed)
        cls.output = FULL.convert(RESIDENTS_CSV)
        cls.printed = "\n".join(FULL.printed[cls.printed_before:])
        cls.rows = pd.read_csv(io.StringIO(cls.output), dtype=str, keep_default_na=False)
        cls.expected = pd.read_csv(io.StringIO(RESIDENTS_CSV), dtype=str, keep_default_na=False)

    def test_正式CSVは5列のまま(self):
        self.assertEqual(self.output.splitlines()[0], "resident_id,address,latitude,longitude,geocode_status")
        self.assertEqual(list(self.rows.columns),
                         ["resident_id", "address", "latitude", "longitude", "geocode_status"])

    def test_addressとresident_idは入力のまま(self):
        self.assertEqual(list(self.rows["address"]), list(self.expected["address"]))
        self.assertEqual(list(self.rows["resident_id"]), list(self.expected["resident_id"]))

    def test_matchedの行だけに座標が入る(self):
        statuses = dict(zip(self.rows["resident_id"], self.rows["geocode_status"]))
        self.assertEqual(statuses, {
            "T001": "matched", "T002": "matched", "T003": "matched",
            "T004": "residential_number_not_found", "T005": "blank_address",
            "T006": "ambiguous_parcel", "0007": "town_not_found",
        })
        for _, row in self.rows.iterrows():
            with self.subTest(row["resident_id"]):
                has_coordinates = row["latitude"] != "" and row["longitude"] != ""
                self.assertEqual(has_coordinates, row["geocode_status"] == "matched")
        t003 = self.rows[self.rows["resident_id"] == "T003"].iloc[0]
        self.assertEqual((float(t003["latitude"]), float(t003["longitude"])), COORD_KAKU_37_14)

    def test_確認用の情報は正式CSVへ出さない(self):
        # 方書（除外した部分）・照合に使った住所・住所の種類等は、address 以外の列に入っていない
        for column in ("resident_id", "latitude", "longitude", "geocode_status"):
            for value in self.rows[column]:
                with self.subTest(column=column, value=value):
                    self.assertNotIn("架空", value)
                    self.assertNotIn(value, ("地番", "住居表示"))
        # Notebook上では件数を確認できる
        self.assertIn("方書を除外して照合できた住所: 2件", self.printed)
        self.assertIn("住居表示住所として照合できた住所: 1件", self.printed)


class NormalizeAddressTest(unittest.TestCase):
    """正規化そのものの確認。"""

    def test_丁目の漢数字を算用数字へ揃える(self):
        normalize = LOGIC["normalize_address"]
        self.assertEqual(normalize("一丁目"), "1丁目")
        self.assertEqual(normalize("十丁目"), "10丁目")
        self.assertEqual(normalize("二十三丁目"), "23丁目")
        self.assertEqual(normalize("１丁目"), "1丁目")

    def test_丁目以外の漢数字は変えない(self):
        normalize = LOGIC["normalize_address"]
        for text in ("三和", "六七三番地", "十日市場", "字糸満"):
            with self.subTest(text):
                self.assertEqual(normalize(text), text)

    def test_空白は取り除くが数字の間の空白は残す(self):
        normalize = LOGIC["normalize_address"]
        self.assertEqual(normalize(" 沖縄県 糸満市　字糸満 673番地2 "), "沖縄県糸満市字糸満673番地2")
        self.assertEqual(normalize("673-2 101"), "673-2 101")
        self.assertEqual(normalize("６７３－２　１０１"), "673-2 101")

    def test_方書の候補(self):
        """元の住所の空白の位置で右から順に切る。数字の後の空白で、後ろが数字だけの語の場合は切らない。"""
        suffix_candidates = LOGIC["suffix_candidates"]
        self.assertEqual(suffix_candidates("字糸満927番地の2 架空ホーム 東棟"),
                         ["字糸満927番地の2 架空ホーム", "字糸満927番地の2"])
        self.assertEqual(suffix_candidates("字糸満673-2-101"), [])
        self.assertEqual(suffix_candidates("字糸満673-2 101"), [])
        self.assertEqual(suffix_candidates("字糸満673-2 101号室"), ["字糸満673-2"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
