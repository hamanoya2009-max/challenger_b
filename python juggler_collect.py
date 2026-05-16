"""
juggler_collect.py
チャレンジャーB館 台データ収集スクリプト - 第一段階
対象: ネオアイムジャグラーEX（台番号1〜16）

必要なライブラリ:
  pip install playwright anthropic gspread oauth2client
  playwright install chromium

環境変数（.envファイルまたはGitHub Secrets）:
  ANTHROPIC_API_KEY   : Claude APIキー
  GOOGLE_CREDENTIALS  : GASサービスアカウントJSONの内容（文字列）
  SPREADSHEET_ID      : Google SheetsのID
"""

import os
import base64
import json
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright
import anthropic
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ========================================
# 設定
# ========================================

# 対象機種（第一段階はネオアイムのみ）
MACHINES = [
    {
        "name": "ネオアイムジャグラーEX",
        "url": "https://challenger.pt.teramoba2.com/b-kan/standlist_slot/?kind_code=21&machine_name=S%EF%BE%88%EF%BD%B5%EF%BD%B1%EF%BD%B2%EF%BE%91%EF%BD%BC%EF%BE%9E%EF%BD%AC%EF%BD%B8%EF%BE%9E%EF%BE%97%EF%BD%B0EX-KK",
    },
]

# 同意ページのURL
CONSENT_URL = "https://challenger.pt.teramoba2.com/b-kan/protection_redirect"

# スクショ保存先
SCREENSHOT_DIR = Path("screenshots")
SCREENSHOT_DIR.mkdir(exist_ok=True)

# ========================================
# Step 1: ブラウザ操作・スクショ取得
# ========================================

def take_screenshots(machines: list) -> dict:
    """
    各機種のページにアクセスしてスクショを撮る
    戻り値: {機種名: スクショのバイナリ}
    """
    screenshots = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = context.new_page()

        # --- 同意ページを通過 ---
        print("同意ページに移動...")
        page.goto(CONSENT_URL, wait_until="networkidle")
        time.sleep(2)

        # 「同意する」ボタンをクリック
        try:
            page.click("text=同意する")
            print("同意ボタンをクリックしました")
            time.sleep(2)
        except Exception as e:
            print(f"同意ボタンが見つかりません（既に同意済みの可能性）: {e}")

        # --- 各機種ページをスクショ ---
        for machine in machines:
            name = machine["name"]
            url = machine["url"]
            print(f"\n{name} のページに移動中...")

            page.goto(url, wait_until="networkidle")
            time.sleep(3)  # データ読み込み待機

            # ページ全体が見えるようにズームアウト
            page.evaluate("document.body.style.zoom = '75%'")
            time.sleep(1)

            # フルページスクショ
            screenshot_path = SCREENSHOT_DIR / f"{name}_{datetime.now().strftime('%Y%m%d')}.png"
            page.screenshot(path=str(screenshot_path), full_page=True)

            with open(screenshot_path, "rb") as f:
                screenshots[name] = f.read()

            print(f"スクショ保存: {screenshot_path}")
            time.sleep(2)  # サーバー負荷軽減

        browser.close()

    return screenshots

# ========================================
# Step 2: Claude APIで画像を読み取る
# ========================================

def extract_data_from_screenshot(client: anthropic.Anthropic, machine_name: str, image_bytes: bytes) -> list:
    """
    スクショ画像をClaudeに渡してデータを抽出
    戻り値: [{"台番号": 1, "BB": 5, "RB": 3, "総回転数": 3000}, ...]
    """
    print(f"\nClaude APIで {machine_name} のデータを読み取り中...")

    image_base64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    prompt = f"""この画像はパチスロホール「チャレンジャーB館」の台データ一覧ページのスクリーンショットです。
機種名: {machine_name}

表に表示されている全ての台のデータを読み取り、以下のJSON形式で返してください。
ARTとスタートの列は不要です。

{{
  "machines": [
    {{"台番号": 1, "BB": 5, "RB": 3, "総回転数": 3000}},
    {{"台番号": 2, "BB": 2, "RB": 1, "総回転数": 1500}},
    ...
  ]
}}

注意事項:
- 台番号・BB・RB・総回転数のみ抽出してください
- 数値は整数で返してください
- 読み取れない台はスキップしてください
- JSONのみ返し、説明文は不要です"""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_base64,
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ],
            }
        ],
    )

    # レスポンスからJSONを抽出
    raw_text = response.content[0].text.strip()

    # ```json ... ``` のフェンスを除去
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.strip()

    data = json.loads(raw_text)
    machines_data = data.get("machines", [])
    print(f"  → {len(machines_data)}台分のデータを読み取りました")
    return machines_data

# ========================================
# Step 3: Google Sheetsに書き込む
# ========================================

def write_to_sheets(all_data: list):
    """
    抽出したデータをraw_dataシートに書き込む
    """
    print("\nGoogle Sheetsに書き込み中...")

    # 認証
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if creds_json:
        creds_dict = json.loads(creds_json)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(
            creds_dict,
            ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        )
    else:
        # ローカルテスト用
        creds = ServiceAccountCredentials.from_json_keyfile_name(
            "credentials.json",
            ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        )

    gc = gspread.authorize(creds)
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    sheet = gc.open_by_key(spreadsheet_id).worksheet("raw_data")

    today = datetime.now().strftime("%Y/%m/%d")

    # 既存データの最終行を取得
    existing = sheet.get_all_values()
    last_row = len(existing) + 1

    # 書き込むデータを組み立て
    rows = []
    for item in all_data:
        row = [
            today,               # A: 日付
            item["台番号"],       # B: 台番号
            item["機種名"],       # C: 機種名
            "",                  # D: 島番号（空欄、masterシートから参照）
            item["BB"],          # E: BIG
            item["RB"],          # F: REG
            item["総回転数"],     # G: 総回転数
        ]
        rows.append(row)

    if rows:
        sheet.append_rows(rows, value_input_option="USER_ENTERED")
        print(f"  → {len(rows)}行を書き込みました（行{last_row}〜）")
    else:
        print("  → 書き込むデータがありませんでした")

# ========================================
# メイン処理
# ========================================

def main():
    print("=" * 50)
    print("チャレンジャーB館 台データ収集")
    print(f"実行日時: {datetime.now().strftime('%Y/%m/%d %H:%M:%S')}")
    print("=" * 50)

    # Claude APIクライアント
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY が設定されていません")
    client = anthropic.Anthropic(api_key=api_key)

    # Step 1: スクショ取得
    screenshots = take_screenshots(MACHINES)

    # Step 2: データ抽出
    all_data = []
    for machine_name, image_bytes in screenshots.items():
        try:
            machine_data = extract_data_from_screenshot(client, machine_name, image_bytes)
            for item in machine_data:
                item["機種名"] = machine_name
            all_data.extend(machine_data)
        except Exception as e:
            print(f"  ERROR: {machine_name} の読み取りに失敗: {e}")

    print(f"\n合計 {len(all_data)}台分のデータを抽出")

    # Step 3: Sheets書き込み
    if all_data:
        write_to_sheets(all_data)

    print("\n✅ 完了")

if __name__ == "__main__":
    main()
