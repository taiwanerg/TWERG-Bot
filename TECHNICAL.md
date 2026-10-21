# TWERG-Bot Technical Guide

Developer reference for TWERG-Bot. New technical notes go into this file.

## Contents

1. [Outbox: reliable background delivery](#outbox)
   - [How it works](#outbox-how)
   - [Sending](#outbox-send)
   - [Replying in a thread](#outbox-reply)
   - [Editing a message later](#outbox-edit)
   - [Late-delivery callbacks](#outbox-late)
   - [Delayed work](#outbox-timer)
   - [Catching up after a restart](#outbox-catchup)
   - [Don'ts](#outbox-dont)
   - [API](#outbox-api)
   - [Existing keys and TTLs](#outbox-keys)
   - [Reading the logs](#outbox-logs)
   - [Troubleshooting](#outbox-trouble)

<a name="outbox"></a>

## Outbox: reliable background delivery

`module/outbox.py` is a shared send queue. Messages are saved to disk before sending, retried automatically when Discord or the network is down, and resumed after a restart.

`module/push_state.py` lets each push remember the last item it sent, so it can catch up on what happened while the bot was offline.

<a name="outbox-how"></a>

### How it works

```
push module ──► outbox.send()
                  │
                  ├─ 1. write to data/outbox.json (images go to data/outbox_files/)
                  ├─ 2. try to send immediately
                  │      ├─ success → remove from queue, remember the message ID
                  │      ├─ transient error (5xx, 429, disconnect, timeout) → keep, retry later
                  │      └─ permanent error (no permission, channel gone, 400) → drop and log
                  └─ 3. every 3 s, retry whatever is due
                         backoff: 10 s → 30 s → 1 min → 2 min → then every 5 min
                         dropped once past its TTL (default 2 hours)
```

- **Survives restarts**: the queue and timers live on disk.
- **No duplicates**: each key is sent once.
- **Threads stay in order**: a reply waits until the message it replies to is sent. If that message is dropped, the reply is sent without the reference.
- **Edits collapse**: when one message is edited several times in a row, only the latest content is sent.
- **One broken channel does not block the others.**
- **No added latency**: when Discord is healthy, messages go out immediately. The queue only matters on failure.

<a name="outbox-send"></a>

### Sending

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
            content="Title",
            embed=embed,
            files=[(png, "map.png")],
            view=view,
            ttl=2 * 3600,
        )
```

- **The key must be the same every time for the same message**, usually `feature:event_id:channel_id`, so restarts and repeated triggers never send twice.
- Pass images as `(bytes, filename)`, not `discord.File`, so they can be saved.
- `view` supports **link buttons only**; regular button callbacks cannot be saved.
- `ttl` is how long the message is still worth delivering. Default 2 hours; use shorter values for time-critical pushes (early warnings, live monitors).
- Returns the `discord.Message` if it was sent right away; `None` if queued or dropped. Use `self.box.state(key)` for `"sent"`, `"queued"` or `"dropped"`.

<a name="outbox-reply"></a>

### Replying in a thread

```python
await self.box.send(report_key, channel_id, embed=embed)
await self.box.send(
    prompt_key, channel_id,
    content="Did you feel it?",
    reply_to=report_key,
    mention_author=False,
)
```

`reply_to` takes the other message's **key**; the outbox waits for it to be sent first. If you already have the message ID, pass `(channel_id, message_id)` instead.

<a name="outbox-edit"></a>

### Editing a message later

```python
await self.box.edit(f"{send_key}:edit", channel_id, send_key, content="Updated", embed=new_embed)
```

The third argument, `target`, is the original message's key or `(channel_id, message_id)`. Repeated calls with the same edit key send only the latest version. If the original has not been sent yet, the edit waits for it.

<a name="outbox-late"></a>

### Late-delivery callbacks

For example, to record a message ID for later edits:

```python
self.box.on_sent("my_feature", self.on_late_sent)
self.box.on_dropped("my_feature", self.on_dropped)

await self.box.send(key, channel_id, content="...", kind="my_feature", meta={"event": event_id})

def on_late_sent(self, key, meta, message):
    self.records[meta["event"]].append(message.id)

def on_dropped(self, key, meta, reason):
    ...
```

- Register `on_sent` and `on_dropped` in the cog's `__init__`, so queued messages delivered after a restart still find their handler.
- `meta` is saved to disk; JSON-serialisable values only (strings, numbers, lists, dicts).
- `reason` is one of `Forbidden`, `NotFound`, `HTTP 400`, `expired`, `target_missing`, `error`.

<a name="outbox-timer"></a>

### Delayed work (instead of `asyncio.sleep`)

```python
self.box.on_timer("my_followup", self.run_followup)
self.box.schedule(f"my_followup:{event_id}", "my_followup", time.time() + 1800, {"event": event_id})

def run_followup(self, args):
    self.bot.loop.create_task(self.do_followup(args["event"]))
```

An `asyncio.sleep()` is lost on restart; `schedule()` is saved and runs on time after a restart. Each schedule key runs once; scheduling it again returns `False`.

<a name="outbox-catchup"></a>

### Catching up after a restart

```python
from module import push_state

last = push_state.load("my_feature").get("last_id")
push_state.save("my_feature", {"last_id": newest_id})
if push_state.fresh(event_epoch):
    ...
```

- On the very first start (no saved state), record the newest item and send nothing, so old data is not flooded out.
- `fresh()` defaults to "within the last 2 hours".
- For UTC timestamps formatted `YYYYMMDDhhmmss`, use `push_state.fresh_utc_stamp(stamp)`.

<a name="outbox-dont"></a>

### Don'ts

- ❌ `channel.send()` directly from a background push.
- ❌ Values that change every call (such as `time.time()`) in a key, unless it is an admin's forced resend.
- ❌ `discord.Channel`, `discord.Message` or similar objects in `meta` or timer args. Store IDs.
- ❌ The outbox inside console-forwarding code. Its own logs are forwarded to the console and would loop forever.
- ❌ Slash command responses. Those are live interactions, not background pushes.

<a name="outbox-api"></a>

### API

| Method | Purpose |
|---|---|
| `outbox.get(bot)` | The shared outbox; created and started on first call |
| `send(key, channel_id, *, content, embed, files, view, allowed_mentions, reply_to, mention_author, ttl, kind, meta, raise_on_drop)` | Send a message |
| `edit(key, channel_id, target, *, content, embed, files, allowed_mentions, ttl, kind, meta)` | Edit a message |
| `publish(key, channel_id, message_id, ttl=600)` | Publish a message in an announcement channel |
| `schedule(key, name, run_at, args)` | Schedule delayed work |
| `state(key)` | `"sent"`, `"queued"`, `"dropped"` or `None` |
| `message_ref(key)` | `(channel_id, message_id)` of a sent message |
| `on_sent(kind, fn)`, `on_dropped(kind, fn)`, `on_timer(name, fn)` | Register callbacks |

With `raise_on_drop=True`, permanent errors (missing permissions, etc.) re-raise the original `discord` exception, for code that already has its own error handling (such as report revisions).

<a name="outbox-keys"></a>

### Existing keys and TTLs

| Push | Key | TTL |
|---|---|---|
| CWA earthquake report | `cwa:{no}:{channel}:report` | 2 h |
| DYFI prompt | `cwa:{no}:{channel}:prompt` | 2 h |
| 30-minute DYFI summary | `dyfi:{no}:{channel}` (timer `dyfi:{no}`) | 2 h |
| KMA warning / report | `kma:{image}:{channel}` | warning 10 min / report 2 h |
| KMA edit | `kma:edit:{channel}:{message}` | same as above |
| KMA upgrade mention | `kma:ping:{channel}:{message}` | same as above |
| RMT / GRMT | `rmt:{time}:{channel}` / `grmt:{time}:{channel}` | 2 h |
| Early-est | `earlyest:{event}:{channel}`, edits add `:edit` | 2 h |
| Report revision | `revision:{event}:{hash}:{channel}` | 2 h |
| YouTube live monitor | `yt:{time}:{channel}` | 15 min |
| Announcement auto-publish | `publish:{message}` | 10 min |

Give new features their own prefix to avoid collisions.

<a name="outbox-logs"></a>

### Reading the logs

| Log | Meaning |
|---|---|
| `⏳ [Outbox] xxx 發送失敗（第 N 次），N 秒後重試` | Transient failure; it will retry on its own |
| `✅ [Outbox] xxx 補發成功（重試 N 次後）` | A previously failed message was delivered |
| `🗑️ [Outbox] 放棄 xxx：Forbidden／NotFound` | Missing permission or the channel/message is gone; check that channel's settings |
| `🗑️ [Outbox] 放棄 xxx：expired` | Past its TTL (usually a long outage) |
| `📮 [Outbox] 載入待發送 N 則` | Unsent messages found on restart; they will be sent |

<a name="outbox-trouble"></a>

### Troubleshooting

- Clear the queue: stop the bot, delete `data/outbox.json` and `data/outbox_files/`, then start it.
- Stop a push from catching up after a restart: delete its `data/push_state_*.json`; it starts again from the newest item.
- Keep the `data/` folder when updating the code.
