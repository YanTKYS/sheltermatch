"""比較実験Notebook（experiments/gsi_shelter_compare/gsi_shelter_compare.ipynb）の軽量な静的テスト。

Notebookは実行せず、ソースを読んで次を確認する。外部通信は行わない。

* 比較ロジックは compare_logic.py を読み込んで使い、Notebookへコピーしていない
* BODIKの resource_id が本番の sheltermatch.ipynb と同じ、GSIのレイヤーが skhb01〜skhb08
* 本番のデータソース切替（SHELTER_SOURCE）や、要支援者CSV・ハザード・本番結果CSVの扱いが無い
* 取得元URLが1か所だけで、実行結果（出力）がNotebookに残っていない

    python3 -m unittest discover -s test
"""
import json
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = REPO_ROOT / "experiments" / "gsi_shelter_compare"
sys.path.insert(0, str(EXPERIMENT_DIR))
import compare  # noqa: E402  通信するのは main() の中だけなので、import しても通信しない
import compare_logic as cl  # noqa: E402


def load_notebook(path):
    return json.loads(path.read_text(encoding="utf-8"))


def code_source(notebook):
    return "\n".join("".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "code")


NOTEBOOK = load_notebook(EXPERIMENT_DIR / "gsi_shelter_compare.ipynb")
SOURCE = code_source(NOTEBOOK)

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class NotebookStructureTest(unittest.TestCase):
    def test_最初のセルは説明で_GSIを正解としないこと等を明記している(self):
        first = NOTEBOOK["cells"][0]
        self.assertEqual(first["cell_type"], "markdown")
        text = "".join(first["source"])
        for phrase in ("正解データとして", "データソースを変更するものではありません", "公開データだけ", "個人情報は使いません"):
            self.assertIn(phrase, text)

    def test_利用者設定のセルに通常変更する値がまとまっている(self):
        settings = [c for c in NOTEBOOK["cells"] if c["cell_type"] == "code" and "利用者設定" in "".join(c["source"])[:40]]
        self.assertEqual(len(settings), 1)
        text = "".join(settings[0]["source"])
        for name in ("TILE_RING", "EXACT_MAX_DISTANCE_M", "NEAR_DISTANCE_M"):
            self.assertRegex(text, rf"(?m)^{name} = ")

    def test_実行結果は保存していない(self):
        for cell in NOTEBOOK["cells"]:
            if cell["cell_type"] == "code":
                self.assertEqual(cell["outputs"], [])
                self.assertIsNone(cell["execution_count"])


class NotebookUsesSharedLogicTest(unittest.TestCase):
    def test_compare_logicを読み込んで使う(self):
        self.assertIn("import compare_logic as cl", SOURCE)
        for function in ("merge_gsi_layers", "assign_gsi_scope", "compare_facilities", "summarize", "tiles_for_points"):
            self.assertIn(f"cl.{function}(", SOURCE)
            self.assertTrue(callable(getattr(cl, function)))

    def test_比較ロジックをNotebookへコピーしていない(self):
        for name in ("normalize_text", "merge_gsi_layers", "assign_gsi_scope", "compare_facilities", "summarize",
                     "distance_stats", "classify_pair", "parse_coordinate", "tile_xy"):
            self.assertNotRegex(SOURCE, rf"def {name}\b")
        self.assertNotIn("geodesic", SOURCE.replace("geopy.distance.geodesic）", ""))
        self.assertNotIn("unicodedata", SOURCE)

    def test_取得とCSV出力はcompare_pyの関数を使う(self):
        for function in ("fetch_bodik_records", "bodik_facilities", "fetch_gsi_features", "write_comparison_csv",
                         "write_gsi_unique_csv"):
            self.assertIn(f"compare.{function}(", SOURCE)
            self.assertTrue(callable(getattr(compare, function)))

    def test_取得元URLは1か所だけで_版はCOMPARE_LOGIC_REFで決まる(self):
        self.assertEqual(SOURCE.count("raw.githubusercontent.com"), 1)
        self.assertEqual(len(re.findall(r"(?m)^COMPARE_LOGIC_REF = ", SOURCE)), 1)
        self.assertEqual(SOURCE.count("{COMPARE_LOGIC_REF}"), 2)  # 取得URLと表示
        self.assertIn('COMPARE_LOGIC_REF = "main"', SOURCE)
        self.assertEqual(SOURCE.count("https://"), 1)


class NotebookDataSourceTest(unittest.TestCase):
    def test_BODIKのresource_idは本番Notebookと同じ(self):
        production = code_source(load_notebook(REPO_ROOT / "sheltermatch.ipynb"))
        match = re.search(r'BODIK_RESOURCE_ID = "([0-9a-f-]{36})"', production)
        self.assertIsNotNone(match)
        self.assertEqual(compare.BODIK_RESOURCE_ID, match.group(1))
        self.assertEqual(compare.BODIK_BASE_URL, "https://data.bodik.jp")
        # Notebookが独自の別のresource_idを持っていないこと（compare.BODIK_RESOURCE_ID を使う）
        self.assertEqual(set(UUID_RE.findall(SOURCE)), set())
        self.assertIn("compare.BODIK_RESOURCE_ID", SOURCE)

    def test_GSIのレイヤーはskhb01からskhb08(self):
        self.assertEqual(list(cl.GSI_LAYERS), [f"skhb0{i}" for i in range(1, 9)])
        self.assertIn("cl.GSI_LAYERS", SOURCE)
        self.assertNotIn("sih", SOURCE)
        self.assertNotIn("sfh", SOURCE)
        self.assertEqual(compare.GSI_ZOOM, 10)

    def test_件数を固定値として埋め込んでいない(self):
        # 公開データの更新で変わる件数（BODIK 45件など）を、Notebookの仕様として書いていない
        for number in ("45", "80", "18", "27", "40", "352", "1109", "4877"):
            self.assertNotRegex(SOURCE, rf"(?<![\w.]){number}(?![\w.])")


class NotebookIsolatedFromProductionTest(unittest.TestCase):
    def test_本番のデータソース切替や要支援者データを扱わない(self):
        for forbidden in ("SHELTER_SOURCE", "files.upload", "resident", "assigned_shelters", "geocode_status",
                          "hazard", "ENABLE_HAZARD_CHECK", "read_csv"):
            self.assertNotIn(forbidden, SOURCE)

    def test_導入するライブラリは必要な3つだけ(self):
        installs = re.findall(r"(?m)^%pip install (.+)$", SOURCE)
        self.assertEqual(len(installs), 1)
        self.assertEqual(sorted(installs[0].replace("-q", "").split()), ["geopy", "pandas", "requests"])

    def test_ダウンロードはColabの場合だけ(self):
        self.assertEqual(SOURCE.count("files.download("), 2)
        self.assertIn("if IN_COLAB:", SOURCE)


if __name__ == "__main__":
    unittest.main()
