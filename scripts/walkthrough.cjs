/* Run against the local Docker demo: NODE_PATH=/tmp/launchops-browser/node_modules node scripts/walkthrough.cjs
 * Creates a fresh audit employee and completes only its workflow; never modifies existing workflows or deletes data.
 */
const { chromium } = require('playwright');
const { execFileSync } = require('child_process');
const fs = require('fs');
const assert = require('assert/strict');
const base = process.env.LAUNCHOPS_URL || 'http://localhost:13000';
const api = process.env.LAUNCHOPS_API || 'http://localhost:18000/api';
for (const url of [base, api]) assert(['localhost', '127.0.0.1', '[::1]'].includes(new URL(url).hostname), 'Walkthrough is local-only');
const password = process.env.LAUNCHOPS_DEMO_PASSWORD || 'LaunchOpsDemo2026!';
const reportDir = process.env.LAUNCHOPS_REPORT_DIR || '/tmp/launchops-walkthrough-' + Date.now();
fs.mkdirSync(reportDir, { recursive: true });
const report = { started: new Date().toISOString(), pages: [], actions: [], attacks: [], errors: [], timings: [] };
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN || (process.platform === 'darwin' ? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' : '/usr/bin/google-chrome'), headless: true, args: ['--no-sandbox'] });
  const clients = {};
  async function login(role, username, accountPassword = password) {
    const context = await browser.newContext(); const page = await context.newPage(); page.setDefaultTimeout(60000);
    page.on('pageerror', e => report.errors.push(`${username}: ${e.message}`));
    await page.goto(base); await page.locator(`a[href="/login/${role}"]`).click(); await page.locator('[name=username]').fill(username); await page.locator('[name=password]').fill(accountPassword);
    await page.getByRole('button', { name: 'Sign In', exact: true }).click(); await page.waitForURL('**/workspace'); await page.locator('.ops-hero').waitFor();
    report.actions.push({ action: 'role login through landing page', role, username, path: '/login/' + role });
    const request = async (method, path, data) => {
      const token = await page.evaluate(() => sessionStorage.access_token);
      const start = Date.now(); const res = await context.request.fetch(api + path, { method, headers: { Authorization: 'Bearer ' + token }, data });
      report.timings.push({ method, path, ms: Date.now() - start, status: res.status() });
      return { status: res.status(), data: await res.json() };
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
  try {
    for (const role of ['hr', 'it', 'manager', 'employee']) clients[role] = await login(role, 'demo_' + role);
    const hr = clients.hr, it = clients.it;
    let workflows = await workflowsFor(hr);
    const demo = workflows.find(w => w.employee === 'demo_employee');
    // Read every demo task visible to each role, without changing the user's workflow.
    for (const role of process.env.SKIP_INSPECTION ? [] : Object.keys(clients)) {
      const c = clients[role]; const visible = await workflowsFor(c);
      for (const w of visible.filter(w => w.id <= 2)) for (const s of w.steps) {
        await c.page.goto(`${base}/workspace/tasks/${s.id}`); await c.page.locator('h1').waitFor();
        report.pages.push({ role, workflow: w.id, task: s.key, heading: await c.page.locator('h1').innerText(), status: await c.page.locator('.ops-hero .status').innerText() });
      }
      console.log('Inspected tasks for', role);
    }
    // Seed a fresh account; HR still launches its workflow through the actual form.
    const username = 'audit_employee_' + Date.now();
    execFileSync('docker', ['compose', 'exec', '-T', 'backend', 'python', 'manage.py', 'seed_launchops', '--walkthrough-employee', username], { stdio: 'pipe' });
    const fresh = await login('employee', username, 'LaunchOpsDemo2026!');
    await hr.page.goto(base + '/workspace'); await hr.page.getByRole('button', { name: '+ Start onboarding' }).click();
    await hr.page.getByLabel('New hire', { exact: true }).selectOption({ label: username });
    await hr.page.getByLabel('Manager', { exact: true }).selectOption({ label: 'demo_manager' });
    await hr.page.getByLabel('Job title', { exact: true }).fill('Audit engineer'); await hr.page.getByLabel('Department', { exact: true }).fill('Product audit');
    await hr.page.getByLabel('Start date', { exact: true }).fill(new Date().toISOString().slice(0,10));
    await hr.page.getByRole('button', { name: 'Launch workflow →' }).click(); await hr.page.getByRole('status').filter({ hasText: 'Onboarding started' }).waitFor();
    workflows = await workflowsFor(hr);
    const newWorkflow = workflows.find(w => w.employee === username); assert(newWorkflow);
    report.createdWorkflow = newWorkflow.id; report.createdEmployee = username;
    // Direct hostile requests before dependencies are satisfied.
    const attack = async (client, method, path, data, status, label) => {
      const r = await client.request(method, path, data); assert.equal(r.status, status, label + JSON.stringify(r.data)); report.attacks.push({ label, status });
    };
    await attack(fresh, 'GET', `/onboarding/workflows/${demo.id}/`, undefined, 404, 'Other employee workflow');
    await attack(clients.employee, 'POST', `/onboarding/steps/${newWorkflow.steps.find(s=>s.key==='contact').id}/complete/`, { evidence: {} }, 403, 'Other employee completion');
    if (newWorkflow.steps.find(s=>s.key==='profile').state !== 'completed') await attack(it, 'POST', `/onboarding/steps/${newWorkflow.steps.find(s=>s.key==='equipment').id}/complete/`, { evidence: { device:'Demo laptop',delivery_date:'2026-09-27' } }, 400, 'Skipped prerequisites');
    await attack(fresh, 'POST', `/onboarding/steps/${newWorkflow.steps.find(s=>s.key==='equipment').id}/complete/`, { evidence:{} }, 403, 'Employee cannot complete IT work');
    if (newWorkflow.steps.find(s=>s.key==='profile').state !== 'completed') await attack(hr, 'POST', `/onboarding/steps/${newWorkflow.steps.find(s=>s.key==='profile').id}/complete/`, { evidence:{review:'Match demo profile',_proof:{mode:'verified'}} }, 400, 'Forged simulation proof');
    // Complete all tasks from their role-specific browser forms, including policy attestation.
    for (const initial of [newWorkflow]) {
      const employee = fresh;
      let w = (await hr.request('GET', `/onboarding/workflows/${initial.id}/`)).data;
      // Repeatedly select ready tasks to respect prerequisites.
      while (w.steps.some(s => s.state !== 'completed')) {
        const s = w.steps.find(s => s.state === 'ready'); assert(s, 'No ready task: deadlock');
        const c = s.owner_role === 'employee' ? employee : clients[s.owner_role];
        await c.page.goto(`${base}/workspace/tasks/${s.id}`); await c.page.locator('h1').waitFor();
        const task = (await c.request('GET', `/onboarding/steps/${s.id}/`)).data;
        if (s.key === 'equipment') {
          await employee.page.goto(`${base}/workspace/tasks/${s.id}`); await employee.page.getByText('Waiting for IT', { exact:true }).waitFor();
          assert.equal(await employee.page.locator('form.task-form').count(),0);
          await employee.page.screenshot({path:reportDir + '/employee-waiting.png',fullPage:true});
        }
        if (s.key === 'ready') {
          assert(task.issues.some(issue => !issue.resolved_at), 'Expected a reported blocker');
          const blockedEvidence = Object.fromEntries(task.step.form_fields.map(field => [field.name, field.type === 'checkbox' ? true : 'Audit review']));
          await attack(hr, 'POST', `/onboarding/steps/${s.id}/complete/`, { evidence: blockedEvidence }, 400, 'Final signoff blocked by unresolved issue');
          await c.page.getByLabel('Resolution (no sensitive details)', {exact:true}).fill('Demo support discussion completed with the employee.');
          await c.page.getByRole('button', {name:'Record resolution',exact:true}).click();
          await c.page.getByText('Resolved (HR self-report)',{exact:true}).waitFor();
          report.actions.push({workflow:w.id,action:'HR resolved reported blocker in browser'});
        }
        const values = {};
        for (const field of task.step.form_fields) {
          const input = c.page.locator('.task-form').getByLabel(field.label,{exact:true});
          const value = field.type === 'checkbox' ? true : field.type === 'select' ? (s.key === 'feedback' && field.name === 'status' ? 'Needs support' : field.options[0]) : field.type === 'date' ? new Date().toISOString().slice(0,10) : 'Audit record: discuss the first-week delivery plan with the assigned team.';
          values[field.name] = value;
          if (field.type === 'checkbox') await input.check(); else if (field.type === 'select') await input.selectOption(value); else await input.fill(value);
        }
        await c.page.locator('.task-form button').click(); await c.page.getByRole('status').filter({hasText:'Task completed'}).waitFor();
        const retry = await c.request('POST', `/onboarding/steps/${s.id}/complete/`, { evidence: values }); assert.equal(retry.status,200); assert.equal(retry.data.changed,false);
        const saved = (await c.request('GET', `/onboarding/steps/${s.id}/`)).data; assert(saved.step.evidence._proof); assert.equal(saved.step.completed_by,c.username);
        w = (await hr.request('GET', `/onboarding/workflows/${w.id}/`)).data;
        assert.equal(w.events.filter(e=>e.step===s.key && e.action==='completed').length,1);
        report.actions.push({workflow:w.id,task:s.key,actor:c.username,mode:saved.step.evidence._proof.mode,action:'UI → API → persisted evidence + single event'});
        console.log('Completed',w.id,s.key);
      }
      await hr.page.goto(base+'/workspace'); await hr.page.locator('.person').filter({hasText:w.employee}).click(); await hr.page.getByText('20/20 complete',{exact:true}).waitFor();
    }
    // Logout and login in the same browser must replace identity and view.
    await clients.employee.page.getByRole('button',{name:'Logout'}).click(); await clients.employee.page.locator('[name=username]').fill('demo_it'); await clients.employee.page.locator('[name=password]').fill(password); await clients.employee.page.getByRole('button',{name:'Sign In',exact:true}).click(); await clients.employee.page.waitForURL('**/workspace'); await clients.employee.page.getByText('demo_it',{exact:true}).waitFor();
    await hr.page.goto(base+'/does-not-exist'); await hr.page.getByRole('heading',{name:'Page not found'}).waitFor();
    const final = (await hr.request('GET', `/onboarding/workflows/${newWorkflow.id}/`)).data;
    assert.equal(final.steps.filter(step => step.state === 'completed').length,20);
    assert.equal(final.events.filter(event => event.action === 'completed').length,20);
    report.completedTasks=20; report.completionEvents=20;
    assert.deepEqual(report.errors,[]); report.result='PASS';
  } catch(e) { report.result='FAIL'; report.failure=e.stack; console.error(e); for (const [role,c] of Object.entries(clients)) { await c.page.screenshot({path:`${reportDir}/failure-${role}.png`,fullPage:true}).catch(()=>{}); } process.exitCode=1; }
  finally { report.finished=new Date().toISOString();fs.writeFileSync(reportDir + '/walkthrough.json',JSON.stringify(report,null,2));console.log('Report:',reportDir + '/walkthrough.json');await browser.close(); }
})();
