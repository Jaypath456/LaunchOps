const { chromium } = require('playwright');
const fs = require('fs');
const assert = require('assert/strict');
const base = 'http://localhost:33000';
const result = {checks: [], errors: []};
const fixture = JSON.parse(fs.readFileSync('docs/review/live-fixture.json'));
(async () => {
  const browser = await chromium.launch({executablePath:'/usr/bin/google-chrome',args:['--no-sandbox']});
  try {
    async function page(username) {
      const context = await browser.newContext();
      context.setDefaultTimeout(60000);
      context.setDefaultNavigationTimeout(120000);
      await context.addInitScript(() => {
        const WS = window.WebSocket;
        window.reviewSockets = [];
        window.WebSocket = class extends WS {
          constructor(...args) { super(...args); window.reviewSockets.push(this); }
        };
      });
      const p = await context.newPage();
      p.on('pageerror', e => result.errors.push(e.message));
      await p.goto(base);
      await p.locator('[name=username]').fill(username);
      await p.locator('[name=password]').fill('password123');
      await p.getByRole('button',{name:'Sign In',exact:true}).click();
      await p.waitForURL(/instructor\/dashboard|student\/catalog/);
      return p;
    }
    const host = await page('demo_instructor');
    const first = await page('loadtest_student_0000');
    const second = await page('loadtest_student_0001');
    await host.goto(`${base}/instructor/quizzes/${fixture.quiz}/live`);
    await host.getByRole('button',{name:'Create Live Session'}).click();
    const code = await host.locator('.live-room-code-display').innerText();
    await first.goto(`${base}/student/live/${code}`);
    await second.goto(`${base}/student/live/${code}`);
    await first.getByText('Waiting for the instructor...').waitFor();
    await second.getByText('Waiting for the instructor...').waitFor();
    await host.getByRole('button',{name:'Start Session',exact:true}).click();
    await host.getByRole('button',{name:'Reveal First Question'}).click();
    for (const p of [first,second]) {
      await p.getByLabel('B',{exact:true}).check();
      await p.getByRole('button',{name:'Submit Answer'}).click();
      await p.getByRole('heading',{name:'Correct!',exact:true}).waitFor();
    }
    await host.getByText('2 response(s)',{exact:true}).waitFor();
    result.checks.push('Instructor creates/starts room; lobby students receive question and private grading; host chart reaches 2');
    await first.evaluate(() => window.reviewSockets.at(-1).close());
    await first.getByRole('button',{name:'Submit Answer'}).waitFor();
    await first.getByLabel('A',{exact:true}).check();
    await first.getByRole('button',{name:'Submit Answer'}).click();
    await first.getByRole('heading',{name:'Correct!',exact:true}).waitFor();
    result.checks.push('Student automatically reconnects; changed duplicate preserves original correctness');
    // Drop the host connection, then end from another authenticated socket
    // before its reconnect. It must recover the ended snapshot and scores.
    await host.evaluate(async code => {
      window.reviewSockets.at(-1).close();
      await new Promise((resolve,reject) => {
        const socket = new WebSocket(`ws://localhost:39090/ws/live/${code}/`,[sessionStorage.access_token]);
        socket.onopen = () => socket.send(JSON.stringify({type:'session.end'}));
        socket.onmessage = event => { if (JSON.parse(event.data).type === 'session.ended') { socket.close(); resolve(); } };
        socket.onerror = reject;
      });
    }, code);
    await host.getByText(`Session ${code} has ended.`,{exact:true}).waitFor({timeout:15000});
    await host.getByRole('heading',{name:'Saved live results'}).waitFor();
    for (const p of [first,second]) {
      await p.getByText('Your saved live result: 2 points across 1 answers.').waitFor();
    }
    result.checks.push('Missed end event recovered after host reconnect; instructor and students display Mongo scores');
    assert.deepEqual(result.errors,[]);
    await host.screenshot({path:'docs/review/live-host.png',fullPage:true});
    await first.screenshot({path:'docs/review/live-student.png',fullPage:true});
    result.result = 'PASS';
  } catch (error) {
    result.result = 'FAIL'; result.failure = error.stack; process.exitCode = 1;
    result.pages = await Promise.all(browser.contexts().flatMap(c => c.pages()).map(async p => ({
      url: p.url(), text: (await p.locator('body').innerText().catch(() => '')).slice(0, 1500),
    })));
  } finally {
    fs.writeFileSync('docs/review/live-browser.json',JSON.stringify(result,null,2));
    console.log(JSON.stringify(result,null,2));
    await browser.close();
  }
})();
