"""Move only retired global training files outside runtime data; preserve checksums."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from worldcup_mvp.console.store import now_iso
from worldcup_mvp.console.training import atomic_json

RETIRED = ('club_history.json', 'club_model.json', 'club_evaluation.json',
           'history_staging.json', 'history_staging.md')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def protected():
    registry = ROOT/'data/training/competitions'
    paths = [p for p in registry.rglob('*.json')
             if 'task_artifacts' not in p.parts and 'tasks' not in p.parts]
    # Historical journal remains available through the read-only compatibility reader.
    paths += [ROOT/'data/cache/prediction_journal.json']
    return {str(p.relative_to(ROOT)):digest(p) for p in paths if p.is_file()}


def quarantine(destination, apply=False):
    destination = destination.resolve()
    allowed = (ROOT/'.ai/backups').resolve()
    if not destination.is_relative_to(allowed) or destination == allowed:
        raise ValueError('Destination must be a new child of .ai/backups')
    if destination.exists():
        raise ValueError('Archive already exists')
    before = protected()
    entries = []
    for name in RETIRED:
        source = (ROOT/'data/training'/name).resolve()
        if not source.is_relative_to((ROOT/'data/training').resolve()):
            raise ValueError('Source is outside the training directory')
        if source.is_symlink():
            raise ValueError('Refusing a linked source')
        if source.exists():
            entries.append({'source':str(source.relative_to(ROOT)), 'archive':name,
                            'bytes':source.stat().st_size, 'sha256':digest(source)})
    manifest = {'created_at':now_iso(), 'policy':'retired-global-files-only', 'files':entries,
                'protected_before':before, 'applied':False}
    if apply:
        destination.mkdir(parents=True, exist_ok=False)
        atomic_json(destination/'manifest.json', manifest)
        moved = []
        try:
            for entry in entries:
                source, target = ROOT/entry['source'], destination/entry['archive']
                if digest(source) != entry['sha256']:
                    raise ValueError('Source changed after inventory')
                source.rename(target)
                moved.append((source,target))
                if digest(target) != entry['sha256']:
                    raise ValueError('Archive checksum mismatch')
            if before != protected():
                raise ValueError('Protected artifact changed during isolation')
        except Exception:
            for source, target in reversed(moved):
                if not source.exists():
                    target.rename(source)
            raise
        manifest.update(applied=True, protected_unchanged=True)
        atomic_json(destination/'manifest.json', manifest)
    print(json.dumps({'destination':str(destination), 'apply':apply, 'files':entries,
                      'protected_files':len(before)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    quarantine(args.destination, args.apply)
