"""Collect license texts from exact crates in upstream macmon's Cargo.lock.
Includes all lockfile targets (a conservative superset of the arm64 build).
Archives are checked against the upstream lockfile SHA-256 before reading.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import html
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tomllib

root = Path(__file__).resolve().parent
assets = root / 'assets'
cache = root / 'build' / 'crate-cache'
cache.mkdir(parents=True, exist_ok=True)
lock = tomllib.loads((assets / 'macmon-Cargo.lock').read_text())
packages = [p for p in lock['package'] if p.get('source', '').startswith('registry+')]

def collect(p):
    name, version = p['name'], p['version']
    target = cache / f'{name}-{version}.crate'
    if not target.exists():
        subprocess.run(['curl', '-fsSL', '--retry', '2', '--max-time', '90',
                        f'https://static.crates.io/crates/{name}/{name}-{version}.crate',
                        '-o', str(target)], check=True)
    data = target.read_bytes()
    if hashlib.sha256(data).hexdigest() != p['checksum']:
        raise ValueError(f'Checksum mismatch: {name} {version}')
    texts = []
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        cargo = archive.extractfile(f'{name}-{version}/Cargo.toml').read().decode()
        manifest = tomllib.loads(cargo)['package']
        license_file = manifest.get('license-file', manifest.get('license_file', ''))
        for member in archive.getmembers():
            short = member.name.split('/', 1)[-1]
            base = Path(short).name.lower()
            if member.isfile() and (any(x in base for x in ('license', 'licence', 'copying', 'copyright', 'notice')) or
                                    (license_file and short == license_file)):
                if member.size < 1000000:
                    texts.append((short, archive.extractfile(member).read().decode('utf-8', errors='replace')))
        # A few crates keep their only license notice in README or in source headers.
        if not texts:
            for member in archive.getmembers():
                if member.isfile() and (Path(member.name).name.lower().startswith('readme') or member.name.endswith('/src/lib.rs')):
                    text = archive.extractfile(member).read().decode('utf-8', errors='replace')
                    if 'license' in text.lower() or 'copyright' in text.lower():
                        texts.append((member.name, text))
    if not texts:
        fallback = {'ratatui-termina': 'RATATUI-LICENSE.txt',
                    'vtparse': 'WEZTERM-LICENSE.txt',
                    'wezterm-input-types': 'WEZTERM-LICENSE.txt'}.get(name)
        if fallback:
            texts.append(('Upstream repository license (not included in crate archive)',
                          (assets / fallback).read_text()))
    return dict(name=name, version=version, license=manifest.get('license', ''), texts=texts)

with ThreadPoolExecutor(max_workers=12) as pool:
    results = list(pool.map(collect, packages))
out = ['<!doctype html><meta charset="utf-8"><title>macmon dependency notices</title>',
       '<style>body{max-width:960px;margin:40px auto;font:15px system-ui}pre{white-space:pre-wrap;background:#f3f5f8;padding:20px}h2{margin-top:50px}</style>',
       '<h1>macmon 0.10.0 — dependency notices</h1><p>Exact versions from the upstream Cargo.lock. All lockfile targets are included, a superset of the Apple Silicon runtime dependencies.</p>']
for item in results:
    out.append(f'<h2>{html.escape(item["name"])} {item["version"]}</h2><p>Declared license: {html.escape(item["license"])}</p>')
    for name, text in item['texts']:
        out.append(f'<h3>{html.escape(name)}</h3><pre>{html.escape(text)}</pre>')
(assets / 'macmon-dependencies.html').write_text('\n'.join(out))
missing = [f'{p["name"]} {p["version"]}' for p in results if not p['texts']]
print(json.dumps({'packages': len(results), 'without_notice_files': missing}))
