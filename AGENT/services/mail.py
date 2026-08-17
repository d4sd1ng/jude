from __future__ import annotations

import email
import imaplib
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.policy import default

from bs4 import BeautifulSoup


def _env(prefix: str, key: str, default_value: str = "") -> str:
    return os.getenv(f"MAIL_{prefix}_{key}", default_value)


ACCOUNTS = {
    "gmx": {"address": "d4sd1ng@gmx.de", "imap": "imap.gmx.net", "smtp": "mail.gmx.net", "prefix": "GMX"},
    "yahoo": {"address": "t.schneiderx@yahoo.de", "imap": "imap.mail.yahoo.com", "smtp": "smtp.mail.yahoo.com", "prefix": "YAHOO"},
    "gmail": {"address": "d4sd1ng@gmail.com", "imap": "imap.gmail.com", "smtp": "smtp.gmail.com", "prefix": "GMAIL"},
    "proton_nurovelle": {"address": "nurovelle@proton.me", "imap": "127.0.0.1", "smtp": "127.0.0.1", "prefix": "PROTON_NUROVELLE", "bridge": True},
    "proton_mongojude": {"address": "mongojude@proton.me", "imap": "127.0.0.1", "smtp": "127.0.0.1", "prefix": "PROTON_MONGOJUDE", "bridge": True},
}


class MailService:
    def account_status(self) -> list[dict]:
        return [{"id": name, "address": cfg["address"], "configured": bool(_env(cfg["prefix"], "PASSWORD")),
                 "bridge": bool(cfg.get("bridge"))} for name, cfg in ACCOUNTS.items()]

    def _config(self, account: str) -> dict:
        if account not in ACCOUNTS:
            raise ValueError("Unbekanntes Mailkonto.")
        cfg = dict(ACCOUNTS[account])
        prefix = cfg["prefix"]
        cfg["username"] = _env(prefix, "USERNAME", cfg["address"])
        cfg["password"] = _env(prefix, "PASSWORD")
        cfg["imap"] = _env(prefix, "IMAP_HOST", cfg["imap"])
        cfg["smtp"] = _env(prefix, "SMTP_HOST", cfg["smtp"])
        cfg["imap_port"] = int(_env(prefix, "IMAP_PORT", "1143" if cfg.get("bridge") else "993"))
        cfg["smtp_port"] = int(_env(prefix, "SMTP_PORT", "1025" if cfg.get("bridge") else "465"))
        cfg["verify_tls"] = _env(prefix, "VERIFY_TLS", "false" if cfg.get("bridge") else "true").lower() in {
            "1", "true", "yes", "on",
        }
        if not cfg["password"]:
            raise RuntimeError(f"MAIL_{prefix}_PASSWORD fehlt.")
        return cfg

    def _imap(self, account: str):
        cfg = self._config(account)
        if cfg.get("bridge"):
            client = imaplib.IMAP4(cfg["imap"], cfg["imap_port"])
            client.starttls(ssl_context=self._tls_context(cfg["verify_tls"]))
        else:
            client = imaplib.IMAP4_SSL(cfg["imap"], cfg["imap_port"])
        client.login(cfg["username"], cfg["password"])
        return client

    @staticmethod
    def _folders(client) -> dict[str, str]:
        result: dict[str, str] = {}
        status, folders = client.list()
        if status != "OK":
            return result
        for raw in folders:
            line = raw.decode(errors="replace")
            name = line.rsplit(' "/" ', 1)[-1].strip('"')
            lowered = line.lower()
            if "\\drafts" in lowered:
                result["drafts"] = name
            if "\\trash" in lowered:
                result["trash"] = name
            if "\\archive" in lowered or "\\all" in lowered or name.lower() in {"archive", "archiv"}:
                result["archive"] = name
        return result

    def search(self, account: str, query: str = "ALL", limit: int = 30, folder: str = "INBOX") -> list[dict]:
        client = self._imap(account)
        try:
            client.select(folder, readonly=True)
            clean_query = query.strip().replace("\\", " ").replace('"', " ")[:200]
            criterion = "ALL" if clean_query.upper() == "ALL" else f'(OR SUBJECT "{clean_query}" FROM "{clean_query}")'
            status, data = client.search(None, criterion)
            if status != "OK":
                raise RuntimeError("IMAP-Suche fehlgeschlagen.")
            ids = data[0].split()[-max(1, min(limit, 100)):]
            messages = []
            for message_id in reversed(ids):
                status, raw = client.fetch(message_id, "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID)])")
                if status != "OK" or not raw or not isinstance(raw[0], tuple):
                    continue
                msg = email.message_from_bytes(raw[0][1], policy=default)
                messages.append({"id": message_id.decode(), "from": str(msg.get("From", "")), "to": str(msg.get("To", "")),
                                 "subject": str(msg.get("Subject", "")), "date": str(msg.get("Date", "")),
                                 "message_id": str(msg.get("Message-ID", "")), "folder": folder})
            return messages
        finally:
            client.logout()

    def read(self, account: str, message_id: str, folder: str = "INBOX") -> dict:
        client = self._imap(account)
        try:
            client.select(folder, readonly=True)
            status, raw = client.fetch(message_id, "(BODY.PEEK[])")
            if status != "OK" or not raw or not isinstance(raw[0], tuple):
                raise RuntimeError("Nachricht nicht gefunden.")
            msg = email.message_from_bytes(raw[0][1], policy=default)
            text = ""
            for part in msg.walk() if msg.is_multipart() else [msg]:
                if part.get_content_type() == "text/plain" and part.get_content_disposition() != "attachment":
                    text += part.get_content() + "\n"
            if not text.strip():
                for part in msg.walk() if msg.is_multipart() else [msg]:
                    if part.get_content_type() == "text/html" and part.get_content_disposition() != "attachment":
                        text += BeautifulSoup(part.get_content(), "html.parser").get_text("\n", strip=True) + "\n"
            return {"id": message_id, "from": str(msg.get("From", "")), "to": str(msg.get("To", "")),
                    "subject": str(msg.get("Subject", "")), "date": str(msg.get("Date", "")), "text": text.strip()}
        finally:
            client.logout()

    def create_draft(self, account: str, to: str, subject: str, body: str) -> str:
        cfg = self._config(account)
        msg = self._message(cfg["address"], to, subject, body)
        client = self._imap(account)
        try:
            folder = self._folders(client).get("drafts", "Drafts")
            status, _ = client.append(folder, "(\\Draft)", None, msg.as_bytes())
            if status != "OK":
                raise RuntimeError(f"Entwurf konnte nicht in {folder} gespeichert werden.")
            return f"Entwurf in {account}/{folder} gespeichert"
        finally:
            client.logout()

    def archive(self, account: str, message_id: str, folder: str = "INBOX") -> str:
        client = self._imap(account)
        try:
            client.select(folder)
            archive = self._folders(client).get("archive", "Archive")
            status, _ = client.copy(message_id, archive)
            if status != "OK":
                raise RuntimeError(f"Archivieren nach {archive} fehlgeschlagen.")
            client.store(message_id, "+FLAGS", "\\Deleted")
            client.expunge()
            return f"Nachricht nach {archive} archiviert"
        finally:
            client.logout()

    def send_confirmed(self, account: str, to: str, subject: str, body: str) -> str:
        cfg = self._config(account)
        msg = self._message(cfg["address"], to, subject, body)
        if cfg.get("bridge"):
            smtp = smtplib.SMTP(cfg["smtp"], cfg["smtp_port"], timeout=20)
            smtp.starttls(context=self._tls_context(cfg["verify_tls"]))
        else:
            smtp = smtplib.SMTP_SSL(cfg["smtp"], cfg["smtp_port"], timeout=20)
        try:
            smtp.login(cfg["username"], cfg["password"])
            smtp.send_message(msg)
        finally:
            smtp.quit()
        return f"E-Mail über {account} gesendet"

    def delete_confirmed(self, account: str, message_id: str, folder: str = "INBOX") -> str:
        client = self._imap(account)
        try:
            client.select(folder)
            trash = self._folders(client).get("trash")
            if trash:
                status, _ = client.copy(message_id, trash)
                if status != "OK":
                    raise RuntimeError("Verschieben in Papierkorb fehlgeschlagen.")
            client.store(message_id, "+FLAGS", "\\Deleted")
            client.expunge()
            return f"Nachricht aus {folder} gelöscht" if not trash else f"Nachricht nach {trash} verschoben"
        finally:
            client.logout()

    @staticmethod
    def _message(sender: str, to: str, subject: str, body: str) -> EmailMessage:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = sender, to, subject
        msg.set_content(body)
        return msg

    @staticmethod
    def _tls_context(verify: bool) -> ssl.SSLContext:
        context = ssl.create_default_context()
        if not verify:
            # Proton Bridge lauscht ausschließlich lokal und verwendet ein selbstsigniertes Zertifikat.
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        return context
