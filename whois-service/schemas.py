"""Response models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class _Orm(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserSummary(_Orm):
    id: str
    display_name: str | None
    mail: str | None
    user_principal_name: str | None
    job_title: str | None
    department: str | None
    office_location: str | None


class GroupSummary(_Orm):
    id: str
    display_name: str | None
    mail: str | None
    security_enabled: bool | None
    mail_enabled: bool | None


class UserDetail(UserSummary):
    given_name: str | None
    surname: str | None
    company_name: str | None
    employee_id: str | None
    mobile_phone: str | None
    business_phones: list[str] | None
    account_enabled: bool | None
    groups: list[GroupSummary]


class Member(BaseModel):
    id: str
    type: str
    display_name: str | None = None
    mail: str | None = None


class GroupDetail(GroupSummary):
    description: str | None
    group_types: list[str] | None
    members: list[Member]


class Page[T](BaseModel):
    total: int
    limit: int
    offset: int
    items: list[T]


class SyncStatus(_Orm):
    resource: str
    has_delta_link: bool
    requested_at: datetime | None
    last_run_at: datetime | None
    last_success_at: datetime | None
    last_full_sync_at: datetime | None
    last_item_count: int | None
    last_error: str | None
