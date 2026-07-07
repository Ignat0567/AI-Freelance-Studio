import json, sys
with open('projects_state.json', encoding='utf-8') as f:
    ps = json.load(f)
pid = '2dcfa8fe-6fbc-4951-9184-870dcf9853f1'
p = ps['projects'].get(pid, {})
print('Status:', p.get('status'))
print('Error:', p.get('error', 'N/A'))
logs = p.get('logs', [])
print('Logs count:', len(logs))
for i, log in enumerate(logs):
    txt = log[:200] if isinstance(log, str) else str(log)[:200]
    txt = txt.encode('ascii', errors='replace').decode('ascii')
    print(f"{i:3d}: {txt}")
