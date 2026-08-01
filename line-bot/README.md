# 排程曆 LINE Bot

每天早上把今日事項推到 LINE，也可以直接問它問題。

```
你：今天
Bot：📅 8/1（週六） 今天
     14:00 與指導教授 meeting（1h）
     ・文獻回顧初稿 2.5h
     共 3.5h ／ 可投入 4h

你：文獻回顧還剩多久？來得及嗎？
Bot：還剩 8 小時，排到 8/7 做完，截止是 8/10，來得及。
```

## 它怎麼運作

```
排程曆網頁 ──把「算好的行程」上傳──▶ Cloudflare Worker ──▶ LINE
（GitHub Pages）      PUT /sync         （存在 KV）      推播／回覆
```

**伺服器不重算排程。** v1～v5 的 LINE 版在 Python 端重寫了一份排程演算法，
兩邊行為很容易走鐘（見[開發紀錄](../開發紀錄.md)挑戰 1，那也是 v6 砍掉 LINE 的主因之一）。
這次網頁端算完把結果上傳，Worker 只負責轉述，不會有第二份演算法。

代價是 **Bot 看到的是最後一次上傳的快照**。網頁開著時會自動上傳，
但如果你三天沒開網頁又改了手機上的什麼，Bot 不會知道 —— 所以每則回覆都附上
「資料更新於 X 前」，超過一天會直接提醒你去按同步。

---

## 需要準備

| 東西 | 用途 | 費用 |
|---|---|---|
| LINE 官方帳號（Messaging API） | 收發訊息 | 免費 |
| Cloudflare 帳號 | 跑 Worker + 存資料 | 免費方案就夠 |
| Anthropic API key | 自由問答（選用） | 按用量，見下方 |

不裝 Node、不用命令列也可以 —— 程式可以直接貼進 Cloudflare 的網頁後台。

---

## 一、建立 LINE 官方帳號

1. 到 <https://developers.line.biz/console/> 用 LINE 帳號登入
2. **Create a new provider**（隨便取個名字，例如你的名字）
3. 在 provider 裡 **Create a new channel** → 選 **Messaging API**
4. 填名稱（例如「排程曆」）、類別，建立
5. 建好後記下兩個東西：
   - **Basic settings** 分頁 → `Channel secret`
   - **Messaging API** 分頁 → 最下面 `Channel access token (long-lived)` → **Issue**
6. 同一頁的 **Auto-reply messages** 和 **Greeting messages** 建議關掉
   （不然每則訊息都會多一句罐頭回覆）
7. 用手機掃 **Messaging API** 分頁上的 QR code，把這個帳號加為好友

## 二、部署 Cloudflare Worker

### 建立 Worker

1. 到 <https://dash.cloudflare.com/> 註冊／登入
2. 左側 **Workers & Pages** → **Create** → **Start with Hello World!** → **Deploy**
3. 部署完點 **Edit code**，把整個編輯器內容刪掉，
   貼上 [`worker.js`](worker.js) 的全部內容 → **Deploy**
4. 記下你的網址，長得像 `https://xxxxx.你的帳號.workers.dev`

### 建立 KV（存行事曆資料的地方）

1. 左側 **Storage & Databases** → **KV** → **Create Instance**，名稱填 `schedcal`
2. 回到你的 Worker → **Settings** → **Bindings** → **Add** → **KV namespace**
   - Variable name：**`CAL`**（一定要叫這個）
   - KV namespace：選剛剛建的 `schedcal`
3. **Deploy**

### 設定密鑰

Worker → **Settings** → **Variables and Secrets** → **Add**，型別選 **Secret**：

| 名稱 | 值 |
|---|---|
| `LINE_CHANNEL_SECRET` | 步驟一的 Channel secret |
| `LINE_ACCESS_TOKEN` | 步驟一的 Channel access token |
| `SYNC_TOKEN` | 自己想一組長一點的密碼，等下網頁要填同一組 |
| `ANTHROPIC_API_KEY` | 選用，見下方「自由問答」 |
| `LINE_OWNER_ID` | 選用，見下方「只服務你自己」 |

存完按 **Deploy**。

### 設定每日推播時間

Worker → **Settings** → **Trigger Events** → **Add** → **Cron Trigger**，
填 `0 23 * * *`（UTC 23:00 ＝ **台北早上 7 點**）。

要改時間就換算：台北時間減 8 小時。例如台北 8:00 → `0 0 * * *`，台北 21:00 → `0 13 * * *`。

### 把 webhook 接上 LINE

回到 LINE Developers Console → **Messaging API** 分頁：

1. **Webhook URL** 填 `https://你的網址.workers.dev/webhook`
2. **Verify** 應該顯示 Success
3. **Use webhook** 打開

## 三、只服務你自己

`LINE_OWNER_ID` 沒設定時，**第一個跟 Bot 講話的人就成為擁有者**，之後其他人一律擋下。
自己先傳一句「今天」給 Bot，然後到 Worker 的 **Logs** 分頁（按 **Begin log stream** 再傳一次）
就會看到你的 user ID，把它填進 `LINE_OWNER_ID` 更保險。

沒有這道防線的話，任何人加到你的官方帳號就能看光你的任務清單和截止日
——這是 v3 修過的洞（開發紀錄挑戰 3），這版一開始就內建。

## 四、把網頁接上

打開[排程曆](https://carolkao-fin.github.io/smart-calendar/) → **設定** → 展開 **LINE 連動**：

- **Worker 網址**：`https://你的網址.workers.dev`（結尾不用加 `/sync`）
- **同步密鑰**：和 `SYNC_TOKEN` 一模一樣
- 按 **立即同步**，顯示「已同步」就成功了

之後任務一有變動就會自動上傳（延遲幾秒），不用再手動按。

## 五、試試看

傳「說明」給 Bot。可用的固定指令：

| 你打 | 回你 |
|---|---|
| 今天／明天 | 那天排了什麼、共幾小時、有什麼到期 |
| 本週 | 一週七天的行程一覽 |
| 風險 | 哪些事會遲交、排不進 |
| 說明 | 指令列表 |

其他話會交給 Claude 自由回答（需要 API key）。

---

## 自由問答（選用）

沒有設 `ANTHROPIC_API_KEY` 時，Bot 只認得上面那幾個指令，其他問題會直說。
設了之後就能問「這禮拜最該擔心哪件事」「下次跟教授開會是什麼時候」這種問題。

API key 在 <https://console.anthropic.com/> 申請。

**費用**：預設用 `claude-opus-5`，一次問答大約 NT$0.5～1（行事曆資料當作提示送過去，
問題越少資料越便宜）。想更省可以改 `worker.js` 開頭的：

```js
const MODEL = "claude-opus-5";     // 改成 "claude-sonnet-5" 約省一半
                                   // 改成 "claude-haiku-4-5" 最便宜
```

每日推播和固定指令**完全不用 API key**，那部分是純字串組裝，一毛錢都不花。

---

## 費用與額度

| 項目 | 免費額度 | 這個 Bot 的用量 |
|---|---|---|
| Cloudflare Workers | 每天 10 萬次請求 | 一天幾十次，遠遠用不完 |
| Cloudflare KV | 每天 1000 次寫入 | 每次改任務寫一次 |
| LINE 官方帳號 | 免費方案每月 200 則推播 | 每天早上 1 則 ≈ 30 則／月 |
| Anthropic API | 無免費額度 | 只有自由問答會用到 |

> LINE 的「回覆訊息」通常不計入推播額度，但各地區方案不同，
> 以你自己的官方帳號後台顯示的為準。

---

## 個資與隱私

**這是整個專案唯一會把資料送出裝置的功能，開啟前請看清楚。**

啟用 LINE 連動後，你的**任務名稱、截止日、時數、完成進度**會離開瀏覽器：

| 送到哪 | 存多久 | 誰看得到 |
|---|---|---|
| 你自己的 Cloudflare KV | 直到被新的快照覆蓋 | 你的 Cloudflare 帳號 |
| LINE 伺服器 | 依 LINE 政策 | 你和 LINE |
| Anthropic API（**只有**自由問答時） | 依 Anthropic 政策 | 依 API 條款 |

不啟用（設定留空）的話，排程曆完全不連外，資料只存在你自己的瀏覽器裡 —— 這仍是預設值。

另外注意：**JSON 備份檔會包含你填的同步密鑰**。備份檔要當作密碼一樣保管，
不要傳給別人或放進公開的 repo。

---

## 測試

```
python line-bot/test_worker.py
```

不需要 Node，也不需要真的部署。它會用 headless Chrome 把 `worker.js` 實際跑起來，
KV、LINE API、Anthropic API 全部用替身，驗證權限、簽章、報表內容與每日推播（40 項檢查）。

網頁端上傳的內容格式由 `../test_browser.py` 一併驗證 —— 兩邊的欄位必須對得上。

## 用 wrangler 部署（進階）

不想用網頁後台的話：

```sh
npx wrangler kv namespace create CAL     # 把回傳的 id 填進 wrangler.toml
npx wrangler secret put LINE_CHANNEL_SECRET
npx wrangler secret put LINE_ACCESS_TOKEN
npx wrangler secret put SYNC_TOKEN
npx wrangler secret put ANTHROPIC_API_KEY
npx wrangler secret put LINE_OWNER_ID
npx wrangler deploy
```

## 排錯

| 症狀 | 大多是這個原因 |
|---|---|
| LINE 的 Verify 失敗 | 網址少了 `/webhook`，或 `LINE_CHANNEL_SECRET` 貼錯 |
| 網頁顯示「同步失敗（503）」 | Worker 沒設 `SYNC_TOKEN`（這是刻意的：沒設密鑰就停用，不是全開） |
| 網頁顯示「同步失敗（401）」 | 網頁和 Worker 的密鑰不一樣 |
| 網頁顯示「連不上 Worker」 | 網址打錯，或結尾多打了 `/sync` |
| Bot 說「還沒收到行事曆資料」 | 還沒按過「立即同步」，或 KV 綁定的變數名稱不是 `CAL` |
| Bot 回「這是私人用的排程助理」 | 你不是擁有者。清掉 `LINE_OWNER_ID` 或到 KV 裡刪掉 `owner` 這筆 |
| 每天早上沒收到推播 | Cron Trigger 沒設，或 `LINE_OWNER_ID` 還沒設定過也沒人跟 Bot 講過話 |
| 自由問答都回「只認得指令」 | `ANTHROPIC_API_KEY` 沒設 |

用 `https://你的網址.workers.dev/health` 可以快速看目前狀態：
同步是否啟用、有沒有收到過資料、資料多新、AI 有沒有開。
