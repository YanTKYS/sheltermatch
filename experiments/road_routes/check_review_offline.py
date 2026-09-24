"""検証用: レビューZIPを展開し、file:// で review.html を開いて、外部通信なしで操作できることを確認する。

Playwright（Chromium）で開き、file:// 以外への通信はすべて遮断・記録する。確認内容は --mode で切り替える。

    on       道路に沿った参考経路あり（確認用の要支援者 residents_cases.csv を想定）
    off      道路経路を作成しない設定（従来どおりの画面）
    failed   道路データの取得に失敗した場合

使い方:
    python3 experiments/road_routes/check_review_offline.py <sheltermatch_review.zip> --mode on --shots <画像の出力先>
"""
import argparse
import json
import sys
import tempfile
import zipfile
from pathlib import Path

from playwright.sync_api import sync_playwright

CHROMIUM = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"

results = []
SHOTS = None


def check(name, condition, detail=""):
    results.append({"check": name, "ok": bool(condition), "detail": detail})
    print(("OK  " if condition else "NG  ") + name + (f"  … {detail}" if detail else ""))


# 候補の切替では、Leaflet が地図の移動（約250ms）と古いラベルの消去（約200ms）をアニメーションで行うため、
# それが終わるまで待ってから確認する
SETTLE_MS = 450


def select_resident(page, query):
    page.fill("#search", query)
    page.wait_for_timeout(SETTLE_MS)


def rows(page):
    return page.locator("#candidates tbody tr")


def click_rank(page, rank):
    page.locator(f'#candidates tbody tr[data-rank="{rank}"]').click()
    page.wait_for_timeout(SETTLE_MS)


def count(page, selector):
    return page.locator(selector).count()


def active_rank(page):
    """候補表で選択状態（active）になっている行の順位。無ければNone。"""
    rows = page.locator("#candidates tbody tr.active")
    return rows.first.get_attribute("data-rank") if rows.count() else None


def box(page, selector):
    return page.locator(selector).bounding_box()


def focus_extent(page):
    """選択中の候補に関わる要素（本人の地点・選択中の避難所マーカー・道路経路・接続部分）が地図の中に
    収まっているか、地図の幅・高さに対してどれだけの大きさで表示されているかを返す。"""
    return page.evaluate("""() => {
      const map = document.querySelector('#map').getBoundingClientRect();
      const nodes = document.querySelectorAll(
        '.resident-marker, .marker-pin.selected, path.road-route-line, path.road-route-connector');
      let l = Infinity, t = Infinity, r = -Infinity, b = -Infinity;
      nodes.forEach(n => { const x = n.getBoundingClientRect();
        l = Math.min(l, x.left); t = Math.min(t, x.top); r = Math.max(r, x.right); b = Math.max(b, x.bottom); });
      return { count: nodes.length, inside: l >= map.left - 1 && r <= map.right + 1 && t >= map.top - 1 && b <= map.bottom + 1,
               width: (r - l) / map.width, height: (b - t) / map.height, mapHeight: map.height };
    }""")


def view_span(page):
    """表示範囲の経度の幅（度）。「表示範囲 緯度 a〜b / 経度 c〜d」の表示から読む。"""
    text = page.inner_text("#view-bounds")
    west, east = text.split("経度 ")[1].split("（")[0].split("〜")
    return float(east) - float(west)


def check_focus(page, label):
    """地図が選択中の候補へ寄っていること（遠いほかの候補のために引いていないこと）を確認する。
    寄せた範囲は地図の幅または高さの3割以上を占める。例外は、対象が近すぎて拡大の上限（ズーム17）に
    達している場合（表示範囲の経度の幅が0.02度未満）。"""
    extent = focus_extent(page)
    near_limit = view_span(page) < 0.02
    check(f"{label}: 本人の地点・選択中の避難所・経路が地図に収まり、その範囲へ寄せて表示",
          extent["count"] >= 2 and extent["inside"]
          and (max(extent["width"], extent["height"]) >= 0.3 or near_limit),
          f"幅{extent['width']:.0%}・高さ{extent['height']:.0%}・経度幅{view_span(page):.4f}度")


def check_selected_marker(page, rank, name=None):
    """選択中の候補が、表（「選択中」の表示）と地図（大きいマーカー・常時表示の名前）の両方で分かること。"""
    selected = page.locator(".marker-pin.selected")
    label = page.locator(".leaflet-tooltip.selected-candidate-label")
    text = label.first.inner_text() if label.count() else ""
    row_label = page.locator("#candidates tbody tr.active .selected-label")
    check(f"候補{rank}: 地図で選択中の候補だけ大きいマーカー＋候補名を常時表示し、表にも「選択中」",
          selected.count() == 1 and selected.first.inner_text() == str(rank)
          and label.count() == 1 and text.startswith(f"候補{rank} ") and (name is None or name in text)
          and row_label.count() == 1 and active_rank(page) == str(rank)
          and count(page, ".marker-pin") >= 2,
          f"マーカー={selected.count()} ラベル='{text}' 候補マーカー総数={count(page, '.marker-pin')}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("zip")
    parser.add_argument("--mode", choices=["on", "off", "failed"], required=True)
    parser.add_argument("--shots", default=None)
    parser.add_argument("--report", default=None)
    parser.add_argument("--height", type=int, default=800, help="画面の高さ（幅は1366）")
    args = parser.parse_args()
    global SHOTS
    shots = SHOTS = Path(args.shots) if args.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)

    work = Path(tempfile.mkdtemp(prefix="review_check_"))
    with zipfile.ZipFile(args.zip) as archive:
        archive.extractall(work)
    html = work / "sheltermatch_review" / "review.html"
    blocked, console_errors = [], []

    with sync_playwright() as playwright:
        # 閉域環境を模すため、ブラウザの通信はすべて到達できないプロキシ（127.0.0.1:9）へ向け、
        # 外部へ出られない状態にする（ブラウザ自身の更新確認などの背景通信も含む）。
        # そのうえで、ページからの file:// 以外への通信の試みを下の route で記録する。
        browser = playwright.chromium.launch(
            executable_path=CHROMIUM,
            proxy={"server": "http://127.0.0.1:9"},
            args=["--disable-background-networking", "--disable-component-update", "--no-first-run"],
        )
        context = browser.new_context(viewport={"width": 1366, "height": args.height})

        def route(route_request):
            url = route_request.request.url
            if url.startswith("file://"):
                route_request.continue_()
            else:
                blocked.append(url)
                route_request.abort()

        context.route("**/*", route)
        page = context.new_page()
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(str(e)))
        page.goto(html.as_uri())
        page.wait_for_timeout(800)

        check("Leafletを同梱ファイルから読み込めている", page.evaluate("typeof L !== 'undefined'"))
        check("背景地図の画像を読み込めている",
              page.evaluate("(() => { const i = document.querySelector('.leaflet-image-layer');"
                            " return !!i && i.complete && i.naturalWidth > 0; })()"))
        attribution = page.inner_text("#map-attribution")
        check("出典: 国土地理院（背景地図）を表示", "背景地図: 国土地理院" in attribution, attribution)

        if args.mode == "on":
            check_on(page, shots, attribution)
        elif args.mode == "off":
            check_off(page, shots, attribution)
        else:
            check_failed(page, shots, attribution)

        check_common(page)
        check("外部への通信が発生していない（file:// 以外は0件）", not blocked, ", ".join(blocked[:5]))
        check("JavaScriptのエラーが無い", not console_errors, " / ".join(console_errors[:5]))
        browser.close()

    failed = [r for r in results if not r["ok"]]
    print(f"\n{len(results) - len(failed)}/{len(results)} 件OK")
    if args.report:
        Path(args.report).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    sys.exit(1 if failed else 0)


def check_on(page, shots, attribution):
    check("出典: 道路データ（OpenStreetMap）を背景地図と分けて表示",
          "道路経路の道路データ: © OpenStreetMap contributors（ODbL 1.0" in attribution
          and attribution.index("国土地理院") < attribution.index("OpenStreetMap"), attribution)
    header = page.inner_text("header")
    check("上部に「最終的な割り当てではない」「安全性を確認した避難経路ではない」の2点を表示",
          "最終的な割り当てではありません" in header
          and "通行できるか・安全かを確認した避難経路ではありません" in header, header)
    check("上部で直線の説明を重ねて出さない（凡例で示す）", not page.is_visible("#line-notice"))
    check("職員向け画面に内部の状態名（match_status）を表示しない",
          "match_status" not in page.inner_text("body"))
    check("凡例（直線・道路経路・接続）を表示", page.is_visible("#route-legend"))
    check("「道路に沿った参考経路」の切替を表示", page.is_visible("#route-toggle"))

    select_resident(page, "C01")
    info = page.inner_text("#route-info")
    check("要支援者を選ぶと候補1が自動で選択状態になる", active_rank(page) == "1", f"選択中の行: {active_rank(page)}")
    check("候補1の道路経路と接続部分をすぐに表示",
          count(page, "path.road-route-line") == 1 and count(page, "path.road-route-connector") == 2,
          f"経路{count(page, 'path.road-route-line')} / 接続{count(page, 'path.road-route-connector')}")
    check("経路情報欄に候補1の情報を表示", info.startswith("候補1「") and "道路上" in info, info)
    check_selected_marker(page, 1)
    check_focus(page, "候補1（初期選択）")
    check("正常な座標（ok）の場合は状態表示を出さない",
          count(page, "#selected-summary .status-warn") == 0,
          page.inner_text("#selected-summary"))
    check("候補がある場合は「候補表の行を選ぶと」の案内を出さない", "候補表の行を選ぶと" not in info, info)
    if shots:
        page.screenshot(path=str(shots / "01_select_resident_rank1_auto.png"))

    paths = {}
    for rank in (1, 2, 3):
        click_rank(page, rank)
        info = page.inner_text("#route-info")
        route_count = count(page, "path.road-route-line")
        check(f"候補{rank}を選ぶと、その候補の道路経路を1本表示", route_count == 1 and active_rank(page) == str(rank),
              info)
        check(f"候補{rank}: 道路上の距離と接続部分を区別して表示",
              f"候補{rank}" in info and "道路上" in info and "含みません" in info and "直線距離" in info, info)
        check(f"候補{rank}: 地点と道路の接続部分を点線で2本表示",
              count(page, "path.road-route-connector") == 2)
        check(f"候補{rank}: 直線（破線）も残している", count(page, "path.straight-line") == 3)
        check_selected_marker(page, rank)
        check_focus(page, f"候補{rank}へ切替")
        paths[rank] = page.locator("path.road-route-line").get_attribute("d")
        dash = page.locator("path.road-route-line").get_attribute("stroke-dasharray")
        straight_dash = page.locator("path.straight-line").first.get_attribute("stroke-dasharray")
        check(f"候補{rank}: 線種の区別（道路経路は実線・直線は破線）", dash is None and straight_dash == "6 5",
              f"経路={dash} / 直線={straight_dash}")
        if shots:
            page.screenshot(path=str(shots / f"02_rank{rank}_road_route.png"))
    check("候補1〜3で別の経路を表示", len(set(paths.values())) == 3)
    vertices = {rank: d.count("L") + 1 for rank, d in paths.items()}
    check("道路の形に沿って曲がった経路がある（直線とは異なる）", max(vertices.values()) >= 3,
          f"頂点数 {vertices}")
    toggle = box(page, "#detail-toggle")
    detail = box(page, "#detail")
    check("候補表の下の行を選んでも「候補詳細をたたむ」ボタンが隠れない",
          page.is_visible("#detail-toggle") and toggle["y"] >= detail["y"] - 1
          and toggle["y"] + toggle["height"] <= detail["y"] + detail["height"] + 1,
          f"ボタン y={toggle['y']:.0f} / 詳細欄 y={detail['y']:.0f}〜{detail['y'] + detail['height']:.0f}")
    click_rank(page, 3)
    check("選択中の候補をもう一度選んでも選択を保つ",
          active_rank(page) == "3" and count(page, "path.road-route-line") == 1)

    note = page.inner_text("#candidates .count")
    check("ハザード列は直線についての判定である旨を表示", "道路に沿った参考経路についての判定ではありません" in note, note)

    map_on, table_on = box(page, "#map"), box(page, "#candidates table")
    page.uncheck("#route-toggle")
    page.wait_for_timeout(150)
    check("切替をオフにすると道路経路と接続部分だけを消す（直線・選択状態は残す）",
          count(page, "path.road-route-line") == 0 and count(page, "path.road-route-connector") == 0
          and count(page, "path.straight-line") == 3 and active_rank(page) == "3",
          page.inner_text("#route-info"))
    map_off, table_off = box(page, "#map"), box(page, "#candidates table")
    check("経路の表示・非表示で地図の大きさがほとんど変わらない（高さの差30px以内）",
          abs(map_on["height"] - map_off["height"]) <= 30 and map_on["width"] == map_off["width"],
          f"{map_off['height']:.0f}px（非表示） / {map_on['height']:.0f}px（表示）")
    check("経路の表示・非表示で候補表の高さが変わらない", abs(table_on["height"] - table_off["height"]) < 1,
          f"{table_off['height']:.0f}px / {table_on['height']:.0f}px")
    select_resident(page, "C02")
    check("切替オフのまま別の要支援者を選んでも道路経路は表示しない（候補1は選択状態）",
          count(page, "path.road-route-line") == 0 and active_rank(page) == "1")
    page.check("#route-toggle")
    page.wait_for_timeout(150)
    check("切替をオンに戻すと再表示", count(page, "path.road-route-line") == 1)

    # 別の要支援者へ移ると、その人の候補1に選択状態が戻る（前の人の候補3を引き継がない）
    select_resident(page, "")
    page.locator("#results li", has_text="C01").click()
    page.wait_for_timeout(150)
    click_rank(page, 3)
    page.click("#next")
    page.wait_for_timeout(150)
    check("「次へ」で別の要支援者へ移ると候補1にリセット",
          page.inner_text("#selected-summary .title") == "C02" and active_rank(page) == "1"
          and page.inner_text("#route-info").startswith("候補1「"), page.inner_text("#route-info"))
    click_rank(page, 2)
    page.locator("#results li", has_text="C03").click()
    page.wait_for_timeout(150)
    check("一覧から別の要支援者を選ぶと候補1にリセット",
          page.inner_text("#selected-summary .title") == "C03" and active_rank(page) == "1")

    # C07 の候補には「道路から離れた地点」の架空避難所も含まれ、その候補は避難所側の理由になる
    for query, expected, label in (
        ("C04", "本人の地点から150m以内に道路データがありません", "道路まで遠すぎる（本人側）"),
        ("C07", "つながっていません", "経路なし（道路がつながっていない）"),
    ):
        select_resident(page, query)
        info = page.inner_text("#route-info")
        check(f"{label}（{query}）: 選んだ直後に候補1のまま「経路を算出できません」と理由を表示"
              "（候補2・3へ自動で切り替えない）",
              active_rank(page) == "1" and info.startswith("候補1「") and "経路を算出できません" in info
              and count(page, "path.road-route-line") == 0, info)
        check_selected_marker(page, 1)
        check_focus(page, f"経路を算出できない候補1（{query}）")
        if shots:
            page.screenshot(path=str(shots / f"03_{query}_rank1_no_route.png"))
        seen = 0
        for rank in (1, 2, 3):
            click_rank(page, rank)
            info = page.inner_text("#route-info")
            seen += expected in info
            check(f"{label}（{query}）: 候補{rank}は「経路を算出できません」と表示し、経路を描かない",
                  "経路を算出できません" in info
                  and count(page, "path.road-route-line") == 0
                  and count(page, "path.road-route-connector") == 0, info)
        check(f"{label}（{query}）: 該当する理由を表示した候補がある", seen >= 1, f"{seen}件")

    select_resident(page, "C08")
    found = False
    for rank in (1, 2, 3):
        click_rank(page, rank)
        info = page.inner_text("#route-info")
        if "避難所の地点から150m以内に道路データがありません" in info:
            found = True
            check("道路まで遠すぎる（避難所側）: 経路を描かない", count(page, "path.road-route-line") == 0, info)
            if shots:
                page.screenshot(path=str(shots / "04_C08_shelter_too_far.png"))
    check("道路まで遠すぎる（避難所側）の候補がある", found)

    select_resident(page, "C09")
    for rank in (1, 2, 3):
        click_rank(page, rank)
        info = page.inner_text("#route-info")
        check(f"取得範囲の端（C09）: 候補{rank}は算出せず、その理由を表示",
              "経路を算出できません" in info and "取得範囲の端に近い" in info
              and count(page, "path.road-route-line") == 0, info)

    for query, expected, badge in (("C05", "座標がありません", "座標なし"),
                                   ("C06", "座標を確認してください", "座標を確認")):
        select_resident(page, query)
        summary = page.inner_text("#selected-summary")
        check(f"座標なし・不正（{query}）: 線を描かず、候補が無いことを概要の1か所だけで表示",
              count(page, "path.straight-line") == 0 and count(page, "path.road-route-line") == 0
              and "避難所候補は算出されていません" in summary and not page.is_visible("#route-info")
              and not page.inner_text("#candidates").strip(), summary)
        list_badge = page.inner_text("#results li.selected .badge")
        check(f"座標なし・不正（{query}）: 職員向けの日本語で表示（「{expected}」／一覧は「{badge}」）",
              expected in summary and list_badge == badge and "match_status" not in summary
              and "no_coordinates" not in summary and "invalid_coordinates" not in summary,
              f"{summary} / {list_badge}")
        if shots:
            page.screenshot(path=str(shots / f"05_{query}_status.png"))


def check_off(page, shots, attribution):
    check("出典に道路データの表示が無い（道路経路を作成していない）", "OpenStreetMap" not in attribution, attribution)
    check("道路経路の切替・凡例・案内を表示しない",
          not page.is_visible("#route-toggle") and not page.is_visible("#route-legend")
          and not page.is_visible("#route-info") and page.inner_text("#route-notice") == "")
    check("要支援者を選んでも候補は自動で選択しない（従来どおり）", active_rank(page) is None)
    check("上部に直線の説明を表示し、道路経路の説明は出さない",
          page.is_visible("#line-notice") and "道路に沿った参考経路" not in page.inner_text("header"))
    check("職員向け画面に内部の状態名（match_status）を表示しない",
          "match_status" not in page.inner_text("body"))
    click_rank(page, 1)
    check("候補を選んでも直線だけを表示", count(page, "path.road-route-line") == 0
          and count(page, "path.straight-line") == 3 and active_rank(page) == "1")
    click_rank(page, 1)
    check("選択中の候補をもう一度選ぶと選択を外す（従来どおり）", active_rank(page) is None)
    if shots:
        page.screenshot(path=str(shots / "05_off.png"))


def check_failed(page, shots, attribution):
    check("出典に道路データの表示が無い（道路データを使っていない）", "OpenStreetMap" not in attribution, attribution)
    notice = page.inner_text("#route-notice")
    check("上部に「道路に沿った参考経路は作成できませんでした」を1回だけ表示",
          "道路に沿った参考経路は作成できませんでした" in notice
          and page.inner_text("body").count("作成できませんでした") == 1, notice)
    check("上部で存在しない道路経路の安全性の説明は出さず、直線の説明を表示",
          "安全かを確認した避難経路ではありません" not in page.inner_text("header")
          and page.is_visible("#line-notice"))
    check("道路経路の切替は表示しない", not page.is_visible("#route-toggle"))
    check("要支援者を選んでも候補は自動で選択しない（経路が無いため従来どおり）", active_rank(page) is None)
    click_rank(page, 1)
    check("候補を選ぶと直線だけを描き、経路情報欄は出さない（上部の表示と重ねない）",
          not page.is_visible("#route-info") and count(page, "path.road-route-line") == 0
          and count(page, "path.straight-line") == 3 and active_rank(page) == "1")
    if shots:
        page.screenshot(path=str(shots / "06_failed.png"))


def check_collapse(page, shots):
    """候補詳細の折りたたみ: 地図が広がり、たたんだままでも操作でき、再表示で元に戻ること。"""
    select_resident(page, "")
    page.locator("#results li").first.click()
    page.wait_for_timeout(300)
    routes_on = page.is_visible("#route-toggle")
    map_before = box(page, "#map")["height"]
    rank_before = active_rank(page)
    page.click("#detail-toggle")
    page.wait_for_timeout(400)
    map_after = box(page, "#map")["height"]
    check("候補詳細をたたむと、経路情報・候補表・注意書きを隠して地図が広がる",
          not page.is_visible("#candidates table") and not page.is_visible("#route-info")
          and page.inner_text("#detail-toggle") == "候補詳細を表示"
          and page.get_attribute("#detail-toggle", "aria-expanded") == "false"
          and map_after - map_before >= 100,
          f"地図の高さ {map_before:.0f}px → {map_after:.0f}px")
    check("たたんだ後も背景地図・マーカーを表示（空白・位置ずれなし）",
          page.evaluate("(() => { const i = document.querySelector('.leaflet-image-layer');"
                        " return !!i && i.naturalWidth > 0; })()")
          and focus_extent(page)["inside"] and count(page, ".marker-pin") == 3)
    if routes_on:
        check("たたんだ後も選択中の候補の道路経路を表示", count(page, "path.road-route-line") == 1)
    if shots:
        page.screenshot(path=str(shots / "07_detail_collapsed.png"))

    # たたんだまま、検索・前へ／次へ・道路経路の切替・地図操作ができること
    select_resident(page, "C02")
    ok_search = page.inner_text("#selected-summary .title") == "C02"
    select_resident(page, "")
    page.locator("#results li", has_text="C02").click()
    page.wait_for_timeout(SETTLE_MS)
    page.click("#next")
    page.wait_for_timeout(150)
    ok_next = page.inner_text("#selected-summary .title") != "C02"
    page.click("#prev")
    page.wait_for_timeout(150)
    ok_toggle = True
    if routes_on:
        page.uncheck("#route-toggle")
        page.wait_for_timeout(150)
        ok_toggle = count(page, "path.road-route-line") == 0
        page.check("#route-toggle")
        page.wait_for_timeout(150)
        ok_toggle = ok_toggle and count(page, "path.road-route-line") == 1
    before = page.inner_text("#view-bounds")
    page.click(".leaflet-control-zoom-out")
    page.wait_for_timeout(500)
    check("たたんだまま検索・前へ／次へ・道路経路の切替・ズームができる",
          ok_search and ok_next and ok_toggle and page.inner_text("#view-bounds") != before,
          f"検索{ok_search} 次へ{ok_next} 経路切替{ok_toggle}")

    page.click("#detail-toggle")
    page.wait_for_timeout(400)
    restored = box(page, "#map")["height"]
    expected_rank = "1" if routes_on else None
    check("候補詳細を再表示すると、候補表・選択状態・地図の大きさが元に戻る",
          page.is_visible("#candidates table") and page.inner_text("#detail-toggle") == "候補詳細をたたむ"
          and active_rank(page) == expected_rank and abs(restored - map_before) <= 30
          and focus_extent(page)["inside"],
          f"地図の高さ {restored:.0f}px（たたむ前 {map_before:.0f}px）/ 選択 {active_rank(page)}"
          f"（たたむ前 {rank_before}）")


def check_common(page):
    """検索・前後移動・ハザード表示・地図操作（どの設定でも同じように動くこと）。"""
    select_resident(page, "")
    total = page.inner_text("#count")
    select_resident(page, "C02")
    check("検索で絞り込める", page.inner_text("#count").startswith("該当 1件"), page.inner_text("#count"))
    select_resident(page, "")
    check("検索を消すと全件に戻る", page.inner_text("#count") == total, total)
    first = page.inner_text("#selected-summary .title")
    page.click("#next")
    page.wait_for_timeout(100)
    second = page.inner_text("#selected-summary .title")
    page.click("#prev")
    page.wait_for_timeout(100)
    check("前へ・次へで要支援者を切り替えられる",
          first != second and page.inner_text("#selected-summary .title") == first, f"{first} → {second}")

    if page.is_visible("#layers-all"):
        page.click("#layers-all")
        shown = count(page, "img.leaflet-image-layer")
        page.click("#layers-none")
        hidden = count(page, "img.leaflet-image-layer")
        check("ハザード区域の表示・非表示を切り替えられる", shown > hidden, f"画像レイヤー {shown} → {hidden}")

    before = page.inner_text("#view-bounds")
    page.click(".leaflet-control-zoom-in")
    page.wait_for_timeout(500)
    after_zoom = page.inner_text("#view-bounds")
    map_box = box(page, "#map")
    page.mouse.move(map_box["x"] + map_box["width"] / 2, map_box["y"] + map_box["height"] / 2)
    page.mouse.down()
    page.mouse.move(map_box["x"] + map_box["width"] / 2 + 120, map_box["y"] + map_box["height"] / 2 + 60,
                    steps=8)
    page.mouse.up()
    page.wait_for_timeout(400)
    after_drag = page.inner_text("#view-bounds")
    check("地図を拡大できる（表示範囲が変わる）", before != after_zoom, f"{before} → {after_zoom}")
    check("地図をドラッグで移動できる（表示範囲が変わる）", after_zoom != after_drag, after_drag)
    check_collapse(page, SHOTS)

if __name__ == "__main__":
    main()
