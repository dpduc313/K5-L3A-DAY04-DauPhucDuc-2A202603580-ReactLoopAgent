"""In trace của một case trong file eval (hoặc output của run.py) cho gọn để demo.

Cách dùng:
  python demo_trace.py eval_after.json H1          # một case trong file eval
  python demo_trace.py run_output.json             # output JSON của run.py
Thêm --full để in toàn bộ câu trả lời.
"""
import json
import sys

args = [a for a in sys.argv[1:] if a != '--full']
full = '--full' in sys.argv
data = json.load(open(args[0], encoding='utf-8'))

if 'cases' in data:
    case = next(c for c in data['cases'] if c['id'] == args[1].upper())
    result = case['result']
    print(f"Case {case['id']}  |  agent={data['config']['model']}  judge={data['config']['judge_model']}")
    print(f"Câu hỏi: {case['question']}\n")
else:
    case, result = None, data

for t in result['trace']:
    if t['event'] == 'model':
        print(f"[step {t['step']}] model  finish={t['finish']}  tool_calls={t['tool_calls']}")
    elif t['event'] == 'tool':
        r = t['result'] if isinstance(t['result'], dict) else {}
        if r.get('ok') is False:
            out = f"LỖI {r.get('error')}: {r.get('message', '')}"
        elif 'incidents' in r:
            out = f"overall={r['overall'].get('description')}  incidents={[i['id'] for i in r['incidents']]}"
        else:
            out = f"{r.get('name')}  status={r.get('status')}  url={r.get('url')}"
        print(f"         tool   {t['name']}({json.dumps(t['arguments'], ensure_ascii=False)})")
        print(f"                -> {out}")
    else:
        print(f"[step {t['step']}] {t['event']}  {json.dumps({k: v for k, v in t.items() if k not in ('event', 'step')}, ensure_ascii=False)}")

answer = result.get('answer') or ''
print(f"\nstatus: {result['status']}")
print('--- answer ---')
print(answer if full or len(answer) <= 1200 else answer[:1200] + '\n… (thêm --full để xem hết)')
if case:
    g = case['grade']
    print(f"\n--- grade: {g.get('score')}  mechanical violations: {(g.get('mechanical') or {}).get('violations')}")
    print('reason:', g.get('reason'))
