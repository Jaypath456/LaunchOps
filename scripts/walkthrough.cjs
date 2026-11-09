/* Full-stack v2 walkthrough with the local Chrome, all four roles, against the Docker demo.
 *
 *   NODE_PATH=/tmp/launchops-browser/node_modules LAUNCHOPS_URL=http://localhost:23300 \
 *   LAUNCHOPS_API=http://localhost:28300/api COMPOSE_PROJECT=cc-e2e LAUNCHOPS_REPORT_DIR=/tmp/launchops-walkthrough \
 *   node scripts/walkthrough.cjs
 *
 * It creates two fresh employees (seed --walkthrough-employee) and never modifies existing workflows.
 * Docker compose is used to (a) seed users, (b) stop/start the provisioner replicas so the pending state is
 * observable, and (c) put two provisioning jobs of the second hire into `dead` through MongoDB.
 * mock-saas is a simulator; nothing here talks to a real SaaS.
 * Set COMPOSE_PROJECT to the compose project name (the same BACKEND_PORT... variables must be exported).
 */
const { chromium } = require('playwright');
const { execFileSync } = require('child_process');
const fs = require('fs');
const assert = require('assert/strict');
const base = process.env.LAUNCHOPS_URL || 'http://localhost:13000';
const api = process.env.LAUNCHOPS_API || 'http://localhost:18000/api';
for (const url of [base, api]) assert(['localhost', '127.0.0.1', '[::1]'].includes(new URL(url).hostname), 'Walkthrough is local-only');
const password = process.env.LAUNCHOPS_DEMO_PASSWORD || 'LaunchOpsDemo2026!';
const project = process.env.COMPOSE_PROJECT || '';
const reportDir = process.env.LAUNCHOPS_REPORT_DIR || '/tmp/launchops-walkthrough-' + Date.now();
const screenDir = process.env.LAUNCHOPS_SCREEN_DIR || reportDir + '/screens';
fs.mkdirSync(screenDir, { recursive: true });
const today = new Date().toISOString().slice(0, 10);
const report = { started: new Date().toISOString(), base, api, composeProject: project, pages: [], actions: [], checks: [], errors: [], timings: [], screenshots: [] };
const check = (name, detail) => { report.checks.push({ check: name, ...(detail ? { detail } : {}) }); console.log('OK  ', name); };

function compose(...args) {
  return execFileSync('docker', ['compose', ...(project ? ['-p', project] : []), ...args], { stdio: 'pipe' }).toString();
}
const mongosh = js => compose('exec', '-T', 'mongo', 'mongosh', '--quiet', 'launchops', '--eval', js).trim();
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN || (process.platform === 'darwin' ? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' : '/usr/bin/google-chrome'), headless: true, args: ['--no-sandbox'] });
  const clients = {};
  async function shot(page, name) {
    const file = `${screenDir}/${name}.png`;
    await page.screenshot({ path: file }); report.screenshots.push(`screens/${name}.png`);
  }
  async function login(role, username, accountPassword = password) {
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 } }); const page = await context.newPage(); page.setDefaultTimeout(30000);
    page.on('pageerror', e => report.errors.push(`${username}: ${e.message}`));
    await page.goto(base); await page.locator(`a[href="/login/${role}"]`).click(); await page.locator('[name=username]').fill(username); await page.locator('[name=password]').fill(accountPassword);
    await page.getByRole('button', { name: 'Sign In', exact: true }).click(); await page.waitForURL('**/workspace'); await page.locator('.ops-hero').waitFor();
    report.actions.push({ action: 'role login through landing page', role, username });
    const request = async (method, path, data, headers = {}) => {
      const token = await page.evaluate(() => sessionStorage.access_token);
      const start = Date.now(); const res = await context.request.fetch(api + path, { method, headers: { Authorization: 'Bearer ' + token, ...headers }, data });
      report.timings.push({ method, path, ms: Date.now() - start, status: res.status() });
      return { status: res.status(), data: await res.json().catch(() => null), headers: res.headers() };
    };
    return { context, page, request, username, role };
  }
  async function workflowsFor(client) {
    const rows = [];
    for (let page = 1; ; page++) {
      const r = await client.request('GET', '/onboarding/workflows/?page=' + page);
      assert.equal(r.status, 200); rows.push(...r.data.results);
      if (!r.data.next) return rows;
    }
  }
  const getWorkflow = async (client, id) => (await client.request('GET', `/onboarding/workflows/${id}/`)).data;
  const openTask = async (client, id, key) => { await client.page.goto(`${base}/workspace/workflows/${id}/tasks/${key}`); await client.page.locator('h1').waitFor(); };

  async function createHire(username, title, department) {
    const hr = clients.hr;
    await hr.page.goto(base + '/workspace'); await hr.page.locator('.ops-hero').waitFor();
    await hr.page.getByRole('button', { name: '+ Start onboarding' }).click();
    await hr.page.getByLabel('New hire', { exact: true }).selectOption({ label: username });
    await hr.page.getByLabel('Manager', { exact: true }).selectOption({ label: 'demo_manager' });
    await hr.page.getByLabel('Job title', { exact: true }).fill(title); await hr.page.getByLabel('Department', { exact: true }).fill(department);
    await hr.page.getByLabel('Start date', { exact: true }).fill(today);
    if (!report.screenshots.some(s => s.includes('create-hire'))) await shot(hr.page, '03-create-hire');
    await hr.page.getByRole('button', { name: 'Launch workflow →' }).click();
    await hr.page.getByRole('status').filter({ hasText: `Onboarding started for ${username}` }).waitFor();
    const w = (await workflowsFor(hr)).find(x => x.employee === username); assert(w, 'created workflow is listed');
    assert.equal(w.steps.length, 20); check(`HR created hire ${username} through the UI`, w.id);
    report.actions.push({ action: 'HR created hire in UI', employee: username, workflow: w.id });
    return w;
  }

  // Fill the role's browser form from the server's field definitions, then submit once (or double-click).
  async function completeInUi(client, w, key, { dblclick = false, keepForm = false } = {}) {
    await openTask(client, w.id, key);
    const task = (await client.request('GET', `/onboarding/workflows/${w.id}/steps/${key}/`)).data;
    const values = {};
    for (const f of task.step.form_fields) {
      const input = client.page.locator('.task-form').getByLabel(f.label, { exact: true });
      const value = f.type === 'checkbox' ? true : f.type === 'select' ? f.options[0] : f.type === 'date' ? today : `Walkthrough record for ${key}`;
      values[f.name] = value;
      if (f.type === 'checkbox') await input.check(); else if (f.type === 'select') await input.selectOption(value); else await input.fill(value);
    }
    if (keepForm) await shot(client.page, keepForm);
    const completionPath = `/onboarding/workflows/${w.id}/steps/${key}/complete/`;
    let posts = 0; const onReq = r => { if (r.method() === 'POST' && new URL(r.url()).pathname.endsWith('/api' + completionPath)) posts++; };
    client.page.on('request', onReq);
    const completed = client.page.waitForResponse(r => new URL(r.url()).pathname.endsWith('/api' + completionPath) && r.request().method() === 'POST');
    const button = client.page.getByRole('button', { name: /Run simulation and save result|Save record and complete task/ });
    if (dblclick) await button.dblclick({ delay: 0 }); else await button.click();
    const winner = await completed;
    assert.equal(winner.status(), 200, `${key}: ${await winner.text()}`);
    await client.page.getByRole('status').filter({ hasText: 'Task completed' }).waitFor();
    await sleep(300); client.page.off('request', onReq);
    const requestKey = winner.request().headers()['idempotency-key']; assert(requestKey, 'UI completion must send an Idempotency-Key');
    // Replay with the same key: identical body, flagged only by the header.
    const retry = await client.request('POST', completionPath, { evidence: values }, { 'Idempotency-Key': requestKey });
    assert.equal(retry.status, 200); assert.deepEqual(retry.data, await winner.json()); assert.equal(retry.headers['idempotent-replayed'], 'true');
    const after = await getWorkflow(clients.hr, w.id);
    assert.equal(after.events.filter(e => e.step === key && e.action === 'completed').length, 1, `${key}: exactly one completion event`);
    assert.equal(after.steps.find(s => s.key === key).state, 'done');
    report.actions.push({ workflow: w.id, task: key, actor: client.username, uiPosts: posts, action: 'completed through UI form; replay identical' });
    console.log('Completed', key, 'by', client.username, `(POSTs from UI: ${posts})`);
    return { posts };
  }

  const panel = (client, connector) => client.page.getByRole('article', { name: `${connector} provisioning` });
  async function waitJobs(client, w, statuses, label) {
    for (let i = 0; i < 40; i++) {
      await openTask(client, w.id, 'access');
      const got = [];
      for (const c of ['workspace', 'slack', 'github']) got.push(await panel(client, c).locator('dd').first().innerText());
      if (got.every((s, n) => statuses[n].includes(s))) { check(label, got.join(',')); return got; }
      await sleep(1500);
    }
    throw new Error(`${label}: jobs did not reach ${JSON.stringify(statuses)}`);
  }

  try {
    for (const role of ['hr', 'it', 'manager', 'employee']) clients[role] = await login(role, 'demo_' + role);
    await shot(clients.hr.page, '02-workspace-list');
    const stamp = Date.now();
    const userA = `walk_a_${stamp}`, userB = `walk_b_${stamp}`;
    for (const u of [userA, userB]) compose('exec', '-T', 'backend', 'python', 'manage.py', 'seed_launchops', '--walkthrough-employee', u);
    const empA = await login('employee', userA), empB = await login('employee', userB);

    // Hold the provisioners so "pending" is observable and the dead-state setup cannot race them.
    compose('stop', 'provisioner'); check('provisioner replicas stopped for the pending-state checks');
    const A = await createHire(userA, 'Walkthrough engineer', 'Product audit');
    const B = await createHire(userB, 'Walkthrough analyst', 'Operations');
    let a = await getWorkflow(clients.hr, A.id);
    assert.deepEqual(a.events, [], 'new workflow has no events');

    // HR completes profile (UI) so the access step becomes ready.
    await completeInUi(clients.hr, A, 'profile');
    // Access: IT is refused while jobs are not terminal-success.
    await openTask(clients.it, A.id, 'access');
    await clients.it.page.getByRole('status').filter({ hasText: 'Waiting for every job to succeed or be overridden' }).waitFor();
    assert(await clients.it.page.getByRole('button', { name: /Run simulation and save result/ }).isDisabled(), 'Complete is disabled while jobs are pending');
    await clients.it.page.evaluate(() => window.scrollBy(0, 420)); await shot(clients.it.page, '04-provisioning-pending');
    const early = await clients.it.request('POST', `/onboarding/workflows/${A.id}/steps/access/complete/`, { evidence: { bundle: (await clients.it.request('GET', `/onboarding/workflows/${A.id}/steps/access/`)).data.step.form_fields[0].options[0] } });
    assert.equal(early.status, 400); assert(JSON.stringify(early.data).includes('Every job must succeed or be overridden before completing access'), JSON.stringify(early.data));
    check('IT completing access before the jobs succeed is refused (UI disabled with message; API 400)', JSON.stringify(early.data));
    report.attacks = [{ label: 'access before provisioning', status: early.status, body: early.data }];

    // Hire B: put two jobs into `dead` through MongoDB (provisioners are stopped).
    for (const connector of ['workspace', 'slack']) {
      const out = mongosh(`const r=db.workflow_instances.updateOne({_id:ObjectId('${B.id}')},{$set:{'provisioning.$[j].status':'dead','provisioning.$[j].last_error':'walkthrough: injected dead job'}},{arrayFilters:[{'j.connector':'${connector}','j.status':'pending'}]});print(r.matchedCount+':'+r.modifiedCount)`);
      assert.equal(out, '1:1', 'dead injection for ' + connector);
    }
    report.actions.push({ action: 'set workspace and slack jobs of hire B to dead through MongoDB', workflow: B.id });

    // Start provisioners: hire A's three jobs must become succeeded through the Go workers.
    compose('start', 'provisioner'); check('provisioner replicas started');
    await completeInUi(clients.hr, B, 'profile');
    await waitJobs(clients.it, A, [['succeeded'], ['succeeded'], ['succeeded']], 'hire A: three jobs succeeded through the provisioners');
    await clients.it.page.evaluate(() => window.scrollBy(0, 420)); await shot(clients.it.page, '06-provisioning-succeeded');
    const aJobs = (await clients.it.request('GET', `/onboarding/workflows/${A.id}/steps/access/`)).data.provisioning;
    assert(aJobs.every(j => j.status === 'succeeded' && j.external_ref), 'succeeded jobs carry an external ref');

    // Hire B: dead jobs are shown, IT retries one and overrides one with a reason; the third succeeds by itself.
    await waitJobs(clients.it, B, [['dead'], ['dead'], ['succeeded', 'pending', 'running', 'retry']], 'hire B: dead jobs visible in the panel');
    await clients.it.page.evaluate(() => window.scrollBy(0, 420)); await shot(clients.it.page, '05-provisioning-dead');
    assert(await clients.it.page.getByRole('button', { name: 'Retry slack' }).isEnabled());
    assert(await clients.it.page.getByRole('button', { name: 'Override slack' }).isDisabled(), 'override needs a reason');
    await clients.it.page.getByRole('button', { name: 'Retry workspace', exact: true }).click();
    await clients.it.page.getByRole('status').filter({ hasText: 'workspace: retry requested.' }).waitFor();
    await clients.it.page.getByLabel('Override reason for slack', { exact: true }).fill('Walkthrough: slack account created by hand');
    await clients.it.page.getByRole('button', { name: 'Override slack', exact: true }).click();
    await clients.it.page.getByRole('status').filter({ hasText: 'slack: override recorded.' }).waitFor();
    await waitJobs(clients.it, B, [['succeeded'], ['overridden'], ['succeeded']], 'hire B: retry succeeded, override recorded, third job succeeded');
    await clients.it.page.getByText('Walkthrough: slack account created by hand').waitFor();
    await clients.it.page.evaluate(() => window.scrollBy(0, 420)); await shot(clients.it.page, '07-provisioning-override');
    const bEvents = (await getWorkflow(clients.hr, B.id)).events.map(e => e.action);
    assert(bEvents.includes('provisioning.manual_retry') && bEvents.includes('provisioning.overridden'), 'retry and override events: ' + bEvents);
    // And the access step can now be completed for B from IT's form.
    await completeInUi(clients.it, B, 'access');
    check('hire B: IT completed access after retry and override');

    // Hire A: every role completes its steps through the UI, in dependency order.
    let dblDone = false, first = true;
    for (let guard = 0; guard < 40; guard++) {
      const w = await getWorkflow(clients.hr, A.id);
      if (w.steps.every(s => s.state === 'done')) break;
      const s = w.steps.find(s => s.state === 'ready'); assert(s, 'No ready step: deadlock ' + JSON.stringify(w.steps.map(x => [x.key, x.state])));
      const c = s.owner_role === 'employee' ? empA : clients[s.owner_role];
      if (s.key === 'equipment') { // employee view of an IT task: no form
        await openTask(empA, A.id, 'equipment'); await empA.page.getByText(/must complete this task/).waitFor();
        assert.equal(await empA.page.locator('form.task-form').count(), 0);
      }
      const dbl = !dblDone && s.owner_role === 'hr' && s.key !== 'profile';
      const { posts } = await completeInUi(c, A, s.key, { dblclick: dbl, keepForm: s.key === 'contract' ? '08-task-form' : false });
      if (dbl) { dblDone = true; check(`duplicate click on Complete (${s.key}) produced exactly one completion`, `UI POSTs: ${posts}`); }
      first = false;
    }
    assert(dblDone, 'duplicate-click check ran');
    const finalA = await getWorkflow(clients.hr, A.id);
    assert(finalA.steps.every(s => s.state === 'done'));
    check('all 20 steps done through the UI by hr, it, manager and employee', finalA.steps.length);
    const owners = new Set(report.actions.filter(x => x.workflow === A.id && x.task).map(x => x.actor));
    assert.deepEqual([...owners].sort(), [userA, 'demo_hr', 'demo_it', 'demo_manager'].sort());
    assert.equal(finalA.events.filter(e => e.action === 'completed').length, 20);

    // Timeline: the UI shows every event, in the API's array order.
    const tl = (await clients.hr.request('GET', `/onboarding/workflows/${A.id}/timeline/`)).data.events;
    assert.equal(tl.length, finalA.events.length);
    await clients.hr.page.goto(base + '/workspace'); await clients.hr.page.locator('.person').filter({ hasText: userA }).click();
    await clients.hr.page.getByText('20/20 complete', { exact: true }).waitFor();
    const region = clients.hr.page.getByRole('region', { name: 'Timeline' });
    await region.locator('.event-list > div').first().waitFor();
    const uiLines = await region.locator('.event-list > div strong').allInnerTexts();
    assert.deepEqual(uiLines, tl.map(e => e.detail || e.action));
    const uiSmall = await region.locator('.event-list > div small').allInnerTexts();
    assert.deepEqual(uiSmall.map(t => t.split(' · ').pop()), tl.map(e => e.action));
    check('timeline lists every event in order', `${uiLines.length} events`);
    await region.scrollIntoViewIfNeeded(); await shot(clients.hr.page, '09-timeline');

    // Final sign-off marks the workflow ready.
    assert(finalA.steps.find(s => s.key === 'ready').state === 'done');
    console.log('final workflow status:', finalA.status); report.finalStatus = finalA.status;
    assert.equal(finalA.status, 'ready');
    await clients.hr.page.evaluate(() => window.scrollTo(0, 0)); await shot(clients.hr.page, '10-ready-state');
    check('final sign-off marked the workflow ready', finalA.status);

    // Role views of the same workflow, and logout/login identity switch.
    await openTask(clients.manager, A.id, 'manager_signoff'); await shot(clients.manager.page, '11-manager-task-done');
    await openTask(empA, A.id, 'ready'); await shot(empA.page, '12-employee-view');
    await empA.page.getByRole('button', { name: 'Logout' }).click();
    await empA.page.locator('[name=username]').fill('demo_it'); await empA.page.locator('[name=password]').fill(password);
    await empA.page.getByRole('button', { name: 'Sign In', exact: true }).click(); await empA.page.waitForURL('**/workspace'); await empA.page.getByText('demo_it', { exact: true }).waitFor();
    check('logout then login replaces the identity in the same browser');
    await clients.hr.page.goto(base + '/does-not-exist'); await clients.hr.page.getByRole('heading', { name: 'Page not found' }).waitFor();

    report.workflows = { A: A.id, B: B.id }; report.employees = { A: userA, B: userB };
    assert.deepEqual(report.errors, []); report.result = 'PASS';
  } catch (e) {
    report.result = 'FAIL'; report.failure = e.stack; console.error(e); process.exitCode = 1;
    for (const [role, c] of Object.entries(clients)) await c.page.screenshot({ path: `${reportDir}/failure-${role}.png` }).catch(() => {});
    try { compose('start', 'provisioner'); } catch {}
  } finally {
    report.finished = new Date().toISOString(); fs.writeFileSync(reportDir + '/report.json', JSON.stringify(report, null, 2)); console.log('Report:', reportDir + '/report.json', report.result); await browser.close();
  }
})();
