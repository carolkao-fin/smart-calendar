# 排程曆

輸入「什麼時候要完成」和「需要多久」，自動排進未來的工作日。
`index.html` 是網頁本體，`line_bot.py` 是選用的 LINE 連動伺服器。

**線上版**

- GitHub Pages — <https://carolkao-fin.github.io/smart-calendar/>
- Streamlit Cloud — 見下方「部署」

---

## 一、只用網頁

用瀏覽器打開 `index.html` 就能用，資料存在瀏覽器本機（localStorage），不會上傳。

**排程規則**

每天從所有未完成的任務裡，挑「密度」最高的先做：

```
密度 = 剩餘時數 ÷ 到期前還剩幾個工作天
```

越接近截止、剩越多的，密度越高，就越早被排進來。排完一天的可用時數就換下一天，
遇到週末、休假日、非工作日直接跳過。

**可以調的設定**

| 設定 | 預設 | 作用 |
|---|---|---|
| 每日可投入時數 | 4h | 一天總共排多少工作 |
| 單一任務每日上限 | 2.5h | 避免一整天被同一件事吃掉 |
| 工作日 | 週一～週五 | 取消勾選就不排那天 |
| 提前完成緩衝 | 1 天 | 目標比截止日早幾天做完 |
| 排程策略 | 平均分攤 | 每天拆一小塊；或改「儘早做完」把最急的一次做完 |
| 請假日 | — | 指定日期完全不排 |

**狀態標示**

- `從容` 排得進，且在緩衝日前做完
- `緊繃` 排得進，但會用掉緩衝
- `會遲交` 依現在的容量會超過截止日
- `排不進` 工作日容量已被更急的事佔滿，這些時數無處可放

日曆上每格底部的細條是當天的負載，背景越深表示排得越滿，超過容量會轉紅。

**匯出**

「匯出」可下載 `.ics` 匯入 Google 日曆／Outlook／Apple 行事曆（每段工作從上午九點起排），
或下載 `.json` 備份全部任務與設定。

---

## 二、加上 LINE 連動（選用）

接上之後，在 LINE 打一句就能新增任務，網頁每 60 秒自動同步；每天早上推播當日工作。

### 1. 建立 LINE 官方帳號

1. 到 [LINE Developers](https://developers.line.biz/console/) 登入
2. 建立 Provider → 建立 **Messaging API** channel
3. 記下 **Channel secret**（Basic settings 分頁）
4. 發行並記下 **Channel access token**（Messaging API 分頁）
5. 同一頁把 **Auto-reply messages** 關掉，否則官方罐頭訊息會蓋掉 Bot 的回覆

### 2. 啟動伺服器

```powershell
pip install flask requests

$env:LINE_CHANNEL_SECRET = "剛剛記下的 channel secret"
$env:LINE_CHANNEL_TOKEN  = "剛剛記下的 access token"
$env:SYNC_TOKEN          = "自己取一組密碼，網頁端要填一樣的"
python line_bot.py
```

伺服器跑在 8000 埠。其他可調的環境變數：

| 變數 | 預設 | 說明 |
|---|---|---|
| `PORT` | 8000 | 監聽埠 |
| `PUSH_HOUR` | 8 | 每天幾點推播當日工作，設 `-1` 關閉 |
| `STORE_PATH` | store.json | 資料存放位置 |

### 3. 讓 LINE 找得到你的伺服器

LINE 的 webhook 只能連公開網址，本機測試用 [ngrok](https://ngrok.com/)：

```
ngrok http 8000
```

把它給的 `https://xxxx.ngrok-free.app` 貼回 LINE Developers 的
**Webhook URL**，後面接 `/callback`：

```
https://xxxx.ngrok-free.app/callback
```

按 **Verify** 確認通過，再把 **Use webhook** 打開。
最後用手機掃 QR code 加這個官方帳號為好友。

長期使用建議部署到 [Render](https://render.com/) 或 Railway 等平台（免費方案即可），
拿到固定網址就不必每次重開 ngrok。啟動指令 `python line_bot.py`，
環境變數照上表設定。

### 4. 接上網頁

網頁右上角「LINE 連動」→ 填入伺服器網址和剛才設定的 `SYNC_TOKEN` →
「測試連線」通過後勾選「每 60 秒自動同步」。

> 若網頁是透過 claude.ai 的分享連結開啟，瀏覽器安全政策會擋掉對外連線。
> 請把 `index.html` 存到本機或自己的網域再用連動功能。

### 5. 在 LINE 裡怎麼講

| 你說 | 結果 |
|---|---|
| `文獻回顧 8/20 6h` | 新增任務，回覆重排後的安排 |
| `期末報告 下週五 4小時` | 日期也吃「明天」「下週五」「+10」 |
| `今天` / `明天` / `本週` | 看安排；不是工作日會告訴你下次何時做 |
| `清單` | 所有任務、進度、預計完工日 |
| `完成 文獻回顧 2h` | 記錄做了 2 小時，剩下的自動重排 |
| `完成 文獻回顧` | 整件事標記完成 |
| `刪除 文獻回顧` | 移除任務 |
| `設定 每日 6h` | 改每天可投入時數 |
| `說明` | 指令一覽 |

名稱、日期、時數的順序隨意，Bot 會自己認出哪個是哪個。

---

## 三、部署

### GitHub Pages（推薦，行事曆的正式家）

這是純前端網頁，Pages 是最合適的載體：載入快、網址固定、localStorage 正常運作。
repo 設定裡 **Settings → Pages → Source 選 `Deploy from a branch`、分支 `main`、資料夾 `/ (root)`**，
存檔後約一分鐘就會出現在
<https://carolkao-fin.github.io/smart-calendar/>。
之後每次 push 到 `main` 會自動更新。

### Streamlit Cloud

`streamlit_app.py` 把 `index.html` 原樣嵌進 Streamlit 頁面。

1. 到 <https://share.streamlit.io/> 用 GitHub 帳號登入
2. **Create app** → **Deploy a public app from GitHub**
3. Repository 選 `carolkao-fin/smart-calendar`，Branch `main`，
   Main file path 填 `streamlit_app.py`
4. **Deploy**，等相依套件裝完即可

> Streamlit 是把網頁放進 iframe 呈現。若你的瀏覽器封鎖了第三方網站資料，
> iframe 內可能無法保存 —— 真的發生時頁面上方會出現「無法存檔」的橘色提示，
> 這時請改用 GitHub Pages 的網址，或用「匯出 → 下載 JSON」自行備份。

### LINE Bot 要另外找地方跑

Streamlit Cloud 和 GitHub Pages 都無法接收 LINE 的 webhook（前者不能自訂路由，
後者只能放靜態檔）。`line_bot.py` 請部署到 Render、Railway 之類可以跑
Flask 的平台，啟動指令 `python line_bot.py`，環境變數照上一節設定。

---

## 檔案

```
index.html        網頁本體，單檔、無外部相依
streamlit_app.py  Streamlit Cloud 入口，只是把 index.html 嵌進去
line_bot.py       LINE webhook + 同步 API + 每日推播
requirements.txt  Streamlit / Flask 相依套件
store.json        LINE Bot 的本機資料（執行後自動產生，已在 .gitignore）
```

`index.html` 的 `schedule()` 和 `line_bot.py` 的 `schedule()` 是同一套邏輯，
兩邊排出來的結果一致。改動排程規則時**兩邊都要改**。
