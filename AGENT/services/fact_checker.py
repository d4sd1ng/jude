from __future__ import annotations

import json
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urlparse

from json_repair import loads as repair_json

from services.database import connection
from services.scraper import ScraperService


class FactCheckerService:
    def __init__(self, router, scraper: ScraperService | None = None):
        self.router, self.scraper = router, scraper or ScraperService()

    @staticmethod
    def _json(text: str) -> dict:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < start:
            raise ValueError("Modellantwort enthält kein JSON-Objekt.")
        value = repair_json(text[start:end + 1])
        if not isinstance(value, dict):
            raise ValueError("Modellantwort ist kein JSON-Objekt.")
        return value

    def _ask_json(self, prompt: str) -> dict:
        error = ""
        for _ in range(3):
            correction = (f"\nDie vorige Antwort war ungültig ({error}). Gib ausschließlich ein vollständiges JSON-Objekt "
                          "mit doppelten Anführungszeichen zurück." if error else "")
            response = self.router.call_with_fallback([{"role": "user", "content": prompt + correction}])
            try:
                return self._json(str(response.get("content", "")))
            except (ValueError, json.JSONDecodeError) as exc:
                error = str(exc)
        raise ValueError("Das Modell lieferte dreimal kein auswertbares JSON: " + error)

    def check(self, url: str) -> dict:
        source = self.scraper.extract(url)
        if len(source.get("text", "").strip()) < 80:
            raise ValueError("Aus der URL konnte nicht genug prüfbarer Text bzw. kein Video-Transkript extrahiert werden.")
        try:
            claim_limit = max(1, min(int(os.getenv("FACT_CHECK_MAX_CLAIMS", "5")), 8))
        except ValueError as exc:
            raise ValueError("FACT_CHECK_MAX_CLAIMS muss eine Zahl zwischen 1 und 8 sein.") from exc
        extraction = self._ask_json(
            f"Extrahiere aus dem folgenden Inhalt maximal {claim_limit} "
            "zentrale, konkrete, extern prüfbare Tatsachenbehauptungen. "
            "Ignoriere Meinungen, Prognosen, Werbung und Satire. Bestimme zusätzlich subject als den spezifischsten "
            "Eigennamen, die ID, Person, Organisation oder das Ereignis des Inhalts. Jeder search_query muss subject "
            "sowie charakteristische Namen, Zahlen oder Datumsangaben der Behauptung enthalten. Antworte nur als JSON "
            "{subject,claims:[{claim,search_query}]}.\n" +
            json.dumps({"title": source["title"], "url": source["url"], "text": source["text"][:50000]}, ensure_ascii=False)
        )
        claims = [item for item in extraction.get("claims", []) if isinstance(item, dict) and item.get("claim")][:claim_limit]
        subject = str(extraction.get("subject") or source["title"]).strip()[:200]
        identifiers = list(dict.fromkeys(re.findall(r"\b[A-Z]{2,}(?:[- ]?[A-Z0-9]+)*[- ]?\d[\w.-]*\b", source["text"])))[:4]
        search_context = " ".join([subject, *(item for item in identifiers if item.lower() not in subject.lower())]).strip()[:300]
        checked = []
        source_domain = urlparse(source["url"]).hostname or ""
        for claim in claims:
            primary_query = f'"{search_context}" {claim.get("search_query") or claim.get("claim")}'
            candidates = self.scraper.search(primary_query, 6)
            if len(candidates) < 4:
                candidates.extend(self.scraper.search(f'"{search_context}" {claim.get("claim")}', 6 - len(candidates)))
            if len(candidates) < 4:
                candidates.extend(self.scraper.search(f'"{search_context}"', 6 - len(candidates)))
            candidates = list({item["url"]: item for item in candidates}.values())
            evidence = []
            seen_domains = {source_domain.removeprefix("www.")}
            selected = []
            for candidate in candidates:
                domain = (urlparse(candidate["url"]).hostname or "").removeprefix("www.")
                if not domain or domain in seen_domains:
                    continue
                seen_domains.add(domain)
                selected.append((candidate, domain))
                if len(selected) >= 6:
                    break
            with ThreadPoolExecutor(max_workers=min(4, len(selected) or 1)) as pool:
                futures = {pool.submit(self.scraper.extract, candidate["url"]): domain for candidate, domain in selected}
                for future in as_completed(futures):
                    try:
                        page = future.result()
                    except Exception:
                        continue
                    if len(page.get("text", "").strip()) < 120:
                        continue
                    evidence.append({"url": page["url"], "title": page["title"], "published_at": page["published_at"],
                                     "domain": futures[future], "text": page["text"][:18000]})
                    if len(evidence) >= 3:
                        for pending in futures:
                            pending.cancel()
                        break
            for index, item in enumerate(evidence, start=1):
                item["evidence_id"] = f"E{index}"
            verdict_prompt = """Bewerte die Tatsachenbehauptung ausschließlich anhand der gelieferten Quellen.
Eine Quelle zählt nur, wenn sie die Behauptung inhaltlich direkt belegt oder widerlegt. Unabhängige Domains sind erforderlich.
Bevorzuge Primärquellen, Behörden, wissenschaftliche Institutionen und redaktionell verantwortete Medien.
independent bedeutet: anderer Herausgeber und andere Domain als der geprüfte Ursprung und die übrigen Belege. Setze es bei
verschiedenen gelieferten Domains auf true, außer der Text kennzeichnet die Quelle ausdrücklich als Kopie oder Syndikat.
Antworte nur als JSON mit explanation, uncertainty und assessments. assessments ist eine Liste mit exakt:
evidence_id, serious (boolean), independent (boolean), stance (supports|contradicts|irrelevant), reason.
Verwende ausschließlich die gelieferten IDs E1, E2 usw. und gib keine URL zurück.
Bewerte Seriosität und inhaltlichen Beleg getrennt; eine bloße Erwähnung ist irrelevant.
""" + json.dumps({"claim": claim.get("claim"), "evidence": evidence}, ensure_ascii=False)
            try:
                judged = self._ask_json(verdict_prompt)
            except ValueError as exc:
                judged = {"explanation": "Automatische Einzelbewertung war nicht maschinell auswertbar.",
                          "uncertainty": str(exc), "assessments": []}
            allowed = {item["url"] for item in evidence}
            evidence_by_id = {item["evidence_id"]: item["url"] for item in evidence}
            assessments = []
            raw_assessments = judged.get("assessments") or judged.get("evidence") or []
            for item in raw_assessments:
                if not isinstance(item, dict):
                    continue
                url = evidence_by_id.get(str(item.get("evidence_id", ""))) or item.get("url")
                if url in allowed:
                    normalized = dict(item)
                    normalized["url"] = url
                    assessments.append(normalized)
            qualified = [item for item in assessments if item.get("serious") is True and item.get("independent") is True]
            supporting = list(dict.fromkeys(item["url"] for item in qualified if item.get("stance") == "supports"))
            contradicting = list(dict.fromkeys(item["url"] for item in qualified if item.get("stance") == "contradicts"))
            support_domains = {(urlparse(item).hostname or "").removeprefix("www.") for item in supporting}
            contradict_domains = {(urlparse(item).hostname or "").removeprefix("www.") for item in contradicting}
            status = "contradicted" if contradict_domains else "confirmed" if len(support_domains) >= 2 else "partially_confirmed" if len(support_domains) == 1 else "unverified"
            checked.append({"claim": claim.get("claim"), "status": status, "explanation": judged.get("explanation", ""),
                            "uncertainty": judged.get("uncertainty", ""), "supporting_urls": supporting,
                            "contradicting_urls": contradicting, "source_assessments": assessments})
        statuses = [item.get("status") for item in checked]
        overall = "confirmed" if statuses and all(s == "confirmed" for s in statuses) else (
            "contradicted" if "contradicted" in statuses else "partially_confirmed" if any(s in {"confirmed", "partially_confirmed"} for s in statuses) else "unverified")
        report = {"id": uuid.uuid4().hex[:16], "source": {k: source[k] for k in ("url", "title", "author", "published_at")},
                  "search_subject": subject, "search_identifiers": identifiers,
                  "verdict": overall, "claims": checked, "created_at": datetime.now(timezone.utc).isoformat(),
                  "notice": "Fehlende Belege sind kein Beweis für Falschheit. Quellen bitte selbst öffnen und prüfen."}
        with connection() as db:
            db.execute("INSERT INTO fact_checks(id,source_url,verdict,created_at,report_json) VALUES(?,?,?,?,?)",
                       (report["id"], source["url"], overall, report["created_at"], json.dumps(report, ensure_ascii=False)))
        return report
