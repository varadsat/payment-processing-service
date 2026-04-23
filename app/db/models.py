import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, ForeignKey, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class PaymentStatus(str, enum.Enum):
    initiated = "initiated"
    processed = "processed"
    failed = "failed"


class SettlementStatus(str, enum.Enum):
    pending = "pending"
    settled = "settled"
    not_applicable = "not_applicable"


class ConflictReason(str, enum.Enum):
    stuck_settlement = "stuck_settlement"
    settled_after_fail = "settled_after_fail"
    conflicting_transition = "conflicting_transition"
    duplicate_detected = "duplicate_detected"


class Base(DeclarativeBase):
    pass


class Merchant(Base):
    __tablename__ = "merchants"

    merchant_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()"
    )

    transactions: Mapped[list["Transaction"]] = relationship(back_populates="merchant")
    events: Mapped[list["Event"]] = relationship(back_populates="merchant")


class Transaction(Base):
    __tablename__ = "transactions"

    transaction_id: Mapped[str] = mapped_column(Text, primary_key=True)
    merchant_id: Mapped[str] = mapped_column(
        Text, ForeignKey("merchants.merchant_id"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    payment_status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(PaymentStatus, name="payment_status", create_type=False), nullable=False
    )
    settlement_status: Mapped[SettlementStatus] = mapped_column(
        SAEnum(SettlementStatus, name="settlement_status", create_type=False),
        nullable=False,
    )
    first_event_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_event_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    has_conflict: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    conflict_reason: Mapped[ConflictReason | None] = mapped_column(
        SAEnum(ConflictReason, name="conflict_reason", create_type=False), nullable=True
    )

    merchant: Mapped["Merchant"] = relationship(back_populates="transactions")
    events: Mapped[list["Event"]] = relationship(
        back_populates="transaction", order_by="Event.event_timestamp"
    )


class Event(Base):
    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    transaction_id: Mapped[str] = mapped_column(
        Text, ForeignKey("transactions.transaction_id"), nullable=False
    )
    merchant_id: Mapped[str] = mapped_column(
        Text, ForeignKey("merchants.merchant_id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()"
    )
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    transaction: Mapped["Transaction"] = relationship(back_populates="events")
    merchant: Mapped["Merchant"] = relationship(back_populates="events")
