import logging
import json
import os
import re
from datetime import datetime, timezone, timedelta
import discord

# 這是抓取 Discord 頻道中使用者回報的模組

# Discord 震度表情對照表
GRADE_EMOJIS = {
    "A0": "0",
    "A1": "1",
    "A2": "2",
    "A3": "3",
    "A4": "4",
    "A5j": "5弱",
    "A5k": "5強",
    "A6j": "6弱",
    "A6k": "6強",
    "A7": "7"
}

def get_towns_mapping():
    """讀取地圖資料檔建立台灣所有縣市與鄉鎮的對照名稱陣列"""
    topo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'maps/towns-mercator-10t.json')
    aliases_raw = []
    try:
        if os.path.exists(topo_path):
            with open(topo_path, 'r', encoding='utf-8') as f:
                topo = json.load(f)
            
            counties = set()
            for g in topo['objects']['towns']['geometries']:
                props = g.get('properties', {})
                c, t = props.get('COUNTYNAME', ''), props.get('TOWNNAME', '')
                if c and t:
                    # 統一將 topo 的台轉為臺，以符合氣象署與 TWERG API 標準
                    c = c.replace('台', '臺')
                    t = t.replace('台', '臺')
                    counties.add(c)
                    aliases_raw.append((c + t, c, t)) # 完整名稱 (新北市新店區)
                    
                    short_c = c.replace('市', '').replace('縣', '')
                    aliases_raw.append((short_c + t, c, t)) # 簡化縣市 (新北新店區)
                    
                    short_t = t.replace('市', '').replace('鎮', '').replace('鄉', '').replace('區', '')
                    aliases_raw.append((short_c + short_t, c, t)) # 全簡化 (新北新店)
                    aliases_raw.append((t, c, t)) # 僅鄉鎮完整名 (新店區)
                    
                    if len(short_t) >= 2:
                        aliases_raw.append((short_t, c, t)) # 僅鄉鎮簡化名 (新店)
                        
            # 加入僅縣市層級的名稱，並對應到各縣市政府所在地，以利地圖繪製
            county_centers = {
                "基隆市": "中正區", "臺北市": "信義區", "新北市": "板橋區", "桃園市": "桃園區",
                "新竹縣": "竹北市", "新竹市": "北區", "苗栗縣": "苗栗市", "臺中市": "西屯區",
                "彰化縣": "彰化市", "南投縣": "南投市", "雲林縣": "斗六市", "嘉義縣": "太保市",
                "嘉義市": "東區", "臺南市": "安平區", "高雄市": "苓雅區", "屏東縣": "屏東市",
                "宜蘭縣": "宜蘭市", "花蓮縣": "花蓮市", "臺東縣": "臺東市", "澎湖縣": "馬公市",
                "金門縣": "金城鎮", "連江縣": "南竿鄉"
            }
            for c in counties:
                default_t = county_centers.get(c, "")
                aliases_raw.append((c, c, default_t)) # 完整縣市 (新北市)
                
                # 處理嘉義與新竹的簡稱衝突：若為縣，則不加入簡稱，讓「新竹/嘉義」優先配對到市
                if c in ["新竹縣", "嘉義縣"]:
                    continue
                    
                short_c = c.replace('市', '').replace('縣', '')
                aliases_raw.append((short_c, c, default_t)) # 簡化縣市 (新北)
                        
            # 統計並排除模糊地名 (例如「信義」、「東區」對應到多個不同縣市)
            alias_map = {}
            for alias, c, t in aliases_raw:
                if alias not in alias_map:
                    alias_map[alias] = set()
                alias_map[alias].add((c, t))
                
            aliases = []
            added_aliases = set()
            for alias, c, t in aliases_raw:
                # 只有唯一對應的組合才加入
                if len(alias_map[alias]) == 1 and alias not in added_aliases:
                    aliases.append((alias, c, t))
                    added_aliases.add(alias)
                        
            # 長字串優先比對，避免如「中區」和「台中」衝突的問題
            aliases.sort(key=lambda x: len(x[0]), reverse=True)
            return aliases
    except Exception as e:
        logging.warning("⚠️ 載入鄉鎮資料失敗:", e)
    return []

TOWNS_ALIASES = get_towns_mapping()

async def fetch_discord_reports(bot, origin_time_str: str):
    """讀取 config.json 中的頻道，並分析地震發生後 30 分鐘內的體感訊息"""
    # ⚠️ 如果未開啟 Message Content Intent，將無法讀取使用者發送的訊息內容，直接略過
    if not bot.intents.message_content:
        logging.warning("⚠️ 由於關閉了 message_content 權限，無法讀取歷史訊息，已自動略過 Discord 體感回報分析。")
        return None

    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        channel_id = config.get("DYFI_CHANNEL_ID")
        api_key = config.get("CWA_API_KEY")
        if not channel_id:
            return None
            
        channel = bot.get_channel(int(channel_id))
        if not channel:
            return None
            
        tw_tz = timezone(timedelta(hours=8))
        try:
            try:
                dt = datetime.fromisoformat(origin_time_str.replace('Z', '+00:00'))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=tw_tz)
                origin_time = dt
            except ValueError:
                dt = datetime.strptime(origin_time_str, "%Y-%m-%d %H:%M:%S")
                origin_time = dt.replace(tzinfo=tw_tz)
        except ValueError:
            return None
            
        end_time = origin_time + timedelta(minutes=30)
        
        # 尋找是否在 30 分鐘內有其他地震發生，避免訊息重疊
        if api_key:
            try:
                urls = [
                    f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/E-A0015-001?Authorization={api_key}&format=JSON",
                    f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/E-A0016-001?Authorization={api_key}&format=JSON"
                ]
                for url in urls:
                    async with bot.session.get(url) as res:
                        if res.status == 200:
                            data = await res.json()
                            for eq in data.get('records', {}).get('Earthquake', []):
                                eq_time_str = eq.get('EarthquakeInfo', {}).get('OriginTime', '')
                                try:
                                    try:
                                        eq_dt = datetime.fromisoformat(eq_time_str.replace('Z', '+00:00'))
                                        if eq_dt.tzinfo is None:
                                            eq_dt = eq_dt.replace(tzinfo=tw_tz)
                                    except ValueError:
                                        eq_dt = datetime.strptime(eq_time_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=tw_tz)
                                    # 如果這筆地震發生在 origin_time 之後，且比當前的 end_time 早
                                    if origin_time < eq_dt < end_time:
                                        end_time = eq_dt
                                except ValueError:
                                    continue
            except Exception as e:
                logging.warning(f"⚠️ 檢查下一筆地震時間失敗: {e}")

        # 防洗版機制：使用字典記錄，確保每個 Discord 用戶只能有一筆有效的體感回報
        user_reports = {}
        
        async for msg in channel.history(after=origin_time, before=end_time, limit=None):
            if msg.author.bot: continue
            content = msg.content
            
            content_clean = content.replace('台', '臺')
            
            # 1. 尋找地名
            loc_count = 0
            found_county = None
            found_town = None
            for alias, c, t in TOWNS_ALIASES:
                if alias in content_clean:
                    count = content_clean.count(alias)
                    loc_count += count
                    if found_county is None:
                        found_county = c
                        found_town = t
                    content_clean = content_clean.replace(alias, ' ')
                    
            # 若無發現地名，或是填入超過 1 個地名，則忽略此訊息
            if loc_count != 1: continue
                    
            # 2. 尋找震度
            grades_found = []
            temp_content = content_clean
            
            # 2.1 自定義 Emoji
            for m in re.finditer(r'<:(A[0-7][jk]?):\d+>', temp_content):
                grades_found.append(GRADE_EMOJIS.get(m.group(1)))
            temp_content = re.sub(r'<:(A[0-7][jk]?):\d+>', ' ', temp_content)
            
            # 2.2 預設文字 Emoji
            for m in re.finditer(r':(A[0-7][jk]?):', temp_content):
                grades_found.append(GRADE_EMOJIS.get(m.group(1)))
            temp_content = re.sub(r':(A[0-7][jk]?):', ' ', temp_content)
            
            # 2.3 Unicode 數字
            unicode_map = {"0️⃣": "0", "1️⃣": "1", "2️⃣": "2", "3️⃣": "3", "4️⃣": "4", "5️⃣": "5弱", "6️⃣": "6弱", "7️⃣": "7"}
            for u, g in unicode_map.items():
                count = temp_content.count(u)
                for _ in range(count):
                    grades_found.append(g)
                temp_content = temp_content.replace(u, ' ')
                        
            # 2.4 純文字級別 (含強弱)
            for m in re.finditer(r'(5|6)[ \t]*([弱強\-\+])', temp_content):
                grades_found.append(f"{m.group(1)}弱" if m.group(2) in ['-', '弱'] else f"{m.group(1)}強")
            temp_content = re.sub(r'(5|6)[ \t]*([弱強\-\+])', ' ', temp_content)
            
            # 2.5 X級
            for m in re.finditer(r'([0-7])[ \t]*級', temp_content):
                val = m.group(1)
                grades_found.append('5弱' if val == '5' else '6弱' if val == '6' else val)
            temp_content = re.sub(r'([0-7])[ \t]*級', ' ', temp_content)
            
            # 2.6 單獨數字
            for m in re.finditer(r'(?<![a-zA-Z\d\./:\-])([0-7])(?![a-zA-Z\d\./:\-樓層點分月日])', temp_content):
                val = m.group(1)
                grades_found.append('5弱' if val == '5' else '6弱' if val == '6' else val)
                    
            # 若無發現震度，或是發現超過 1 個震度數字，則忽略此訊息
            if len(grades_found) != 1: continue
                
            found_grade = grades_found[0]
            
            # 將解析結果存入字典，若同一用戶發送多則，將以最後一則為準
            user_reports[msg.author.id] = {"county": found_county, "town": found_town, "grade": found_grade}
                    
        return list(user_reports.values())
    except Exception as e:
        logging.warning(f"⚠️ 讀取 Discord 體感回報失敗: {e}")
        return None

async def setup(bot):
    pass