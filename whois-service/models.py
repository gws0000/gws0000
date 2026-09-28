"""Database schema. Rows mirror Entra ID objects keyed by their Graph object id."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(256), index=True)
    given_name: Mapped[str | None] = mapped_column(String(128))
    surname: Mapped[str | None] = mapped_column(String(128))
    mail: Mapped[str | None] = mapped_column(String(320), index=True)
    user_principal_name: Mapped[str | None] = mapped_column(String(320), index=True)
    job_title: Mapped[str | None] = mapped_column(String(256))
    department: Mapped[str | None] = mapped_column(String(256), index=True)
    office_location: Mapped[str | None] = mapped_column(String(256))
    company_name: Mapped[str | None] = mapped_column(String(256))
    employee_id: Mapped[str | None] = mapped_column(String(64))
    mobile_phone: Mapped[str | None] = mapped_column(String(64))
    business_phones: Mapped[list[str] | None] = mapped_column(JSON)
    account_enabled: Mapped[bool | None] = mapped_column(Boolean)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(256), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    mail: Mapped[str | None] = mapped_column(String(320))
    mail_enabled: Mapped[bool | None] = mapped_column(Boolean)
    security_enabled: Mapped[bool | None] = mapped_column(Boolean)
    group_types: Mapped[list[str] | None] = mapped_column(JSON)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GroupMember(Base):
    """Direct membership. member_id has no FK: members can be devices, service
    principals or nested groups, and a user can arrive after its membership."""

    __tablename__ = "group_members"

    group_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    member_id: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    member_type: Mapped[str] = mapped_column(String(64))  # "user", "group", "device", ...
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SyncState(Base):
    __tablename__ = "sync_state"

    resource: Mapped[str] = mapped_column(String(32), primary_key=True)  # "users" | "groups"
    delta_link: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_full_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_item_count: Mapped[int | None] = mapped_column(Integer)
