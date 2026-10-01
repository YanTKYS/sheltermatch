"""sheltermatch.ipynb のセルを、外部通信なしで順に実行するためのテスト用ハーネス（完全な架空データ用）。

通常運用の Notebook は、`SHELTERMATCH_CODE_REF`（現在は v1.1.0）の `src/` を GitHub から取得する。
このハーネスは **Notebook のセルを書き換えず**、取得先の版を `main` へ戻すこともせず、次のものだけを差し替える。

    google.colab.files       アップロード＝指定した内容を順に返す / ダウンロード＝記録のみ
    %pip 行                  実行しない
    requests.get             GitHub raw（src/）への取得は、作業ツリーの src/ の内容を返す
                             （URLの版 SHELTERMATCH_CODE_REF によらず、作業ツリーを明示的に使う）
                             BODIK Data API は、架空の避難所データを返す
                             それ以外のURL（OSM・地理院タイル・unpkg 等）は、接続せずに失敗させる
    review_builder           地図ライブラリの同梱と背景地図PNGの生成（外部通信が必要な処理）だけを、
                             ダミーのファイルを書く処理へ差し替える（review_builder のそのほかの処理はそのまま使う）

ハーネスは、取得しようとしたURLをすべて記録する。
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import types
import zipfile
from pathlib import Path
from unittest import mock

import requests

REPO = Path(__file__).resolve().parent.parent
NOTEBOOK_PATH = REPO / "sheltermatch.ipynb"
SRC_DIR = REPO / "src"

GITHUB_RAW_PREFIX = "https://raw.githubusercontent.com/YanTKYS/sheltermatch/"
BODIK_API_PREFIX = "https://data.bodik.jp/api/action/datastore_search"

# 1x1の透明PNG（背景地図の代わり）
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000002000001e221bc330000000049454e44ae426082"
)


class FakeResponse:
    def __init__(self, content=b"", status_code=200):
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return json.loads(self.content.decode("utf-8"))


class FakeFiles:
    """google.colab.files の代わり。upload() は呼ばれた順に、指定された内容を返す。"""

    def __init__(self, uploads):
        self.uploads = list(uploads)
        self.downloads = []

    def upload(self):
        if not self.uploads:
            raise RuntimeError("テスト用ハーネス: アップロードする内容が指定されていません")
        return self.uploads.pop(0)

    def download(self, name):
        self.downloads.append(str(name))


def code_cells():
    notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
    cells = []
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            source = cell["source"]
            cells.append(source if isinstance(source, str) else "".join(source))
    return cells


def cell_starting_with(title):
    matches = [source for source in code_cells() if source.startswith(title)]
    assert len(matches) == 1, f"{title} のセルが1つではありません（{len(matches)}件）"
    return matches[0]


def bodik_response(shelters):
    """CKANの datastore_search 形式（架空の避難所。自治体標準ODSの列名）。"""
    records = [dict(shelter, _id=position + 1) for position, shelter in enumerate(shelters)]
    payload = {"success": True, "result": {"records": records, "total": len(records)}}
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class NotebookRun:
    """sheltermatch.ipynb のコードセルを順に実行する。

    residents_csv    要支援者CSV（bytes）
    shelters         架空の避難所（BODIK Data API の records 形式の辞書のリスト）
    hazard_uploads   ハザードデータ（{ファイル名: bytes}）。Noneなら ENABLE_HAZARD_CHECK=False で実行する
    """

    def __init__(self, residents_csv, shelters, hazard_uploads=None):
        self.residents_csv = residents_csv
        self.shelters = shelters
        self.hazard_uploads = hazard_uploads
        self.urls = []
        self.output = io.StringIO()
        self.namespace = {"__name__": "__main__", "display": lambda *args, **kwargs: None}
        self.workdir = None

    # ---- 差し替え ----
    def _fake_get(self, url, *args, **kwargs):
        self.urls.append(url)
        if url.startswith(GITHUB_RAW_PREFIX):
            # 版（タグ）の部分によらず、作業ツリーの src/ の内容を返す
            source_path = url.split("/src/", 1)[1]
            target = SRC_DIR / source_path
            if not target.is_file():
                return FakeResponse(status_code=404)
            return FakeResponse(target.read_bytes())
        if url.startswith(BODIK_API_PREFIX):
            return FakeResponse(bodik_response(self.shelters))
        raise AssertionError(f"テスト中に想定していない外部通信です: {url}")

    @staticmethod
    def _fake_review_builder_downloads(review_builder):
        def bundle_leaflet(assets_dir):
            (assets_dir / "js").mkdir(parents=True, exist_ok=True)
            (assets_dir / "js" / "leaflet.js").write_text("/* ダミー */", encoding="utf-8")

        def render_offline_basemap(bounds, assets_dir):
            (assets_dir / review_builder.BASEMAP_FILENAME).write_bytes(TINY_PNG)
            return {
                "image": f"assets/{review_builder.BASEMAP_FILENAME}",
                "bounds": [[bounds[0], bounds[1]], [bounds[2], bounds[3]]],
                "zoom": 15, "width": 1, "height": 1,
            }

        review_builder.bundle_leaflet = bundle_leaflet
        review_builder.render_offline_basemap = render_offline_basemap

    # ---- 実行 ----
    def execute(self, upto_title=None, settings=None):
        """全セルを順に実行する。upto_title を指定すると、そのタイトルのセルの手前で止める。
        settings は利用者設定セルの代入文の置き換え（例: {"SHELTER_SOURCE": '"csv"'}）。"""
        uploads = [{"residents.csv": self.residents_csv}]
        if self.hazard_uploads is not None:
            uploads.append(self.hazard_uploads)
        fake_files = FakeFiles(uploads)
        self.files = fake_files

        google_module = types.ModuleType("google")
        colab_module = types.ModuleType("google.colab")
        colab_module.files = fake_files
        google_module.colab = colab_module

        self._tempdir = tempfile.TemporaryDirectory()
        self.workdir = Path(self._tempdir.name)
        previous_cwd = os.getcwd()
        os.chdir(self.workdir)
        try:
            # sys.modules へ入れるのは偽の google だけ（mock.patch.dict で包むと、実行中に初めて読み込まれた
            # numpy / pandas まで元に戻されて、再読み込みに失敗するため使わない）
            sys.modules["google"] = google_module
            sys.modules["google.colab"] = colab_module
            with mock.patch("requests.get", self._fake_get), contextlib.redirect_stdout(self.output):
                for source in code_cells():
                    if upto_title and source.startswith(upto_title):
                        break
                    source = self._prepare(source, settings or {})
                    exec(compile(source, "<sheltermatch.ipynb>", "exec"), self.namespace)
                    if source.startswith("# ===== 外部モジュール準備"):
                        self._fake_review_builder_downloads(self.namespace["review_builder"])
        finally:
            sys.modules.pop("google.colab", None)
            sys.modules.pop("google", None)
            os.chdir(previous_cwd)
        return self

    def _prepare(self, source, settings):
        lines = ["" if line.lstrip().startswith(("%", "!")) else line for line in source.splitlines()]
        source = "\n".join(lines)
        if source.startswith("# ===== 利用者設定"):
            enable = "True" if self.hazard_uploads is not None else "False"
            source = source.replace("ENABLE_HAZARD_CHECK = False", f"ENABLE_HAZARD_CHECK = {enable}")
            for name, value in settings.items():
                assert f"{name} = " in source, name
                source = "\n".join(
                    f"{name} = {value}" if line.startswith(f"{name} = ") else line
                    for line in source.splitlines()
                )
        return source

    def close(self):
        self._tempdir.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- 結果の取り出し ----
    def result_csv_text(self):
        return (self.workdir / "assigned_shelters.csv").read_bytes().decode("utf-8-sig")

    def review_zip(self):
        return zipfile.ZipFile(self.workdir / "sheltermatch_review.zip")

    def review_html(self):
        with self.review_zip() as archive:
            return archive.read("sheltermatch_review/review.html").decode("utf-8")

    def review_data(self):
        """review.html に埋め込まれたデータ（JSON）を取り出す。"""
        html = self.review_html()
        start = html.index('id="review-data">') + len('id="review-data">')
        end = html.index("</script>", start)
        return json.loads(html[start:end])


def load_src_module(relative_path, module_name):
    """src/ 配下のPythonファイルを、Notebookと同じ方法（ファイルからのモジュール読み込み）で読み込む。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(module_name, SRC_DIR / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
