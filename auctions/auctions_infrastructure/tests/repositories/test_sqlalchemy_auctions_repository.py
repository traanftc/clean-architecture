from typing import Generator
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Connection, Engine, RowProxy

from auctions.domain.entities import Auction, Bid
from auctions.domain.factories import get_dollars
from auctions_infrastructure import auctions, bidders, bids, metadata, setup
from auctions_infrastructure.repositories import SqlAlchemyAuctionsRepo


@pytest.fixture(scope="session")
def engine() -> Generator[Engine, None, None]:
    eng = setup()
    yield eng
    metadata.drop_all(eng)


@pytest.fixture()
def connection(engine: Engine) -> Generator[Connection, None, None]:
    conn = engine.connect()
    tx = conn.begin()
    yield conn
    tx.rollback()
    conn.close()


@pytest.fixture()
def winning_bid_amount() -> Decimal:
    return Decimal("15.00")


@pytest.fixture()
def bidder_id(connection: Connection) -> int:
    return connection.execute(bidders.insert()).inserted_primary_key[0]


another_bidder_id = bidder_id


@pytest.fixture()
def expired_auction(connection: Connection, past_date: datetime) -> RowProxy:
    connection.execute(
        auctions.insert().values(
            {
                "id": 0,
                "title": "Nothing interesting",
                "starting_price": Decimal("1.99"),
                "current_price": Decimal("1.99"),
                "ends_at": past_date,
            }
        )
    )
    return connection.execute(auctions.select(whereclause=auctions.c.id == 0)).first()


@pytest.fixture()
def auction_model_with_a_bid(
    connection: Connection, winning_bid_amount: Decimal, bidder_id: int, ends_at: datetime
) -> RowProxy:
    connection.execute(
        auctions.insert().values(
            {
                "id": 1,
                "title": "Cool socks",
                "starting_price": winning_bid_amount / 2,
                "current_price": winning_bid_amount,
                "ends_at": ends_at,
            }
        )
    )
    connection.execute(bids.insert().values({"amount": winning_bid_amount, "auction_id": 1, "bidder_id": bidder_id}))
    return connection.execute(auctions.select(whereclause=auctions.c.id == 1)).first()


@pytest.fixture()
def bid_model(connection: Connection, auction_model_with_a_bid: RowProxy) -> RowProxy:
    return connection.execute(bids.select().where(bids.c.auction_id == auction_model_with_a_bid.id)).first()


def test_gets_existing_auction(
    connection: Connection, auction_model_with_a_bid: RowProxy, bid_model: RowProxy, ends_at: datetime
) -> None:
    auction = SqlAlchemyAuctionsRepo(connection).get(auction_model_with_a_bid.id)

    assert auction.id == auction_model_with_a_bid.id
    assert auction.title == auction_model_with_a_bid.title
    assert auction.starting_price == get_dollars(auction_model_with_a_bid.starting_price)
    assert auction.current_price == get_dollars(bid_model.amount)
    assert auction.ends_at == ends_at
    assert set(auction.bids) == {Bid(bid_model.id, bid_model.bidder_id, get_dollars(bid_model.amount))}


def test_saves_auction_changes(
    connection: Connection,
    another_bidder_id: int,
    bid_model: RowProxy,
    auction_model_with_a_bid: RowProxy,
    ends_at: datetime,
) -> None:
    new_bid_price = get_dollars(bid_model.amount * 2)
    auction = Auction(
        id=auction_model_with_a_bid.id,
        title=auction_model_with_a_bid.title,
        starting_price=get_dollars(auction_model_with_a_bid.starting_price),
        ends_at=ends_at,
        bids=[
            Bid(bid_model.id, bid_model.bidder_id, get_dollars(bid_model.amount)),
            Bid(None, another_bidder_id, new_bid_price),
        ],
    )

    SqlAlchemyAuctionsRepo(connection).save(auction)

    assert connection.execute(select([func.count()]).select_from(bids)).scalar() == 2
    assert (
        connection.execute(
            select([auctions.c.current_price]).where(auctions.c.id == auction_model_with_a_bid.id)
        ).scalar()
        == new_bid_price.amount
    )


def test_removes_withdrawn_bids(
    connection: Connection, bid_model: RowProxy, auction_model_with_a_bid: dict, ends_at: datetime
) -> None:
    auction = Auction(
        id=auction_model_with_a_bid.id,
        title=auction_model_with_a_bid.title,
        starting_price=get_dollars(auction_model_with_a_bid.starting_price),
        ends_at=ends_at,
        bids=[Bid(bid_model.id, bid_model.bidder_id, get_dollars(bid_model.amount))],
    )
    auction.withdraw_bids([bid_model.id])

    SqlAlchemyAuctionsRepo(connection).save(auction)

    assert connection.execute(select([func.count()]).select_from(bids)).scalar() == 0
