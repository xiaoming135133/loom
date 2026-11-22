from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import Browser, Page, sync_playwright


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EDGE = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")


def assert_no_horizontal_overflow(page: Page) -> None:
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1, f"页面横向溢出 {overflow}px"


def attach_error_collectors(page: Page) -> tuple[list[str], list[str]]:
    console_errors: list[str] = []
    page_errors: list[str] = []
    page.on(
        "console",
        lambda message: console_errors.append(message.text) if message.type == "error" else None,
    )
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    return console_errors, page_errors


def run(edge_path: Path, base_url: str, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser: Browser = playwright.chromium.launch(
            executable_path=str(edge_path),
            headless=True,
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=1,
        )
        page = context.new_page()
        console_errors, page_errors = attach_error_collectors(page)

        page.goto(base_url, wait_until="networkidle")
        page.wait_for_selector(".sample-card")
        assert page.locator(".sample-card").count() == 7
        assert page.locator(".tree-node").count() >= 8
        assert_no_horizontal_overflow(page)
        page.screenshot(path=output_dir / "home-1440.png", full_page=True)

        page.locator(".sample-card").first.click()
        page.wait_for_selector("#suggestionPanel:not([hidden])", timeout=15_000)
        page.wait_for_function(
            "() => document.querySelector('#loadingOverlay').hidden",
            timeout=15_000,
        )
        assert page.locator("#relationValue").inner_text() == "挂载"
        assert page.locator("#targetSelect option").count() >= 5
        page.screenshot(path=output_dir / "review-1440.png", full_page=True)

        page.locator('.relation-picker button[data-relation="挂载"]').click()
        page.locator("#targetSelect").select_option("node-limit-sequence")
        page.locator("#confirmDecisionButton").click()
        page.wait_for_function(
            "() => document.querySelector('#loadingOverlay').hidden"
            " && document.querySelector('#suggestionPanel').hidden",
            timeout=15_000,
        )
        page.locator('.tab[data-view="revisions"]').click()
        page.wait_for_selector(".revision-row")
        assert page.locator(".revision-row").count() >= 1
        page.screenshot(path=output_dir / "revisions-1440.png", full_page=True)

        page.locator('.tab[data-view="graph"]').click()
        page.wait_for_selector("#graphCanvas svg")
        assert page.locator(".graph-node").count() >= 10
        page.screenshot(path=output_dir / "graph-1440.png", full_page=True)

        page.locator("#searchInput").fill("ε-δ")
        page.locator("#searchInput").press("Enter")
        page.wait_for_selector(".result-row")
        assert page.locator(".result-row").count() <= 5
        assert page.locator(".result-row").count() >= 1
        page.screenshot(path=output_dir / "search-1440.png", full_page=True)

        # 通过 API 准备三条待定内容，再从网页检查批量确认入口。
        for sample_id in ("pending-context-a", "pending-context-b", "pending-context-c"):
            created = context.request.post(
                f"{base_url}/api/assets",
                form={"sample_id": sample_id},
            )
            assert created.ok, created.text()
            asset_id = created.json()["id"]
            aligned = context.request.post(f"{base_url}/api/assets/{asset_id}/align")
            assert aligned.ok, aligned.text()

        page.reload(wait_until="networkidle")
        page.locator("#pendingButton").click()
        page.wait_for_selector("#pendingModal:not([hidden])")
        checked_pending = page.locator("#pendingModalList input:checked").count()
        assert 3 <= checked_pending <= 5
        page.screenshot(path=output_dir / "pending-1440.png", full_page=True)
        page.locator("#closePendingModal").click()

        page.set_input_files(
            "#fileInput",
            str(ROOT / "web" / "assets" / "demo" / "mount-lhopital.png"),
        )
        page.wait_for_selector("#manualNotice:not([hidden])")
        page.wait_for_function(
            "() => document.querySelector('#loadingOverlay').hidden",
            timeout=15_000,
        )
        assert not page.locator("#contextEmpty").is_hidden()
        page.screenshot(path=output_dir / "manual-upload-1440.png", full_page=True)

        page.set_viewport_size({"width": 1024, "height": 768})
        page.reload(wait_until="networkidle")
        page.wait_for_selector(".sample-card")
        assert_no_horizontal_overflow(page)
        page.screenshot(path=output_dir / "home-1024.png", full_page=True)

        page.set_viewport_size({"width": 390, "height": 844})
        page.reload(wait_until="networkidle")
        page.wait_for_selector(".sample-card")
        assert_no_horizontal_overflow(page)
        page.screenshot(path=output_dir / "home-390.png", full_page=True)

        if console_errors:
            raise AssertionError("浏览器控制台错误：\n" + "\n".join(console_errors))
        if page_errors:
            raise AssertionError("页面脚本错误：\n" + "\n".join(page_errors))

        browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="LOOM 网页无头冒烟测试")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--edge", type=Path, default=DEFAULT_EDGE)
    parser.add_argument("--output", type=Path, default=ROOT / "work" / "ui-screens")
    args = parser.parse_args()
    if not args.edge.exists():
        print(f"Edge not found: {args.edge}", file=sys.stderr)
        raise SystemExit(2)
    run(args.edge, args.base_url, args.output)
    print(f"UI smoke passed; screenshots: {args.output}")


if __name__ == "__main__":
    main()
