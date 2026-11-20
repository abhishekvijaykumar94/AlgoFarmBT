"""Replay the shared US ETF option selection rules against historical listings."""

from dataclasses import dataclass
from datetime import date
from typing import Callable, Iterable, Mapping, Optional

import pandas as pd

from algoLibs.converters.equity_market_data_converter import TickMarketFeedColumns
from algoLibs.dao.query.equity_market_data_query import EquityMarketDataQuery
from algoLibs.enum.aggregation_type import AggregationType
from algoLibs.market_data_stream.option_universe import (
    Chain,
    ExpirySubscriptionPlan,
    OptionContract,
    US_ETF_EXPIRY_RULES,
    plan_expiry_subscriptions,
    plan_snapshot_batches,
    select_option_codes,
)


@dataclass(frozen=True)
class OptionReplaySession:
    session: date
    plan: ExpirySubscriptionPlan
    contract_codes: tuple[str, ...]
    snapshot_batches: tuple[tuple[str, ...], ...]
    market_data_query: Optional[EquityMarketDataQuery]


class OptionUniverseReplay:
    """Build one-minute quote queries using point-in-time option metadata.

    The sources must return listings known on the requested session.
    `contract_source` receives only the selected ETF chains. The independent
    `stock_contract_source` supplies the single-stock snapshot universe.
    This avoids looking ahead to contracts listed after the replay date.
    """

    def __init__(
        self,
        expiry_source: Callable[[date], Mapping[str, Iterable[date]]],
        contract_source: Callable[[date, frozenset[Chain]], Iterable[OptionContract]],
        stock_contract_source: Callable[[date], Iterable[OptionContract]],
        initial_selection: Optional[Mapping[str, Iterable[date]]] = None,
    ):
        self.expiry_source = expiry_source
        self.contract_source = contract_source
        self.stock_contract_source = stock_contract_source
        self.initial_selection = initial_selection
        self.active_chains: frozenset[Chain] = frozenset()

    def build_session(
        self, session: date, start_time: str, end_time: str
    ) -> OptionReplaySession:
        plan = plan_expiry_subscriptions(
            self.expiry_source(session),
            self.active_chains,
            session,
            initial_selection=self.initial_selection,
        )
        contracts = tuple(self.contract_source(session, plan.desired))
        codes = select_option_codes(contracts, plan.desired, session)
        stock_contracts = tuple(self.stock_contract_source(session))
        if any(contract.underlying in US_ETF_EXPIRY_RULES for contract in stock_contracts):
            raise ValueError("stock snapshot universe contains an ETF streaming underlying")
        stock_chains = {
            (contract.underlying, contract.expiry) for contract in stock_contracts
        }
        snapshot_batches = plan_snapshot_batches(
            stock_contracts, stock_chains
        )
        self.active_chains = plan.desired
        query = None
        if codes:
            query = EquityMarketDataQuery(
                tag_string=TickMarketFeedColumns.tag,
                tags=list(codes),
                start_time=start_time,
                end_time=end_time,
                columns=[
                    "_time",
                    TickMarketFeedColumns.tag,
                    TickMarketFeedColumns.bid_price,
                    TickMarketFeedColumns.ask_price,
                    TickMarketFeedColumns.bid_volume,
                    TickMarketFeedColumns.ask_volume,
                ],
                aggregate_timeframe="1m",
                aggregation_fn=AggregationType.LAST,
                add_pivot=True,
            )
        return OptionReplaySession(session, plan, codes, snapshot_batches, query)

    @staticmethod
    def with_mid(quotes: pd.DataFrame) -> pd.DataFrame:
        """Add mid only for valid bid/ask pairs, preserving missing observations."""
        result = quotes.copy()
        bid = pd.to_numeric(result[TickMarketFeedColumns.bid_price], errors="coerce")
        ask = pd.to_numeric(result[TickMarketFeedColumns.ask_price], errors="coerce")
        valid = bid.ge(0) & ask.gt(0) & ask.ge(bid)
        result[TickMarketFeedColumns.mid_price] = ((bid + ask) / 2).where(valid)
        return result

    def load_quotes(self, replay_session: OptionReplaySession, repository, executor_data):
        """Read selected one-minute quotes through algoLibs' DataRepository."""
        if replay_session.market_data_query is None:
            return pd.DataFrame()
        quotes = repository.query(replay_session.market_data_query, executor_data)
        if quotes.empty:
            return quotes
        return self.with_mid(quotes)
