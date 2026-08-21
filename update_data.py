import requests
from bs4 import BeautifulSoup
import pandas as pd
import re
import json
import time

print("データ収集を開始します...")

PREFECTURES = [
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", 
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県", 
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県", 
    "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県", 
    "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県", 
    "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県", 
    "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県"
]

base_url = "https://www.courts.go.jp"
index_url = "https://www.courts.go.jp/about/sosiki/kankatu/index.html"

res_index = requests.get(index_url)
res_index.encoding = res_index.apparent_encoding
soup_index = BeautifulSoup(res_index.text, "html.parser")

pref_links = []
for a_tag in soup_index.find_all("a"):
    href = a_tag.get("href")
    text = a_tag.get_text(strip=True)
    if href and text in PREFECTURES:
        if href.startswith("http"): full_url = href
        elif href.startswith("/"): full_url = base_url + href
        else: full_url = "https://www.courts.go.jp/about/sosiki/kankatu/" + href
            
        if not any(p[0] == text for p in pref_links):
            pref_links.append((text, full_url))

results = []

# --- 2. 各都道府県ページから順番にデータを取得する ---
for pref_name, pref_url in pref_links:
    print(f"読込中: {pref_name} ...")
    time.sleep(1) 
    
    try:
        res = requests.get(pref_url)
        res.encoding = res.apparent_encoding
        soup = BeautifulSoup(res.text, "html.parser")
        
        tables = soup.find_all("table")
        
        for table in tables:
            area_text = "地域不明"
            prev_elements = table.find_all_previous(['p', 'h3', 'h4', 'div'])
            for element in prev_elements:
                text = element.get_text(strip=True)
                if len(text) > 3 and "管轄" not in text and "裁判所" not in text:
                    area_text = text
                    break
            
            # 【ここが修正ポイント】
            # すべての裁判所候補をリストに集め、最も適切なものを選ぶ
            courts_in_table = []
            for row in table.find_all("tr"):
                row_text = row.get_text(strip=True)
                
                # 行に「家庭裁判所」「家裁」「支部」のいずれかが含まれていれば調べる
                if "家庭裁判所" in row_text or "家裁出張所" in row_text or "支部" in row_text:
                    tds = row.find_all("td")
                    if tds:
                        # 表の一番右のセルが具体的な裁判所名
                        cell_text = tds[-1].get_text(strip=True)
                        
                        # 単なるラベル（「支部」だけ等）や、簡易裁判所・高等裁判所を除外してリストに追加
                        if cell_text and cell_text not in ["地方・家庭裁判所", "家庭裁判所", "家裁出張所", "本庁", "支部", "－", "-"]:
                            if "簡易" not in cell_text and "高等" not in cell_text:
                                courts_in_table.append(cell_text)
            
            # 優先順位（1.出張所 -> 2.支部 -> 3.本庁）に基づいて最適な裁判所を決定する
            best_court = "情報なし"
            for c in courts_in_table:
                if "出張所" in c:
                    best_court = c
                    break
            
            if best_court == "情報なし":
                for c in courts_in_table:
                    if "支部" in c:
                        best_court = c
                        break
                        
            if best_court == "情報なし" and courts_in_table:
                best_court = courts_in_table[0] # 出張所も支部もない場合は、最初に見つけた本庁
                        
            if best_court != "情報なし":
                results.append({
                    "都道府県": pref_name,
                    "対象地域テキスト": area_text, 
                    "管轄家庭裁判所": best_court
                })
    except Exception as e:
        print(f"⚠️ {pref_name} でエラーが発生しました: {e}")

if not results:
    print("\n❌ エラー: データを取得できませんでした。")
else:
    # --- 3. データの自動分割（クレンジング） ---
    df = pd.DataFrame(results)
    df = df[df["対象地域テキスト"] != "地域不明"].drop_duplicates()

    def parse_area_text(text):
        text = text.replace('(', '（').replace(')', '）').replace(',', '，')
        municipalities = []
        blocks = text.split('，')
        for block in blocks:
            block = block.strip()
            if not block: continue
            match = re.search(r'^(.*?)(?:の内)?（(.*?)）', block)
            if match:
                prefix = match.group(1).strip()
                inside = match.group(2).strip()
                sub_items = re.split(r'[\s ]+', inside)
                for item in sub_items:
                    if item:
                        if "特別区" in prefix or "支庁" in prefix: municipalities.append(item)
                        else: municipalities.append(prefix + item)
            else:
                clean_block = re.sub(r'[\s ]+', '', block)
                municipalities.append(clean_block)
        return municipalities

    df["市区町村リスト"] = df["対象地域テキスト"].apply(parse_area_text)
    df_expanded = df.explode("市区町村リスト").reset_index(drop=True)
    df_final = df_expanded[["都道府県", "市区町村リスト", "管轄家庭裁判所"]].rename(columns={"市区町村リスト": "市区町村"})

    # --- 4. HTMLファイルの自動生成 ---
    data_dict = {}
    for index, row in df_final.iterrows():
        city_name = str(row['市区町村']).strip()
        if city_name:
            key = f"{row['都道府県']} {city_name}"
            data_dict[key] = row['管轄家庭裁判所']

    data_json = json.dumps(data_dict, ensure_ascii=False)

    html_template = f"""<!DOCTYPE html>
    <html lang="ja">
    <head>
        <meta charset="UTF-8">
        <title>【全国版】管轄家庭裁判所 検索</title>
        <style>
            body {{ font-family: 'Meiryo', sans-serif; background-color: #f4f7f6; color: #333; padding: 30px; text-align: center; }}
            .container {{ background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); max-width: 650px; margin: 0 auto; }}
            input {{ font-size: 18px; padding: 10px; width: 65%; border: 1px solid #ccc; border-radius: 5px; }}
            button {{ font-size: 18px; padding: 10px 20px; background-color: #0056b3; color: white; border: none; border-radius: 5px; cursor: pointer; margin-left: 5px; }}
            button:hover {{ background-color: #004494; }}
            #result {{ margin-top: 25px; font-size: 18px; text-align: left; line-height: 1.8; }}
            .match-item {{ background: #e9f5ff; padding: 10px; margin-bottom: 10px; border-left: 5px solid #0056b3; border-radius: 3px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h2>🏠 管轄家庭裁判所 検索システム (全国版)</h2>
            <p>調べたい市区町村名を入力してください（例：明石、江別）</p>
            <input type="text" id="searchInput" placeholder="例：明石市" onkeypress="if(event.key === 'Enter') searchCourt()">
            <button onclick="searchCourt()">検索</button>
            <div id="result"></div>
        </div>

        <script>
            const courtData = {data_json};

            function searchCourt() {{
                const query = document.getElementById("searchInput").value.trim();
                const resultDiv = document.getElementById("result");
                
                if (!query) {{
                    resultDiv.innerHTML = "<span style='color:red;'>キーワードを入力してください。</span>";
                    return;
                }}
                
                let foundHtml = "";
                let count = 0;
                
                for (const [prefCity, court] of Object.entries(courtData)) {{
                    if (prefCity.includes(query)) {{
                        foundHtml += `<div class='match-item'>【 <b>${{prefCity}}</b> 】の管轄は <b>${{court}}</b> です。</div>`;
                        count++;
                    }}
                }}
                
                if (count > 0) {{
                    resultDiv.innerHTML = `<p style="font-size: 14px; color: gray;">${{count}}件 見つかりました</p>` + foundHtml;
                }} else {{
                    resultDiv.innerHTML = "<span style='color:gray;'>見つかりませんでした。別のキーワードでお試しください。</span>";
                }}
            }}
        </script>
    </body>
    </html>
    """

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_template)
