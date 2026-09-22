"""address_geocode.ipynb の住所変換ロジックの回帰テスト。

実データ（要支援者名簿）で実際に起こり得る住所表記が、同じ地番・同じ座標へ変換されることと、
別の住所を同一視していないことを確認する。Notebookのセルから住所変換ロジックをそのまま
読み込んで実行するため、Notebook側を直した場合もこのテストがそのまま効く。

ABRマスター（町字・地番・地番位置参照）は公式データをコミットできないため、実データと同じ
列構成の架空データを用意して使う。

実行方法:
    python3 test/test_address_conversion.py
    python3 -m unittest discover -s test
"""
import json
import re
import unicodedata
import unittest
from pathlib import Path

import pandas as pd

NOTEBOOK_PATH = Path(__file__).resolve().parent.parent / "address_geocode.ipynb"
ADDRESS_LOGIC_CELL = 3  # 「住所変換ロジック定義」セル

# 糸満市のABR町字マスタ相当（架空。列構成だけを実データに合わせる）
TOWN_ROWS = [
    # machiaza_id, oaza_cho, chome, koaza, machiaza_dist, rsdt_addr_flg
    ("0001", "字糸満", "", "", "", "0"),
    ("0002", "字真栄里", "", "", "", "0"),
    ("0003", "字座波", "", "", "", "0"),
    ("0004", "字糸満町", "", "", "", "0"),      # 「字糸満」より長い別の町字
    ("0005", "西崎町", "１丁目", "", "", "1"),   # 住居表示（座標は確定させない）
    ("0006", "字国吉", "１丁目", "", "", "0"),   # 地番のまま丁目を持つ町字
    ("0007", "字上里", "", "", "", "0"),        # 「大字上里」と別名が衝突する
    ("0008", "大字上里", "", "", "", "0"),
]

# 地番マスタ＋地番位置参照 相当
PARCEL_ROWS = [
    # machiaza_id, prc_id, prc_num1, prc_num2, prc_num3, rep_lon, rep_lat
    ("0001", "P1", "673", "", "", "127.6801", "26.1201"),
    ("0001", "P2", "673", "2", "", "127.6802", "26.1202"),
    ("0001", "P3", "6732", "", "", "127.6900", "26.1300"),
    ("0001", "P4", "1", "2", "3", "127.6803", "26.1203"),
    ("0002", "P5", "1448", "", "", "127.6700", "26.1400"),
    ("0003", "P6", "1444", "1", "", "127.6600", "26.1500"),
    ("0004", "P7", "1", "", "", "127.6000", "26.1000"),
    ("0006", "P8", "12", "3", "", "127.6400", "26.1700"),
    ("0007", "P9", "1", "", "", "127.6100", "26.1100"),
    ("0008", "P10", "1", "", "", "127.6200", "26.1200"),
]


def load_address_logic():
    """Notebookの「住所変換ロジック定義」セルを読み込み、架空のABRマスターを差し込む。"""
    cell = json.loads(NOTEBOOK_PATH.read_text())["cells"][ADDRESS_LOGIC_CELL]["source"]
    # セル末尾のTOWN_INDEX / PARCEL_MASTER生成は、ここで架空マスターから作り直す
    cell = cell.replace('if master_status.get("extract"):', "if False:")
    cell = cell.replace('if master_status.get("merge"):', "if False:")

    env = {"re": re, "unicodedata": unicodedata, "pd": pd,
           "PREF_NAME": "沖縄県", "CITY_NAME": "糸満市",
           "master_status": {}, "print": lambda *args, **kwargs: None}
    exec(compile(cell, str(NOTEBOOK_PATH), "exec"), env)

    columns = ["machiaza_id", "oaza_cho", "chome", "koaza", "machiaza_dist", "rsdt_addr_flg"]
    town = pd.DataFrame(TOWN_ROWS, columns=columns)
    town["town_label"] = town["oaza_cho"] + town["chome"] + town["koaza"] + town["machiaza_dist"]

    parcel = pd.DataFrame(PARCEL_ROWS, columns=["machiaza_id", "prc_id", "prc_num1", "prc_num2",
                                                "prc_num3", "rep_lon", "rep_lat"])
    parcel["rep_lon"] = pd.to_numeric(parcel["rep_lon"])
    parcel["rep_lat"] = pd.to_numeric(parcel["rep_lat"])
    parcel = parcel.merge(town[["machiaza_id", "town_label", "rsdt_addr_flg"]],
                          on="machiaza_id", how="left")

    env["TOWN_INDEX"] = env["build_town_index"](town)
    env["PARCEL_MASTER"] = parcel
    return env


LOGIC = load_address_logic()


def geocode(address):
    return LOGIC["geocode_one_address"](address)


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
                self.assertEqual(result["parcel_number"], "673-2")
                self.assertEqual(result["prc_id"], expected["prc_id"])
                self.assertEqual((result["latitude"], result["longitude"]),
                                 (expected["latitude"], expected["longitude"]))

    def test_枝番なしの地番(self):
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

    def test_住居表示の町字は地番を確定させない(self):
        """住居表示の町字は、丁目の表記に関わらずresidential_display_areaとして保留する。"""
        for address in ("沖縄県糸満市西崎町1丁目1番地", "沖縄県糸満市西崎町１丁目1番地",
                        "沖縄県糸満市西崎町一丁目1番地"):
            with self.subTest(address):
                result = geocode(address)
                self.assertEqual(result["status"], "residential_display_area")
                self.assertIsNone(result["latitude"])
                self.assertIsNone(result["longitude"])


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

    def test_接頭辞を省略した入力だけ曖昧扱いにする(self):
        """「上里」は「字上里」「大字上里」のどちらとも判断できないためambiguous_townとする。"""
        self.assertEqual(geocode("糸満市上里1番地")["status"], "ambiguous_town")

    def test_接頭辞の無い町字に字大字の別名を作らない(self):
        """正式名称が「字」「大字」で始まらない町字（西崎町1丁目）に、存在しない別名を作らない。"""
        self.assertEqual(geocode("沖縄県糸満市西崎町1丁目1番地")["status"], "residential_display_area")
        for address in ("沖縄県糸満市字西崎町1丁目1番地", "沖縄県糸満市大字西崎町1丁目1番地"):
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

    def test_方書や部屋番号が付く住所は地番を推測しない(self):
        for address in ("糸満市字糸満673番地2 みどり荘101", "糸満市字糸満673番地2号室"):
            with self.subTest(address):
                self.assertEqual(geocode(address)["status"], "parcel_not_found")

    def test_存在しない地番は確定させない(self):
        for address in ("糸満市字糸満9999番地", "糸満市字糸満673番地99"):
            with self.subTest(address):
                self.assertEqual(geocode(address)["status"], "parcel_not_found")

    def test_市外や空欄の住所(self):
        self.assertEqual(geocode("沖縄県那覇市おもろまち1丁目1番地")["status"], "town_not_found")
        self.assertEqual(geocode("")["status"], "blank_address")
        self.assertEqual(geocode("   ")["status"], "blank_address")
        self.assertEqual(geocode(None)["status"], "blank_address")


class NormalizeAddressTest(unittest.TestCase):
    """正規化そのものの確認（丁目の漢数字だけを変換していること）。"""

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
