"""Chạy checkpoint bằng đúng môi trường; không hiển thị API key."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent


def python_in(directory: Path) -> str:
    return str(directory / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python'))


def request(url: str, payload: dict, headers: dict | None = None):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json', **(headers or {})})
    with urllib.request.urlopen(req, timeout=120) as response:
        return json.load(response)


def auth_fail() -> None:
    payload = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
               'params': {'protocolVersion': '2025-03-26', 'capabilities': {},
                          'clientInfo': {'name': 'lab-auth-check', 'version': '1.0'}}}
    for label, headers in [('missing token', {}), ('invalid token', {'Authorization': 'Bearer invalid-lab-token'})]:
        try:
            request('http://localhost:8000/mcp', payload,
                    {'Accept': 'application/json, text/event-stream', **headers})
        except urllib.error.HTTPError as error:
            print(f'{label}: HTTP {error.code} (access denied)', flush=True)
            assert error.code in (401, 403), 'Unexpected rejection status'
        else:
            raise RuntimeError('Server accepted an unauthorized request')
    print('PASS: missing and invalid tokens cannot access MCP tools', flush=True)


def adk_check(kind: str) -> None:
    session_id = 'lab-' + kind + '-' + uuid.uuid4().hex[:8]
    base = 'http://localhost:8000'
    request(f'{base}/apps/weather_agent/users/lab-user/sessions/{session_id}', {})
    prompt = ('Use get_current_weather for Hanoi and report the actual temperature, humidity, condition and last updated time.'
              if kind == 'current' else
              'Use get_forecast for Hanoi with days=3 and report all three dates, high/low temperatures and rain probability.')
    print('User: ' + prompt, flush=True)
    events = request(base + '/run', {'appName': 'weather_agent', 'userId': 'lab-user',
                     'sessionId': session_id, 'newMessage': {'role': 'user', 'parts': [{'text': prompt}]}})
    expected = 'get_current_weather' if kind == 'current' else 'get_forecast'
    calls, responses, answers = [], [], []
    for event in events:
        if event.get('errorCode'):
            raise RuntimeError('ADK event failed: ' + str(event['errorCode']))
        for part in event.get('content', {}).get('parts', []):
            if 'functionCall' in part:
                call = part['functionCall']; calls.append(call['name'])
                print('Tool call: ' + json.dumps(call, ensure_ascii=False), flush=True)
            if 'functionResponse' in part:
                response = part['functionResponse']; responses.append(response)
                print('Tool response: ' + json.dumps(response, ensure_ascii=False), flush=True)
            if part.get('text'):
                answers.append(part['text']); print('Agent: ' + part['text'], flush=True)
    assert expected in calls, 'Expected MCP tool was not called'
    assert any(r.get('name') == expected for r in responses), 'Missing tool result'
    assert answers, 'Missing agent answer'
    raw = json.dumps(responses)
    assert 'Unable to fetch' not in raw and 'not configured' not in raw, 'WeatherAPI failed'
    print(f'PASS: ADK called {expected} through MCP and returned a final answer', flush=True)
    print('Session: ' + session_id, flush=True)
    (ROOT / 'artifacts').mkdir(exist_ok=True)
    (ROOT / 'artifacts' / f'04-adk-{kind}-events.json').write_text(
        json.dumps(events, ensure_ascii=False, indent=2), encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('checkpoint', choices=['01', '02', '03a-ok', '03a-fail', '03b', '03c',
                        '04-verify', '04-current', '04-forecast', 'auth-server', 'weather-server', 'adk-web'])
    parser.add_argument('--save', action='store_true', help='Lưu output thật vào artifacts/ đồng thời in ra terminal')
    args = parser.parse_args()
    os.environ['PYTHONUTF8'] = '1'
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    if args.save:
        if args.checkpoint in ('auth-server', 'weather-server', 'adk-web'):
            parser.error('--save chỉ dành cho checkpoint, không dành cho server')
        log_names = {'01': '01-function-calling', '02': '02-mcp-client',
                     '03a-ok': '03a-auth-ok', '03a-fail': '03a-auth-fail',
                     '03b': '03b-registry', '03c': '03c-versioning',
                     '04-verify': '04-verify-setup', '04-current': '04-adk-current',
                     '04-forecast': '04-adk-forecast'}
        (ROOT / 'artifacts').mkdir(exist_ok=True)
        with (ROOT / 'artifacts' / (log_names[args.checkpoint] + '.log')).open('w', encoding='utf-8') as log:
            with subprocess.Popen([sys.executable, '-u', str(Path(__file__).resolve()), args.checkpoint],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, encoding='utf-8', env=os.environ.copy()) as process:
                for line in process.stdout:
                    print(line, end='', flush=True); log.write(line); log.flush()
                return process.wait()
    if args.checkpoint == '03a-fail':
        auth_fail(); return 0
    if args.checkpoint in ('04-current', '04-forecast'):
        adk_check(args.checkpoint.split('-')[1]); return 0
    client = ROOT / '04-lab/mcp-client'
    server = ROOT / '04-lab/mcp-server'
    targets = {
        '01': (ROOT / '01-function-calling', ROOT, ['weather_function_calling.py']),
        '02': (ROOT / '02-mcp-basics', ROOT, ['weather_client.py']),
        '03a-ok': (ROOT / '03-production', ROOT, ['auth_client.py']),
        '03b': (ROOT / '03-production', ROOT, ['registry_client.py']),
        '03c': (ROOT / '03-production', ROOT, ['versioned_client.py']),
        'auth-server': (ROOT / '03-production', ROOT, ['auth_server.py']),
        'weather-server': (server, server, ['weather.py']),
        '04-verify': (client, client, ['verify_setup.py']),
        'adk-web': (client, client, ['-m', 'google.adk.cli', 'web', '--host', '127.0.0.1', '--port', '8000']),
    }
    cwd, envdir, command = targets[args.checkpoint]
    print('Checkpoint: ' + args.checkpoint, flush=True)
    return subprocess.call([python_in(envdir), *command], cwd=cwd, env=os.environ.copy())


if __name__ == '__main__':
    raise SystemExit(main())
