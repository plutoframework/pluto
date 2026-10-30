"""Execute the short README examples against normal public package releases."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
checks = [
    ('pluto-core', 'github.com/plutoframework/pluto-core@v0.4.0', '10'),
    ('pluto-auth', 'github.com/plutoframework/pluto-auth@v0.3.0', 'Session manager ready'),
    ('pluto-jobs', 'github.com/plutoframework/pluto-jobs@v0.1.1', 'purchase.submitted'),
]


def check(item):
    name, module, expected = item
    source = re.search(r'```go\n(.*?)\n```', (ROOT.parent / name / 'README.md').read_text(), re.S)[1]
    with tempfile.TemporaryDirectory(prefix='pluto-readme-go-') as directory:
        path = Path(directory)
        env = {**os.environ, 'GOWORK': 'off'}
        def run(*args):
            return subprocess.run(args, cwd=path, env=env, check=True, capture_output=True, text=True, timeout=120)
        run('go', 'mod', 'init', 'example.com/readme-check')
        run('go', 'get', module)
        (path / 'main.go').write_text(source + '\n')
        run('gofmt', '-w', 'main.go')
        output = run('go', 'run', '.').stdout.strip()
        assert output == expected, (name, output)
    return f'PASS: {name} published-package snippet'

with ThreadPoolExecutor(max_workers=3) as pool:
    for result in pool.map(check, checks):
        print(result, flush=True)

source = re.search(r'```js\n(.*?)\n```', (ROOT.parent / 'pluto-frontend/README.md').read_text(), re.S)[1]
with tempfile.TemporaryDirectory(prefix='pluto-readme-js-') as directory:
    path = Path(directory)
    (path / 'package.json').write_text('{"private":true,"type":"module"}')
    subprocess.run(['npm', 'install', '--ignore-scripts', '--no-audit', '--no-fund', '@plutoframework/frontend@0.2.1'], cwd=path, check=True, capture_output=True, timeout=120)
    (path / 'action.mjs').write_text(source + '\n')
    result = subprocess.run(['node', 'action.mjs'], cwd=path, check=True, capture_output=True, text=True)
    assert result.stdout.strip() == 'succeeded', result
print('PASS: frontend published-package snippet')
