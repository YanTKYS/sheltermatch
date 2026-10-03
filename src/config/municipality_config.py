"""自治体ごとの設定ファイル（configs/<設定名>.json）の読込・検証・Notebook既定値との合成。

sheltermatch.ipynb・address_geocode.ipynb・experiments/gsi_shelter_compare/gsi_shelter_compare.ipynb が、
自治体固有の値（自治体名・コード・BODIKのresource_id）と、通常使う利用者設定を、Notebookを編集せずに切り替える
ために使う。設定ファイルの形と意味は configs/README.md を参照。

設定の優先順位（resolve_setting）:
    設定ファイルに値がある    → 設定ファイルの値
    設定ファイルに該当キーが無い → Notebookの既定値

設定ファイルそのものを取得・解釈できない場合は、Notebookの既定値で続行せず、原因を示して止まる（ConfigError）。
誤った設定のまま業務処理が進む方が危険なため。不正な型・値も、補正せずに止まる（"true" や 1 を真偽値へ
変換しない）。通信は load_config() の中の設定ファイル取得だけで、公開データや個人情報には触れない。
"""
import json
import re
from pathlib import Path

# Notebook側が想定するこのモジュールのAPIバージョン（互換性のない組み合わせで処理を続けないための確認用）
CONFIG_LOADER_API_VERSION = 1

SUPPORTED_SCHEMA_VERSION = 1

# 設定名（configs/<設定名>.json）に使える文字。URLやパスへ組み込むため、英小文字・数字・ハイフンに限る。
CONFIG_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

SHELTER_SOURCES = ("api", "csv")

# 設定ファイルが持てるセクションとキー。ここに無いキーは、綴り間違いを黙って無視しないよう、エラーにする。
ALLOWED_KEYS = {
    "": {"schema_version", "municipality", "bodik", "sheltermatch", "gsi_shelter_compare"},
    "municipality": {"id", "name", "code", "prefecture"},
    "bodik": {"resource_id"},
    "sheltermatch": {"enable_hazard_check", "shelter_source"},
    "gsi_shelter_compare": {"tile_ring", "exact_max_distance_m", "near_distance_m"},
}

SOURCE_JSON = "JSON"
SOURCE_DEFAULT = "Notebook既定値"

LOCAL_SEARCH_LEVELS = 3  # 現在のフォルダから、何階層上まで configs/ を探すか


class ConfigError(Exception):
    """設定ファイルを取得・解釈できない、または内容が不正なとき。処理を止めるために使う。"""


# ---------------------------------------------------------------------------
# 取得
# ---------------------------------------------------------------------------

def config_relative_path(config_name):
    """設定名から、リポジトリ内の設定ファイルの相対パス（configs/<設定名>.json）を返す。"""
    if not isinstance(config_name, str) or not CONFIG_NAME_PATTERN.match(config_name):
        raise ConfigError(
            f"設定名 CONFIG_NAME が不正です: {config_name!r}\n"
            "英小文字・数字・ハイフンだけで指定してください（例: 'itoman-city'）。"
        )
    return f"configs/{config_name}.json"


def find_local_config(config_name, start=None):
    """現在のフォルダ（と、数階層上まで）にある configs/<設定名>.json を探す。無ければ None。"""
    relative = config_relative_path(config_name)
    folder = Path.cwd() if start is None else Path(start)
    for candidate in [folder, *list(folder.parents)[:LOCAL_SEARCH_LEVELS]]:
        path = candidate / relative
        if path.is_file():
            return path
    return None


def parse_config_text(text, origin):
    try:
        data = json.loads(text)
    except ValueError as error:
        raise ConfigError(
            f"設定ファイルのJSONを解釈できません: {origin}\n（詳細: {error}）"
        ) from error
    return data


def load_config(config_name, repo_raw_url, start=None):
    """設定ファイルを読み込んで検証し、(設定の辞書, 設定ファイルの場所の表示) を返す。

    同じ名前のファイルがローカル（現在のフォルダか、その上位）の configs/ にあればそれを使い、無ければ
    repo_raw_url（例: https://raw.githubusercontent.com/YanTKYS/sheltermatch/<版>）の configs/ から取得する。
    ローカルにあるファイルが不正な場合に、GitHubのファイルへ切り替えることはしない（止まる）。
    ローカルの探索は start のフォルダ（省略時は現在のフォルダ）から行う。
    取得・解釈・検証のいずれかに失敗したら ConfigError。
    """
    relative = config_relative_path(config_name)
    local_path = find_local_config(config_name, start)
    if local_path is not None:
        origin = str(local_path)
        try:
            text = local_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise ConfigError(f"設定ファイルを読み込めません: {origin}\n（詳細: {error}）") from error
    else:
        import requests

        origin = f"{repo_raw_url}/{relative}"
        try:
            response = requests.get(origin, timeout=30)
            response.raise_for_status()
            text = response.content.decode("utf-8")
        except Exception as error:
            raise ConfigError(
                f"設定ファイル {relative} をGitHubから取得できませんでした。\n"
                "CONFIG_NAME が正しいか、その設定ファイルがリポジトリにあるか（取得する版に含まれているか）、"
                "インターネット接続を確認してください。\n"
                f"（取得先: {origin} / 詳細: {error}）"
            ) from error
    config = parse_config_text(text, origin)
    validate_config(config, config_name, origin)
    return config, origin


# ---------------------------------------------------------------------------
# 検証
# ---------------------------------------------------------------------------

def _fail(origin, path, message):
    raise ConfigError(f"設定ファイルの内容が不正です: {path}\n  {message}\n（設定ファイル: {origin}）")


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _require_section(config, name, origin, required):
    if name not in config:
        if required:
            _fail(origin, name, "必須のセクションがありません。")
        return None
    section = config[name]
    if not isinstance(section, dict):
        _fail(origin, name, f"オブジェクト（{{}}）である必要があります。現在の値: {section!r}")
    unknown = sorted(set(section) - ALLOWED_KEYS[name])
    if unknown:
        _fail(origin, name, f"未知のキーがあります: {', '.join(unknown)}（綴り間違いを確認してください）")
    return section


def _require_text(section, section_name, key, origin):
    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        _fail(origin, f"{section_name}.{key}", f"空でない文字列が必要です。現在の値: {value!r}")
    return value


def validate_config(config, config_name, origin="(設定)"):
    """設定の型・値を検証する。不正な値は補正せず、ConfigError で止める。問題がなければ config をそのまま返す。"""
    if not isinstance(config, dict):
        _fail(origin, "(全体)", f"オブジェクト（{{}}）である必要があります。現在の値の型: {type(config).__name__}")
    unknown = sorted(set(config) - ALLOWED_KEYS[""])
    if unknown:
        _fail(origin, "(全体)", f"未知のキーがあります: {', '.join(unknown)}（綴り間違いを確認してください）")

    version = config.get("schema_version")
    if not _is_int(version) or version != SUPPORTED_SCHEMA_VERSION:
        _fail(origin, "schema_version",
              f"{SUPPORTED_SCHEMA_VERSION} のみ対応しています。現在の値: {version!r}")

    municipality = _require_section(config, "municipality", origin, required=True)
    municipality_id = _require_text(municipality, "municipality", "id", origin)
    if municipality_id != config_name:
        _fail(origin, "municipality.id",
              f"設定名（CONFIG_NAME = {config_name!r}）と一致しません。現在の値: {municipality_id!r}")
    _require_text(municipality, "municipality", "name", origin)
    code = _require_text(municipality, "municipality", "code", origin)
    if not re.fullmatch(r"\d{5}", code):
        _fail(origin, "municipality.code", f"5桁の数字（全国地方公共団体コード）の文字列が必要です。現在の値: {code!r}")
    if "prefecture" in municipality:
        _require_text(municipality, "municipality", "prefecture", origin)

    bodik = _require_section(config, "bodik", origin, required=True)
    resource_id = _require_text(bodik, "bodik", "resource_id", origin)
    if not UUID_PATTERN.match(resource_id):
        _fail(origin, "bodik.resource_id", f"BODIK（CKAN）のresource_id（UUID）が必要です。現在の値: {resource_id!r}")

    sheltermatch = _require_section(config, "sheltermatch", origin, required=False)
    if sheltermatch is not None:
        if "enable_hazard_check" in sheltermatch and not isinstance(sheltermatch["enable_hazard_check"], bool):
            _fail(origin, "sheltermatch.enable_hazard_check",
                  f"true / false（真偽値）のみ指定できます（文字列や数値は変換しません）。現在の値: "
                  f"{sheltermatch['enable_hazard_check']!r}")
        if "shelter_source" in sheltermatch and sheltermatch["shelter_source"] not in SHELTER_SOURCES:
            _fail(origin, "sheltermatch.shelter_source",
                  f"{' / '.join(SHELTER_SOURCES)} のいずれかを指定してください。現在の値: "
                  f"{sheltermatch['shelter_source']!r}")

    compare = _require_section(config, "gsi_shelter_compare", origin, required=False)
    if compare is not None:
        if "tile_ring" in compare and (not _is_int(compare["tile_ring"]) or compare["tile_ring"] < 0):
            _fail(origin, "gsi_shelter_compare.tile_ring",
                  f"0以上の整数が必要です。現在の値: {compare['tile_ring']!r}")
        for key in ("exact_max_distance_m", "near_distance_m"):
            if key in compare and (not _is_number(compare[key]) or compare[key] <= 0):
                _fail(origin, f"gsi_shelter_compare.{key}", f"0より大きい数値が必要です。現在の値: {compare[key]!r}")
    return config


# ---------------------------------------------------------------------------
# Notebookの既定値との合成・表示
# ---------------------------------------------------------------------------

def resolve_setting(config, section, key, notebook_default):
    """設定ファイルに値があればその値、無ければNotebookの既定値を返す。(値, 設定元) の組。
    設定元は SOURCE_JSON または SOURCE_DEFAULT。値の型・範囲は validate_config() で検証済みであること。"""
    section_values = config.get(section)
    if isinstance(section_values, dict) and key in section_values:
        return section_values[key], SOURCE_JSON
    return notebook_default, SOURCE_DEFAULT


def local_government_code(code):
    """5桁の全国地方公共団体コードに検査数字を付けた6桁のコード（例: 47210 → 472107）。
    BODIKのデータセット名（472107_evacuation_space）やABRの lg_code が使う形式。"""
    if not re.fullmatch(r"\d{5}", str(code)):
        raise ConfigError(f"5桁の数字の自治体コードが必要です: {code!r}")
    digits = [int(c) for c in code]
    total = sum(d * w for d, w in zip(digits, (6, 5, 4, 3, 2)))
    return f"{code}{(11 - total % 11) % 10}"


def describe_municipality(config, config_name, origin):
    """実行時に表示する、対象自治体と設定ファイルの行のリスト。origin は load_config() が返した取得元。"""
    municipality = config["municipality"]
    return [
        f"対象自治体: {municipality['name']}",
        f"自治体コード: {municipality['code']}",
        f"設定ファイル: {config_relative_path(config_name)}（取得元: {origin}）",
    ]


def describe_settings(settings):
    """[(名前, 値, 設定元), ...] を、揃えた表示行のリストにする（例: '  TILE_RING = 1  [JSON]'）。"""
    name_width = max(len(name) for name, _, _ in settings)
    value_width = max(len(str(value)) for _, value, _ in settings)
    return [f"  {name:<{name_width}} = {str(value):<{value_width}} [{source}]" for name, value, source in settings]
