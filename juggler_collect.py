"""
juggler_collect.py
チャレンジャーB館 台データ収集スクリプト v2.0
Playwright不要・API直接取得版

必要なライブラリ:
  pip install requests gspread oauth2client

環境変数（GitHub Secrets）:
  GOOGLE_CREDENTIALS  : サービスアカウントJSONの内容
  SPREADSHEET_ID      : Google SheetsのID
"""

import os
import json
import time
from datetime import datetime, timedelta

import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ========================================
# 設定
# ========================================

HALL_ID = 1403
BASE_URL = "https://challenger.pt.teramoba2.com/n-api/rack_info"

# リクエストヘッダー（ブラウザに偽装）
_cookie = os.environ.get("CHALLENGER_COOKIE", "")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://challenger.pt.teramoba2.com/",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "ja,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Cookie": _cookie,
}

# 対象機種（machine_nameはPayloadタブで確認した値）
MACHINES = [
    {
        "name": "ネオアイムジャグラーEX",
        "kind_code": 21,
        "machine_name": "S%EF%BE%88%EF%BD%B5%EF%BD%B1%EF%BD%B2%EF%BE%91%EF%BD%BC%EF%BE%9E%EF%BD%AC%EF%BD%B8%EF%BE%9E%EF%BE%97%EF%BD%B0EX-KK",
    },
    # 動作確認後に追加
    # {"name": "ゴーゴージャグラー3", "kind_code": 21, "machine_name": "..."},
    # {"name": "マイジャグラーV",     "kind_code": 21, "machine_name": "..."},
]

# ========================================
# API取得関数
# ========================================

def fetch_bb_history(machine: dict, history_day: int = 7) -> dict:
    """日付別BB履歴を取得"""
    # machine_nameは既にURLエンコード済みなのでparamsに渡さずURLに直接組み込む
    url = (
        f"{BASE_URL}/machine_bb_history"
        f"?hall_id={HALL_ID}"
        f"&kind_code={machine['kind_code']}"
        f"&machine_name={machine['machine_name']}"
        f"&history_day={history_day}"
        f"&place="
    )
    print(f"  BB履歴取得中...")
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    print(f"  → {len(data)}台分")
    return data


def fetch_closed_info(rack_nos: list, history_day: int = 7) -> dict:
    """閉店データ（回転数・詳細）を取得"""
    url = f"{BASE_URL}/closed_info"
    params = {
        "hall_id": HALL_ID,
        "rackNos": ",".join(str(n) for n in rack_nos),
        "history_day": history_day,
    }
    print(f"  閉店データ取得中...")
    resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    print(f"  → {len(data)}台分")
    return data


# ========================================
# データ整形
# ========================================

def build_rows(machine_name: str, bb_history: dict, closed_info: dict, target_date: str) -> list:
    """bb_historyとclosed_infoを結合してSheets行形式に変換"""
    rows = []
    date_str = datetime.strptime(target_date, "%Y-%m-%d").strftime("%Y/%m/%d")

    for rack_no_str, history_list in bb_history.items():
        rack_no = int(rack_no_str)

        # 対象日のデータを検索
        day_data = next(
            (item for item in history_list if item.get("day") == target_date),
            None
        )
        if day_data is None:
            continue

        big = day_data.get("bounus 1")
        reg = day_data.get("bounus 2")

        # BIG・REGどちらもnullなら営業中または非稼働
        if big is None and reg is None:
            continue

        # closed_infoから回転数を取得
        # ※実際のフィールド名はclosed_infoのResponseを見て調整が必要
        games = ""
        rack_closed = closed_info.get(str(rack_no), [])
        for c in rack_closed:
            if c.get("day") == target_date:
                games = (
                    c.get("total_games")
                    or c.get("start")
                    or c.get("games")
                    or c.get("total")
                    or ""
                )
                break

        rows.append([
            date_str,     # A: 日付
            rack_no,      # B: 台番号
            machine_name, # C: 機種名
            "",           # D: 島番号（masterシートから参照）
            big or 0,     # E: BIG
            reg or 0,     # F: REG
            games,        # G: 総回転数
        ])

    rows.sort(key=lambda r: r[1])  # 台番号順にソート
    return rows


# ========================================
# Google Sheets書き込み
# ========================================

def write_to_sheets(all_rows: list):
    print("\nGoogle Sheetsに書き込み中...")

    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if creds_json:
        creds_dict = json.loads(creds_json)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(
            creds_dict,
            ["https://spreadsheets.google.com/feeds",
             "https://www.googleapis.com/auth/drive"]
        )
    else:
        # ローカルテスト用
        creds = ServiceAccountCredentials.from_json_keyfile_name(
            "credentials.json",
            ["https://spreadsheets.google.com/feeds",
             "https://www.googleapis.com/auth/drive"]
        )

    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(os.environ["SPREADSHEET_ID"]).worksheet("raw_data")
    sheet.append_rows(all_rows, value_input_option="USER_ENTERED")
    print(f"  → {len(all_rows)}行を書き込みました")


# ========================================
# メイン
# ========================================

def main():
    print("=" * 50)
    print("チャレンジャーB館 台データ収集 v2.0")
    print(f"実行日時: {datetime.now().strftime('%Y/%m/%d %H:%M:%S')}")
    print("=" * 50)

    # 前日のデータを取得（閉店後に実行する想定）
    target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"対象日: {target_date}")

    all_rows = []

    for machine in MACHINES:
        print(f"\n【{machine['name']}】")
        try:
            bb_history = fetch_bb_history(machine)
            if not bb_history:
                print("  データなし、スキップ")
                continue

            rack_nos = list(bb_history.keys())
            closed_info = fetch_closed_info(rack_nos)

            rows = build_rows(machine["name"], bb_history, closed_info, target_date)
            print(f"  → {len(rows)}行を整形")
            all_rows.extend(rows)

            time.sleep(2)  # サーバー負荷軽減

        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n合計 {len(all_rows)}行")

    if all_rows:
        write_to_sheets(all_rows)
    else:
        print("書き込むデータがありません（営業中または取得失敗）")

    print("\n✅ 完了")


if __name__ == "__main__":
    main()
