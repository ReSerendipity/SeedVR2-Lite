/**
 * 响应式溢出门禁：在平板 / 移动断点渲染关键页面，断言
 * documentElement.scrollWidth <= clientWidth（即无横向滚动），并落截图供肉眼复核。
 *
 * 背景：sv2 工作台一度完全没有响应式规则（既有 @media 指向已废弃的旧类名），
 * 桌面端一切正常，只有窄视口才暴露工具条重叠、侧栏盖死画布等问题。
 * 背景账本见维护者本地文件 docs/project/KNOWN_ISSUES.md #49、#51（未随仓库分发）。
 *
 * 用法: node check-responsive.js
 *   SEEDVR2_BASE_URL  默认 http://127.0.0.1:7870
 *   OUT_DIR           默认 ../screenshots/responsive
 *   THEME             默认 dark
 */
const { chromium } = require('@playwright/test');
const path = require('path');
const fs = require('fs');

const BASE = process.env.SEEDVR2_BASE_URL || 'http://127.0.0.1:7870';
const OUT = process.env.OUT_DIR || path.join(__dirname, '..', 'screenshots', 'responsive');
const THEME = process.env.THEME || 'dark';
fs.mkdirSync(OUT, { recursive: true });

const TABLET = { width: 768, height: 1024 };
const MOBILE = { width: 375, height: 812 };

const CASES = [
  { name: 'tablet-restore-single', vp: TABLET },
  { name: 'tablet-restore-params-open', vp: TABLET, openParams: true },
  { name: 'tablet-batch', vp: TABLET, hash: '#batch' },
  { name: 'tablet-system', vp: TABLET, page: '/system-status' },
  { name: 'tablet-history', vp: TABLET, page: '/history' },
  { name: 'tablet-settings', vp: TABLET, page: '/settings' },
  { name: 'mobile-restore-single', vp: MOBILE },
  { name: 'mobile-restore-params-open', vp: MOBILE, openParams: true },
  { name: 'mobile-restore-batch', vp: MOBILE, hash: '#batch' },
  { name: 'mobile-history', vp: MOBILE, page: '/history' },
  { name: 'mobile-system', vp: MOBILE, page: '/system-status' },
  { name: 'mobile-settings', vp: MOBILE, page: '/settings' },
  { name: 'mobile-home', vp: MOBILE, page: '/' },
];

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext();
  await ctx.addInitScript((theme) => {
    localStorage.setItem('sv_onboarding_seen_v2', '1');
    localStorage.setItem('sv-theme-brand', theme);
  }, THEME);
  const page = await ctx.newPage();
  // 拦截外部字体 CDN：无外网时 render-blocking 的样式表会挂住 domcontentloaded
  await ctx.route('**/*', (r) =>
    r.request().url().startsWith('http://fonts.') || r.request().url().startsWith('https://fonts.')
      ? r.abort() : r.continue());

  let failed = 0;
  for (const c of CASES) {
    await page.setViewportSize(c.vp);
    await page.goto(BASE + (c.page || '/restore') + (c.hash || ''), {
      waitUntil: 'domcontentloaded', timeout: 30000,
    });
    await page.waitForTimeout(2500);
    if (c.openParams) {
      await page.evaluate(() => {
        const b = document.getElementById('btnRestoreSidebar');
        if (!b) return;
        // 注意：fixed 定位元素的 offsetParent 恒为 null，不能用它判可见
        const r = b.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) b.click();
      });
      await page.waitForTimeout(600);
    }
    const overflow = await page.evaluate(() => {
      const vw = document.documentElement.clientWidth;
      const bad = [];
      document.querySelectorAll('body *').forEach((el) => {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && (r.right > vw + 1 || r.left < -1)) {
          const cls = (el.className && String(el.className).split(' ')[0]) || el.tagName.toLowerCase();
          bad.push(`${el.tagName.toLowerCase()}.${cls} right=${Math.round(r.right)} left=${Math.round(r.left)}`);
        }
      });
      return {
        scrollW: document.documentElement.scrollWidth,
        clientW: vw,
        offenders: [...new Set(bad)].slice(0, 8),
      };
    });
    await page.screenshot({ path: path.join(OUT, c.name + '.png'), fullPage: true });
    const ok = overflow.scrollW <= overflow.clientW + 1;
    console.log(`${ok ? 'OK  ' : 'FAIL'} ${c.name.padEnd(30)} scrollW=${overflow.scrollW} clientW=${overflow.clientW}`);
    if (!ok) {
      failed++;
      console.log('     溢出元素:', overflow.offenders.join(' | '));
    }
  }
  await browser.close();
  if (failed) {
    console.error(`\n${failed} 个用例存在横向溢出`);
    process.exit(1);
  }
  console.log('\n全部通过：无横向溢出');
})();
