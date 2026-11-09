"""Dated published tariff estimate; never substitute it for the provider invoice."""
from datetime import datetime, timezone
from .observation_features import instant

SOURCE='https://api-docs.deepseek.com/quick_start/pricing/'
VERIFIED='2026-11-09T12:44:00+00:00'
EXPIRES='2026-11-16T12:44:00+00:00'


def estimate(usage, started_at, completed_at):
    model=usage.get('response_model')
    if model not in ('deepseek-flash','deepseek-v4-pro'): return None
    start,end=instant(started_at),instant(completed_at)
    if start<instant(VERIFIED) or end>=instant(EXPIRES): return None
    hit=usage.get('prompt_cache_hit_tokens');miss=usage.get('prompt_cache_miss_tokens');out=usage.get('completion_tokens')
    if any(type(x) is not int or x<0 for x in (hit,miss,out)): return None
    if hit+miss!=usage.get('prompt_tokens'):return None
    rates=(.006,.3,1.2) if model=='deepseek-flash' else (.044,1.32,3.96)
    peak=sum(n*p/1_000_000 for n,p in zip((hit,miss,out),rates))
    # Holidays are deliberately not guessed. If either boundary can be peak,
    # report the published half-to-full range rather than an exact debit.
    def possible_peak(t): return t.weekday()<5 and (1<=t.hour<4 or 6<=t.hour<10)
    same_hour=start.replace(minute=0,second=0,microsecond=0)==end.replace(minute=0,second=0,microsecond=0)
    upper=peak if not same_hour or possible_peak(start) else peak/2
    return {'currency':'USD','minimum':round(peak/2,8),'maximum':round(upper,8),
            'basis':'published_tariff_estimate_not_invoice','model':model,'source_url':SOURCE,
            'verified_at':VERIFIED,'expires_at':EXPIRES,'cache_hit_tokens':hit,'cache_miss_tokens':miss,'output_tokens':out}
