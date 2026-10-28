/* Local-only acceptance check. Requires seed_launchops --pagination-demo.
 * NODE_PATH=/tmp/launchops-browser/node_modules node scripts/check_session_pagination.cjs
 * Creates signed, already-expired demo tokens in memory; never logs or saves tokens.
 */
const { chromium } = require('playwright');
const { execFileSync } = require('child_process');
const fs = require('fs');
const assert = require('assert/strict');
const base = process.env.LAUNCHOPS_URL || 'http://localhost:13000';
const api = process.env.LAUNCHOPS_API || 'http://localhost:18000/api';
for (const url of [base, api]) assert(['localhost', '127.0.0.1', '[::1]'].includes(new URL(url).hostname), 'Local-only check');
const reportDir = process.env.LAUNCHOPS_REPORT_DIR || '/tmp/launchops-session-pagination-' + Date.now();
fs.mkdirSync(reportDir, { recursive: true });
const report = { started: new Date().toISOString(), checks: [], errors: [] };
const code = `
import json
from datetime import timedelta
from django.conf import settings
from users.models import User
from rest_framework_simplejwt.tokens import RefreshToken
assert settings.DEBUG, 'Local DEBUG=True required'
user = User.objects.get(username='demo_hr', role='hr', is_active=True, is_staff=False, is_superuser=False)
refresh = RefreshToken.for_user(user)
access = refresh.access_token
access.set_exp(lifetime=timedelta(seconds=-60))
expired_refresh = RefreshToken.for_user(user)
expired_refresh.set_exp(lifetime=timedelta(seconds=-60))
print(json.dumps({'access':str(access), 'refresh':str(refresh), 'expired_refresh':str(expired_refresh)}))
`;
(async () => {
  const tokens = JSON.parse(execFileSync('docker', ['compose', 'exec', '-T', 'backend', 'python', 'manage.py', 'shell', '-v', '0', '-c', code], { encoding: 'utf8' }));
  const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN || (process.platform === 'darwin' ? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' : '/usr/bin/google-chrome'), headless: true });
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(30000);
    page.on('pageerror', error => report.errors.push(error.message));
    let refreshCalls = 0;
    page.on('request', request => { if (request.url().endsWith('/auth/refresh/')) refreshCalls += 1; });
    await page.goto(base);
    await page.locator('a[href="/login/hr"]').click();
    await page.getByLabel('Username', { exact: true }).fill('demo_hr');
    await page.getByLabel('Password', { exact: true }).fill(process.env.LAUNCHOPS_DEMO_PASSWORD || 'LaunchOpsDemo2026!');
    await page.getByRole('button', { name: 'Sign In', exact: true }).click();
    await page.waitForURL('**/workspace');
    await page.locator('.person').first().waitFor();
    // Follow every API page, then open both endpoints of the list in the browser.
    const rows = await page.evaluate(async api => {
      const rows = [];
      for (let page = 1; ; page++) {
        const response = await fetch(api + '/onboarding/workflows/?page=' + page, { headers: { Authorization: 'Bearer ' + sessionStorage.access_token } });
        if (!response.ok) throw new Error('Pagination failed: ' + response.status);
        const data = await response.json();
        rows.push(...data.results);
        if (!data.next) return { rows, total_pages: data.total_pages, count: data.count };
      }
    }, api);
    assert(rows.count > 100);
    assert.equal(new Set(rows.rows.map(row => row.id)).size, rows.count);
    const newest = rows.rows[0], oldest = rows.rows.at(-1);
    await page.locator('.person').filter({ hasText: newest.employee }).click();
    await page.getByRole('heading', { name: newest.employee + "'s first chapter" }).waitFor();
    // Navigate through actual Next buttons rather than jumping directly to the last page.
    for (let index = 1; index < rows.total_pages; index++) {
      await page.getByRole('button', { name: 'Next', exact: true }).click();
      await page.getByText(`Page ${index + 1} of ${rows.total_pages}`, { exact: true }).waitFor();
      await page.locator('.person').first().waitFor();
    }
    await page.locator('.person').filter({ hasText: oldest.employee }).click();
    await page.getByRole('heading', { name: oldest.employee + "'s first chapter" }).waitFor();
    assert(await page.getByRole('button', { name: 'Next', exact: true }).isDisabled());
    await page.screenshot({ path: reportDir + '/oldest-workflow.png', fullPage: true });
    report.checks.push({ name: 'all pages unique; newest and oldest accessible in UI', count: rows.count, pages: rows.total_pages, newest: newest.id, oldest: oldest.id });
    // Real signed expired access tokens cause a server 401, followed by a shared refresh.
    await page.evaluate(tokens => { sessionStorage.access_token = tokens.access; sessionStorage.refresh_token = tokens.refresh; }, tokens);
    const before = refreshCalls;
    await page.evaluate(async () => {
      const { default: api } = await import('/src/api/axios.ts');
      await Promise.all([api.get('/auth/me/'), api.get('/onboarding/workflows/'), api.get('/onboarding/directory/')]);
    });
    assert.equal(refreshCalls - before, 1);
    report.checks.push({ name: 'concurrent signed expired access requests refresh once and retry successfully', refreshCalls: refreshCalls - before });
    // Rehydration works even when only the valid refresh token remains.
    await page.evaluate(() => sessionStorage.removeItem('access_token'));
    await page.reload();
    await page.locator('.person').first().waitFor();
    assert(await page.evaluate(() => !!sessionStorage.access_token));
    report.checks.push({ name: 'refresh-only session restores current user' });
    // Both tokens expired: clear state, leave protected routes, show an actionable message.
    await page.evaluate(tokens => { sessionStorage.access_token = tokens.access; sessionStorage.refresh_token = tokens.expired_refresh; }, tokens);
    const beforeFailure = refreshCalls;
    await page.reload();
    await page.waitForURL('**/login');
    await page.getByRole('alert').filter({ hasText: 'Your session expired' }).waitFor();
    assert.equal(refreshCalls - beforeFailure, 1);
    assert(await page.evaluate(() => ['access_token', 'refresh_token', 'user'].every(key => sessionStorage.getItem(key) === null)));
    report.checks.push({ name: 'expired refresh ends session and redirects with message', refreshCalls: refreshCalls - beforeFailure });
    // Missing refresh fails without attempting a refresh loop.
    await page.evaluate(tokens => sessionStorage.setItem('access_token', tokens.access), tokens);
    const beforeMissing = refreshCalls;
    await page.goto(base + '/workspace');
    await page.waitForURL('**/login');
    await page.getByRole('alert').filter({ hasText: 'Your session expired' }).waitFor();
    assert.equal(refreshCalls - beforeMissing, 0);
    report.checks.push({ name: 'missing refresh ends session without a refresh request' });
    assert.deepEqual(report.errors, []);
    report.result = 'PASS';
  } catch (error) { report.result = 'FAIL'; report.failure = error.stack; process.exitCode = 1; console.error(error); }
  finally { report.finished = new Date().toISOString(); fs.writeFileSync(reportDir + '/checks.json', JSON.stringify(report, null, 2)); console.log(JSON.stringify(report, null, 2)); await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
