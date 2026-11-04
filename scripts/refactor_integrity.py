"""Consistent backup and comparison of immutable football workspace records."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]


def digest(value):
    return hashlib.sha256(value).hexdigest()


def records(db):
    result = {}
    for table in ('analyses', 'workspace_state'):
        rows = db.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall()
        result[table] = [dict(r) for r in rows]
    return result


def snapshot(destination):
    destination.mkdir(parents=True, exist_ok=False)
    source = sqlite3.connect((ROOT / 'data/console/football.sqlite3').as_uri() + '?mode=ro', uri=True)
    backup = sqlite3.connect(destination / 'football.sqlite3')
    try:
        source.backup(backup)
        backup.row_factory = sqlite3.Row
        assert backup.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        saved = records(backup)
    finally:
        backup.close()
        source.close()
    hashes = {}
    for folder in ('data/training', 'config', 'web/console'):
        for path in (ROOT / folder).rglob('*'):
            if path.is_file() and path.suffix != '.lock':
                rel = path.relative_to(ROOT)
                target = destination / 'files' / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                hashes[rel.as_posix()] = digest(target.read_bytes())
    manifest = {'created_at': datetime.now(timezone.utc).isoformat(), 'files': hashes, 'records': saved}
    (destination / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    status = subprocess.check_output(['git', 'status', '--short'], cwd=ROOT)
    (destination / 'git-status.txt').write_bytes(status)
    print(json.dumps({'backup': str(destination), 'files': len(hashes), 'analyses': len(saved['analyses']), 'state_rows': len(saved['workspace_state'])}, ensure_ascii=False))


def compare(destination):
    previous = json.loads((destination / 'manifest.json').read_text(encoding='utf-8'))
    changed = []
    for path, expected in previous['files'].items():
        if not path.startswith('data/training/'):
            continue
        current = ROOT / path
        if not current.is_file() or digest(current.read_bytes()) != expected:
            changed.append(path)
    db = sqlite3.connect((ROOT / 'data/console/football.sqlite3').as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    current = records(db)
    db.close()
    now = {r['id']: r for r in current['analyses']}
    altered = [r['id'] for r in previous['records']['analyses'] if now.get(r['id']) != r]
    result = {'changed_training_files': changed, 'changed_archives': altered,
              'state_unchanged': previous['records']['workspace_state'] == current['workspace_state'],
              'new_archives': len(now) - len(previous['records']['analyses'])}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return not changed and not altered


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('backup', 'compare'))
    parser.add_argument('path', type=Path)
    args = parser.parse_args()
    if args.operation == 'backup':
        snapshot(args.path)
    else:
        raise SystemExit(0 if compare(args.path) else 1)
