# -*- coding: utf-8 -*-
"""在 headless Chrome 裡真的把 worker.js 跑起來，驗證它的行為。

    python line-bot/test_worker.py

開發機沒有 Node，但 Worker 用到的東西（fetch、crypto.subtle、Response、btoa）
瀏覽器全都有，所以把 `export default` 換成一個全域變數就能直接執行。
KV、LINE API、Anthropic API 全部用假的替身，測的是這支程式自己的邏輯：
權限、簽章、報表組字串、日期換算。
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'worker.js')

BROWSERS = [
    os.path.expandvars(r'%ProgramFiles%\Google\Chrome\Application\chrome.exe'),
    os.path.expandvars(r'%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe'),
    os.path.expandvars(r'%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe'),
    os.path.expandvars(r'%ProgramFiles%\Microsoft\Edge\Application\msedge.exe'),
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    'google-chrome', 'chromium', 'chromium-browser',
]

HARNESS = r"""
<meta charset="utf-8">
<script>
(async function(){
  var out = { errors: [], cases: {} };
  try{
    // ---- 替身：KV ----
    function fakeKV(){
      var m = new Map();
      return {
        get: function(k){ return Promise.resolve(m.has(k) ? m.get(k) : null); },
        put: function(k, v){ m.set(k, v); return Promise.resolve(); },
        _dump: m
      };
    }
    // ---- 替身：外部 HTTP（LINE / Anthropic）----
    var sent = [];
    var aiReply = 'AI 回答：文獻回顧還剩 8 小時。';
    window.fetch = function(url, opt){
      var body = {};
      try{ body = JSON.parse(opt.body); }catch(e){}
      sent.push({ url: String(url), body: body });
      if(String(url).indexOf('api.anthropic.com') >= 0){
        return Promise.resolve(new Response(JSON.stringify({
          stop_reason: 'end_turn',
          content: [{ type: 'text', text: aiReply }]
        }), { status: 200 }));
      }
      return Promise.resolve(new Response('{}', { status: 200 }));
    };

    var TODAY = new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Taipei' });
    function addDays(key, n){
      var d = new Date(key + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() + n);
      return d.toISOString().slice(0, 10);
    }
    var SNAP = {
      v: 1,
      generatedAt: new Date().toISOString(),
      settings: { dailyCapacity: 4, workdays: [1,2,3,4,5] },
      days: (function(){
        var o = {};
        o[TODAY] = [
          { title: '與指導教授 meeting', hours: 1, at: '14:00' },
          { title: '文獻回顧初稿', hours: 2.5, at: '' }
        ];
        o[addDays(TODAY, 1)] = [{ title: '問卷資料清理', hours: 3, at: '' }];
        return o;
      })(),
      tasks: [
        { title: '文獻回顧初稿', deadline: addDays(TODAY, 9), hours: 10, doneHours: 2,
          remaining: 8, done: false, status: 'ok', repeat: '' },
        { title: '問卷資料清理', deadline: addDays(TODAY, 1), hours: 5, doneHours: 0,
          remaining: 5, done: false, status: 'late', repeat: '' },
        { title: '訂研討會的會議室', deadline: TODAY, hours: 0, doneHours: 0,
          remaining: 0, done: false, status: 'list', repeat: '' }
      ],
      recentLog: []
    };

    function mkEnv(extra){
      var env = { CAL: fakeKV(), LINE_CHANNEL_SECRET: 'sec', LINE_ACCESS_TOKEN: 'tok' };
      for(var k in (extra || {})) env[k] = extra[k];
      return env;
    }
    // Worker 把慢工作丟給 waitUntil，測試要等它做完才讀結果，否則會讀到上一輪的訊息
    var pending = [];
    var ctx = { waitUntil: function(p){ pending.push(p); return p; } };
    async function settle(){ while(pending.length) await Promise.all(pending.splice(0)); }

    async function sign(raw, secret){
      var key = await crypto.subtle.importKey('raw', new TextEncoder().encode(secret),
        { name:'HMAC', hash:'SHA-256' }, false, ['sign']);
      var mac = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(raw));
      return btoa(String.fromCharCode.apply(null, new Uint8Array(mac)));
    }
    function msgEvent(text, userId){
      return JSON.stringify({ events: [{
        type:'message', replyToken:'rt-1', source:{ userId: userId || 'U-owner' },
        message:{ type:'text', text: text }
      }]});
    }
    async function talk(env, text, userId){
      sent.length = 0;
      var raw = msgEvent(text, userId);
      var res = await WORKER.fetch(new Request('https://w/webhook', {
        method:'POST', body: raw, headers:{ 'x-line-signature': await sign(raw, env.LINE_CHANNEL_SECRET) }
      }), env, ctx);
      await settle();
      var line = sent.filter(function(s){ return s.url.indexOf('api.line.me') >= 0; })[0];
      return { status: res.status, text: line ? line.body.messages[0].text : null, sent: sent.slice() };
    }
    function syncReq(token, payload){
      var h = { 'content-type':'application/json' };
      if(token !== null) h['x-sync-token'] = token;
      return new Request('https://w/sync', { method:'PUT', headers:h, body: JSON.stringify(payload) });
    }

    // ================= 1. /sync 權限 =================
    var envNoToken = mkEnv();
    out.cases.syncNoToken = (await WORKER.fetch(syncReq('anything', SNAP), envNoToken, ctx)).status;

    var env = mkEnv({ SYNC_TOKEN: 's3cret', LINE_OWNER_ID: 'U-owner' });
    out.cases.syncWrongToken = (await WORKER.fetch(syncReq('nope', SNAP), env, ctx)).status;
    out.cases.syncNoHeader = (await WORKER.fetch(syncReq(null, SNAP), env, ctx)).status;
    out.cases.syncBadPayload = (await WORKER.fetch(syncReq('s3cret', { nope: 1 }), env, ctx)).status;
    var okRes = await WORKER.fetch(syncReq('s3cret', SNAP), env, ctx);
    out.cases.syncOk = okRes.status;
    out.cases.syncOkBody = await okRes.json();
    out.cases.syncCors = (await WORKER.fetch(new Request('https://w/sync', { method:'OPTIONS' }), env, ctx))
      .headers.get('Access-Control-Allow-Origin');
    out.cases.stored = !!(await env.CAL.get('snapshot'));

    // ================= 2. webhook 簽章與權限 =================
    var raw = msgEvent('今天');
    out.cases.badSignature = (await WORKER.fetch(new Request('https://w/webhook', {
      method:'POST', body: raw, headers:{ 'x-line-signature': 'bogus' }
    }), env, ctx)).status;
    out.cases.noSignature = (await WORKER.fetch(new Request('https://w/webhook', {
      method:'POST', body: raw
    }), env, ctx)).status;
    out.cases.stranger = (await talk(env, '今天', 'U-stranger')).text;

    // ================= 3. 回答 =================
    out.cases.today = (await talk(env, '今天')).text;
    out.cases.tomorrow = (await talk(env, '明天')).text;
    out.cases.week = (await talk(env, '本週')).text;
    out.cases.risk = (await talk(env, '風險')).text;
    out.cases.help = (await talk(env, '說明')).text;

    // 沒有 API key 時，自由問答要講清楚，而不是靜靜失敗
    out.cases.freeformNoKey = (await talk(env, '文獻回顧還剩多久？')).text;

    var envAI = mkEnv({ SYNC_TOKEN: 's3cret', LINE_OWNER_ID: 'U-owner', ANTHROPIC_API_KEY: 'sk-test' });
    await WORKER.fetch(syncReq('s3cret', SNAP), envAI, ctx);
    var ai = await talk(envAI, '文獻回顧還剩多久？');
    out.cases.freeformAI = ai.text;
    var call = ai.sent.filter(function(s){ return s.url.indexOf('anthropic') >= 0; })[0];
    out.cases.aiRequest = call ? {
      model: call.body.model,
      effort: call.body.output_config && call.body.output_config.effort,
      fallbacks: call.body.fallbacks,
      hasSystem: typeof call.body.system === 'string' && call.body.system.length > 50,
      systemHasData: (call.body.system || '').indexOf('文獻回顧初稿') >= 0,
      systemHasToday: (call.body.system || '').indexOf(TODAY) >= 0,
      question: call.body.messages[0].content
    } : null;

    // 安全分類器擋下時要好好講，不是丟出空白
    aiReply = '';
    window.fetch = (function(orig){
      return function(url, opt){
        if(String(url).indexOf('anthropic') >= 0){
          sent.push({ url:String(url), body: JSON.parse(opt.body) });
          return Promise.resolve(new Response(JSON.stringify({ stop_reason:'refusal', content:[] }), { status:200 }));
        }
        return orig(url, opt);
      };
    })(window.fetch);
    out.cases.refusal = (await talk(envAI, '幫我做壞事')).text;

    // ================= 4. 還沒同步過 =================
    var envEmpty = mkEnv({ LINE_OWNER_ID: 'U-owner' });
    out.cases.noSnapshot = (await talk(envEmpty, '今天')).text;

    // ================= 5. 每日推播 =================
    sent.length = 0;
    await WORKER.scheduled({}, env, ctx); await settle();
    var pushCall = sent.filter(function(s){ return s.url.indexOf('/push') >= 0; })[0];
    out.cases.push = pushCall ? { to: pushCall.body.to, text: pushCall.body.messages[0].text } : null;

    sent.length = 0;
    await WORKER.scheduled({}, envEmpty, ctx); await settle();
    var p2 = sent.filter(function(s){ return s.url.indexOf('/push') >= 0; })[0];
    out.cases.pushNoSnapshot = p2 ? p2.body.messages[0].text : null;

    // ================= 6. 先到先得的擁有者 =================
    var envOpen = mkEnv({ SYNC_TOKEN: 's3cret' });
    await WORKER.fetch(syncReq('s3cret', SNAP), envOpen, ctx);
    out.cases.firstComer = (await talk(envOpen, '今天', 'U-first')).text;
    out.cases.firstComerStored = await envOpen.CAL.get('owner');
    out.cases.secondComer = (await talk(envOpen, '今天', 'U-second')).text;

    out.today = TODAY;
    out.tomorrow = addDays(TODAY, 1);
  }catch(e){ out.errors.push(String(e) + ' @ ' + (e.stack || '').split('\n')[1]); }
  document.title = 'PROBE' + JSON.stringify(out) + 'ENDPROBE';
})();
</script>
"""

FAILS = []


def check(label, cond, detail=''):
    print(('  [ok]   ' if cond else '  [FAIL] ') + label + (('  -> ' + str(detail)) if detail else ''))
    if not cond:
        FAILS.append(label)


def find_browser():
    for b in BROWSERS:
        if os.path.isfile(b):
            return b
        found = shutil.which(b)
        if found:
            return found
    return None


def main():
    browser = find_browser()
    if not browser:
        print('找不到 Chrome 或 Edge，無法執行測試。')
        return 2
    print('瀏覽器：%s\n' % browser)

    src = io.open(SRC, encoding='utf-8').read()
    # Worker 的模組匯出換成全域變數，就能當一般 script 跑
    runnable = src.replace('export default {', 'var WORKER = {', 1)
    if 'var WORKER' not in runnable:
        print('!! worker.js 的 export default 格式變了，測試的替換規則要跟著改')
        return 1

    tmp = tempfile.mkdtemp()
    page = os.path.join(tmp, 'probe.html')
    io.open(page, 'w', encoding='utf-8', newline='').write(
        '<meta charset="utf-8">\n<script>\n' + runnable + '\n</script>\n' + HARNESS)

    res = subprocess.run(
        [browser, '--headless=new', '--disable-gpu', '--no-sandbox', '--dump-dom',
         '--virtual-time-budget=8000', '--user-data-dir=' + os.path.join(tmp, 'profile'),
         'file:///' + page.replace('\\', '/')],
        capture_output=True, timeout=180)
    dom = res.stdout.decode('utf-8', 'replace')

    m = re.search(r'PROBE(\{.*?\})ENDPROBE', dom, re.S)
    if not m:
        print('!! worker.js 沒有跑起來')
        print(res.stderr.decode('utf-8', 'replace')[:2000])
        return 1
    d = json.loads(m.group(1))
    c = d['cases']

    print('=== 載入 ===')
    check('worker.js 執行無例外', not d['errors'], d['errors'])

    print('\n=== /sync 權限（挑戰 4 的教訓：沒設密鑰要 fail-closed）===')
    check('沒設 SYNC_TOKEN 時同步停用（503，不是放行）', c['syncNoToken'] == 503, c['syncNoToken'])
    check('密鑰錯誤擋下（401）', c['syncWrongToken'] == 401, c['syncWrongToken'])
    check('沒帶密鑰擋下（401）', c['syncNoHeader'] == 401, c['syncNoHeader'])
    check('格式不對的內容擋下（400）', c['syncBadPayload'] == 400, c['syncBadPayload'])
    check('密鑰正確才收下（200）', c['syncOk'] == 200, c['syncOk'])
    check('回報收到幾筆任務', c['syncOkBody'].get('tasks') == 3, c['syncOkBody'])
    check('資料真的存進 KV', c['stored'])
    check('跨網域預檢有回 CORS 標頭', c['syncCors'] == '*', c['syncCors'])

    print('\n=== webhook 簽章與權限（挑戰 3 的教訓）===')
    check('簽章錯誤擋下（401）', c['badSignature'] == 401, c['badSignature'])
    check('沒有簽章擋下（401）', c['noSignature'] == 401, c['noSignature'])
    check('非擁有者拿不到任何行程資料',
          c['stranger'] and '沒有開放' in c['stranger'] and '文獻' not in c['stranger'], c['stranger'])

    print('\n=== 指令回覆 ===')
    print('  「今天」→\n    ' + (c['today'] or '').replace('\n', '\n    '))
    check('今天的回覆有今天的日期', c['today'] and d['today'][5:].replace('-', '/').lstrip('0') in c['today']
          or (c['today'] and '/' in c['today']), c['today'])
    check('今天的回覆列出固定時段與時間', c['today'] and '14:00' in c['today'], c['today'])
    check('今天的回覆列出彈性工作與時數', c['today'] and '文獻回顧初稿' in c['today'] and '2.5h' in c['today'])
    check('今天的回覆有當日合計與可投入時數', c['today'] and '共 3.5h' in c['today'] and '可投入 4h' in c['today'])
    check('今天到期的事有標出來', c['today'] and '今天到期：訂研討會的會議室' in c['today'], c['today'])
    check('明天的報表講「明天到期」而不是「今天到期」',
          c['tomorrow'] and '明天到期' in c['tomorrow'] and '今天到期' not in c['tomorrow'], c['tomorrow'])
    check('有標示資料新鮮度', c['today'] and '資料更新於' in c['today'])
    check('明天的回覆是明天的事', c['tomorrow'] and '問卷資料清理' in c['tomorrow']
          and '文獻回顧初稿' not in c['tomorrow'], c['tomorrow'])
    check('本週的回覆有七天', c['week'] and c['week'].count('（週') == 7, c['week'])
    print('  「風險」→\n    ' + (c['risk'] or '').replace('\n', '\n    '))
    check('風險只列有風險的事', c['risk'] and '問卷資料清理' in c['risk'] and '文獻回顧初稿' not in c['risk'],
          c['risk'])
    check('風險標出原因', c['risk'] and '會遲交' in c['risk'], c['risk'])
    check('說明列出可用指令', c['help'] and '今天' in c['help'] and '風險' in c['help'])

    print('\n=== 自由問答 ===')
    check('沒有 API key 時說清楚只認得指令',
          c['freeformNoKey'] and 'ANTHROPIC_API_KEY' in c['freeformNoKey'], c['freeformNoKey'])
    check('有 API key 時回傳 AI 的答案',
          c['freeformAI'] and 'AI 回答' in c['freeformAI'], c['freeformAI'])
    a = c['aiRequest']
    print('  送給 Claude 的請求：', a)
    check('用 claude-opus-5', a and a['model'] == 'claude-opus-5', a)
    check('用低 effort（LINE 要秒回）', a and a['effort'] == 'low', a)
    check('有開啟安全備援（fallbacks）', a and a['fallbacks'] == 'default', a)
    check('把行事曆資料放進 system', a and a['systemHasData'] and a['systemHasToday'], a)
    check('使用者的問題原樣送出', a and a['question'] == '文獻回顧還剩多久？', a)
    check('被安全分類器擋下時好好回話',
          c['refusal'] and '沒辦法回答' in c['refusal'], c['refusal'])

    print('\n=== 還沒同步過 ===')
    check('沒有資料時教使用者去按同步',
          c['noSnapshot'] and '立即同步' in c['noSnapshot'], c['noSnapshot'])

    print('\n=== 每日推播 ===')
    print('  推播內容 →\n    ' + ((c['push'] or {}).get('text') or '').replace('\n', '\n    '))
    check('推播給擁有者', c['push'] and c['push']['to'] == 'U-owner', c['push'])
    check('推播內容是今日事項', c['push'] and '文獻回顧初稿' in c['push']['text'])
    check('推播提醒有風險的事', c['push'] and '風險' in c['push']['text'], c['push'])
    check('沒有資料時推播也會說明', c['pushNoSnapshot'] and '立即同步' in c['pushNoSnapshot'],
          c['pushNoSnapshot'])

    print('\n=== 未指定擁有者時「先到先得」===')
    check('第一個講話的人成為擁有者', c['firstComer'] and '文獻回顧初稿' in c['firstComer'], c['firstComer'])
    check('擁有者記進 KV', c['firstComerStored'] == 'U-first', c['firstComerStored'])
    check('第二個人被擋下', c['secondComer'] and '沒有開放' in c['secondComer'], c['secondComer'])

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILS:
        print('=== %d 項失敗 ===' % len(FAILS))
        for f in FAILS:
            print('  -', f)
        return 1
    print('=== 全部通過 ===')
    return 0


if __name__ == '__main__':
    sys.exit(main())
