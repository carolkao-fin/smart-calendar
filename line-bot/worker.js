/**
 * 排程曆 LINE Bot — Cloudflare Worker
 *
 * 三件事：
 *   PUT  /sync     網頁把「已經算好的排程」上傳過來（存進 KV）
 *   POST /webhook  LINE 的訊息進來，回覆今日事項或回答問題
 *   cron           每天早上把今日事項推播給擁有者
 *
 * 這支程式**不重算排程**。v1～v5 的 LINE 版在 Python 端重寫了一份排程演算法，
 * 兩邊行為很容易走鐘（見開發紀錄挑戰 1）。這次網頁端算完直接把結果上傳，
 * 伺服器只負責轉述，不會有第二份演算法。
 *
 * 安全性沿用當年修過的兩個洞（挑戰 3、4）：
 *   - 只服務擁有者，其他人一律擋下
 *   - 沒設密鑰時同步 API 直接停用（fail-closed），不是放行
 */

const LINE_API = "https://api.line.me/v2/bot/message";
const ANTHROPIC_API = "https://api.anthropic.com/v1/messages";
const MODEL = "claude-opus-5";
const SNAPSHOT_KEY = "snapshot";
const OWNER_KEY = "owner";
const TZ = "Asia/Taipei";
const DOW = ["日", "一", "二", "三", "四", "五", "六"];

/* ================= 進入點 ================= */

export default {
  async fetch(request, env, ctx) {
    const path = new URL(request.url).pathname;
    if (path === "/webhook") return handleWebhook(request, env, ctx);
    if (path === "/sync") return handleSync(request, env);
    if (path === "/health") return handleHealth(env);
    return new Response("排程曆 LINE Bot", { status: 200 });
  },

  async scheduled(event, env, ctx) {
    ctx.waitUntil(pushDaily(env));
  },
};

/* ================= /sync：接收網頁上傳的排程 ================= */

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "PUT, POST, OPTIONS",
  "Access-Control-Allow-Headers": "content-type, x-sync-token",
  "Access-Control-Max-Age": "86400",
};

async function handleSync(request, env) {
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
  if (request.method !== "PUT" && request.method !== "POST") {
    return json({ error: "method_not_allowed" }, 405, CORS);
  }
  // 沒設密鑰就停用，而不是放行 —— 「忘了設定就全開」是危險的預設值
  if (!env.SYNC_TOKEN) {
    return json({ error: "sync_disabled", message: "伺服器還沒設定 SYNC_TOKEN，同步已停用。" }, 503, CORS);
  }
  if (!safeEqual(request.headers.get("x-sync-token") || "", env.SYNC_TOKEN)) {
    return json({ error: "unauthorized" }, 401, CORS);
  }

  let snap;
  try {
    snap = await request.json();
  } catch (e) {
    return json({ error: "bad_json" }, 400, CORS);
  }
  if (!snap || typeof snap !== "object" || !Array.isArray(snap.tasks)) {
    return json({ error: "bad_payload", message: "缺少 tasks 陣列。" }, 400, CORS);
  }

  snap.receivedAt = new Date().toISOString();
  await env.CAL.put(SNAPSHOT_KEY, JSON.stringify(snap));
  return json({ ok: true, tasks: snap.tasks.length, receivedAt: snap.receivedAt }, 200, CORS);
}

async function handleHealth(env) {
  const snap = await readSnapshot(env);
  return json({
    ok: true,
    syncEnabled: !!env.SYNC_TOKEN,
    hasSnapshot: !!snap,
    generatedAt: snap ? snap.generatedAt : null,
    tasks: snap ? snap.tasks.length : 0,
    ai: !!env.ANTHROPIC_API_KEY,
  });
}

/* ================= /webhook：LINE 訊息 ================= */

async function handleWebhook(request, env, ctx) {
  if (request.method !== "POST") return new Response("method not allowed", { status: 405 });
  if (!env.LINE_CHANNEL_SECRET || !env.LINE_ACCESS_TOKEN) {
    return new Response("not configured", { status: 503 });
  }

  const raw = await request.text();
  if (!(await verifyLine(raw, request.headers.get("x-line-signature") || "", env.LINE_CHANNEL_SECRET))) {
    return new Response("bad signature", { status: 401 });
  }

  let body;
  try {
    body = JSON.parse(raw);
  } catch (e) {
    return new Response("bad json", { status: 400 });
  }

  // LINE 只等 200，實際處理放到背景 —— 問 Claude 要幾秒，卡在這裡會被判逾時
  ctx.waitUntil(processEvents(body.events || [], env));
  return new Response("ok", { status: 200 });
}

// LINE 的簽章：channel secret 當金鑰，對原始 body 做 HMAC-SHA256 再 base64
async function verifyLine(raw, signature, secret) {
  if (!signature) return false;
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]
  );
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(raw));
  return safeEqual(btoa(String.fromCharCode(...new Uint8Array(mac))), signature);
}

async function processEvents(events, env) {
  for (const ev of events) {
    if (ev.type !== "message" || !ev.message || ev.message.type !== "text") continue;
    const userId = ev.source && ev.source.userId;
    if (!(await isOwner(userId, env))) {
      console.log("拒絕非擁有者：", userId);
      await reply(env, ev.replyToken, "這是私人用的排程助理，沒有開放使用。");
      continue;
    }
    let text;
    try {
      text = await answer(env, (ev.message.text || "").trim());
    } catch (err) {
      console.log("處理失敗：", err && err.stack ? err.stack : String(err));
      text = "出了點狀況，這次沒能回答你。稍後再試一次。";
    }
    await reply(env, ev.replyToken, text);
  }
}

// 擁有者由環境變數指定；沒指定時「先到先得」，第一個講話的人成為擁有者並記在日誌裡
async function isOwner(userId, env) {
  if (!userId) return false;
  if (env.LINE_OWNER_ID) return userId === env.LINE_OWNER_ID;
  const known = await env.CAL.get(OWNER_KEY);
  if (known) return userId === known;
  await env.CAL.put(OWNER_KEY, userId);
  console.log("已把這個 user ID 記為擁有者，請填進 LINE_OWNER_ID：", userId);
  return true;
}

async function reply(env, token, text) {
  if (!token) return;
  await lineCall(env, "reply", { replyToken: token, messages: [{ type: "text", text: clip(text) }] });
}

async function push(env, to, text) {
  await lineCall(env, "push", { to, messages: [{ type: "text", text: clip(text) }] });
}

async function lineCall(env, kind, payload) {
  const res = await fetch(`${LINE_API}/${kind}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${env.LINE_ACCESS_TOKEN}`,
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) console.log(`LINE ${kind} 失敗 ${res.status}：`, await res.text());
}

const clip = (s) => (s && s.length > 4900 ? s.slice(0, 4900) + "…" : s || "（沒有內容）");

/* ================= 回答 ================= */

async function answer(env, q) {
  const snap = await readSnapshot(env);
  if (!snap) {
    return "還沒收到行事曆資料。請打開排程曆網頁 → 設定 → LINE 連動，填好網址與密鑰後按「立即同步」。";
  }
  const t = today();

  // 固定指令走本地組字串：免費、秒回、不會亂答
  if (/^(今天|今日|today)$/i.test(q)) return dayReport(snap, t, "今天");
  if (/^(明天|明日|tomorrow)$/i.test(q)) return dayReport(snap, addDays(t, 1), "明天");
  if (/^(本週|這週|本周|這周|week)$/i.test(q)) return weekReport(snap, t);
  if (/^(風險|risk)$/i.test(q)) return riskReport(snap);
  if (/^(說明|help|\?|？)$/i.test(q)) return HELP;

  if (!env.ANTHROPIC_API_KEY) {
    return "我只認得「今天／明天／本週／風險」這幾個指令。\n（要問更自由的問題，需要在 Worker 設定 ANTHROPIC_API_KEY。）";
  }
  return askClaude(env, q, snap, t);
}

const HELP = [
  "可以這樣問我：",
  "・今天／明天／本週 — 那天排了什麼",
  "・風險 — 哪些事會遲交或排不進",
  "也可以直接用問的，例如：",
  "・文獻回顧還剩幾小時？",
  "・這禮拜最該擔心哪件事？",
  "・下次跟教授開會是什麼時候？",
].join("\n");

/* ---- 本地報表 ---- */

function dayReport(snap, key, label) {
  const blocks = (snap.days && snap.days[key]) || [];
  const lines = [`📅 ${pretty(key)} ${label}`];

  if (!blocks.length) {
    lines.push("", "沒有排定的工作。");
  } else {
    lines.push("");
    let total = 0;
    for (const b of blocks) {
      total += b.hours || 0;
      lines.push(b.at ? `${b.at} ${b.title}（${hrs(b.hours)}h）` : `・${b.title} ${hrs(b.hours)}h`);
    }
    const cap = snap.settings && snap.settings.dailyCapacity;
    lines.push("", `共 ${hrs(total)}h${cap ? ` ／ 可投入 ${hrs(cap)}h` : ""}${cap && total > cap + 0.01 ? " ⚠️ 超載" : ""}`);
  }

  const due = (snap.tasks || []).filter((x) => !x.done && x.deadline === key);
  if (due.length) lines.push("", `⏰ ${label}到期：` + due.map((x) => x.title).join("、"));

  lines.push("", freshness(snap));
  return lines.join("\n");
}

function weekReport(snap, t) {
  const mon = addDays(t, -((dow(t) + 6) % 7));
  const lines = ["📅 本週"];
  for (let i = 0; i < 7; i++) {
    const key = addDays(mon, i);
    const blocks = (snap.days && snap.days[key]) || [];
    const total = blocks.reduce((s, b) => s + (b.hours || 0), 0);
    const mark = key === t ? "◀ 今天" : "";
    lines.push(
      `${key.slice(5).replace("-", "/")}（週${DOW[dow(key)]}）${total ? ` ${hrs(total)}h` : " —"} ${mark}`.trimEnd()
    );
    for (const b of blocks) lines.push(`   ${b.at ? b.at + " " : ""}${b.title} ${hrs(b.hours)}h`);
  }
  lines.push("", freshness(snap));
  return lines.join("\n");
}

function riskReport(snap) {
  const bad = (snap.tasks || []).filter((x) => ["late", "unfit", "past"].includes(x.status));
  const tight = (snap.tasks || []).filter((x) => x.status === "tight");
  if (!bad.length && !tight.length) return "目前沒有會遲交或排不進的事，一切從容。\n\n" + freshness(snap);

  const lines = [];
  const NAME = { late: "會遲交", unfit: "排不進", past: "已過時間", tight: "緊繃" };
  for (const x of bad.concat(tight)) {
    lines.push(
      `${x.status === "tight" ? "⚠️" : "🔴"} ${x.title}（${NAME[x.status]}）` +
        (x.deadline ? `　截止 ${x.deadline.slice(5).replace("-", "/")}` : "") +
        (x.remaining > 0 ? `　剩 ${hrs(x.remaining)}h` : "")
    );
  }
  lines.push("", freshness(snap));
  return lines.join("\n");
}

/* ---- 交給 Claude ---- */

async function askClaude(env, question, snap, t) {
  const system = [
    "你是使用者「排程曆」的私人助理，透過 LINE 回話。",
    `今天是 ${t}（週${DOW[dow(t)]}）。`,
    "",
    "回答規則：",
    "1. 只依據下面的行事曆資料回答。資料裡沒有的，直接說不知道，不要猜也不要編。",
    "2. 用繁體中文，語氣自然簡短。這是 LINE 訊息，不要用 Markdown 標題或表格，最多用「・」列點。",
    "3. 時數用小時表示；日期用 M/D。",
    "4. 資料是網頁端上傳的快照，若使用者問的事情在快照之後才改，你不會知道 —— 必要時提醒他去網頁看。",
    "5. 不要重複整份清單，只回答他問的部分。",
    "",
    "行事曆資料（JSON）：",
    JSON.stringify(compact(snap)),
  ].join("\n");

  const res = await fetch(ANTHROPIC_API, {
    method: "POST",
    headers: {
      "x-api-key": env.ANTHROPIC_API_KEY,
      "anthropic-version": "2023-06-01",
      // 安全分類器偶爾會擋掉正常請求，掛上官方建議的備援模型
      "anthropic-beta": "server-side-fallback-2026-07-01",
      "content-type": "application/json",
    },
    body: JSON.stringify({
      model: MODEL,
      max_tokens: 2000,          // 含思考，留寬一點免得答到一半被截斷
      output_config: { effort: "low" },   // LINE 要秒回，這種查詢不需要深想
      fallbacks: "default",
      system,
      messages: [{ role: "user", content: question }],
    }),
  });

  if (!res.ok) {
    console.log("Claude API 失敗", res.status, await res.text());
    return "AI 回覆暫時不通，先用「今天／明天／本週／風險」這幾個指令看看。";
  }
  const data = await res.json();
  if (data.stop_reason === "refusal") return "這個問題我沒辦法回答，換個問法試試。";
  const text = (data.content || [])
    .filter((b) => b.type === "text")
    .map((b) => b.text)
    .join("")
    .trim();
  return text || "（沒有取得回覆，請再問一次。）";
}

// 丟給模型的資料要瘦身：只留未完成的事，行程只留未來兩週
function compact(snap) {
  const t = today();
  const end = addDays(t, 14);
  const days = {};
  for (const k of Object.keys(snap.days || {})) {
    if (k >= t && k <= end) days[k] = snap.days[k];
  }
  return {
    今天: t,
    設定: snap.settings,
    未完成任務: (snap.tasks || []).filter((x) => !x.done),
    已完成任務: (snap.tasks || []).filter((x) => x.done).map((x) => x.title),
    未來兩週行程: days,
    資料產生時間: snap.generatedAt,
  };
}

/* ================= 每日推播 ================= */

async function pushDaily(env) {
  const owner = env.LINE_OWNER_ID || (await env.CAL.get(OWNER_KEY));
  if (!owner) return console.log("還沒有擁有者，跳過推播。");

  const snap = await readSnapshot(env);
  if (!snap) {
    return push(env, owner, "早安。我這邊還沒有行事曆資料 —— 請打開排程曆網頁按一次「立即同步」。");
  }
  const t = today();
  let text = "早安 ☀️\n" + dayReport(snap, t, "今天");

  const risky = (snap.tasks || []).filter((x) => ["late", "unfit", "past"].includes(x.status));
  if (risky.length) text += "\n\n🔴 有 " + risky.length + " 件事有風險，打「風險」看細節。";

  await push(env, owner, text);
}

/* ================= 小工具 ================= */

async function readSnapshot(env) {
  const raw = await env.CAL.get(SNAPSHOT_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch (e) {
    console.log("快照解析失敗", e);
    return null;
  }
}

// 一律以台北時間算「今天」—— Worker 跑在哪個機房都不影響
const today = () => new Date().toLocaleDateString("sv-SE", { timeZone: TZ });

function addDays(key, n) {
  const d = new Date(key + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}
const dow = (key) => new Date(key + "T00:00:00Z").getUTCDay();
const pretty = (key) => `${Number(key.slice(5, 7))}/${Number(key.slice(8, 10))}（週${DOW[dow(key)]}）`;
const hrs = (v) => String(Math.round((v || 0) * 10) / 10).replace(/\.0$/, "");

function freshness(snap) {
  if (!snap.generatedAt) return "";
  const mins = Math.round((Date.now() - new Date(snap.generatedAt).getTime()) / 60000);
  if (mins < 60) return `（資料更新於 ${Math.max(1, mins)} 分鐘前）`;
  if (mins < 60 * 24) return `（資料更新於 ${Math.round(mins / 60)} 小時前）`;
  return `（資料是 ${Math.round(mins / 1440)} 天前的，記得去網頁按同步）`;
}

// 定值時間比較，避免用回應時間差猜密鑰
function safeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function json(obj, status = 200, extra = {}) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...extra },
  });
}
