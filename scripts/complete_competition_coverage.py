"""Explicit competition import/experiment, then a separate observation verification.

Never pools competitions or changes selection as a consequence of training.
"""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console import model_registry
from worldcup_mvp.console.catalog import classify
from worldcup_mvp.console.model_tasks import ModelTasks
from worldcup_mvp.console.store import Store, fingerprint, now_iso


def baseline(store):
    with store.connect() as db:
        archives = {r['id']: fingerprint(dict(r)) for r in db.execute('SELECT * FROM analyses')}
    pointers = {p.parent.name: fingerprint(json.loads(p.read_text(encoding='utf-8')))
                for p in model_registry.REGISTRY.glob('*/observation.json')}
    return {'archives': archives, 'observation': pointers}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    from worldcup_mvp.console.training import atomic_json
    atomic_json(path, value)


def train(args, store, evidence):
    data = json.loads(args.dataset.read_text(encoding='utf-8'))
    evidence['source_dataset'] = {'path':str(args.dataset.resolve()),'digest':fingerprint(data),
        'scope':{key:data.get('manifest',{}).get(key) for key in
                 ('source_competition','source_competitions','qualifying_enabled','collection_status','collection_errors')}}
    imported = model_registry.import_competition(data, args.competition)
    evidence['import'] = imported
    tasks = ModelTasks(store)
    job = tasks.start(args.competition, {'kind': 'experiment', 'target': 'full_time',
        'dataset_digest': imported['dataset_digest'], 'request_key': args.request_key})
    evidence['task_id'] = job['id']
    save(args.output, evidence)
    previous = None
    while True:
        job = next(j for j in tasks.list(args.competition) if j['id'] == job['id'])
        summary = {k: job.get(k) for k in ('id', 'status', 'phase', 'algorithm', 'completed', 'total', 'error')}
        if summary != previous:
            print(json.dumps(summary, ensure_ascii=False), flush=True)
            previous = summary
        if job['status'] not in ('queued', 'running'):
            evidence['job'] = job
            return job['status'] == 'completed'
        time.sleep(2)


def verify(args, store, evidence):
    from worldcup_mvp.console.service import ConsoleService
    from worldcup_mvp.console.sources import parse_date
    from datetime import datetime, timezone, timedelta
    model_registry.load_run(args.competition, args.run)
    pointer = model_registry.selection(args.competition)
    if pointer.get('run_id') != args.run:
        if not args.select:
            raise ValueError('该运行未被选用；请核对报告后显式使用 --select')
        pointer = model_registry.select_observation(args.competition, args.run,
                                                   expected_revision=pointer['revision'])
    evidence['selection'] = pointer
    app = ConsoleService(store)
    app.sync(force=True)
    if app.last_error:
        raise ValueError(app.last_error)
    matches = [m for m in store.list_matches() if classify(m)['id'] == args.competition
        and (m.get('sale_date') == args.date or
             parse_date(m['kickoff']).astimezone(timezone(timedelta(hours=8))).date().isoformat() == args.date)
        and (not args.match or m['id'] in args.match)]
    if not matches:
        raise ValueError('目标范围没有比赛，不能作为覆盖验收成功')
    evidence['matches'] = []
    for match in matches:
        row = {k: match[k] for k in ('id', 'home', 'away', 'kickoff')}
        try:
            detail = app.detail(match['id'], force=True)
            model = detail.get('briefing', {}).get('football_model') or {}
            row.update(quality=detail['quality'], model=model)
            if parse_date(match['kickoff']) <= datetime.now(timezone.utc):
                row['phase'] = 'already_started'
            elif args.archive and detail['quality']['can_predict']:
                result = app.prediction(match['id'], request_key=args.request_key+'-'+match['id'])['prediction']
                row.update(archive_id=result['id'], probabilities=result['probabilities'],
                           input_receipt=result['input_receipt'], phase='archived')
            else:
                row['phase'] = 'ready' if detail['quality']['can_predict'] else 'blocked'
        except Exception as exc:
            row.update(phase='failed', error=str(exc))
        evidence['matches'].append(row)
        save(args.output, evidence)
        print(json.dumps({k:v for k,v in row.items() if k not in ('quality','model','input_receipt')},ensure_ascii=False),flush=True)
    upcoming = [r for r in evidence['matches'] if r['phase'] != 'already_started']
    return bool(upcoming) and all(r['phase'] in ('ready','archived') for r in upcoming)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('train', 'verify'))
    parser.add_argument('--competition', required=True)
    parser.add_argument('--dataset', type=Path)
    parser.add_argument('--run')
    parser.add_argument('--date')
    parser.add_argument('--match', action='append')
    parser.add_argument('--select', action='store_true')
    parser.add_argument('--archive', action='store_true')
    parser.add_argument('--request-key', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.operation == 'train' and (not args.dataset or args.select or args.archive):
        parser.error('训练须指定数据文件，不能同时选用或归档')
    if args.operation == 'verify' and (not args.run or not args.date):
        parser.error('验证须指定运行和北京时间日期')
    store = Store()
    evidence = {'competition_id': args.competition, 'started_at': now_iso(), 'before': baseline(store)}
    success = False
    try:
        success = train(args, store, evidence) if args.operation == 'train' else verify(args, store, evidence)
    finally:
        after = baseline(store)
        evidence['old_archives_unchanged'] = all(after['archives'].get(k)==v for k,v in evidence['before']['archives'].items())
        evidence['other_selections_unchanged'] = all(after['observation'].get(k)==v for k,v in evidence['before']['observation'].items()
                                                    if args.operation == 'train' or k != args.competition)
        evidence['new_archives'] = len(after['archives'])-len(evidence['before']['archives'])
        evidence['finished_at'] = now_iso()
        save(args.output, evidence)
    if not evidence['old_archives_unchanged'] or not evidence['other_selections_unchanged']:
        raise RuntimeError('不可变记录或其他赛事选用发生变化，请检查验收记录')
    return 0 if success else 2


if __name__ == '__main__':
    raise SystemExit(main())
