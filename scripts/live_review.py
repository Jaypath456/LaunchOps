"""Destructive fault tests ONLY for the fixed, isolated review project.
Run after seed_demo --students 100. Requires requests and websockets.
"""
import asyncio
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

import requests
import websockets

HTTP = 'http://localhost:38000'
A, B, GATEWAY = ['ws://localhost:' + str(p) for p in (39091, 39092, 39090)]
ROOT = Path(__file__).resolve().parents[1]
checks = []


def check(condition, label):
    checks.append({'check': label, 'passed': bool(condition)})
    print(('PASS ' if condition else 'FAIL ') + label, flush=True)
    (ROOT / 'docs/review/live-checks.json').write_text(json.dumps(checks, indent=2))
    if not condition:
        raise AssertionError(label)


def api(method, path, token=None, data=None, status=200):
    res = requests.request(method, HTTP + '/api' + path,
                           headers={'Authorization': f'Bearer {token}'} if token else {},
                           json=data, timeout=20)
    assert res.status_code == status, (path, res.status_code, res.text[:400])
    return res.json()


def login(username):
    return api('POST', '/auth/login/', data={'username': username, 'password': 'password123'})['access']


def compose(*args):
    return subprocess.check_output(['sh', str(ROOT / 'scripts/review-compose.sh'), *args], text=True)


async def receive(ws, kind=None):
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), 12))
        if kind is None or msg.get('type') == kind or 'error' in msg:
            return msg


async def connect(base, room, token):
    ws = await websockets.connect(f'{base}/ws/live/{room}/', subprotocols=[token])
    return ws, await receive(ws, 'session.state')


async def event(ws, kind, **fields):
    await ws.send(json.dumps({'type': kind, **fields}))


async def deny(base, room, token=None, origin=None):
    try:
        ws = await websockets.connect(f'{base}/ws/live/{room}/', subprotocols=[token] if token else [], origin=origin)
    except websockets.exceptions.InvalidStatus as exc:
        return exc.response.status_code
    await ws.close()
    return 101


async def main():
    host = login('demo_instructor')
    students = [login(f'loadtest_student_{n:04d}') for n in range(3)]
    outsider_name = 'review_out_' + uuid.uuid4().hex[:8]
    api('POST', '/auth/register/', data={'username': outsider_name, 'password': 'password123',
        'password_confirm': 'password123', 'email': outsider_name+'@example.com'}, status=201)
    outsider = login(outsider_name)
    courses = api('GET', '/courses/', host)['results']
    course = next(c['id'] for c in courses if c['title'] == 'Introduction to Python')
    quiz = api('POST', f'/courses/{course}/quizzes/create/', host,
               {'title': 'Review ' + uuid.uuid4().hex[:8], 'is_published': True}, 201)['id']
    questions = []
    for index, (kind, body, points, seconds) in enumerate([
        ('single_choice', {'options': [{'id':'a','text':'A'}, {'id':'b','text':'B'}], 'correct_option_ids':['b']}, 2, 60),
        ('multiple_choice', {'options': [{'id':'a','text':'A'}, {'id':'b','text':'B'}], 'correct_option_ids':['a','b']}, 3, 60),
        ('short_answer', {'correct_answer':'LaunchOps'}, 4, 60),
        ('single_choice', {'options': [{'id':'a','text':'A'}, {'id':'b','text':'B'}], 'correct_option_ids':['b']}, 1, 1),
    ]):
        body['prompt'] = [{'type':'p','children':[{'text': f'Review question {index+1}'}]}]
        questions.append(api('POST', f'/quizzes/{quiz}/questions/create/', host,
            {'question_type':kind,'body':body,'points':points,'order_index':index,'time_limit_seconds':seconds},201)['id'])
    def room(engine='go'):
        code = api('POST', f'/quizzes/{quiz}/sessions/', host, {'engine':engine},201)['room_code']
        api('POST', f'/sessions/{code}/start/', host)
        return code
    code, other = room(), room()
    (ROOT/'docs/review/live-fixture.json').write_text(json.dumps({'quiz':quiz,'room':code,'other_room':other}))
    for base in (A, B):
        check(await deny(base,code) == 401, base+' rejects unauthenticated connection')
        check(await deny(base,code,outsider) == 403, base+' rejects non-enrolled user')
        check(await deny(base,code,students[0],'http://evil.example') == 403, base+' rejects foreign Origin')
    api('GET', f'/sessions/{code}/live-results/', outsider, status=403)
    api('GET', f'/_internal/live/{code}/bootstrap/', students[0], status=403)
    api('POST', f'/sessions/{code}/start/', students[0], status=403)
    check(True,'REST result, service bootstrap and host lifecycle permission gates')
    h, _ = await connect(A,code,host)
    s0, _ = await connect(A,code,students[0])
    s1, _ = await connect(B,code,students[1])
    other_s, _ = await connect(B,other,students[2])
    for kind in ('question.advance','session.end'):
        await event(s1,kind)
        check('Only the host' in (await receive(s1)).get('error',''), 'student denied '+kind)
    await event(h,'question.advance')
    hm, m0, m1 = await asyncio.gather(receive(h,'question.revealed'),receive(s0,'question.revealed'),receive(s1,'question.revealed'))
    check(hm.get('correct_option_ids') == ['b'], 'host receives private answer key')
    check(m0 == m1 and 'correct_option_ids' not in json.dumps(m1), 'cross-worker reveal without student answer key')
    try:
        await asyncio.wait_for(other_s.recv(), .3)
        isolated = False
    except asyncio.TimeoutError:
        isolated = True
    check(isolated,'separate room receives no reveal')
    await event(h,'answer.submit',question_id=questions[0],answer=['b'])
    check('Host cannot' in (await receive(h)).get('error',''),'host cannot answer')
    for malformed in (['b','b'], ['forged'], {'answer':'b'}, None):
        await event(s1,'answer.submit',question_id=questions[0],answer=malformed)
        check('Invalid answer' in (await receive(s1)).get('error',''), 'malformed answer rejected '+repr(malformed))
    duplicate, snap = await connect(B,code,students[0])
    check(snap['question']['id']==questions[0] and 'correct_option_ids' not in json.dumps(snap),'cross-worker reconnect gets sanitized current question')
    check('chart' not in snap and 'leaderboard' not in snap,'student reconnect excludes host-only aggregates')
    await asyncio.gather(event(s0,'answer.submit',question_id=questions[0],answer=['b']),event(duplicate,'answer.submit',question_id=questions[0],answer=['b']))
    acks=await asyncio.gather(receive(s0),receive(duplicate))
    check(any(m.get('type')=='answer.accepted' for m in acks),'concurrent duplicate has accepted answer')
    await event(duplicate,'answer.submit',question_id=questions[0],answer=['a'])
    check((await receive(duplicate))['is_correct'] is True,'duplicate retry preserves first saved correctness')
    await event(s1,'answer.submit',question_id=questions[0],answer=['a'])
    check((await receive(s1))['is_correct'] is False,'incorrect answer saved privately')
    await s0.close()
    await duplicate.close()
    s0, snap = await connect(B,code,students[0])
    check(snap['question']['id']==questions[0], 'reconnect across workers preserves question')
    await event(h,'question.advance')
    await asyncio.gather(receive(h,'question.revealed'),receive(s0,'question.revealed'),receive(s1,'question.revealed'))
    for ws, value in ((s0,['b','a']),(s1,['a'])):
        await event(ws,'answer.submit',question_id=questions[1],answer=value)
        await receive(ws,'answer.accepted')
    check(True,'multiple-choice correct and partial answers accepted')
    # Stop only the review replica and reconnect the host through the gateway.
    compose('stop','go-live-a')
    try:
        h2, snap = await connect(GATEWAY,code,host)
        check(snap['question']['id']==questions[1], 'gateway reconnect after worker A stop preserves room')
        await event(h2,'question.advance')
        await asyncio.gather(receive(h2,'question.revealed'),receive(s0,'question.revealed'),receive(s1,'question.revealed'))
        for ws, value in ((s0,' launchops '),(s1,'wrong')):
            await event(ws,'answer.submit',question_id=questions[2],answer=value)
            await receive(ws,'answer.accepted')
        check(True,'surviving worker grades short answers after failover')
        await event(h2,'question.advance')
        await asyncio.gather(receive(h2,'question.revealed'),receive(s0,'question.revealed'),receive(s1,'question.revealed'))
        await asyncio.sleep(1.2)
        await event(s0,'answer.submit',question_id=questions[3],answer=['b'])
        check('not currently open' in (await receive(s0)).get('error',''),'late answer rejected')
        await event(h2,'session.end')
        await asyncio.gather(receive(h2,'session.ended'),receive(s0,'session.ended'),receive(s1,'session.ended'))
        await h2.close()
    finally:
        compose('start','go-live-a')
    result = api('GET',f'/sessions/{code}/live-results/',host)
    check(sorted((r['score'],r['answered']) for r in result['scores']) == [(0,3),(9,3)],'Mongo saved scores match 6 unique answers and point totals')
    mine = api('GET',f'/sessions/{code}/live-results/',students[0])
    check(len(mine['scores'])==1 and mine['scores'][0]['score']==9,'student results contain only own score')
    ended, state = await connect(B,code,students[0])
    check(state['status']=='ended','reconnect after end restores ended status')
    await ended.close()
    await asyncio.gather(h.close(),s0.close(),s1.close(),other_s.close())
    if '--skip-redis-outage' not in sys.argv:
        compose('stop','redis')
        try:
            check(await deny(B,other,students[0])==503,'Redis outage rejects new connections safely')
            saved=api('GET',f'/sessions/{code}/live-results/',host)
            check(saved==result,'Mongo results remain readable during Redis outage')
        finally:
            compose('start','redis')
    print('Completed live acceptance checks.',flush=True)


if __name__ == '__main__':
    asyncio.run(main())
