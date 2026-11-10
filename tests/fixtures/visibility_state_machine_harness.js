#!/usr/bin/env node
/**
 * @file 可见性状态机行为契约（CSP 收紧 S4 Phase 1）
 * @description 用最小 FakeEl（只实现 classList）加载真实的
 *              `app/integrated_app/static/js/visibility.js`，断言 show/hide/
 *              isVisible 的语义与"切换后读取"行为。
 *
 * 用法：node visibility_state_machine_harness.js <visibility.js 绝对路径>
 * 退出码：0 = 全部通过；1 = 有断言失败（打印 FAIL: <断言名>）。
 *
 * 说明：不引 jsdom，只依赖 classList 契约，便于在无前端依赖的环境秒级运行。
 */
'use strict';

const assert = require('assert');

const target = process.argv[2];
if (!target) {
    console.log('FAIL: 缺少 visibility.js 路径参数');
    process.exit(1);
}

// 真实模块：IIFE 会把 API 挂到 globalThis.SvVisibility
require(target);
const V = globalThis.SvVisibility;

/** 只实现 classList.add/remove/contains 的最小元素替身 */
class FakeEl {
    constructor() {
        this._set = new Set();
    }

    get classList() {
        const s = this._set;
        return {
            add: (c) => s.add(c),
            remove: (c) => s.delete(c),
            contains: (c) => s.has(c),
        };
    }
}

let passed = 0;

/**
 * @param {string} name
 * @param {() => void} fn
 */
function check(name, fn) {
    try {
        fn();
        passed += 1;
    } catch (e) {
        console.log(`FAIL: ${name} :: ${e.message}`);
        process.exitCode = 1;
    }
}

check('模块导出契约', () => {
    assert.ok(V, 'globalThis.SvVisibility 未挂载');
    ['show', 'hide', 'isVisible', 'set', 'showId', 'hideId'].forEach((k) => {
        assert.strictEqual(typeof V[k], 'function', `缺少方法 ${k}`);
    });
    assert.strictEqual(V.HIDDEN_CLASS, 'sv-hidden', '隐藏类名必须是 sv-hidden');
});

check('hide 标记隐藏', () => {
    const el = new FakeEl();
    assert.strictEqual(V.isVisible(el), true, '未处理过的元素应视为可见');
    assert.strictEqual(V.hide(el), true, 'hide 应返回 true');
    assert.ok(el.classList.contains('sv-hidden'), 'hide 后必须带 sv-hidden');
    assert.strictEqual(V.isVisible(el), false, 'hide 后 isVisible 必须为 false');
});

check('show 恢复可见（读侧不再依赖内联 display）', () => {
    const el = new FakeEl();
    V.hide(el);
    assert.strictEqual(V.show(el), true, 'show 应返回 true');
    assert.ok(!el.classList.contains('sv-hidden'), 'show 后不得残留 sv-hidden');
    assert.strictEqual(V.isVisible(el), true, 'show 后 isVisible 必须为 true');
});

check('show 带模式类', () => {
    const el = new FakeEl();
    V.show(el, 'flex');
    assert.ok(el.classList.contains('sv-vis-flex'), 'mode=flex 应加 sv-vis-flex');
    assert.strictEqual(el.classList.contains('sv-vis-block'), false);
});

check('show 切换模式不留残影', () => {
    const el = new FakeEl();
    V.show(el, 'flex');
    V.show(el, 'block');
    assert.ok(!el.classList.contains('sv-vis-flex'), '切到 block 后 flex 模式类必须移除');
    assert.ok(el.classList.contains('sv-vis-block'), '应带 sv-vis-block');
});

check('hide 清掉模式类', () => {
    const el = new FakeEl();
    V.show(el, 'flex');
    V.hide(el);
    assert.ok(!el.classList.contains('sv-vis-flex'), 'hide 后模式类必须清空');
    assert.ok(el.classList.contains('sv-hidden'));
});

check('show 不带模式时回到 CSS 默认', () => {
    const el = new FakeEl();
    V.show(el, 'flex');
    V.show(el);
    assert.ok(!el.classList.contains('sv-vis-flex'), '无模式 show 必须清掉此前的模式类');
    assert.strictEqual(el.classList.contains('sv-vis-block'), false);
});

check('set 跟随布尔值', () => {
    const el = new FakeEl();
    V.set(el, false);
    assert.strictEqual(V.isVisible(el), false);
    V.set(el, true, 'grid');
    assert.strictEqual(V.isVisible(el), true);
    assert.ok(el.classList.contains('sv-vis-grid'));
});

check('空值与非元素安全', () => {
    assert.strictEqual(V.hide(null), false);
    assert.strictEqual(V.show(undefined, 'flex'), false);
    assert.strictEqual(V.isVisible(null), false);
    assert.doesNotThrow(() => V.set(null, true, 'flex'));
    assert.doesNotThrow(() => V.show({}, 'flex'));
});

check('重复调用幂等', () => {
    const el = new FakeEl();
    V.hide(el);
    V.hide(el);
    assert.strictEqual(V.isVisible(el), false);
    V.show(el, 'flex');
    V.show(el, 'flex');
    assert.ok(el.classList.contains('sv-vis-flex'));
    assert.ok(!el.classList.contains('sv-hidden'));
});

check('show/hide 交替不累积状态', () => {
    const el = new FakeEl();
    for (let i = 0; i < 5; i += 1) {
        V.show(el, 'block');
        assert.strictEqual(V.isVisible(el), true, `第 ${i} 轮 show 后应可见`);
        V.hide(el);
        assert.strictEqual(V.isVisible(el), false, `第 ${i} 轮 hide 后应隐藏`);
    }
    assert.ok(!el.classList.contains('sv-vis-block'));
});

console.log(`PASS ${passed} checks`);
if (passed !== 11) {
    console.log(`FAIL: 断言数量异常（预期 11，实际 ${passed}）`);
    process.exitCode = 1;
}
