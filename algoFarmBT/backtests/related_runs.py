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
