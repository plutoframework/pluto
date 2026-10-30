"""Record a real public health-endpoint walkthrough in a disposable project."""
from pathlib import Path
import json
import os
import signal
import socket
import subprocess
import tempfile
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='pluto-readme-demo-') as directory:
    workspace = Path(directory).resolve()
    events = []

    def run(display, args, cwd=workspace):
        started = time.monotonic()
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True)
        output = result.stdout.replace(str(workspace), '.')
        events.append(dict(command=display, output=output.strip(), seconds=round(time.monotonic() - started, 2)))
        return result

    run('pluto version', ['pluto', 'version'])
    run('pluto --no-input init orbit \\\n  --module example.com/orbit --env-prefix ORBIT_ \\\n  --no-docker --no-mise --no-fnox',
        ['pluto', '--no-input', 'init', 'orbit', '--module', 'example.com/orbit',
         '--env-prefix', 'ORBIT_', '--no-docker', '--no-mise', '--no-fnox'])
    app = workspace / 'orbit'
    events.append(dict(command='cd orbit', output='', seconds=0))
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    env = {**os.environ, 'ORBIT_SERVER_ADDRESS': f'127.0.0.1:{port}', 'GOWORK': 'off'}
    process = subprocess.Popen(['go', 'run', './cmd/api'], cwd=app, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=True)
    try:
        started = time.monotonic()
        deadline = started + 90
        while True:
            try:
                with urlopen(f'http://127.0.0.1:{port}/healthz', timeout=1) as response:
                    assert response.status == 200
                break
            except Exception:
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('Demo API did not become ready')
                time.sleep(.1)
        events.append(dict(command=f'ORBIT_SERVER_ADDRESS=127.0.0.1:{port} go run ./cmd/api &',
                           output='', seconds=round(time.monotonic() - started, 2)))
        result = run(f'curl -fsS -w "\\nHTTP %{{http_code}}\\n" http://127.0.0.1:{port}/healthz',
                     ['curl', '-fsS', '-w', '\nHTTP %{http_code}\n', f'http://127.0.0.1:{port}/healthz'], cwd=app)
        assert 'HTTP 200' in result.stdout
        transcript = ['# Pluto: CLI to a working health endpoint', '',
                      'Recorded with the public CLI; temporary paths are normalized to `.`.',
                      'The GIF shortens compilation waits. This is the built-in `/healthz` route;',
                      'generated business operations still need implementation and authorization.', '',
                      '[Static image](../assets/readme/demo.png) · [Animated demo](../assets/readme/demo.gif)', '', '```console']
        for event in events:
            transcript += ['$ ' + event['command'], event['output']]
        transcript += ['```', '']
        (ROOT / 'assets/readme/demo.json').write_text(json.dumps(events, indent=2) + '\n')
        (ROOT / 'docs/DEMO.md').write_text('\n'.join(transcript))
        print('PASS: generated API returned HTTP 200; transcript saved')
    finally:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
