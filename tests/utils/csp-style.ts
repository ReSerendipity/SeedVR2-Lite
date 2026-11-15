/**
 * CSP-safe style injection helpers for SeedVR2 E2E tests.
 *
 * Why this exists: the app ships `style-src 'self' https://fonts.googleapis.com'`
 * (S4 Phase 2, 2026-11-09 deliberately removed `'unsafe-inline'`). Playwright's
 * `page.addStyleTag` creates an inline `<style>` element, which the browser
 * blocks under that policy — the test fails on a security control rather than
 * on the behavior under test.
 *
 * A constructed `CSSStyleSheet` adopted via `document.adoptedStyleSheets` is a
 * CSSOM write, not an inline style element, so it is not subject to `style-src`.
 * This keeps the production CSP untouched while still letting tests freeze
 * transitions/animations.
 */
import { Page } from '@playwright/test';

/**
 * Inject a CSS rule set without creating an inline `<style>` node.
 *
 * @param page - Playwright page instance
 * @param css - CSS text to append to the document's adopted style sheets
 */
export async function addCspSafeStyle(page: Page, css: string): Promise<void> {
  await page.evaluate((cssText) => {
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(cssText);
    document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
  }, css);
}
