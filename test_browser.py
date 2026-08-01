# -*- coding: utf-8 -*-
"""用 headless Chrome 真的把 index.html 跑起來，再從渲染後的 DOM 驗證行為。

    python test_browser.py

不需要安裝任何套件，只要系統裡有 Chrome 或 Edge。
驗證的是「瀏覽器實際算出來、實際畫出來」的結果，不是重寫一份邏輯來對答案。
測試依賴 index.html 內建的範例資料（seed），改動 seed 時這裡也要跟著改。
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


# 注入頁面的探針：把渲染後的 DOM 摘要塞進 document.title 帶回來
PROBE = r"""
<script>
(function(){
  var out = {errors: []};
  try{
    out.cells = [].slice.call(document.querySelectorAll('#grid .cell')).map(function(c,i){
      var tot = c.querySelector('.dnum .tot');
      return {
        col: i % 7,                                   // 月檢視 7 欄，第 0 欄是週日
        total: tot ? parseFloat(tot.textContent) : 0,
        blocks: [].slice.call(c.querySelectorAll('.blk')).map(function(b){
          return {
            name: b.querySelector('.bn').textContent,
            hours: parseFloat(b.querySelector('.bh').textContent),
            fixed: b.classList.contains('fixed'),
            at: b.querySelector('.bt') ? b.querySelector('.bt').textContent : null
          };
        })
      };
    });
    out.groups = [].slice.call(document.querySelectorAll('#taskList .grp'))
      .map(function(g){ return g.firstChild.textContent; });
    out.tasks = [].slice.call(document.querySelectorAll('#taskList .task')).map(function(t){
      return {
        title: t.querySelector('.title').textContent,
        chip: t.querySelector('.chip') ? t.querySelector('.chip').textContent : null,
        hasBar: !!t.querySelector('.pbar'),
        hasCheckbox: !!t.querySelector('.chk'),
        meta: t.querySelector('.meta') ? t.querySelector('.meta').textContent : ''
      };
    });
    out.readout = [].slice.call(document.querySelectorAll('#readout div'))
      .map(function(d){ return d.querySelector('span').textContent + '=' + d.querySelector('b').textContent; });
    out.hasUnitSelect = !!document.querySelector('#fUnit');
    out.hasTimeInput  = !!document.querySelector('#fAt');
    out.dowOptions    = document.querySelectorAll('#fDows label').length;
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
    check('任務清單有渲染 5 筆範例', len(d['tasks']) == 5, [t['title'] for t in d['tasks']])
    check('單位選單存在', d['hasUnitSelect'])
    check('特定時間欄位存在', d['hasTimeInput'])
    check('指定星期有 7 個選項', d['dowOptions'] == 7, d['dowOptions'])

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

    fx = [b for c in d['cells'] for b in c['blocks'] if b['fixed']]
    check('固定時段有渲染且帶時間', len(fx) == 1 and fx[0]['at'] == '14:00', fx)
    check('固定時段沒有被拆開', len(fx) == 1 and fx[0]['hours'] == 1, fx)
    check('純清單項目沒有出現在行事曆',
          '訂研討會的會議室' not in [b['name'] for c in d['cells'] for b in c['blocks']])

    print('\n=== 任務清單 ===')
    print('  分組：', d['groups'])
    check('分組正確', '排程中' in d['groups'] and '清單・無期限' in d['groups'], d['groups'])
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
