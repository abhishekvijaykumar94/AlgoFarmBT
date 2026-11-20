"""Bounded related-run receipts; each case retains its inputs and failures."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass(frozen=True)
class RelatedRun:
    case_id: str
    status: str
    artifacts: str
    run_id: str | None = None
    error: str | None = None

    def to_payload(self):
        return asdict(self)


def run_related_cases(cases, directory, execute, write_json):
    """Execute an explicit, ordered set; ordinary failures never erase trials.

    ``execute`` owns the actual Backtest call and returns a retained manifest ID.
    No automatic retries, search, selection or parallel scheduling is performed.
    """
    records = []
    for case in cases:
        relative = 'cases/' + case['id']
        folder = Path(directory) / relative
        folder.mkdir(parents=True)
        write_json(folder/'case.json', case)
        try:
            run_id = execute(case, folder)
            record = RelatedRun(case['id'], 'succeeded', relative, run_id)
        except Exception as error:
            detail = dict(type=type(error).__name__, message=str(error).replace(str(Path(directory)), '<comparison>'))
            write_json(folder/'failure.json', detail)
            record = RelatedRun(case['id'], 'failed', relative, error=detail['type'] + ': ' + detail['message'])
        records.append(record.to_payload())
    return records


def case_metrics(folder, row):
    if row['status']!='succeeded':
        return {}
    from algoLibs.market_data.types import decode
    import json
    def _read(path):return decode(json.loads(path.read_text()))
    run = folder/'run'
    report, research, result = (_read(run/name) for name in ('report.json','research.json','result.json'))
    strategy = research['summary']['comparison'][0]
    schedule = research['run_details']['context']['execution_schedule']
    orders = result['orders']
    return dict(net_pnl=report['net_pnl'],net_return=report['net_return'],max_drawdown=report['max_drawdown'],
                annualised_daily_sharpe=strategy['annualised_daily_sharpe'],
                annualised_daily_volatility=research['performance']['daily_risk']['annualised_daily_volatility'],
                complete_day_count=strategy['complete_day_count'],mean_close_exposure=strategy['mean_close_exposure'],
                total_fees=report['total_fees'],modeled_slippage=report['modeled_slippage'],
                turnover=report['turnover_notional_over_initial_equity'],fill_count=len(result['fills']),
                order_count=len(orders),unfilled_order_count=sum(o['filled_quantity']==0 for o in orders),
                partial_order_count=sum(0<o['filled_quantity']<o['requested_quantity'] for o in orders),
                censored_decision_count=schedule['censored'],
                same_fill_break_even_fee_bps=research['sections']['trades']['statistics']['same_fill_break_even_fee_bps'])
