#!/usr/bin/env python3
"""Fail on common secret/private-data packaging mistakes."""
from pathlib import Path
import re, sys

ROOT=Path(__file__).resolve().parent.parent
ALLOW_RUNTIME_ENV='--allow-runtime-env' in sys.argv
SKIP_PARTS={'.git','.gradle','.tools','build','staticfiles','__pycache__'}
FORBIDDEN_NAMES={'.env','id_rsa','id_ed25519'}
FORBIDDEN_SUFFIXES={'.pem','.key','.p12','.pfx','.jks','.keystore','.dump','.sql','.sqlite3','.xlsx','.xls'}
PATTERNS={
    'private key':re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'hard-coded Tailscale tailnet hostname':re.compile(r'\b[a-z0-9-]+\.ts\.net\b',re.I),
    'hard-coded Windows user profile path':re.compile(r'C:\\\\Users\\\\(?!<)[^\\\\\r\n]+\\\\',re.I),
    'hard-coded Linux home path':re.compile(r'/home/(?!<)[A-Za-z0-9._-]+/'),
}
errors=[]
for p in ROOT.rglob('*'):
    rel=p.relative_to(ROOT)
    if any(part in SKIP_PARTS for part in rel.parts): continue
    if not p.is_file(): continue
    if p.name=='.env' and ALLOW_RUNTIME_ENV:
        continue
    if p.name in FORBIDDEN_NAMES or p.suffix.lower() in FORBIDDEN_SUFFIXES:
        errors.append(f'forbidden file: {rel}')
        continue
    try: text=p.read_text(encoding='utf-8')
    except (UnicodeDecodeError,OSError): continue
    for label,rx in PATTERNS.items():
        if rx.search(text): errors.append(f'{label}: {rel}')
if errors:
    print('Public-tree check FAILED:')
    for e in errors: print(' -',e)
    sys.exit(1)
print('Public-tree check passed.')
