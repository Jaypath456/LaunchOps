/* Run against the local Docker demo: NODE_PATH=/tmp/launchops-browser/node_modules node scripts/walkthrough.cjs
 * Creates an explicitly named audit employee and completes demo/audit workflows; never deletes data.
 */
const { chromium } = require('playwright');
const fs = require('fs');
const assert = require('assert/strict');
const base = process.env.LAUNCHOPS_URL || 'http://localhost:13000';
const api = process.env.LAUNCHOPS_API || 'http://localhost:18000/api';
const password = 'LaunchOpsDemo2026!';
const report = { started: new Date().toISOString(), pages: [], actions: [], attacks: [], errors: [], timings: [] };
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN || '/usr/bin/google-chrome', headless: true, args: ['--no-sandbox'] });
  const clients = {};
  async function login(role, username) {
    const context = await browser.newContext(); const page = await context.newPage(); page.setDefaultTimeout(60000);
    page.on('pageerror', e => report.errors.push(`${username}: ${e.message}`));
    await page.goto(base); await page.locator('[name=username]').fill(username); await page.locator('[name=password]').fill(password);
    await page.getByRole('button', { name: 'Sign In', exact: true }).click(); await page.waitForURL('**/workspace'); await page.locator('.ops-hero').waitFor();
    const token = await page.evaluate(() => sessionStorage.access_token);
    const request = async (method, path, data) => {
      const start = Date.now(); const res = await context.request.fetch(api + path, { method, headers: { Authorization: 'Bearer ' + token }, data });
      report.timings.push({ method, path, ms: Date.now() - start, status: res.status() });
      return { status: res.status(), data: await res.json() };
    };
    return { context, page, request, username, role };
  }
  try {
    for (const role of ['hr', 'it', 'manager', 'employee']) clients[role] = await login(role, 'demo_' + role);
    const hr = clients.hr, it = clients.it;
    let workflows = (await hr.request('GET', '/onboarding/workflows/')).data;
    const demo = workflows.find(w => w.employee === 'demo_employee');
    // Read every migrated and demo task visible to each role, without changing the user's workflow.
    for (const role of process.env.SKIP_INSPECTION ? [] : Object.keys(clients)) {
      const c = clients[role]; const visible = (await c.request('GET', '/onboarding/workflows/')).data;
      for (const w of visible.filter(w => w.id <= 2)) for (const s of w.steps) {
        await c.page.goto(`${base}/workspace/tasks/${s.id}`); await c.page.locator('h1').waitFor();
        report.pages.push({ role, workflow: w.id, task: s.key, heading: await c.page.locator('h1').innerText(), status: await c.page.locator('.ops-hero .status').innerText() });
      }
      console.log('Inspected tasks for', role);
    }
    // Registration and HR workflow creation exercise the actual forms.
    const username = process.env.REUSE_AUDIT_EMPLOYEE || 'audit_employee_' + Date.now();
    if (!process.env.REUSE_AUDIT_EMPLOYEE) {
    const context = await browser.newContext(); const page = await context.newPage(); await page.goto(base + '/register');
    await page.locator('[name=username]').fill(username); await page.locator('[name=email]').fill(username + '@example.test');
    await page.locator('[name=password]').fill(password);
    await page.locator('button[type=submit]').click(); await page.waitForURL('**/workspace'); await context.close();
    }
    const fresh = await login('employee', username);
    if (!process.env.REUSE_AUDIT_EMPLOYEE) {
    await hr.page.goto(base + '/workspace'); await hr.page.getByRole('button', { name: '+ Start onboarding' }).click();
    await hr.page.getByLabel('New hire', { exact: true }).selectOption({ label: username });
    await hr.page.getByLabel('Manager', { exact: true }).selectOption({ label: 'demo_manager' });
    await hr.page.getByLabel('Job title', { exact: true }).fill('Audit engineer'); await hr.page.getByLabel('Department', { exact: true }).fill('Product audit');
    await hr.page.getByLabel('Start date', { exact: true }).fill(new Date().toISOString().slice(0,10));
    await hr.page.locator('.launch-form').getByLabel('Learning track', { exact: true }).selectOption({ label: 'Security essentials' });
    await hr.page.getByRole('button', { name: 'Launch workflow →' }).click(); await hr.page.getByRole('status').filter({ hasText: 'Onboarding started' }).waitFor();
    }
    workflows = (await hr.request('GET', '/onboarding/workflows/')).data;
    const newWorkflow = workflows.find(w => w.employee === username); assert(newWorkflow);
    const migratedWorkflow = workflows.find(w => w.employee === process.env.AUDIT_MIGRATED_EMPLOYEE);
    const migratedEmployee = migratedWorkflow ? await login('employee', migratedWorkflow.employee) : null;
    const migratedManager = migratedWorkflow ? await login('manager', migratedWorkflow.manager) : null;
    if (migratedWorkflow?.date_needs_correction) {
      await hr.page.goto(base + '/workspace'); await hr.page.locator('.person').filter({hasText:migratedWorkflow.employee}).click();
      await hr.page.getByRole('button',{name:'Correct start date',exact:true}).click();
      await hr.page.getByLabel('Correct start date',{exact:true}).fill(new Date().toISOString().slice(0,10));
      await hr.page.getByRole('button',{name:'Save date',exact:true}).click(); await hr.page.getByRole('status').filter({hasText:'Start date and task due dates updated'}).waitFor();
      report.actions.push({workflow:migratedWorkflow.id,action:'HR corrected copied year-1111 workflow through UI'});
    }
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
    if (migratedWorkflow) {
      await attack(clients.manager,'GET',`/onboarding/workflows/${migratedWorkflow.id}/`,undefined,404,'Other manager workflow');
      await attack(clients.manager,'POST',`/onboarding/steps/${migratedWorkflow.steps.find(s=>s.key==='mentor').id}/complete/`,{evidence:{}},403,'Other manager completion');
    }
    // Complete all tasks from their role-specific browser forms, including a failing quiz then retry.
    for (const initial of [demo, newWorkflow, migratedWorkflow].filter(Boolean)) {
      const employee = initial.id === demo.id ? clients.employee : initial.id === newWorkflow.id ? fresh : migratedEmployee;
      let w = (await hr.request('GET', `/onboarding/workflows/${initial.id}/`)).data;
      // Blueprint order differs on migrated workflows; repeatedly select ready tasks.
      while (w.steps.some(s => s.state !== 'completed')) {
        const s = w.steps.find(s => s.state === 'ready'); assert(s, 'No ready task: deadlock');
        const c = s.owner_role === 'employee' ? employee : s.owner_role === 'manager' && initial.id === migratedWorkflow?.id ? migratedManager : clients[s.owner_role];
        await c.page.goto(`${base}/workspace/tasks/${s.id}`); await c.page.locator('h1').waitFor();
        const task = (await c.request('GET', `/onboarding/steps/${s.id}/`)).data;
        if (s.key === 'equipment') {
          await employee.page.goto(`${base}/workspace/tasks/${s.id}`); await employee.page.getByText('Waiting for IT', { exact:true }).waitFor();
          assert.equal(await employee.page.locator('form.task-form').count(),0);
          await employee.page.screenshot({path:'docs/audit/employee-waiting.png',fullPage:true});
        }
        if (s.key === 'training') {
          for (const lesson of task.lessons) {
            await c.page.goto(`${base}/student/chapters/${lesson.id}`); await c.page.getByRole('button',{name:/Acknowledge this lesson|Lesson acknowledged/}).waitFor();
            if (!lesson.reviewed) await c.page.getByRole('button',{name:'Acknowledge this lesson',exact:true}).click();
            await c.page.getByRole('button',{name:'Lesson acknowledged',exact:true}).waitFor();
            report.actions.push({ workflow:w.id,lesson:lesson.id,action:'acknowledged in browser' });
          }
          for (const [index, q] of task.assessments.entries()) {
            const current = await c.request('GET', `/quizzes/${q.id}/my-result/`);
            if (current.status === 200 && current.data.passed) continue;
            if (current.status === 200) { await c.page.goto(`${base}/student/quizzes/${q.id}/result`); await c.page.getByRole('button',{name:'Retry assessment'}).click(); }
            await c.page.goto(`${base}/student/quizzes/${q.id}/take`); await c.page.locator('.quiz-question-card').first().waitFor();
            if (index === 0 && initial.id === newWorkflow.id) {
              // Intentionally leave all unanswered, then exercise retry navigation.
              await c.page.getByRole('button',{name:'Submit Quiz',exact:true}).click(); await c.page.getByRole('button',{name:'Retry assessment'}).click(); await c.page.locator('.quiz-question-card').first().waitFor();
            }
            const questions = await c.page.locator('.quiz-question-card').all();
            const safe = /Report it to IT without opening it|Report it without opening it|A unique password|Multi-factor authentication|Report to IT|Ask IT to inspect it|Tell IT promptly|Lock the screen|Report it to IT immediately|Apply the update|An approved work system|Only the access they need|Report the incident promptly|Refuse and alert IT|The time and what happened|An approved IT channel/;
            for (const card of questions) await card.locator('label').filter({hasText:safe}).locator('input').check();
            await c.page.getByRole('button',{name:'Submit Quiz',exact:true}).click(); await c.page.getByText(/100% · Passed/).waitFor();
            report.actions.push({workflow:w.id,quiz:q.id,action:'passed in browser'});
          }
          await c.page.goto(`${base}/workspace/tasks/${s.id}`); await c.page.locator('h1').waitFor();
        }
        const values = {};
        for (const field of task.step.form_fields) {
          const input = c.page.locator('.task-form').getByLabel(field.label,{exact:true});
          const value = field.type === 'checkbox' ? true : field.type === 'select' ? field.options[0] : field.type === 'date' ? new Date().toISOString().slice(0,10) : 'Audit record: discuss the first-week delivery plan with the assigned team.';
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
    assert.deepEqual(report.errors,[]); report.result='PASS';
  } catch(e) { report.result='FAIL'; report.failure=e.stack; console.error(e); for (const [role,c] of Object.entries(clients)) { await c.page.screenshot({path:`docs/audit/failure-${role}.png`,fullPage:true}).catch(()=>{}); } process.exitCode=1; }
  finally { report.finished=new Date().toISOString();fs.writeFileSync('docs/audit/walkthrough.json',JSON.stringify(report,null,2));await browser.close(); }
})();
