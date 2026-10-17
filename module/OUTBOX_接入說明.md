# 訊息防漏系統（outbox）接入說明

> 適用於 TWERG-Bot 的所有**背景推送**。新增推送功能時，請照這份說明接上，不要直接 `channel.send()`。

## 目錄

1. [這是什麼](#what)（概要）
2. [運作方式](#how)（技術教學）
3. [怎麼接上](#guide)（技術教學）
   - [一般發送](#guide-send)
   - [要回覆另一則訊息（串成一串）](#guide-reply)
   - [之後要編輯訊息](#guide-edit)
   - [想在「晚一點才發成功」時收到通知](#guide-late)
   - [延遲一段時間後才做的事](#guide-timer)
   - [重開後補發離線期間的資料](#guide-catchup)
   - [不要做的事](#guide-dont)
4. [API 參數一覽](#api)（技術教學）
5. [現有推送的 key 與期限](#keys)（技術教學）
6. [log 怎麼看](#logs)（使用教學）
7. [有問題的話](#trouble)（使用教學）

<a name="what"></a>

## 這是什麼

`module/outbox.py` 是一個共用的**發送佇列**。訊息會先存檔再發送，Discord 或網路暫時掛掉時自動重試，bot 重開後也會接著發。

`module/push_state.py` 是小工具，讓各推送記住「最後推送到哪一筆」，重開後可以補發離線期間的資料。

<a name="how"></a>

## 運作方式

```
推送模組 ──► outbox.send()
              │
              ├─ 1. 先寫進 data/outbox.json（圖片另存 data/outbox_files/）
              ├─ 2. 立刻嘗試發送
              │      ├─ 成功 → 從佇列刪掉，記錄訊息 ID
              │      ├─ 暫時性錯誤（5xx、429、斷線、逾時）→ 留在佇列，稍後重試
              │      └─ 永久錯誤（沒權限、頻道不存在、400）→ 放棄並記 log
              └─ 3. 背景每 3 秒檢查佇列，到時間就重試
                     重試間隔：10 秒 → 30 秒 → 1 分 → 2 分 → 之後每 5 分
                     超過期限（預設 2 小時）就放棄
```

- **重開不會掉**：佇列和排程都存在檔案裡
- **不會重複發**：同一個 key 只會發一次
- **整串不會亂**：要回覆的訊息會等「被回覆的那則」發出去才發；被回覆的那則放棄了，就改成不帶回覆直接發
- **編輯只留最新版**：同一則訊息連續要更新好幾次時，只送最新的內容
- **一個頻道壞掉不影響其他頻道**
- **正常時不變慢**：Discord 正常時一樣當下立刻發，佇列只在失敗時才派上用場

<a name="guide"></a>

## 怎麼接上

**原則：背景推送一律用 `outbox.send()`，不要直接 `channel.send()`。**

<a name="guide-send"></a>

### 1. 一般發送

```python
from module import outbox

class MyAlertCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.box = outbox.get(bot)

    async def push(self, event_id, channel_id, embed, png, view):
        await self.box.send(
            f"my_feature:{event_id}:{channel_id}",
            channel_id,
            content="標題",
            embed=embed,
            files=[(png, "map.png")],
            view=view,
            ttl=2 * 3600,
        )
```

- **key 一定要「同一則訊息每次都算出一樣的值」**，通常是 `功能名:事件ID:頻道ID`，這樣重開或重複觸發都不會發兩次
- 圖片傳 `(bytes, 檔名)`，不要傳 `discord.File`（要能存檔）
- `view` 只支援**連結按鈕**（一般按鈕的 callback 沒辦法存檔）
- `ttl` 是最晚多久內還要補發，預設 2 小時；時效性高的（預警、即時監控）請設短一點
- 回傳值：當下發成功回傳 `discord.Message`；排進佇列或放棄回傳 `None`。要知道狀態用 `self.box.state(key)`，會回 `"sent"`、`"queued"` 或 `"dropped"`

<a name="guide-reply"></a>

### 2. 要回覆另一則訊息（串成一串）

```python
await self.box.send(report_key, channel_id, embed=embed)
await self.box.send(
    prompt_key, channel_id,
    content="您感受到了嗎？",
    reply_to=report_key,
    mention_author=False,
)
```

`reply_to` 填另一則的 **key**，outbox 會等那則發出去才發這則。已經知道訊息 ID 的話，也可以直接填 `(頻道ID, 訊息ID)`。

<a name="guide-edit"></a>

### 3. 之後要編輯訊息

```python
await self.box.edit(f"{send_key}:edit", channel_id, send_key, content="更新後內容", embed=new_embed)
```

第三個參數 `target` 填原訊息的 key 或 `(頻道ID, 訊息ID)`。同一個 edit key 連續呼叫，只會送最新的一次。原訊息還沒發出時，編輯會等它發出後再送。

<a name="guide-late"></a>

### 4. 想在「晚一點才發成功」時收到通知

例如要記錄訊息 ID 給之後編輯用：

```python
self.box.on_sent("my_feature", self.on_late_sent)
self.box.on_dropped("my_feature", self.on_dropped)

await self.box.send(key, channel_id, content="...", kind="my_feature", meta={"event": event_id})

def on_late_sent(self, key, meta, message):
    self.records[meta["event"]].append(message.id)

def on_dropped(self, key, meta, reason):
    ...
```

- `on_sent`、`on_dropped` 請在 cog 的 `__init__` 註冊，重開後佇列裡的舊訊息補發成功時才找得到處理器
- `meta` 會存進檔案，所以只能放 JSON 能存的東西（字串、數字、list、dict）
- `reason` 可能是 `Forbidden`、`NotFound`、`HTTP 400`、`expired`、`target_missing`、`error`

<a name="guide-timer"></a>

### 5. 延遲一段時間後才做的事（取代 `asyncio.sleep`）

```python
self.box.on_timer("my_followup", self.run_followup)
self.box.schedule(f"my_followup:{event_id}", "my_followup", time.time() + 1800, {"event": event_id})

def run_followup(self, args):
    self.bot.loop.create_task(self.do_followup(args["event"]))
```

用 `asyncio.sleep()` 等的話，重開就消失了；改用 `schedule()` 會存檔，重開後照時間執行。同一個排程 key 只會排一次，重複呼叫回傳 `False`。

<a name="guide-catchup"></a>

### 6. 重開後補發離線期間的資料

```python
from module import push_state

last = push_state.load("my_feature").get("last_id")
push_state.save("my_feature", {"last_id": newest_id})
if push_state.fresh(event_epoch):
    ...
```

- 第一次啟動（還沒有紀錄）時，只記錄目前最新的一筆、不要補發，避免一上線就把舊資料全推出去
- `fresh()` 預設判斷是否在 2 小時內，超過就不補
- 時間是 `YYYYMMDDhhmmss` 格式的 UTC 字串時，可以用 `push_state.fresh_utc_stamp(stamp)`

<a name="guide-dont"></a>

### 不要做的事

- ❌ 背景推送直接 `channel.send()`
- ❌ key 裡放 `time.time()` 這種每次都不一樣的值（除非是管理員手動強制重發）
- ❌ 在 `meta` 或排程參數裡放 `discord.Channel`、`discord.Message` 這類物件，請改存 ID
- ❌ 在 console 轉發相關的程式裡用 outbox（outbox 自己的 log 會再丟回 console，會無限循環）
- ❌ 用在斜線指令的回覆上（那是即時互動，不是背景推送）

<a name="api"></a>

## API 參數一覽

| 方法 | 用途 |
|---|---|
| `outbox.get(bot)` | 取得共用的 outbox（第一次呼叫時建立並啟動背景佇列） |
| `send(key, channel_id, *, content, embed, files, view, allowed_mentions, reply_to, mention_author, ttl, kind, meta, raise_on_drop)` | 發送訊息 |
| `edit(key, channel_id, target, *, content, embed, files, allowed_mentions, ttl, kind, meta)` | 編輯訊息 |
| `publish(key, channel_id, message_id, ttl=600)` | 發布公告頻道的訊息 |
| `schedule(key, name, run_at, args)` | 排程延遲工作 |
| `state(key)` | 查詢狀態：`"sent"`／`"queued"`／`"dropped"`／`None` |
| `message_ref(key)` | 已發出訊息的 `(頻道ID, 訊息ID)` |
| `on_sent(kind, fn)`／`on_dropped(kind, fn)`／`on_timer(name, fn)` | 註冊回呼 |

`raise_on_drop=True` 時，永久錯誤（沒權限等）會直接丟出原本的 `discord` 例外，適合原本就有自己錯誤處理的程式（例如地震報告更新）。

<a name="keys"></a>

## 現有推送的 key 與期限

| 推送 | key 格式 | 期限 |
|---|---|---|
| 中央氣象署地震報告 | `cwa:{編號}:{頻道}:report` | 2 小時 |
| 體感回報邀請 | `cwa:{編號}:{頻道}:prompt` | 2 小時 |
| 30 分鐘體感統計 | `dyfi:{編號}:{頻道}`（排程 `dyfi:{編號}`） | 2 小時 |
| KMA 預警／報告 | `kma:{圖檔名}:{頻道}` | 預警 10 分鐘／報告 2 小時 |
| KMA 編輯 | `kma:edit:{頻道}:{訊息}` | 同上 |
| KMA 升級提及 | `kma:ping:{頻道}:{訊息}` | 同上 |
| RMT／GRMT | `rmt:{時間}:{頻道}`／`grmt:{時間}:{頻道}` | 2 小時 |
| Early-est | `earlyest:{事件}:{頻道}`、編輯加 `:edit` | 2 小時 |
| 地震報告更新 | `revision:{事件}:{雜湊}:{頻道}` | 2 小時 |
| YouTube 直播監控 | `yt:{時間}:{頻道}` | 15 分鐘 |
| 公告自動發布 | `publish:{訊息}` | 10 分鐘 |

新功能請用自己的前綴，避免跟現有的撞名。

<a name="logs"></a>

## log 怎麼看

| log | 意思 |
|---|---|
| `⏳ [Outbox] xxx 發送失敗（第 N 次），N 秒後重試` | 暫時失敗，會自動重試，不用處理 |
| `✅ [Outbox] xxx 補發成功（重試 N 次後）` | 之前失敗的訊息已經補發成功 |
| `🗑️ [Outbox] 放棄 xxx：Forbidden／NotFound` | 沒權限或頻道／訊息不存在，請檢查該頻道設定 |
| `🗑️ [Outbox] 放棄 xxx：expired` | 超過補發期限（通常是斷線太久） |
| `📮 [Outbox] 載入待發送 N 則` | 重開時發現有還沒發完的訊息，會接著發 |

<a name="trouble"></a>

## 有問題的話

- 想清空佇列：停掉 bot，刪掉 `data/outbox.json` 和 `data/outbox_files/` 再啟動
- 想讓某個推送重開後不要補發：刪掉對應的 `data/push_state_*.json`，下次啟動會重新從最新一筆開始記
- 更新程式時請保留 `data/` 資料夾
