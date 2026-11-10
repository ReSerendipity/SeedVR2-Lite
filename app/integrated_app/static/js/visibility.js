/**
 * @file 可见性状态机（CSP 收紧 S4 · Phase 1 / 2026-11-09）
 * @project SeedVR2 - AI视频/图像修复系统
 * @description 可见性控制的**唯一出口**：一律走 classList，不再读写内联
 *              `style.display`。
 *
 * 背景（docs/CSP_TIGHTENING_ROADMAP.md §2c）：
 * - 模板曾有大量 `style="display:none"`，JS 侧配套 59 处写 + 11 处读
 *   `el.style.display`；
 * - 一旦把内联 display 迁走，读侧拿到的是空串（`'' !== 'none'` 恒真），
 *   可见性判断会整体翻转，所以必须先有单一出口再迁移。
 *
 * 契约：
 * - 隐藏 = 元素带 `sv-hidden`（style.css 既有 `.sv-hidden{display:none!important}`）；
 * - 显示 = 去掉 `sv-hidden`；若调用方原本靠内联值把元素强制成 flex/block 等，
 *   用对应的 `sv-vis-*` 模式类复现同一 display 值（不再写内联）；
 * - `isVisible(el)` 只认状态类，替代此前的 `el.style.display !== 'none'` 读取。
 *
 * 该文件在 base.html 中先于 app.js 加载，模板内联脚本可直接使用全局
 * `SvVisibility`。
 */
(function (root) {
    'use strict';

    /** 唯一隐藏标记类（style.css 中 `display:none!important`） */
    var HIDDEN_CLASS = 'sv-hidden';

    /** 显示模式 → 模式类。键即调用方传入的 mode 名 */
    var MODE_CLASSES = {
        flex: 'sv-vis-flex',
        block: 'sv-vis-block',
        'inline-flex': 'sv-vis-inline-flex',
        grid: 'sv-vis-grid'
    };

    /**
     * 判断入参是否像一个带 classList 的元素（容忍 null / 非元素 / 无 classList 的假值）
     * @param {*} el
     * @returns {boolean}
     */
    function isElement(el) {
        return !!el && typeof el === 'object' && !!el.classList && typeof el.classList.add === 'function';
    }

    /**
     * 清掉全部显示模式类（隐藏与重新显示前都要清，避免残留模式互相打架）
     * @param {HTMLElement} el
     * @returns {void}
     */
    function clearModes(el) {
        for (var key in MODE_CLASSES) {
            if (Object.prototype.hasOwnProperty.call(MODE_CLASSES, key)) {
                el.classList.remove(MODE_CLASSES[key]);
            }
        }
    }

    /**
     * 隐藏元素（单一出口）
     * @param {HTMLElement|null} el
     * @returns {boolean} 是否确实作用到元素上
     */
    function hide(el) {
        if (!isElement(el)) return false;
        clearModes(el);
        el.classList.add(HIDDEN_CLASS);
        return true;
    }

    /**
     * 显示元素（单一出口）
     * @param {HTMLElement|null} el
     * @param {string} [mode] - 'flex' | 'block' | 'inline-flex' | 'grid'；省略则回到 CSS 默认 display
     * @returns {boolean} 是否确实作用到元素上
     */
    function show(el, mode) {
        if (!isElement(el)) return false;
        el.classList.remove(HIDDEN_CLASS);
        clearModes(el);
        var modeClass = MODE_CLASSES[mode];
        if (modeClass) el.classList.add(modeClass);
        return true;
    }

    /**
     * 读取可见性（替代 `el.style.display !== 'none'`）
     * @param {HTMLElement|null} el
     * @returns {boolean}
     */
    function isVisible(el) {
        if (!isElement(el)) return false;
        return !el.classList.contains(HIDDEN_CLASS);
    }

    /**
     * 按布尔值设置可见性
     * @param {HTMLElement|null} el
     * @param {boolean} visible
     * @param {string} [mode]
     * @returns {boolean}
     */
    function set(el, visible, mode) {
        return visible ? show(el, mode) : hide(el);
    }

    /** @param {string} id @param {string} [mode] */
    function showId(id, mode) {
        if (typeof document === 'undefined' || !document.getElementById) return false;
        return show(document.getElementById(id), mode);
    }

    /** @param {string} id */
    function hideId(id) {
        if (typeof document === 'undefined' || !document.getElementById) return false;
        return hide(document.getElementById(id));
    }

    var api = {
        HIDDEN_CLASS: HIDDEN_CLASS,
        MODE_CLASSES: MODE_CLASSES,
        show: show,
        hide: hide,
        isVisible: isVisible,
        set: set,
        showId: showId,
        hideId: hideId
    };

    root.SvVisibility = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
