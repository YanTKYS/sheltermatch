"""自治体ごとの設定ファイル（configs/<設定名>.json）と、その読込モジュール（src/config/municipality_config.py）の
回帰テスト。

* configs/itoman-city.json が正しいJSONで、必須項目・型・値が妥当であり、従来のNotebookの固定値
  （BODIKのresource_id・自治体名・自治体コード）と一致すること
* 設定ファイルの値がNotebookの既定値より優先され、項目が無いときはNotebookの既定値になること
* 設定ファイルを取得・解釈できないときは、Notebookの既定値で続行せず止まること（キーが無いこととは区別する）
* 不正な型・値は、補正せずに止まること（"true" や 1 を真偽値へ変換しない）
* sheltermatch.ipynb / address_geocode.ipynb / gsi_shelter_compare.ipynb が、設定ファイルの値を使うこと

完全な架空の設定を使い、外部通信は行わない（requests.get は差し替え、想定外のURLはテストを失敗させる）。

    python3 -m unittest discover -s test
"""
import contextlib
import copy
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from notebook_harness import load_src_module

REPO = Path(__file__).resolve().parent.parent
ITOMAN_CONFIG_PATH = REPO / "configs" / "itoman-city.json"
GSI_NOTEBOOK = REPO / "experiments" / "gsi_shelter_compare" / "gsi_shelter_compare.ipynb"
sys.path.insert(0, str(REPO / "experiments" / "gsi_shelter_compare"))

mc = load_src_module("config/municipality_config.py", "municipality_config_under_test")

# 従来、Notebookに固定されていた糸満市の値（設定ファイルへ移したあとも変わっていないことの確認用）
EXPECTED_ITOMAN_RESOURCE_ID = "3132a0a4-f522-4b2d-bf18-f106d8b3a5ae"
RAW_REPO_URL = "https://raw.githubusercontent.com/YanTKYS/sheltermatch/test-ref"
# 通常運用のNotebook（sheltermatch.ipynb・address_geocode.ipynb）が、外部モジュールと設定ファイルを取得する版（タグ）
EXPECTED_RELEASE_REF = "v1.2.0"


def valid_config(**sections):
    """完全な架空の設定（設定名 test-city）。sections で、セクションを置き換える・足す。"""
    config = {
        "schema_version": 1,
        "municipality": {"id": "test-city", "name": "架空市", "code": "99999"},
        "bodik": {"resource_id": "00000000-1111-2222-3333-444444444444"},
    }
    config.update(sections)
    return config


class FakeResponse:
    def __init__(self, content=b"", status_code=200):
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeGitHub:
    """requests.get の差し替え。取得したURLを記録する。files は {URL: 内容(bytes) または 例外} 。"""

    def __init__(self, files):
        self.files = files
        self.urls = []

    def __call__(self, url, *args, **kwargs):
        self.urls.append(url)
        if url not in self.files:
            return FakeResponse(status_code=404)
        content = self.files[url]
        if isinstance(content, Exception):
            raise content
        return FakeResponse(content)


def config_url(name="test-city"):
    return f"{RAW_REPO_URL}/configs/{name}.json"


@contextlib.contextmanager
def isolated_cwd():
    """ローカルの configs/ を拾わない（リポジトリの外の）一時フォルダへ移動する。"""
    with tempfile.TemporaryDirectory() as directory:
        previous = os.getcwd()
        os.chdir(directory)
        try:
            yield Path(directory)
        finally:
            os.chdir(previous)


def fetch(config_name, files):
    """GitHubからの取得として load_config を呼ぶ。(結果, FakeGitHub) を返す。"""
    github = FakeGitHub(files)
    with isolated_cwd(), mock.patch("requests.get", github):
        return mc.load_config(config_name, RAW_REPO_URL), github


class ItomanConfigFileTest(unittest.TestCase):
    def setUp(self):
        self.text = ITOMAN_CONFIG_PATH.read_text(encoding="utf-8")
        self.config = json.loads(self.text)

    def test_正しいJSONで検証を通る(self):
        self.assertIs(mc.validate_config(self.config, "itoman-city"), self.config)

    def test_スキーマと必須項目(self):
        self.assertEqual(self.config["schema_version"], 1)
        self.assertEqual(self.config["municipality"]["id"], "itoman-city")
        self.assertEqual(self.config["municipality"]["name"], "糸満市")
        self.assertEqual(self.config["municipality"]["code"], "47210")
        self.assertEqual(self.config["municipality"]["prefecture"], "沖縄県")
        self.assertIn("resource_id", self.config["bodik"])

    def test_BODIKのresource_idは従来値と一致する(self):
        self.assertEqual(self.config["bodik"]["resource_id"], EXPECTED_ITOMAN_RESOURCE_ID)

    def test_sheltermatchの設定の型と値(self):
        sheltermatch = self.config["sheltermatch"]
        self.assertIs(type(sheltermatch["enable_hazard_check"]), bool)
        self.assertTrue(sheltermatch["enable_hazard_check"])
        self.assertIn(sheltermatch["shelter_source"], ("api", "csv"))

    def test_比較実験の設定は従来のNotebook既定値と同じ(self):
        compare = self.config["gsi_shelter_compare"]
        self.assertEqual((compare["tile_ring"], compare["exact_max_distance_m"], compare["near_distance_m"]),
                         (1, 30, 100))

    def test_内部実装の値は設定ファイルへ入れていない(self):
        for word in ("skhb", "API_VERSION", "api_version", "layer", "ASSIGNMENT"):
            self.assertNotIn(word, self.text)
        self.assertEqual(set(self.config), {"schema_version", "municipality", "bodik", "sheltermatch",
                                            "gsi_shelter_compare"})

    def test_設定ファイルから6桁の自治体コードを計算するとBODIK_ABRのコードと一致する(self):
        self.assertEqual(mc.local_government_code(self.config["municipality"]["code"]), "472107")

    def test_ローカルの設定ファイルを読み込める(self):
        config, origin = mc.load_config("itoman-city", "https://invalid.example/never-used", start=REPO)
        self.assertEqual(config, self.config)
        self.assertEqual(Path(origin), ITOMAN_CONFIG_PATH)


class ValidationTest(unittest.TestCase):
    def check_stops(self, config, fragment=None, name="test-city"):
        with self.assertRaises(mc.ConfigError) as caught:
            mc.validate_config(config, name)
        if fragment:
            self.assertIn(fragment, str(caught.exception))

    def test_最小の設定は通る(self):
        mc.validate_config(valid_config(), "test-city")

    def test_schema_versionは1だけ(self):
        for value in (2, 0, "1", 1.0, True, None):
            config = valid_config()
            config["schema_version"] = value
            self.check_stops(config, "schema_version")
        config = valid_config()
        del config["schema_version"]
        self.check_stops(config, "schema_version")

    def test_必須項目が無ければ止まる(self):
        for section, key in (("municipality", "id"), ("municipality", "name"), ("municipality", "code"),
                             ("bodik", "resource_id")):
            config = valid_config()
            del config[section][key]
            self.check_stops(config, f"{section}.{key}")
        for section in ("municipality", "bodik"):
            config = valid_config()
            del config[section]
            self.check_stops(config, section)

    def test_必須項目の型と値(self):
        for section, key, value in (("municipality", "name", ""), ("municipality", "name", 5),
                                    ("municipality", "code", "4721"), ("municipality", "code", 47210),
                                    ("municipality", "code", "4721a"), ("municipality", "id", "other-city"),
                                    ("bodik", "resource_id", "not-a-uuid"), ("bodik", "resource_id", 1)):
            config = valid_config()
            config[section][key] = value
            self.check_stops(config, f"{section}.{key}")

    def test_enable_hazard_checkは真偽値だけ(self):
        for value in (True, False):
            mc.validate_config(valid_config(sheltermatch={"enable_hazard_check": value}), "test-city")
        for value in ("true", "True", "false", 1, 0, None, "yes"):
            self.check_stops(valid_config(sheltermatch={"enable_hazard_check": value}),
                             "sheltermatch.enable_hazard_check")

    def test_shelter_sourceはapiかcsvだけ(self):
        for value in ("api", "csv"):
            mc.validate_config(valid_config(sheltermatch={"shelter_source": value}), "test-city")
        for value in ("API", "gsi", "", None, True):
            self.check_stops(valid_config(sheltermatch={"shelter_source": value}), "sheltermatch.shelter_source")

    def test_比較実験の数値設定(self):
        mc.validate_config(valid_config(gsi_shelter_compare={"tile_ring": 0, "exact_max_distance_m": 0.5,
                                                              "near_distance_m": 100}), "test-city")
        for key, value in (("tile_ring", -1), ("tile_ring", 1.5), ("tile_ring", "1"), ("tile_ring", True),
                           ("exact_max_distance_m", 0), ("exact_max_distance_m", -30),
                           ("exact_max_distance_m", "30"), ("near_distance_m", 0), ("near_distance_m", None),
                           ("near_distance_m", False)):
            self.check_stops(valid_config(gsi_shelter_compare={key: value}), f"gsi_shelter_compare.{key}")

    def test_未知のキーは綴り間違いとして止まる(self):
        self.check_stops(valid_config(sheltermatch={"enable_hazard_chek": True}), "enable_hazard_chek")
        self.check_stops(valid_config(unknown_section={}), "unknown_section")
        config = valid_config()
        config["municipality"]["extra"] = 1
        self.check_stops(config, "extra")

    def test_セクションの型(self):
        self.check_stops(valid_config(sheltermatch=[]), "sheltermatch")
        self.check_stops([], "(全体)")

    def test_設定名の形式(self):
        for name in ("", "Itoman", "../itoman-city", "itoman city", "itoman/city", "-city", None):
            with self.assertRaises(mc.ConfigError):
                mc.config_relative_path(name)
        self.assertEqual(mc.config_relative_path("itoman-city"), "configs/itoman-city.json")


class PriorityTest(unittest.TestCase):
    def test_JSONに値があればJSON_無ければNotebook既定値(self):
        config = valid_config(sheltermatch={"enable_hazard_check": True})
        self.assertEqual(mc.resolve_setting(config, "sheltermatch", "enable_hazard_check", False),
                         (True, "JSON"))
        self.assertEqual(mc.resolve_setting(config, "sheltermatch", "shelter_source", "api"),
                         ("api", "Notebook既定値"))
        # セクションごと無い場合も、キーが無いのと同じ（Notebook既定値）
        self.assertEqual(mc.resolve_setting(valid_config(), "gsi_shelter_compare", "tile_ring", 7),
                         (7, "Notebook既定値"))

    def test_JSONの値がFalseや0でもJSONが優先される(self):
        config = valid_config(sheltermatch={"enable_hazard_check": False},
                              gsi_shelter_compare={"tile_ring": 0})
        self.assertEqual(mc.resolve_setting(config, "sheltermatch", "enable_hazard_check", True), (False, "JSON"))
        self.assertEqual(mc.resolve_setting(config, "gsi_shelter_compare", "tile_ring", 5), (0, "JSON"))

    def test_表示行(self):
        lines = mc.describe_settings([("ENABLE_HAZARD_CHECK", True, "JSON"), ("SHELTER_SOURCE", "api", "Notebook既定値")])
        self.assertEqual(lines, ["  ENABLE_HAZARD_CHECK = True [JSON]",
                                 "  SHELTER_SOURCE      = api  [Notebook既定値]"])
        info = mc.describe_municipality(valid_config(), "test-city", "ORIGIN")
        self.assertEqual(info, ["対象自治体: 架空市", "自治体コード: 99999",
                                "設定ファイル: configs/test-city.json（取得元: ORIGIN）"])

    def test_自治体コードの検査数字(self):
        self.assertEqual(mc.local_government_code("47210"), "472107")   # 糸満市
        self.assertEqual(mc.local_government_code("47201"), "472018")   # 那覇市
        self.assertEqual(mc.local_government_code("01100"), "011002")   # 札幌市
        for bad in ("4721", "472107", "abcde", ""):
            with self.assertRaises(mc.ConfigError):
                mc.local_government_code(bad)


class LoadConfigTest(unittest.TestCase):
    def test_GitHubから取得して検証する(self):
        text = json.dumps(valid_config(), ensure_ascii=False).encode("utf-8")
        (config, origin), github = fetch("test-city", {config_url(): text})
        self.assertEqual(config["municipality"]["name"], "架空市")
        self.assertEqual(origin, config_url())
        self.assertEqual(github.urls, [config_url()])

    def test_ローカルにあれば_ローカルを使い通信しない(self):
        github = FakeGitHub({})
        with isolated_cwd() as directory, mock.patch("requests.get", github):
            (directory / "configs").mkdir()
            (directory / "configs" / "test-city.json").write_text(json.dumps(valid_config()), encoding="utf-8")
            config, origin = mc.load_config("test-city", RAW_REPO_URL)
        self.assertEqual(config["municipality"]["id"], "test-city")
        self.assertEqual(github.urls, [])
        self.assertTrue(origin.endswith("test-city.json"))

    def test_ローカルの設定ファイルが不正ならGitHubへ切り替えず止まる(self):
        github = FakeGitHub({config_url(): json.dumps(valid_config()).encode("utf-8")})
        with isolated_cwd() as directory, mock.patch("requests.get", github):
            (directory / "configs").mkdir()
            (directory / "configs" / "test-city.json").write_text("{ broken", encoding="utf-8")
            with self.assertRaises(mc.ConfigError):
                mc.load_config("test-city", RAW_REPO_URL)
        self.assertEqual(github.urls, [])

    def test_取得失敗は黙って続行せず止まる(self):
        for label, files in (("404", {}), ("通信エラー", {config_url(): ConnectionError("接続できません")}),
                             ("JSON構文エラー", {config_url(): b"{ not json"}),
                             ("JSONの中身が不正", {config_url(): b'{"schema_version": 1}'}),
                             ("文字コード不正", {config_url(): b"\xff\xfe\x00"})):
            with self.subTest(label), self.assertRaises(mc.ConfigError) as caught:
                fetch("test-city", files)
            self.assertIn("configs/test-city.json", str(caught.exception))

    def test_エラーには原因と取得先が含まれる(self):
        with self.assertRaises(mc.ConfigError) as caught:
            fetch("test-city", {})
        message = str(caught.exception)
        self.assertIn(config_url(), message)
        self.assertIn("HTTP 404", message)
        with self.assertRaises(mc.ConfigError) as caught:
            fetch("test-city", {config_url(): b"{ not json"})
        self.assertIn("JSON", str(caught.exception))


# ---------------------------------------------------------------------------
# Notebook の「設定ファイル読込」セル
# ---------------------------------------------------------------------------

def notebook_cells(path):
    notebook = json.loads(Path(path).read_text(encoding="utf-8"))
    return ["".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "code"]


def cell_starting_with(path, title):
    matches = [s for s in notebook_cells(path) if s.startswith(title)]
    assert len(matches) == 1, f"{title} のセルが1つではありません（{len(matches)}件）"
    return matches[0]


class SheltermatchNotebookConfigTest(unittest.TestCase):
    """sheltermatch.ipynb の「利用者設定」→「設定ファイル読込」セルを、読込モジュールだけを用意して実行する。"""

    def run_cells(self, config_files, settings_edit=None):
        github = FakeGitHub(config_files)
        namespace = {"municipality_config": mc, "GITHUB_RAW_REPO_URL": RAW_REPO_URL}
        output = io.StringIO()
        settings = cell_starting_with(REPO / "sheltermatch.ipynb", "# ===== 利用者設定")
        if settings_edit:
            settings = settings_edit(settings)
        config_cell = cell_starting_with(REPO / "sheltermatch.ipynb", "# ===== 設定ファイル読込")
        with isolated_cwd(), mock.patch("requests.get", github), contextlib.redirect_stdout(output):
            exec(compile(settings, "<利用者設定>", "exec"), namespace)
            namespace["CONFIG_NAME"] = "test-city"  # 実験用の設定名（糸満市の設定ファイルを使わない）
            exec(compile(config_cell, "<設定ファイル読込>", "exec"), namespace)
        return namespace, output.getvalue(), github

    def files(self, config):
        return {config_url(): json.dumps(config, ensure_ascii=False).encode("utf-8")}

    def test_Notebookの既定値(self):
        settings = cell_starting_with(REPO / "sheltermatch.ipynb", "# ===== 利用者設定")
        self.assertIn('CONFIG_NAME = "itoman-city"', settings)
        self.assertIn("ENABLE_HAZARD_CHECK = False", settings)
        self.assertIn('SHELTER_SOURCE = "api"', settings)

    def test_JSONの値がNotebook既定値より優先される(self):
        config = valid_config(sheltermatch={"enable_hazard_check": True, "shelter_source": "csv"})
        namespace, output, _ = self.run_cells(self.files(config))
        self.assertIs(namespace["ENABLE_HAZARD_CHECK"], True)
        self.assertEqual(namespace["SHELTER_SOURCE"], "csv")
        self.assertIn("ENABLE_HAZARD_CHECK = True [JSON]", output)
        self.assertIn("SHELTER_SOURCE      = csv  [JSON]", output)

    def test_JSONにキーが無ければNotebook既定値で続行する(self):
        namespace, output, _ = self.run_cells(self.files(valid_config()))
        self.assertIs(namespace["ENABLE_HAZARD_CHECK"], False)
        self.assertEqual(namespace["SHELTER_SOURCE"], "api")
        self.assertIn("ENABLE_HAZARD_CHECK = False [Notebook既定値]", output)
        self.assertIn("SHELTER_SOURCE      = api   [Notebook既定値]", output)

    def test_一部のキーだけJSONにある場合は項目ごとに決まる(self):
        namespace, output, _ = self.run_cells(self.files(valid_config(sheltermatch={"enable_hazard_check": True})))
        self.assertIs(namespace["ENABLE_HAZARD_CHECK"], True)
        self.assertEqual(namespace["SHELTER_SOURCE"], "api")
        self.assertIn("[JSON]", output)
        self.assertIn("[Notebook既定値]", output)

    def test_Notebook既定値を書き換えても_キーが無ければその値になる(self):
        namespace, _, _ = self.run_cells(
            self.files(valid_config()),
            settings_edit=lambda s: s.replace("ENABLE_HAZARD_CHECK = False", "ENABLE_HAZARD_CHECK = True"))
        self.assertIs(namespace["ENABLE_HAZARD_CHECK"], True)

    def test_自治体固有の値は設定ファイルから取る(self):
        namespace, output, github = self.run_cells(self.files(valid_config()))
        self.assertEqual(namespace["MUNICIPALITY_NAME"], "架空市")
        self.assertEqual(namespace["BODIK_RESOURCE_ID"], "00000000-1111-2222-3333-444444444444")
        self.assertIn("対象自治体: 架空市", output)
        self.assertIn("自治体コード: 99999", output)
        self.assertIn("設定ファイル: configs/test-city.json", output)
        self.assertEqual(github.urls, [config_url()])

    def test_設定ファイルを取得できなければ止まり_既定値で続行しない(self):
        for label, files in (("404", {}), ("構文エラー", {config_url(): b"{ broken"}),
                             ("通信エラー", {config_url(): ConnectionError("down")})):
            with self.subTest(label), self.assertRaises(mc.ConfigError):
                self.run_cells(files)

    def test_不正な型や値では止まる(self):
        for sheltermatch in ({"enable_hazard_check": "true"}, {"enable_hazard_check": 1},
                             {"shelter_source": "gsi"}):
            with self.subTest(sheltermatch), self.assertRaises(mc.ConfigError):
                self.run_cells(self.files(valid_config(sheltermatch=sheltermatch)))

    def test_BODIKのresource_idはNotebookに固定されていない(self):
        for path in (REPO / "sheltermatch.ipynb", GSI_NOTEBOOK, REPO / "address_geocode.ipynb"):
            source = "\n".join(notebook_cells(path))
            self.assertNotIn(EXPECTED_ITOMAN_RESOURCE_ID, source, path.name)
        self.assertIn('BODIK_RESOURCE_ID = config["bodik"]["resource_id"]',
                      cell_starting_with(REPO / "sheltermatch.ipynb", "# ===== 設定ファイル読込"))


class ReleaseRefTest(unittest.TestCase):
    """通常運用のNotebookが取得する版（タグ）。版を更新するときは、このテストも意図した変更として更新する。"""

    @staticmethod
    def code_ref(path):
        matches = re.findall(r'(?m)^SHELTERMATCH_CODE_REF = "([^"]+)"', "\n".join(notebook_cells(path)))
        assert len(matches) == 1, path
        return matches[0]

    def test_通常運用の2つのNotebookは同じ版を取得する(self):
        self.assertEqual(self.code_ref(REPO / "sheltermatch.ipynb"), EXPECTED_RELEASE_REF)
        self.assertEqual(self.code_ref(REPO / "address_geocode.ipynb"), EXPECTED_RELEASE_REF)

    def test_比較実験は通常運用の版に固定されない(self):
        # experiments/ の比較実験は、通常の業務Notebookとは別で、main の比較ロジック・設定を取得する
        source = "\n".join(notebook_cells(GSI_NOTEBOOK))
        self.assertIn('COMPARE_LOGIC_REF = "main"', source)
        self.assertNotIn("SHELTERMATCH_CODE_REF", source)


class AddressGeocodeNotebookConfigTest(unittest.TestCase):
    """address_geocode.ipynb の「設定」セル（自治体名・自治体コード・都道府県名）。"""

    def run_settings(self, files):
        github = FakeGitHub(files)
        namespace = {"__name__": "__main__"}
        output = io.StringIO()
        source = cell_starting_with(REPO / "address_geocode.ipynb", "# ===== 設定")
        with isolated_cwd(), mock.patch("requests.get", github), contextlib.redirect_stdout(output):
            exec(compile(source, "<設定>", "exec"), namespace)
        return namespace, output.getvalue(), github

    def serve_repo(self, config_name_to_content):
        """作業ツリーの読込モジュールと、指定した設定ファイルを返すURLの辞書（版のタグは問わない）。"""
        ref_url = f"https://raw.githubusercontent.com/YanTKYS/sheltermatch/{EXPECTED_RELEASE_REF}"
        files = {f"{ref_url}/src/config/municipality_config.py":
                 (REPO / "src" / "config" / "municipality_config.py").read_bytes()}
        for name, content in config_name_to_content.items():
            files[f"{ref_url}/configs/{name}.json"] = content
        return files

    def test_糸満市の設定が従来のLG_CODE等と一致する(self):
        namespace, output, _ = self.run_settings(
            self.serve_repo({"itoman-city": ITOMAN_CONFIG_PATH.read_bytes()}))
        self.assertEqual(namespace["LG_CODE"], "472107")
        self.assertEqual(namespace["PREF_NAME"], "沖縄県")
        self.assertEqual(namespace["CITY_NAME"], "糸満市")
        self.assertIn("対象自治体: 糸満市", output)
        self.assertIn("LG_CODE   = 472107", output)

    def test_都道府県名が無ければNotebook既定値_あればJSON(self):
        config = json.loads(ITOMAN_CONFIG_PATH.read_text(encoding="utf-8"))
        del config["municipality"]["prefecture"]
        namespace, output, _ = self.run_settings(
            self.serve_repo({"itoman-city": json.dumps(config, ensure_ascii=False).encode("utf-8")}))
        self.assertEqual(namespace["PREF_NAME"], "沖縄県")
        self.assertIn("PREF_NAME = 沖縄県", output.replace("  ", " ").replace("PREF_NAME  ", "PREF_NAME "))
        self.assertIn("[Notebook既定値]", output)

    def test_設定ファイルが取得できなければ止まる(self):
        # Notebookが取得して読み込んだモジュールの ConfigError は、テストが読み込んだものとは別のクラスのため、名前で確認する
        with self.assertRaises(Exception) as caught:
            self.run_settings(self.serve_repo({}))
        self.assertEqual(type(caught.exception).__name__, "ConfigError")
        self.assertIn("configs/itoman-city.json", str(caught.exception))

    def test_外部モジュールと設定ファイルを同じ版から取得する(self):
        namespace, _, github = self.run_settings(
            self.serve_repo({"itoman-city": ITOMAN_CONFIG_PATH.read_bytes()}))
        self.assertEqual(namespace["SHELTERMATCH_CODE_REF"], EXPECTED_RELEASE_REF)
        prefix = f"https://raw.githubusercontent.com/YanTKYS/sheltermatch/{EXPECTED_RELEASE_REF}/"
        self.assertEqual(sorted(github.urls), [prefix + "configs/itoman-city.json",
                                               prefix + "src/config/municipality_config.py"])

    def test_読込モジュールが取得できなければ止まる(self):
        with self.assertRaises(RuntimeError):
            self.run_settings({})

    def test_住所変換のルールや出力の列は設定ファイルに依存しない(self):
        source = "\n".join(notebook_cells(REPO / "address_geocode.ipynb"))
        self.assertIn("resident_id,address,latitude,longitude,geocode_status",
                      json.dumps(json.loads((REPO / "address_geocode.ipynb").read_text(encoding="utf-8"))["cells"],
                                 ensure_ascii=False))
        # 設定ファイルから受け取るのは、自治体名・自治体コード（必須項目）と、都道府県名（resolve_setting）だけ
        self.assertEqual(source.count('config["municipality"]'), 2)
        self.assertEqual(source.count("resolve_setting("), 1)


class GsiCompareConfigTest(unittest.TestCase):
    """比較実験が、自治体名・resource_id・比較設定を設定ファイルから受け取り、比較ロジックは自治体に依存しないこと。"""

    def test_比較ロジックは自治体の固定値を持たない(self):
        import compare
        import compare_logic
        self.assertFalse(hasattr(compare_logic, "TARGET_CITY"))
        self.assertFalse(hasattr(compare, "BODIK_RESOURCE_ID"))
        source = (REPO / "experiments" / "gsi_shelter_compare" / "compare_logic.py").read_text(encoding="utf-8")
        compare_source = (REPO / "experiments" / "gsi_shelter_compare" / "compare.py").read_text(encoding="utf-8")
        for text in (source, compare_source):
            self.assertNotIn("3132a0a4", text)
            self.assertNotIn('"糸満市"', text)

    def test_scope判定は渡された自治体名を使う(self):
        import compare_logic as cl
        bodik = [cl.Facility("bodik", "A", "架空県架空市1-1", 26.1, 127.7)]

        def scope(address, city):
            gsi = cl.Facility("gsi", "B", address, 26.2, 127.8)
            return cl.assign_gsi_scope([gsi], bodik, city)[0].scope

        self.assertEqual(scope("架空県架空市2-2", "架空市"), "in_city")
        self.assertEqual(scope("架空県架空市2-2", "別の市"), "excluded_other_address")
        self.assertEqual(scope("沖縄県糸満市2-2", "糸満市"), "in_city")
        with self.assertRaises(TypeError):
            cl.assign_gsi_scope([], bodik)  # 自治体名は必須（既定値を持たない）

    def test_fetch_bodik_recordsはresource_idを引数で受け取る(self):
        import compare
        calls = []

        class Session:
            def get(self, url, params=None, timeout=None):
                calls.append(params["resource_id"])
                return type("R", (), {"raise_for_status": lambda self: None, "json": lambda self: {
                    "success": True, "result": {"records": [{"名称": "A"}], "total": 1}}})()

        compare.fetch_bodik_records(Session(), "00000000-1111-2222-3333-444444444444")
        self.assertEqual(calls, ["00000000-1111-2222-3333-444444444444"])
        with self.assertRaises(TypeError):
            compare.fetch_bodik_records(Session())

    def test_Notebookは設定ファイルの値で上書きし_自治体名を渡す(self):
        source = "\n".join(notebook_cells(GSI_NOTEBOOK))
        for key in ("tile_ring", "exact_max_distance_m", "near_distance_m"):
            self.assertIn(f'"gsi_shelter_compare", "{key}"', source)
        self.assertIn("cl.assign_gsi_scope(unique_gsi, bodik, MUNICIPALITY_NAME,", source)
        self.assertIn('MUNICIPALITY_NAME = config["municipality"]["name"]', source)
        self.assertIn("compare.fetch_bodik_records(session, BODIK_RESOURCE_ID)", source)
        settings = cell_starting_with(GSI_NOTEBOOK, "# ===== 利用者設定")
        for default in ('CONFIG_NAME = "itoman-city"', "TILE_RING = 1", "EXACT_MAX_DISTANCE_M = 30",
                        "NEAR_DISTANCE_M = 100"):
            self.assertIn(default, settings)

    def test_Notebookの設定ファイル読込セルの優先順位(self):
        github = FakeGitHub({config_url(): json.dumps(valid_config(
            gsi_shelter_compare={"tile_ring": 2, "near_distance_m": 50})).encode("utf-8")})
        namespace = {"municipality_config": mc, "RAW_REPO_URL": RAW_REPO_URL, "CONFIG_NAME": "test-city",
                     "TILE_RING": 1, "EXACT_MAX_DISTANCE_M": 30, "NEAR_DISTANCE_M": 100}
        output = io.StringIO()
        cell = cell_starting_with(GSI_NOTEBOOK, "# ===== 設定ファイル読込")
        with isolated_cwd(), mock.patch("requests.get", github), contextlib.redirect_stdout(output):
            exec(compile(cell, "<設定ファイル読込>", "exec"), namespace)
        self.assertEqual((namespace["TILE_RING"], namespace["EXACT_MAX_DISTANCE_M"], namespace["NEAR_DISTANCE_M"]),
                         (2, 30, 50))
        self.assertIn("TILE_RING            = 2  [JSON]", output.getvalue())
        self.assertIn("EXACT_MAX_DISTANCE_M = 30 [Notebook既定値]", output.getvalue())
        self.assertIn("NEAR_DISTANCE_M      = 50 [JSON]", output.getvalue())
        self.assertEqual(namespace["MUNICIPALITY_NAME"], "架空市")


class NoExternalAccessTest(unittest.TestCase):
    def test_テストは設定ファイルの取得に実際の通信を使わない(self):
        # このファイルのテストは requests.get を差し替えており、想定外のURLは404（FakeGitHub）になる
        github = FakeGitHub({})
        with isolated_cwd(), mock.patch("requests.get", github):
            with self.assertRaises(mc.ConfigError):
                mc.load_config("itoman-city", RAW_REPO_URL)
        self.assertEqual(github.urls, [f"{RAW_REPO_URL}/configs/itoman-city.json"])


if __name__ == "__main__":
    unittest.main()
