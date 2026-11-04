"""One configuration authority; append immutable rule versions before publishing."""
from contextlib import contextmanager, nullcontext
import os
import re
from . import catalog as registry
from .store import fingerprint, now_iso
from .training import atomic_json


@contextmanager
def file_lock(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as handle:
        if path.stat().st_size==0:
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            raise ValueError('操作正在进行，请稍后重试') from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name=='nt':
                msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                fcntl.flock(handle.fileno(),fcntl.LOCK_UN)


def save(spec, expected_revision=None):
    if not isinstance(spec,dict) or not re.fullmatch(r'[a-z0-9_.-]+',spec.get('id','')):
        raise ValueError('赛事标识无效')
    if spec.get('training_scope')!='competition_only' or spec.get('target_period')!='regular_time':
        raise ValueError('仅支持单赛事90分钟训练')
    if not spec.get('name') or not isinstance(spec.get('providers'),dict) or not isinstance(spec.get('seasons'),list):
        raise ValueError('赛事登记不完整')
    if spec.get('training_enabled') and (not spec.get('rule_source') or not spec.get('training_stage_policy')):
        raise ValueError('缺少赛事规则来源或训练阶段政策')
    with file_lock(registry.REGISTRY_PATH.with_suffix('.lock')):
        current = registry.catalog()
        previous = next((c for c in current if c['id']==spec['id']),None)
        if (fingerprint(previous) if previous else None) != expected_revision:
            raise ValueError('赛事规则已变更，请刷新后重试')
        code = spec['providers'].get('sporttery')
        if code and any(c['id']!=spec['id'] and c.get('providers',{}).get('sporttery')==code for c in current):
            raise ValueError('体彩赛事映射重复')
        from .model_registry import commit_lock
        with commit_lock(spec['id']) if previous else nullcontext():
            revision = fingerprint(spec)
            version = registry.REGISTRY_PATH.parent/'football_rules'/spec['id']/(revision+'.json')
            if not version.exists():
                atomic_json(version,{'revision':revision,'previous_revision':expected_revision,'created_at':now_iso(),'rules':spec})
            atomic_json(registry.REGISTRY_PATH,[c for c in current if c['id']!=spec['id']]+[spec])
    return {'competition_id':spec['id'],'revision':revision}
