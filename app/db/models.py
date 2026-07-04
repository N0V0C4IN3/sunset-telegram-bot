from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    latitude_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    longitude_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC", server_default="UTC")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    settings: Mapped["UserSettings"] = relationship(back_populates="user", cascade="all, delete-orphan", lazy="selectin")


class UserSettings(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    subscribed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=70, server_default="70")
    lead_time_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=90, server_default="90")
    pending_input: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_notified_for_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped[User] = relationship(back_populates="settings", lazy="selectin")


class ForecastCache(Base):
    __tablename__ = "forecast_cache"
    __table_args__ = (UniqueConstraint("user_id", "forecast_date", name="uq_forecast_cache_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    forecast_date: Mapped[date] = mapped_column(Date, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sunset_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    weather_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
