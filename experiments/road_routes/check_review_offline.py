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


def check(name, condition, detail=""):
    results.append({"check": name, "ok": bool(condition), "detail": detail})
    print(("OK  " if condition else "NG  ") + name + (f"  … {detail}" if detail else ""))


def select_resident(page, query):
    page.fill("#search", query)
    page.wait_for_timeout(150)


def rows(page):
    return page.locator("#candidates tbody tr")


def click_rank(page, rank):
    page.locator(f'#candidates tbody tr[data-rank="{rank}"]').click()
    page.wait_for_timeout(150)


def count(page, selector):
    return page.locator(selector).count()


def box(page, selector):
    return page.locator(selector).bounding_box()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("zip")
    parser.add_argument("--mode", choices=["on", "off", "failed"], required=True)
    parser.add_argument("--shots", default=None)
    parser.add_argument("--report", default=None)
    args = parser.parse_args()
    shots = Path(args.shots) if args.shots else None
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
        context = browser.new_context(viewport={"width": 1366, "height": 800})

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
    check("見出しに参考経路の注意書きを表示", "通行できるか・安全かは確認していません" in page.inner_text("header"))
    check("凡例（直線・道路経路・接続）を表示", page.is_visible("#route-legend"))
    check("「道路に沿った参考経路」の切替を表示", page.is_visible("#route-toggle"))

    select_resident(page, "C01")
    check("初期表示は直線のみ（直線3本・道路経路0本）",
          count(page, "path.straight-line") == 3 and count(page, "path.road-route-line") == 0,
          f"直線{count(page, 'path.straight-line')} / 経路{count(page, 'path.road-route-line')}")
    check("初期表示の案内文", "候補表の行を選ぶと" in page.inner_text("#route-info"), page.inner_text("#route-info"))
    map_before, table_before = box(page, "#map"), box(page, "#candidates table")
    if shots:
        page.screenshot(path=str(shots / "01_initial_straight_only.png"))

    paths = {}
    for rank in (1, 2, 3):
        click_rank(page, rank)
        info = page.inner_text("#route-info")
        route_count = count(page, "path.road-route-line")
        check(f"候補{rank}を選ぶと、その候補の道路経路を1本表示", route_count == 1, info)
        check(f"候補{rank}: 道路上の距離と接続部分を区別して表示",
              f"候補{rank}" in info and "道路上" in info and "含みません" in info and "直線距離" in info, info)
        check(f"候補{rank}: 地点と道路の接続部分を点線で2本表示",
              count(page, "path.road-route-connector") == 2)
        check(f"候補{rank}: 直線（破線）も残している", count(page, "path.straight-line") == 3)
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
    map_after, table_after = box(page, "#map"), box(page, "#candidates table")
    check("経路表示で地図の大きさがほとんど変わらない（高さの差30px以内）",
          abs(map_before["height"] - map_after["height"]) <= 30 and map_before["width"] == map_after["width"],
          f"{map_before['height']:.0f}px → {map_after['height']:.0f}px")
    check("経路表示で候補表の高さが変わらない", abs(table_before["height"] - table_after["height"]) < 1,
          f"{table_before['height']:.0f}px → {table_after['height']:.0f}px")

    note = page.inner_text("#candidates .count")
    check("ハザード列は直線についての判定である旨を表示", "道路に沿った参考経路についての判定ではありません" in note, note)

    page.uncheck("#route-toggle")
    page.wait_for_timeout(150)
    check("切替をオフにすると道路経路を消す", count(page, "path.road-route-line") == 0,
          page.inner_text("#route-info"))
    page.check("#route-toggle")
    page.wait_for_timeout(150)
    check("切替をオンに戻すと再表示", count(page, "path.road-route-line") == 1)
    click_rank(page, 3)
    check("選択中の候補をもう一度選ぶと直線だけに戻る", count(page, "path.road-route-line") == 0)

    # C07 の候補には「道路から離れた地点」の架空避難所も含まれ、その候補は避難所側の理由になる
    for query, expected, label in (
        ("C04", "本人の地点から150m以内に道路データがありません", "道路まで遠すぎる（本人側）"),
        ("C07", "つながっていません", "経路なし（道路がつながっていない）"),
    ):
        select_resident(page, query)
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
        if shots:
            page.screenshot(path=str(shots / f"03_{query}_no_route.png"))

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

    for query in ("C05", "C06"):
        select_resident(page, query)
        info = page.inner_text("#route-info")
        check(f"座標なし・不正（{query}）: 線を描かず、経路が無い旨を表示",
              count(page, "path.straight-line") == 0 and count(page, "path.road-route-line") == 0
              and "避難所候補が無い" in info, info)


def check_off(page, shots, attribution):
    check("出典に道路データの表示が無い（道路経路を作成していない）", "OpenStreetMap" not in attribution, attribution)
    check("道路経路の切替・凡例・案内を表示しない",
          not page.is_visible("#route-toggle") and not page.is_visible("#route-legend")
          and not page.is_visible("#route-info") and page.inner_text("#route-notice") == "")
    click_rank(page, 1)
    check("候補を選んでも直線だけを表示", count(page, "path.road-route-line") == 0
          and count(page, "path.straight-line") == 3)
    if shots:
        page.screenshot(path=str(shots / "05_off.png"))


def check_failed(page, shots, attribution):
    check("出典に道路データの表示が無い（道路データを使っていない）", "OpenStreetMap" not in attribution, attribution)
    note = page.inner_text("#route-note")
    check("地図の表示欄に「作成できませんでした」を表示", "道路に沿った参考経路は作成できませんでした" in note, note)
    check("道路経路の切替は表示しない", not page.is_visible("#route-toggle"))
    check("見出しに参考経路の注意書きを出さない（経路を表示しないため）", page.inner_text("#route-notice") == "")
    click_rank(page, 1)
    info = page.inner_text("#route-info")
    check("候補を選ぶと「作成できませんでした」と表示し、直線だけを描く",
          "作成できませんでした" in info and count(page, "path.road-route-line") == 0
          and count(page, "path.straight-line") == 3, info)
    if shots:
        page.screenshot(path=str(shots / "06_failed.png"))


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

if __name__ == "__main__":
    main()
