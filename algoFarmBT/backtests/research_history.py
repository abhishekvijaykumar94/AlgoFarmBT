"""Recorded project exposure; a new manifest cannot make viewed history unseen.

This registry records inspected windows, not an invented number of independent
trials. New user-declared inspection cutoffs supplement these known exposures.
"""
from algoLibs.market_data.types import utc


KNOWN_EXPOSURES = (
    dict(start='2026-03-01T00:00:00Z',end='2026-09-01T00:00:00Z',
         symbols=('BTCUSDT','ETHUSDT','SOLUSDT'),
         evidence='Approved baseline and 13-case development study, 2026-11-20',
         data_id='sha256:31de4fd5ec22e0aeda7e349d67856161b3bc68b93e03bb4b4fe6fbbfa2b5401f'),
)


def eligibility_reasons(fold, plan, symbols):
    start,end=utc(fold['test_start']),utc(fold['test_end'])
    reasons=[]
    if fold['designation']!='test':reasons.append('development')
    cutoff=plan['inspected_through']
    if cutoff is None:reasons.append('unknown_inspection_history')
    elif utc(cutoff)>start:reasons.append('declared_inspected_history')
    if any(set(symbols)&set(e['symbols']) and start<utc(e['end']) and utc(e['start'])<end for e in KNOWN_EXPOSURES):
        reasons.append('previously_inspected')
    return reasons
