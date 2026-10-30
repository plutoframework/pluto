"""Validate README/guide links, including cross-repo links in local checkouts."""
from pathlib import Path
from urllib.parse import unquote, urlsplit
import argparse
import re
import subprocess
import textwrap
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--repo-dir', action='append', default=[], help='Additional local repository to check')
args = parser.parse_args()
roots = [ROOT] + [Path(p).resolve() for p in args.repo_dir]
repositories = {}
for root in roots:
    remote = subprocess.check_output(['git', '-C', str(root), 'remote', 'get-url', 'origin'], text=True).strip()
    repositories[remote.removesuffix('.git').rsplit('/', 1)[-1]] = root


def headings(path):
    found = set()
    counts = {}
    text = re.sub(r'^```.*?^```[^\n]*$', '', path.read_text(), flags=re.M | re.S)
    for title in re.findall(r'^#{1,6} (.+)$', text, re.M):
        slug = re.sub(r'[^\w\- ]', '', title.lower()).replace(' ', '-')
        number = counts.get(slug, 0)
        counts[slug] = number + 1
        found.add(slug + (f'-{number}' if number else ''))
    return found

count = 0
for root in roots:
    files = [root / 'README.md', root / 'CONTRIBUTING.md']
    for name in ('docs/REFERENCE.md', 'docs/MAINTAINING.md', 'docs/DEMO.md', 'docs/ROADMAP.md'):
        if (root / name).exists(): files.append(root / name)
    for document in files:
        assert document.is_file(), document
        body = re.sub(r'^```.*?^```[^\n]*$', '', document.read_text(), flags=re.M | re.S)
        examples = re.findall(
            r'<!-- example-source: ([^\n]+) -->\s*```[^\n]*\n(.*?)\n```\s*<!-- /example-source -->',
            document.read_text(), re.S,
        )
        assert len(examples) == document.read_text().count('<!-- example-source:'), f'Malformed example marker: {document}'
        for source, snippet in examples:
            original = (root / source).resolve()
            assert original.is_relative_to(root.resolve()), (document, source)
            assert original.is_file(), (document, source)
            assert snippet.strip() == original.read_text().strip(), f'Example drift: {document}: {source}'
        excerpts = re.findall(
            r'<!-- example-excerpt: ([^\n]+):([a-z_]+) -->\s*```go\n(.*?)\n```\s*<!-- /example-excerpt -->',
            document.read_text(), re.S,
        )
        assert len(excerpts) == document.read_text().count('<!-- example-excerpt:'), f'Malformed excerpt marker: {document}'
        for source, symbol, snippet in excerpts:
            source_path = (root / source).resolve()
            assert source_path.is_relative_to(root.resolve()), (document, source)
            original = source_path.read_text()
            start = original.index('\t' + symbol + ', err :=')
            end = original.index('\n\tif err != nil', start)
            expected = textwrap.dedent(original[start:end]).strip()
            assert snippet.strip() == expected, f'Excerpt drift: {document}: {source}'
        links = re.findall(r'\]\(([^\s)]+)\)', body)
        links += re.findall(r'(?:src|href)="([^"]+)"', body)
        for link in links:
            url = urlsplit(link)
            target = None
            if not url.scheme and not url.netloc:
                target = document.parent / unquote(url.path) if url.path else document
            elif url.netloc in ('github.com', 'raw.githubusercontent.com'):
                parts = unquote(url.path).strip('/').split('/')
                if len(parts) >= 2 and parts[0] == 'plutoframework' and parts[1] in repositories:
                    other = repositories[parts[1]]
                    rest = parts[2:]
                    if not rest:
                        target = other / 'README.md'
                    elif url.netloc == 'raw.githubusercontent.com' and rest[0] == 'main':
                        target = other / '/'.join(rest[1:])
                    elif len(rest) >= 2 and rest[0] in ('blob', 'tree') and rest[1] == 'main':
                        target = other / '/'.join(rest[2:])
            if target is None:
                continue
            assert target.exists(), f'{document}: missing {link}'
            if target.suffix == '.md' and url.fragment:
                assert unquote(url.fragment) in headings(target), f'{document}: missing anchor {link}'
            count += 1
    print(f'PASS: {root.name} README and guide targets')
ET.parse(ROOT / 'assets/readme/banner.svg')
assert (ROOT / 'assets/readme/demo.gif').read_bytes().startswith((b'GIF87a', b'GIF89a'))
assert (ROOT / 'assets/readme/demo.png').read_bytes().startswith(b'\x89PNG')
print(f'PASS: {count} resolved links and shared demo assets')
