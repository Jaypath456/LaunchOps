"""Read-only durable grading checks in the fixed review project.
Usage: python scripts/verify_benchmark_records.py docs/review/live_comparison_verified.json
"""
import base64
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
records = []
for name in sys.argv[1:]:
    for sample in json.loads(Path(name).read_text())['runs']:
        records.append({key: sample[key] for key in ('engine', 'room_code', 'quiz_id', 'accepted_answers')})
encoded = base64.b64encode(json.dumps(records).encode()).decode()
code = '''import os,json,base64
from pymongo import MongoClient
from quizzes.models import LiveSession,Question,Submission
records=json.loads(base64.b64decode("PAYLOAD"))
client=MongoClient(os.environ['MONGO_URI'])
collection=client.launchops_live.live_answers
results=[]
for sample in records:
    session=LiveSession.objects.get(room_code=sample['room_code'])
    questions=list(Question.objects.filter(quiz_id=sample['quiz_id']))
    assert len(questions)==1
    question=questions[0]
    if sample['engine']=='go':
        rows=list(collection.find({'room':sample['room_code']}))
        identities={(row['question_id'],row['user_id']) for row in rows}
        correct=all(row['correct'] and row['points']==question.points and row['question_id']==question.id and json.loads(bytes(row['answer']))==['b'] for row in rows)
        score=sum(row['points'] for row in rows)
    else:
        rows=list(Submission.objects.filter(quiz_id=sample['quiz_id']).values('student_id','score','max_score','answers'))
        identities={row['student_id'] for row in rows}
        correct=all(row['score']==question.points and row['max_score']==question.points and row['answers']=={str(question.id):['b']} for row in rows)
        score=sum(row['score'] for row in rows)
    passed=(len(rows)==sample['accepted_answers'] and len(identities)==len(rows) and correct and session.status=='ended')
    results.append({**sample,'saved_records':len(rows),'unique_records':len(identities),'total_score':score,'expected_score':sample['accepted_answers']*question.points,'ended':session.status=='ended','passed':passed})
client.close()
print('REVIEW_RESULTS='+json.dumps(results))
'''.replace('PAYLOAD', encoded)
result = subprocess.run(['sh',str(ROOT/'scripts/review-compose.sh'),'exec','-T','backend','python','manage.py','shell'],input=code,text=True,capture_output=True,check=True)
line=next(line for line in result.stdout.splitlines() if line.startswith('REVIEW_RESULTS='))
checks=json.loads(line.split('=',1)[1])
path=ROOT/'docs/review/benchmark-saved-record-checks.json'
path.write_text(json.dumps(checks,indent=2))
print(f'{sum(check["passed"] for check in checks)}/{len(checks)} durable grading checks passed; {path}')
if not all(check['passed'] for check in checks):
    raise SystemExit(1)
