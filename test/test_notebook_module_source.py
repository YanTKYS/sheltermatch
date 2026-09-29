"""sheltermatch.ipynb が Google Colab 実行時に取得する外部モジュール（src/）の取得先の回帰テスト。

通常運用の Notebook は、実データで動作確認した版（タグ）の src/ を取得する。main へ変更が取り込まれても、
取得する版（SHELTERMATCH_CODE_REF）を変えない限り、実行されるコードは変わらない。ここでは次を確認する。

* 取得する版が v1.0.0 であること（版を更新するときは、このテストも意図した変更として一緒に更新する）
* main の src/ を取得先として使っていないこと
* hazard_loader・review_builder・review_template.html・road_routes が、すべて同じ取得元から取得されること
* 既存の API バージョンの確認（互換性のないモジュールでは止まる）がそのまま働くこと

外部通信は行わない。「外部モジュール準備」セルをそのまま実行し、requests.get だけを差し替えて、取得した
URL を記録する（返すのは API バージョンだけを持つ架空のモジュール）。

    python3 -m unittest discover -s test
"""
import ast
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

NOTEBOOK_PATH = Path(__file__).resolve().parent.parent / "sheltermatch.ipynb"
MODULE_CELL_TITLE = "# ===== 外部モジュール準備 ====="
ROAD_ROUTES_CELL_TITLE = "# ===== 道路に沿った参考経路の算出"

EXPECTED_REF = "v1.0.0"
EXPECTED_BASE_URL = f"https://raw.githubusercontent.com/YanTKYS/sheltermatch/{EXPECTED_REF}/src"

# 取得するファイルと、v1.0.0 の src/ が持つ API バージョン（Notebook 側の想定値と一致している必要がある）
EXPECTED_MODULES = {
    "hazard/hazard_loader.py": ("hazard_loader", "HAZARD_LOADER_API_VERSION", 1),
    "review/review_builder.py": ("review_builder", "REVIEW_BUILDER_API_VERSION", 2),
    "review/road_routes.py": ("road_routes", "ROAD_ROUTES_API_VERSION", 1),
}
EXPECTED_FILES = {"review/review_template.html"}


def code_cells():
    notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
    cells = []
    for cell in notebook["cells"]:
        if cell["cell_type"] != "code":
            continue
        source = cell["source"]
        cells.append(source if isinstance(source, str) else "".join(source))
    return cells


def cell_starting_with(title):
    matches = [source for source in code_cells() if source.startswith(title)]
    assert len(matches) == 1, f"{title} のセルが1つではありません（{len(matches)}件）"
    return matches[0]


def parse(source):
    """Notebook 固有の行（%pip 等）を除いて構文解析する。"""
    lines = ["" if line.lstrip().startswith(("%", "!")) else line for line in source.splitlines()]
    return ast.parse("\n".join(lines))


def assignments(name):
    """すべてのコードセルから、name への代入を集める。"""
    found = []
    for source in code_cells():
        for node in ast.walk(parse(source)):
            if isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == name for target in node.targets):
                found.append(node)
    return found


def fetch_calls(source):
    """セルの処理（関数定義の中を除く）での download_from_github / import_module_from_github の呼び出しを
    集める。引数はすべて定数であること（取得するファイルがセルに直接書かれていること）を前提にする。"""
    calls = []

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            pass  # 取得関数の定義の中（download_from_github(source_path) 等）は対象外

        def visit_Call(self, node):
            if isinstance(node.func, ast.Name) and node.func.id in (
                    "download_from_github", "import_module_from_github"):
                calls.append((node.func.id, [ast.literal_eval(arg) for arg in node.args], node.keywords))
            self.generic_visit(node)

    Visitor().visit(parse(source))
    return calls


class FakeResponse:
    def __init__(self, content, error=None):
        self.content = content
        self._error = error

    def raise_for_status(self):
        if self._error is not None:
            raise self._error


class FakeRequests:
    """requests.get の差し替え。取得した URL を記録し、架空のモジュールを返す。"""

    def __init__(self, contents):
        self.contents = contents
        self.urls = []

    def get(self, url, timeout=None, **kwargs):
        self.urls.append(url)
        for source_path, content in self.contents.items():
            if url.endswith("/" + source_path):
                if isinstance(content, Exception):
                    raise content
                return FakeResponse(content)
        return FakeResponse(b"", error=RuntimeError(f"404 Not Found: {url}"))


def module_contents(versions=None):
    versions = versions or {}
    contents = {}
    for source_path, (_, attr, version) in EXPECTED_MODULES.items():
        contents[source_path] = f"{attr} = {versions.get(source_path, version)!r}\n".encode("utf-8")
    for source_path in EXPECTED_FILES:
        contents[source_path] = b"<!doctype html>\n"
    return contents


class ModuleCellRun:
    """「外部モジュール準備」セルを、requests.get だけを差し替えてそのまま実行する。"""

    def __init__(self, contents):
        self.requests = FakeRequests(contents)
        self.namespace = {"Path": Path, "requests": self.requests, "__name__": "__main__"}
        self.output = io.StringIO()
        self._workdir = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()

    def __enter__(self):
        os.chdir(self._workdir.name)  # セルが作る sheltermatch_modules/ を一時ディレクトリへ置く
        return self

    def __exit__(self, *exc):
        os.chdir(self._cwd)
        self._workdir.cleanup()

    def run(self):
        with contextlib.redirect_stdout(self.output):
            exec(compile(cell_starting_with(MODULE_CELL_TITLE), "<外部モジュール準備>", "exec"), self.namespace)

    def call(self, name, *args):
        with contextlib.redirect_stdout(self.output):
            return self.namespace[name](*args)


class ModuleSourceStaticTest(unittest.TestCase):
    """Notebook 内の文字列・代入の確認。"""

    def test_取得する版はv1_0_0(self):
        nodes = assignments("SHELTERMATCH_CODE_REF")
        self.assertEqual(len(nodes), 1, "SHELTERMATCH_CODE_REF はNotebook全体で1か所だけで定義する")
        self.assertEqual(ast.literal_eval(nodes[0].value), EXPECTED_REF)

    def test_取得元のURLは版から組み立てる(self):
        nodes = assignments("GITHUB_RAW_BASE_URL")
        self.assertEqual(len(nodes), 1, "GITHUB_RAW_BASE_URL はNotebook全体で1か所だけで定義する")
        expression = compile(ast.Expression(nodes[0].value), "<GITHUB_RAW_BASE_URL>", "eval")
        self.assertEqual(eval(expression, {"SHELTERMATCH_CODE_REF": EXPECTED_REF}), EXPECTED_BASE_URL)
        # 版を変えるとURLも変わる（版がURLへ直接書かれていない）
        self.assertEqual(eval(expression, {"SHELTERMATCH_CODE_REF": "other-ref"}),
                         "https://raw.githubusercontent.com/YanTKYS/sheltermatch/other-ref/src")

    def test_mainのsrcを取得先に使わない(self):
        for source in code_cells():
            self.assertNotIn("/main/src", source)
            self.assertNotIn("sheltermatch/main", source)
        # GitHub raw のURLは GITHUB_RAW_BASE_URL の定義の1か所だけ（モジュールごとに別の取得元を持たない）
        occurrences = sum(source.count("raw.githubusercontent.com") for source in code_cells())
        self.assertEqual(occurrences, 1)

    def test_取得するファイルとAPIバージョンの想定値(self):
        calls = fetch_calls(cell_starting_with(MODULE_CELL_TITLE))
        calls += fetch_calls(cell_starting_with(ROAD_ROUTES_CELL_TITLE))
        imported = {args[0]: tuple(args[1:]) for name, args, _ in calls if name == "import_module_from_github"}
        downloaded = {args[0] for name, args, _ in calls if name == "download_from_github" and args}
        self.assertEqual(imported, EXPECTED_MODULES)
        self.assertEqual(downloaded, EXPECTED_FILES)
        # 取得関数へ版や取得元を個別に渡していない
        for _, _, keywords in calls:
            self.assertEqual(keywords, [])

    def test_取得関数は取得元を引数に取らない(self):
        functions = {node.name: [arg.arg for arg in node.args.args]
                     for node in ast.walk(parse(cell_starting_with(MODULE_CELL_TITLE)))
                     if isinstance(node, ast.FunctionDef)}
        self.assertEqual(functions["download_from_github"], ["source_path"])
        self.assertEqual(functions["import_module_from_github"],
                         ["source_path", "module_name", "api_version_attr", "expected_version"])


class ModuleSourceRunTest(unittest.TestCase):
    """「外部モジュール準備」セルの実行（外部通信は差し替え）。"""

    def test_すべての外部モジュールを同じ版から取得する(self):
        with ModuleCellRun(module_contents()) as run:
            run.run()
            # 道路経路のセルと同じ呼び出しで road_routes を取得する
            for name, args, _ in fetch_calls(cell_starting_with(ROAD_ROUTES_CELL_TITLE)):
                run.call(name, *args)
            self.assertEqual(run.namespace["SHELTERMATCH_CODE_REF"], EXPECTED_REF)
            self.assertEqual(run.namespace["GITHUB_RAW_BASE_URL"], EXPECTED_BASE_URL)
            self.assertEqual(
                sorted(run.requests.urls),
                sorted(f"{EXPECTED_BASE_URL}/{path}" for path in [*EXPECTED_MODULES, *EXPECTED_FILES]),
            )

    def test_取得している版を表示する(self):
        with ModuleCellRun(module_contents()) as run:
            run.run()
            text = run.output.getvalue()
        self.assertIn(f"外部モジュール: {EXPECTED_REF}", text)
        self.assertIn(EXPECTED_BASE_URL, text)

    def test_APIバージョンが一致すれば読み込む(self):
        with ModuleCellRun(module_contents()) as run:
            run.run()
            self.assertEqual(run.namespace["hazard_loader"].HAZARD_LOADER_API_VERSION, 1)
            self.assertEqual(run.namespace["review_builder"].REVIEW_BUILDER_API_VERSION, 2)
            self.assertTrue(Path(run.namespace["REVIEW_TEMPLATE_PATH"]).is_file())
            road_routes = run.call("import_module_from_github", "review/road_routes.py", "road_routes",
                                   "ROAD_ROUTES_API_VERSION", 1)
            self.assertEqual(road_routes.ROAD_ROUTES_API_VERSION, 1)

    def test_APIバージョンが異なれば止まる(self):
        contents = module_contents({"review/review_builder.py": 3})
        with ModuleCellRun(contents) as run:
            with self.assertRaises(RuntimeError) as caught:
                run.run()
        message = str(caught.exception)
        self.assertIn("review_builder の互換性を確認できません", message)
        self.assertIn("想定しているバージョン: 2", message)
        self.assertIn("取得したモジュールのバージョン: 3", message)
        self.assertIn(EXPECTED_REF, message)
        self.assertNotIn("main", message)

    def test_APIバージョンが無ければ止まる(self):
        contents = module_contents()
        contents["hazard/hazard_loader.py"] = "# API バージョンを持たないモジュール\n".encode("utf-8")
        with ModuleCellRun(contents) as run:
            with self.assertRaises(RuntimeError) as caught:
                run.run()
        self.assertIn("hazard_loader の互換性を確認できません", str(caught.exception))

    def test_取得できないときは版とURLと通信エラーを示す(self):
        contents = module_contents()
        contents["hazard/hazard_loader.py"] = ConnectionError("接続できません（テスト用）")
        with ModuleCellRun(contents) as run:
            with self.assertRaises(RuntimeError) as caught:
                run.run()
        message = str(caught.exception)
        self.assertIn("外部モジュールをGitHubから取得できませんでした", message)
        self.assertIn(f"取得対象の版: {EXPECTED_REF}", message)
        self.assertIn(f"{EXPECTED_BASE_URL}/hazard/hazard_loader.py", message)
        self.assertIn("接続できません（テスト用）", message)


if __name__ == "__main__":
    unittest.main()
