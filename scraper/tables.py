"""Legacy and cache SQLAlchemy table metadata used by the scraper."""

from __future__ import annotations

from sqlalchemy import Column, Date, DateTime, Float, Index, Integer, MetaData, Table, Text, UniqueConstraint

_metadata = MetaData()

# Scout cache — αποτρέπει περιττά Apify calls για Stage 1.
# Πραγματικοί τύποι date/timestamptz (migration 20260707_0014 μετέτρεψε τα
# παλιά TEXT columns): έτσι το scheduler cleanup συγκρίνει cached_at με
# now() - interval χωρίς cast errors.
_scout_cache = Table(
    "scout_cache", _metadata,
    Column("id",                      Integer, primary_key=True, autoincrement=True),
    Column("destination",             Text,    nullable=False),
    Column("check_in",                Date,    nullable=False),
    Column("check_out",               Date,    nullable=False),
    Column("adults",                  Integer, nullable=False, default=2),
    Column("children",                Integer, nullable=False, default=0),
    Column("rooms",                   Integer, nullable=False, default=1),
    Column("currency",                Text,    nullable=False, default="EUR"),
    Column("language",                Text,    nullable=False, default="el"),
    Column("hotel_name",              Text),
    Column("hotel_url",               Text),
    Column("stars",                   Float),
    Column("review_score",            Float),
    Column("review_count",            Integer),
    Column("latitude",                Float),
    Column("longitude",               Float),
    Column("property_type",           Text),
    Column("city",                    Text),
    # Nullable: rows cached before migration 20260823_0022 have no address.
    # _load_scout_cache reads NULL back as "" so a cached candidate looks
    # exactly like a freshly scouted one (scraper/utils.py:_extract_meta).
    Column("address",                 Text),
    Column("cached_at",               DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "destination", "check_in", "check_out", "adults", "children", "rooms",
        "currency", "language", "hotel_url", name="uq_scout_cache_query_hotel",
    ),
    Index(
        "ix_scout_cache_lookup",
        "destination", "check_in", "check_out", "adults", "children", "rooms",
        "currency", "language", "cached_at",
    ),
)


# ===========================================================
# UTILITIES
# ===========================================================
