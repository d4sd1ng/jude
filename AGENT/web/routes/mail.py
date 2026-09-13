from __future__ import annotations

from fastapi import APIRouter

from web.app import mail

router = APIRouter()


@router.get("/api/mail/status")
def mail_status(): return mail.account_status()


@router.get("/api/mail/{account}/search")
def mail_search(account: str, q: str = "ALL", limit: int = 30): return mail.search(account, q, limit)


@router.get("/api/mail/{account}/{message_id}")
def mail_read(account: str, message_id: str, folder: str = "INBOX"): return mail.read(account, message_id, folder)


@router.post("/api/mail/draft")
def mail_draft(payload: dict): return {"result": mail.create_draft(str(payload["account"]), str(payload["to"]), str(payload["subject"]), str(payload["body"]))}


@router.post("/api/mail/archive")
def mail_archive(payload: dict): return {"result": mail.archive(str(payload["account"]), str(payload["message_id"]), str(payload.get("folder", "INBOX")))}
