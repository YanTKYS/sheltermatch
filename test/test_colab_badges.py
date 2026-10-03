"""リポジトリ内のすべての Notebook（.ipynb）が、タイトル直下に「Open In Colab」バッジを持つことの静的テスト。

GitHub 上で Notebook を開いた利用者が、ダウンロードや URL の入力をせずに Google Colab で開けるようにするための
入口で、main 上の Notebook を開く。Notebook 内部の取得する版（SHELTERMATCH_CODE_REF 等）とは別のもの。

Notebook は実行せず、JSON として読んで最初の Markdown セルを確認するだけ（外部通信なし）。.ipynb は
リポジトリ内を自動で列挙するため、今後 Notebook を追加したときにバッジを付け忘れると、このテストが失敗する。

    python3 -m unittest discover -s test
"""
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BADGE_IMAGE = "https://colab.research.google.com/assets/colab-badge.svg"
COLAB_BASE = "https://colab.research.google.com/github/YanTKYS/sheltermatch/blob/main/"

SKIPPED_DIRS = {"node_modules", "venv", "env", "__pycache__"}


def find_notebooks():
    """リポジトリ内のすべての .ipynb（隠しフォルダ・仮想環境・チェックポイントを除く）。"""
    notebooks = []
    for path in sorted(REPO.rglob("*.ipynb")):
        parts = path.relative_to(REPO).parts[:-1]
        if any(part.startswith(".") or part in SKIPPED_DIRS for part in parts):
            continue
        notebooks.append(path)
    return notebooks


def first_markdown_source(path):
    notebook = json.loads(path.read_text(encoding="utf-8"))
    for cell in notebook["cells"]:
        if cell["cell_type"] == "markdown":
            source = cell["source"]
            return source if isinstance(source, str) else "".join(source), notebook["cells"][0] is cell
    return None, False


class ColabBadgeTest(unittest.TestCase):
    def test_Notebookを列挙できる(self):
        names = [path.relative_to(REPO).as_posix() for path in find_notebooks()]
        self.assertIn("sheltermatch.ipynb", names)
        self.assertIn("address_geocode.ipynb", names)

    def test_すべてのNotebookがJSONとして読み込める(self):
        for path in find_notebooks():
            with self.subTest(path.relative_to(REPO).as_posix()):
                notebook = json.loads(path.read_text(encoding="utf-8"))
                self.assertIn("cells", notebook)

    def test_すべてのNotebookのタイトル直下にColabバッジがある(self):
        for path in find_notebooks():
            relative = path.relative_to(REPO).as_posix()
            with self.subTest(relative):
                source, is_first_cell = first_markdown_source(path)
                self.assertIsNotNone(source, "Markdownセルがありません")
                self.assertTrue(is_first_cell, "最初のセルがMarkdownではありません")
                expected = f"[![Open In Colab]({BADGE_IMAGE})]({COLAB_BASE}{relative})"
                lines = source.split("\n")
                # タイトル（# 見出し）→ 空行 → バッジ、の並び。説明文の途中や末尾には置かない
                self.assertTrue(lines[0].startswith("# "), f"1行目がタイトルではありません: {lines[0]!r}")
                self.assertGreaterEqual(len(lines), 3, "タイトル直下にバッジがありません")
                self.assertEqual(lines[1], "")
                self.assertEqual(lines[2], expected, "バッジの画像・リンク先がNotebook自身のパスと一致しません")
                self.assertEqual(source.count("Open In Colab"), 1)

    def test_リンク先はmainブランチのNotebook自身(self):
        # 版を固定するタグ（v1.2.0 等）ではなく、main 上の最新の Notebook を開く入口であること
        for path in find_notebooks():
            relative = path.relative_to(REPO).as_posix()
            with self.subTest(relative):
                source, _ = first_markdown_source(path)
                self.assertIn(f"/blob/main/{relative})", source)

    def test_ルートREADMEから通常利用のNotebookをColabで開ける(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        for relative in ("sheltermatch.ipynb", "address_geocode.ipynb"):
            self.assertIn(f"[![Open In Colab]({BADGE_IMAGE})]({COLAB_BASE}{relative})", readme)


if __name__ == "__main__":
    unittest.main()
