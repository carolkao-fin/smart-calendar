# -*- coding: utf-8 -*-
"""用 headless Chrome 真的把 index.html 跑起來，再從渲染後的 DOM 驗證行為。

    python test_browser.py

不需要安裝任何套件，只要系統裡有 Chrome 或 Edge。
驗證的是「瀏覽器實際算出來、實際畫出來」的結果，不是重寫一份邏輯來對答案。
測試依賴 index.html 內建的範例資料（seed），改動 seed 時這裡也要跟著改。
"""
import datetime
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
SRC = os.path.join(HERE, 'index.html')

BROWSERS = [
    os.path.expandvars(r'%ProgramFiles%\Google\Chrome\Application\chrome.exe'),
    os.path.expandvars(r'%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe'),
    os.path.expandvars(r'%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe'),
    os.path.expandvars(r'%ProgramFiles%\Microsoft\Edge\Application\msedge.exe'),
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    'google-chrome', 'chromium', 'chromium-browser',
]


def find_browser():
    for b in BROWSERS:
        if os.path.isfile(b):
            return b
        found = shutil.which(b)
        if found:
            return found
    return None


# 注入頁面的探針：把渲染後的 DOM 摘要塞進 document.title 帶回來。
# 除了讀初始畫面，也真的去點勾選框與恢復鍵，驗證互動後的結果。
PROBE = r"""
<script>
(function(){
  var out = {errors: []};
  var $  = function(s){ return document.querySelector(s); };
  var $$ = function(s){ return [].slice.call(document.querySelectorAll(s)); };

  function readCells(){
    return $$('#grid .cell').map(function(c,i){
      var tot = c.querySelector('.dnum .tot');
      return {
        date: c.dataset.d || null,
        col: i % 7,                                   // 月檢視 7 欄，第 0 欄是週日
        total: tot ? parseFloat(tot.textContent) : 0,
        past: c.classList.contains('past'),
        overdue: [].slice.call(c.querySelectorAll('.due.late span:last-child'))
                   .map(function(s){ return s.textContent; }),
        // 過去那幾天實際做掉的事（來自完成紀錄，不是排程）
        dones: [].slice.call(c.querySelectorAll('.dblk')).map(function(b){
          var h = b.querySelector('.bh');
          return {
            name: b.querySelector('.bn').textContent,
            mark: b.querySelector('.dk').textContent,
            hours: h ? parseFloat(h.textContent) : 0
          };
        }),
        repeats: [].slice.call(c.querySelectorAll('.rep .rn'))
                   .map(function(r){ return r.textContent; }),
        blocks: [].slice.call(c.querySelectorAll('.blk')).map(function(b){
          return {
            name: b.querySelector('.bn').textContent,
            hours: parseFloat(b.querySelector('.bh').textContent),
            fixed: b.classList.contains('fixed'),
            miss: b.classList.contains('miss'),
            at: b.querySelector('.bt') ? b.querySelector('.bt').textContent : null
          };
        })
      };
    });
  }
  function dayKey(off){
    var d = new Date(); d.setDate(d.getDate() + off);
    return d.getFullYear() + '-' + ('0'+(d.getMonth()+1)).slice(-2) + '-' + ('0'+d.getDate()).slice(-2);
  }
  function addTask(title, dueOffset, hours){
    $('#fTitle').value = title;
    $('#fDue').value = dayKey(dueOffset);
    $('#fHours').value = String(hours);
    $('#fStart').value = dayKey(dueOffset - 2);
    $('#addForm').dispatchEvent(new Event('submit', { cancelable:true, bubbles:true }));
  }
  function lateRowOf(title){
    return $$('#lateList .logrow').filter(function(r){
      return r.querySelector('.nm').textContent === title; })[0];
  }
  function readTasks(){
    return $$('#taskList .task').map(function(t){
      return {
        title: t.querySelector('.title').textContent,
        chip: t.querySelector('.chip') ? t.querySelector('.chip').textContent : null,
        hasBar: !!t.querySelector('.pbar'),
        hasCheckbox: !!t.querySelector('.chk'),
        checked: t.querySelector('.chk') ? t.querySelector('.chk').checked : null,
        hasUndo: [].slice.call(t.querySelectorAll('.iconbtn'))
                   .some(function(b){ return b.textContent === '恢復'; }),
        meta: t.querySelector('.meta') ? t.querySelector('.meta').textContent : ''
      };
    });
  }
  function readGroups(){
    return $$('#taskList .grp').map(function(g){ return g.firstChild.textContent; });
  }
  function readLog(){
    return {
      logVisible: !$('#logWrap').hidden && $('#calWrap').hidden,
      sum: $$('#logSum div').map(function(d){
        return d.querySelector('span').textContent + '=' + d.querySelector('b').textContent; }),
      lateVisible: !$('#lateWrap').hidden,
      late: $$('#lateList .logrow').map(function(r){
        var h = r.querySelector('.hr');
        return {
          name: r.querySelector('.nm').textContent,
          chip: r.querySelector('.chip').textContent,
          hours: h ? parseFloat(h.textContent) : 0,
          btns: [].slice.call(r.querySelectorAll('.btn')).map(function(b){ return b.textContent; })
        };
      }),
      days: $$('#logList .logday').map(function(d){ return d.textContent; }),
      rows: $$('#logList .logrow').map(function(r){
        var h = r.querySelector('.hr');
        return {
          name: r.querySelector('.nm').textContent,
          kind: r.querySelector('.chip').textContent,
          hours: h ? parseFloat(h.textContent) : 0,
          btn: r.querySelector('.undo').textContent
        };
      })
    };
  }
  function taskRow(title){
    return $$('#taskList .task').filter(function(t){
      return t.querySelector('.title').textContent === title; })[0];
  }
  function click(node){ node.click(); }
  function toggle(title){ taskRow(title).querySelector('.chk').click(); }
  function undoFirstLogRow(){
    click($('#viewLog'));
    var btn = $('#logList .logrow .undo');
    if(btn) btn.click();
  }
  // 打卡可能連帶把任務做完（兩筆紀錄），全部退掉才回得到原狀
  function undoAllLogRows(){
    click($('#viewLog'));
    for(var i = 0; i < 6; i++){
      var btn = $('#logList .logrow .undo');
      if(!btn) break;
      btn.click();
    }
  }

  try{
    out.cells   = readCells();
    out.groups  = readGroups();
    out.tasks   = readTasks();
    out.repeatMarks = $$('#grid .cell .rep .rn').map(function(r){ return r.textContent; });
    out.readout = $$('#readout div').map(function(d){
      return d.querySelector('span').textContent + '=' + d.querySelector('b').textContent; });
    out.hasUnitSelect   = !!$('#fUnit');
    out.hasTimeInput    = !!$('#fAt');
    out.hasRepeatSelect = !!$('#fRepeat');
    $('#btnSettings').click();                  // 設定值是開啟對話框時才填進去的
    out.hasSliceSetting = !!$('#sSlice');
    out.sliceValue      = $('#sSlice') ? $('#sSlice').value : null;
    out.sliceHint       = $('#sSliceHint') ? $('#sSliceHint').textContent : '';
    $('#sClose').click();
    out.repeatOptions   = $$('#fRepeat option').map(function(o){ return o.value; });
    out.dowOptions      = document.querySelectorAll('#fDows label').length;

    // --- 打卡：今日面板上的「完成 Xh」（今天不是工作日時可能沒有可打卡的項目）---
    var slotBtn = $('#todayList .slot button');
    out.hadSlot = !!slotBtn;
    if(slotBtn){
      out.slotLabel = slotBtn.textContent;
      slotBtn.click();
      // 打卡後，今日面板本身要留下可以就地恢復的那一列
      out.todayAfter = $$('#todayList .slot').map(function(s){
        return { name:s.querySelector('.nm').textContent, did:s.classList.contains('did'),
                 btn:s.querySelector('button') ? s.querySelector('button').textContent : null };
      });
      out.todayHead = $$('#todayList .grp').map(function(g){ return g.textContent; });
      var undoHere = $$('#todayList .slot.did button')[0];
      if(undoHere) undoHere.click();
      out.todayRestored = $$('#todayList .slot').map(function(s){
        return { name:s.querySelector('.nm').textContent, did:s.classList.contains('did'),
                 btn:s.querySelector('button') ? s.querySelector('button').textContent : null };
      });
      slotBtn = $('#todayList .slot button');   // 恢復後重新打卡，繼續驗證紀錄頁
      if(slotBtn) slotBtn.click();
      click($('#viewLog'));
      out.afterCheckin = readLog();
      out.afterCheckinTasks = readTasks();
      undoAllLogRows();                         // 打卡也要能恢復
      out.afterCheckinUndo = readLog();
      out.afterCheckinUndoTasks = readTasks();
      click($('#viewMonth'));
    }

    // --- 一般任務：勾完成 → 進「已完成」→ 有紀錄 → 恢復 ---
    toggle('問卷資料清理');
    out.afterDone = { groups: readGroups(), tasks: readTasks() };
    click($('#viewLog'));
    out.afterDoneLog = readLog();
    undoFirstLogRow();
    out.afterUndo = { groups: readGroups(), tasks: readTasks(), log: readLog() };
    click($('#viewMonth'));

    // --- 重複任務：勾完成 → 不結案，截止日往後推一次 → 恢復回原本日期 ---
    var rep = taskRow('每週進度回報');
    out.repeatMetaBefore = rep ? rep.querySelector('.meta').textContent : '';
    toggle('每週進度回報');
    var rep2 = taskRow('每週進度回報');
    out.repeatStillOpen = !!rep2 && !rep2.classList.contains('done');
    out.repeatMetaAfter = rep2 ? rep2.querySelector('.meta').textContent : '';
    click($('#viewLog'));
    out.repeatLog = readLog();
    undoFirstLogRow();
    var rep3 = taskRow('每週進度回報');
    out.repeatMetaRestored = rep3 ? rep3.querySelector('.meta').textContent : '';
    out.repeatLogAfterUndo = readLog();
    // --- LINE 連動：攔截 fetch，驗證真正上傳出去的內容（不需要真的 Worker）---
    var captured = null;
    window.fetch = function(url, opt){
      try{
        captured = {
          url: String(url), method: opt.method,
          token: opt.headers['x-sync-token'],
          body: JSON.parse(opt.body)
        };
      }catch(err){ captured = { parseError: String(err) }; }
      return Promise.resolve(new Response('{"ok":true}', { status:200 }));
    };
    click($('#btnSettings'));
    out.hasSyncFields = !!($('#sSyncUrl') && $('#sSyncToken') && $('#sSyncNow'));
    $('#sSyncUrl').value = 'https://example.workers.dev/';
    $('#sSyncToken').value = 'test-token';
    click($('#sSyncNow'));
    click($('#sClose'));
    if(captured && captured.body){
      var b = captured.body;
      out.sync = {
        url: captured.url, method: captured.method, token: captured.token,
        keys: Object.keys(b).sort(),
        dayCount: Object.keys(b.days || {}).length,
        firstDay: Object.keys(b.days || {}).sort()[0] || null,
        firstBlock: (b.days && b.days[Object.keys(b.days).sort()[0]] || [])[0] || null,
        taskCount: (b.tasks || []).length,
        taskKeys: b.tasks && b.tasks[0] ? Object.keys(b.tasks[0]).sort() : [],
        hasStatus: !!(b.tasks || []).every(function(t){ return 'status' in t; }),
        settingsKeys: Object.keys(b.settings || {}).sort()
      };
    }else{
      out.sync = captured;
    }

    // --- 未完成清單與行事曆上的過去 ---
    // seed 全是未來的事，所以自己補一件已經過期的進去
    window.confirm = function(){ return true; };
    out.lateDue = dayKey(-3);
    addTask('上週的報告', -3, 2);
    click($('#viewMonth'));
    out.lateBadge = $('#lateBadge').hidden ? null : $('#lateBadge').textContent;
    out.cellsLate = readCells();
    click($('#viewLog'));
    out.lateView = readLog();

    // 補完成：紀錄要落在原本的截止日那天，行事曆上那一格就看得到
    lateRowOf('上週的報告').querySelector('.btn').click();
    out.afterFill = readLog();
    out.badgeAfterFill = $('#lateBadge').hidden;
    click($('#viewMonth'));
    out.cellsAfterFill = readCells();

    // 補錯了也要能收回：那筆紀錄一恢復，事情就回到未完成清單
    click($('#viewLog'));
    var back = $$('#logList .logrow').filter(function(r){
      return r.querySelector('.nm').textContent === '上週的報告'; })[0];
    if(back) back.querySelector('.undo').click();
    out.afterFillUndo = readLog();

    // 刪除：從未完成清單直接把它清掉
    var delRow = lateRowOf('上週的報告');
    var btns = delRow ? [].slice.call(delRow.querySelectorAll('.btn')) : [];
    btns.filter(function(b){ return b.textContent === '刪除'; })[0].click();
    out.afterDelete = readLog();
    out.tasksAfterDelete = readTasks().map(function(t){ return t.title; });
  }catch(e){ out.errors.push(String(e) + ' @ ' + (e.stack || '').split('\n')[1]); }
  document.title = 'PROBE' + JSON.stringify(out) + 'ENDPROBE';
})();
</script>
"""

FAILS = []


def md(meta):
    """從 meta 文字裡抓出第一個 MM/DD（就是截止日），回傳 date 以便相減。"""
    m = re.search(r'(\d{2})/(\d{2})', meta or '')
    if not m:
        return None
    y = datetime.date.today().year
    return datetime.date(y, int(m.group(1)), int(m.group(2)))


def check(label, cond, detail=''):
    print(('  [ok]   ' if cond else '  [FAIL] ') + label + (('  -> ' + str(detail)) if detail else ''))
    if not cond:
        FAILS.append(label)


def main():
    browser = find_browser()
    if not browser:
        print('找不到 Chrome 或 Edge，無法執行瀏覽器測試。')
        return 2
    print('瀏覽器：%s\n' % browser)

    tmp = tempfile.mkdtemp()
    page = os.path.join(tmp, 'probe.html')
    io.open(page, 'w', encoding='utf-8', newline='').write(
        io.open(SRC, encoding='utf-8').read() + PROBE)

    res = subprocess.run(
        [browser, '--headless=new', '--disable-gpu', '--no-sandbox', '--dump-dom',
         '--virtual-time-budget=6000', '--user-data-dir=' + os.path.join(tmp, 'profile'),
         'file:///' + page.replace('\\', '/')],
        capture_output=True, timeout=180)
    dom = res.stdout.decode('utf-8', 'replace')

    m = re.search(r'PROBE(\{.*?\})ENDPROBE', dom, re.S)
    if not m:
        print('!! 頁面沒有跑起來')
        print(res.stderr.decode('utf-8', 'replace')[:1500])
        return 1
    d = json.loads(m.group(1))

    print('=== 頁面載入 ===')
    check('JS 執行無例外', not d['errors'], d['errors'])
    check('行事曆有渲染出格子', len(d['cells']) >= 28, len(d['cells']))
    check('任務清單有渲染 6 筆範例', len(d['tasks']) == 6, [t['title'] for t in d['tasks']])
    check('單位選單存在', d['hasUnitSelect'])
    check('特定時間欄位存在', d['hasTimeInput'])
    check('指定星期有 7 個選項', d['dowOptions'] == 7, d['dowOptions'])
    check('重複頻率選單存在', d['hasRepeatSelect'])
    check('重複頻率有六種可選', d['repeatOptions'] ==
          ['', 'daily', 'weekday', 'weekly', 'biweekly', 'monthly', 'yearly'], d['repeatOptions'])

    print('\n=== 排程結果（瀏覽器實際算出）===')
    for c in d['cells']:
        if c['blocks']:
            print('    週%d  %sh  %s' % (c['col'], c['total'], ' | '.join(
                '%s%s %sh' % ((b['at'] + ' ') if b['at'] else '', b['name'], b['hours'])
                for b in c['blocks'])))

    over = [c for c in d['cells'] if c['total'] > 4.0001]
    check('沒有任何一天超過每日容量 4h', not over, over)

    WEEKEND = '整理田野筆記'          # seed 裡指定只在六日做的那件事
    leak = [(c['col'], b['name']) for c in d['cells'] if c['col'] in (0, 6)
            for b in c['blocks'] if b['name'] != WEEKEND]
    check('週末只排到指定六日的那件事', not leak, leak)
    check('指定六日的任務確實排進週末',
          any(b['name'] == WEEKEND for c in d['cells'] if c['col'] in (0, 6) for b in c['blocks']))
    check('指定六日的任務沒有外洩到平日',
          not [b for c in d['cells'] if c['col'] not in (0, 6)
               for b in c['blocks'] if b['name'] == WEEKEND])

    # 平均分攤：一天的容量分給多件事，但每件都要拿到做得動的一塊（預設至少 2h）
    SLICE = 2.0
    workdays = [c for c in d['cells'] if c['blocks'] and c['col'] not in (0, 6)]
    first = workdays[0] if workdays else None
    check('第一個工作日就排進 2 件事以上', first and len(first['blocks']) >= 2,
          [b['name'] for b in first['blocks']] if first else None)
    check('設定裡有「每件事每天至少排」且預設 2 小時',
          d['hasSliceSetting'] and d['sliceValue'] == '2', (d['hasSliceSetting'], d['sliceValue']))
    # 小於一塊的只能是收尾的零頭（任務剩不到一塊，或當天容量剩不到一塊），一天最多一個
    crumbs = [(c['col'], [b['name'] + ' ' + str(b['hours']) for b in c['blocks']
                          if not b['fixed'] and b['hours'] < SLICE])
              for c in d['cells'] if c['blocks']]
    over = [x for x in crumbs if len(x[1]) > 1]
    check('沒有把一天切成一堆碎塊（每天最多一個零頭）', not over, over)
    dup = [(c['col'], b['name']) for c in d['cells']
           for b in c['blocks']
           if [x['name'] for x in c['blocks']].count(b['name']) > 1]
    check('同一天同一件事只會出現一塊', not dup, dup)

    fx = [b for c in d['cells'] for b in c['blocks'] if b['fixed']]
    check('固定時段有渲染且帶時間', len(fx) == 1 and fx[0]['at'] == '14:00', fx)
    check('固定時段沒有被拆開', len(fx) == 1 and fx[0]['hours'] == 1, fx)
    check('純清單項目沒有出現在行事曆',
          '訂研討會的會議室' not in [b['name'] for c in d['cells'] for b in c['blocks']])

    print('\n=== 任務清單 ===')
    print('  分組：', d['groups'])
    check('有「今天」與「無期限・清單」分組',
          '今天' in d['groups'] and '無期限・清單' in d['groups'], d['groups'])
    check('未完成的分組順序正確',
          [g for g in d['groups'] if g != '已完成'] ==
          [g for g in ['今天', '接下來', '無期限・清單'] if g in d['groups']], d['groups'])
    for t in d['tasks']:
        print('    %-16s %-5s bar=%-5s chk=%s' % (t['title'], t['chip'], t['hasBar'], t['hasCheckbox']))
    check('每個任務都有勾選框', all(t['hasCheckbox'] for t in d['tasks']))

    by = {t['title']: t for t in d['tasks']}
    cl = by['訂研討會的會議室']
    check('純清單項目沒有進度條', not cl['hasBar'])
    check('純清單項目標為「清單」', cl['chip'] == '清單', cl['chip'])
    sd = by[WEEKEND]
    check('無期限任務標為「無期限」而非風險', sd['chip'] == '無期限', sd['chip'])
    check('無期限任務顯示只在六日', '只在' in sd['meta'] and '六日' in sd['meta'], sd['meta'])
    mt = by['與指導教授 meeting']
    check('固定時段標為「固定」且顯示時間', mt['chip'] == '固定' and '14:00' in mt['meta'],
          (mt['chip'], mt['meta']))

    print('\n=== 重複頻率 ===')
    rep_task = by['每週進度回報']
    check('重複任務在清單上標出頻率', '↻' in rep_task['meta'] and '每週' in rep_task['meta'],
          rep_task['meta'])
    check('行事曆預告了之後的重複次數',
          d['repeatMarks'].count('每週進度回報') >= 1, d['repeatMarks'])
    d1, d2 = md(d['repeatMetaBefore']), md(d['repeatMetaAfter'])
    print('  截止日：%s → 完成後 %s' % (d1, d2))
    check('完成重複任務不會結案，仍留在待辦', d['repeatStillOpen'])
    check('完成後截止日往後推一週', d1 and d2 and (d2 - d1).days == 7, (d1, d2))
    check('完成重複任務會留下紀錄並標示下一次',
          any(r['name'] == '每週進度回報' and '下次' in r['kind'] for r in d['repeatLog']['rows']),
          d['repeatLog']['rows'])
    check('恢復後截止日回到原本那天', md(d['repeatMetaRestored']) == d1,
          (d['repeatMetaRestored'], d1))
    check('恢復後該筆紀錄消失',
          not [r for r in d['repeatLogAfterUndo']['rows'] if r['name'] == '每週進度回報'],
          d['repeatLogAfterUndo']['rows'])

    print('\n=== 完成紀錄頁 ===')
    check('切到完成紀錄會換掉行事曆版面', d['afterDoneLog']['logVisible'])
    print('  統計：', d['afterDoneLog']['sum'])
    print('  分日：', d['afterDoneLog']['days'])
    for r in d['afterDoneLog']['rows']:
        print('    %-16s %-14s %sh  [%s]' % (r['name'], r['kind'], r['hours'], r['btn']))
    check('統計有五項（未完成／今日／本週／今日結案／累計）', len(d['afterDoneLog']['sum']) == 5,
          d['afterDoneLog']['sum'])
    check('統計第一項是未完成', d['afterDoneLog']['sum'][0].startswith('未完成='),
          d['afterDoneLog']['sum'][0])
    check('紀錄依日期分段且標出今天',
          any('今天' in x for x in d['afterDoneLog']['days']), d['afterDoneLog']['days'])
    check('完成任務會產生一筆紀錄',
          any(r['name'] == '問卷資料清理' and r['kind'] == '完成'
              for r in d['afterDoneLog']['rows']), d['afterDoneLog']['rows'])
    check('每筆紀錄都有恢復鍵',
          all(r['btn'] == '恢復' for r in d['afterDoneLog']['rows']), d['afterDoneLog']['rows'])

    print('\n=== 誤按完成可以恢復 ===')
    done_by = {t['title']: t for t in d['afterDone']['tasks']}
    check('勾選後歸到「已完成」', '已完成' in d['afterDone']['groups'], d['afterDone']['groups'])
    check('已完成的任務顯示恢復鍵', done_by['問卷資料清理']['hasUndo'],
          done_by['問卷資料清理'])
    undo_by = {t['title']: t for t in d['afterUndo']['tasks']}
    check('恢復後回到未完成', not undo_by['問卷資料清理']['checked'],
          undo_by['問卷資料清理'])
    check('恢復後進度回到原本的 0 / 5 h', '0 / 5 h' in undo_by['問卷資料清理']['meta'],
          undo_by['問卷資料清理']['meta'])
    check('恢復後「已完成」分組消失', '已完成' not in d['afterUndo']['groups'],
          d['afterUndo']['groups'])
    check('恢復後紀錄也一併移除',
          not [r for r in d['afterUndo']['log']['rows'] if r['name'] == '問卷資料清理'],
          d['afterUndo']['log']['rows'])

    print('\n=== 今日打卡 ===')
    if not d['hadSlot']:
        print('  （今天沒有排定工作，略過打卡測試）')
    else:
        print('  按鈕：', d['slotLabel'])
        print('  打卡後今日面板：', d['todayHead'], [s['name'] + ('(已完成)' if s['did'] else '')
                                                     for s in d['todayAfter']])
        check('打卡後今日面板留下已完成那一列',
              any(s['did'] for s in d['todayAfter']), d['todayAfter'])
        check('今日面板有「今天完成」小標與時數',
              any('今天完成' in h for h in d['todayHead']), d['todayHead'])
        check('那一列可以就地恢復',
              any(s['did'] and s['btn'] == '恢復' for s in d['todayAfter']), d['todayAfter'])
        check('就地恢復後回到可打卡的狀態',
              d['todayRestored'] and not any(s['did'] for s in d['todayRestored'])
              and any(s['btn'] and s['btn'].startswith('完成') for s in d['todayRestored']),
              d['todayRestored'])
        ci = [r for r in d['afterCheckin']['rows'] if r['kind'] == '打卡']
        check('打卡會留下一筆紀錄與時數', ci and ci[0]['hours'] > 0, d['afterCheckin']['rows'])
        check('打卡的時數算進今日完成',
              any(s.startswith('今日完成=') and float(s.split('=')[1].rstrip('h')) > 0
                  for s in d['afterCheckin']['sum']), d['afterCheckin']['sum'])
        check('打卡也能恢復',
              not [r for r in d['afterCheckinUndo']['rows'] if r['kind'] == '打卡'],
              d['afterCheckinUndo']['rows'])

    print('\n=== LINE 連動（上傳的內容）===')
    check('設定裡有 LINE 連動欄位', d.get('hasSyncFields'))
    s = d.get('sync')
    if not s or 'keys' not in s:
        check('按「立即同步」會送出資料', False, s)
    else:
        print('  %s %s  token=%s' % (s['method'], s['url'], s['token']))
        print('  欄位：', s['keys'])
        print('  第一天：%s → %s' % (s['firstDay'], s['firstBlock']))
        check('送到 /sync 且用 PUT', s['url'].endswith('/sync') and s['method'] == 'PUT', s['url'])
        check('帶上同步密鑰', s['token'] == 'test-token', s['token'])
        check('網址結尾多餘的斜線有處理掉', '//sync' not in s['url'].replace('https://', ''), s['url'])
        check('內容包含行程、任務與設定',
              set(['days', 'tasks', 'settings', 'generatedAt']) <= set(s['keys']), s['keys'])
        check('上傳的是算好的每日區塊（含時數）',
              s['firstBlock'] and 'title' in s['firstBlock'] and 'hours' in s['firstBlock'],
              s['firstBlock'])
        check('每筆任務都帶狀態（LINE 才能回報風險）', s['hasStatus'], s['taskKeys'])
        check('任務欄位齊全',
              set(['title', 'deadline', 'remaining', 'status', 'repeat']) <= set(s['taskKeys']),
              s['taskKeys'])
        check('設定一併上傳（每日可投入時數等）',
              'dailyCapacity' in s['settingsKeys'], s['settingsKeys'])

    print('\n=== 未完成清單與行事曆上的過去 ===')
    due = d.get('lateDue')
    lv = d.get('lateView') or {}
    late = [r for r in lv.get('late', []) if r['name'] == '上週的報告']
    print('  逾期的那件事：', late)
    check('過期的事會進未完成清單', lv.get('lateVisible') and late, lv.get('late'))
    check('未完成那一列標出逾期幾天', late and late[0]['chip'] == '逾期 3 天',
          late[0]['chip'] if late else None)
    check('未完成那一列有補完成／編輯／刪除',
          late and late[0]['btns'] == ['補完成', '編輯', '刪除'],
          late[0]['btns'] if late else None)
    check('未完成件數算進統計',
          any(s.startswith('未完成=') and s != '未完成=0 件' for s in lv.get('sum', [])),
          lv.get('sum'))
    check('「紀錄」鍵掛上未完成件數的標記', d.get('lateBadge') == '1', d.get('lateBadge'))

    cell_late = [c for c in d.get('cellsLate', []) if c['date'] == due]
    check('行事曆把過去那一格標成逾期',
          cell_late and '上週的報告' in cell_late[0]['overdue'],
          cell_late[0] if cell_late else '（那一天不在目前的月檢視裡）')
    check('過去的格子有標成 past', cell_late and cell_late[0]['past'],
          cell_late[0]['past'] if cell_late else None)

    af = d.get('afterFill') or {}
    filled = [x for x in af.get('days', []) if due in x]
    print('  補完成後的紀錄分日：', af.get('days'))
    check('補完成的紀錄落在原本的截止日那天', bool(filled), af.get('days'))
    check('補完成後那件事離開未完成清單',
          not [r for r in af.get('late', []) if r['name'] == '上週的報告'], af.get('late'))
    check('補完成後「紀錄」鍵的標記消失', d.get('badgeAfterFill') is True, d.get('badgeAfterFill'))

    cell_fill = [c for c in d.get('cellsAfterFill', []) if c['date'] == due]
    print('  那一天的行事曆格子：', cell_fill[0] if cell_fill else None)
    check('行事曆上過去那一天留下完成紀錄',
          cell_fill and any(x['name'] == '上週的報告' for x in cell_fill[0]['dones']),
          cell_fill[0]['dones'] if cell_fill else None)
    check('完成紀錄帶勾號與時數',
          cell_fill and cell_fill[0]['dones'] and cell_fill[0]['dones'][0]['mark'] == '✓'
          and cell_fill[0]['dones'][0]['hours'] == 2.0,
          cell_fill[0]['dones'] if cell_fill else None)
    check('那一天的合計換成實際做掉的時數',
          cell_fill and cell_fill[0]['total'] == 2.0,
          cell_fill[0]['total'] if cell_fill else None)
    check('逾期標記在補完成後消失',
          cell_fill and '上週的報告' not in cell_fill[0]['overdue'],
          cell_fill[0]['overdue'] if cell_fill else None)

    fu = d.get('afterFillUndo') or {}
    check('補錯了恢復回來，事情回到未完成清單',
          any(r['name'] == '上週的報告' for r in fu.get('late', [])), fu.get('late'))
    check('恢復後那筆紀錄也不見了',
          not [x for x in fu.get('days', []) if due in x], fu.get('days'))

    ad = d.get('afterDelete') or {}
    check('可以直接從未完成清單刪掉',
          not [r for r in ad.get('late', []) if r['name'] == '上週的報告'], ad.get('late'))
    check('刪掉之後任務清單也沒有它',
          '上週的報告' not in (d.get('tasksAfterDelete') or []), d.get('tasksAfterDelete'))

    print('\n=== 摘要列 ===')
    print('  ', d['readout'])

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
