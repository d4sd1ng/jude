from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from json_repair import loads as repair_json
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from services.database import connection

OUTPUT_DIR = Path("/media/d4sd1ng/AI-Data/Essensplan")



class MealPlanService:
    HIGH_CARB_TERMS = {
        "kartoffel", "reis", "nudel", "pasta", "brot", "brötchen", "broetchen", "müsli", "muesli",
        "quinoa", "couscous", "bulgur", "hafer", "zucker", "cornflakes",
    }
    def __init__(self, router=None):
        self.router = router

    def generate(self, days: int = 7, people: int = 2) -> dict:
        if days not in range(7, 11) or people not in {1, 2}:
            raise ValueError("Erlaubt sind 7-10 Tage und 1-2 Personen.")
        if self.router is None:
            raise RuntimeError("Für die Planerstellung ist ein Modellrouter erforderlich.")
        prompt = f"""Erstelle einen günstigen Low-Carb-Essensplan für {people} Person(en) und {days} Tage ohne Allergien.
Antworte ausschließlich als JSON-Objekt mit: title, people, days (Array). Jeder Tag enthält day, breakfast, lunch, dinner.
Jede Mahlzeit enthält name und ingredients (Array mit item, amount). Ergänze shopping_list als Array mit category, item, amount.
Nutze leicht erhältliche deutsche Supermarktprodukte, wiederverwende Zutaten und vermeide teure Spezialprodukte.
Low Carb ist verbindlich: keine Kartoffeln, Reis, Nudeln/Pasta, Brot/Brötchen, Müsli, Quinoa, Couscous,
Bulgur, Hafer, Cornflakes oder zugesetzten Zucker. Die Gerichte müssen abwechslungsreich sein; kein Tagesmenü darf
wiederholt werden und mindestens zwei Drittel aller Mahlzeitennamen müssen verschieden sein.
Jede Zutat muss als einzelner atomarer Produktname ohne Klammerlisten erscheinen. shopping_list enthält jeden
verwendeten Produktnamen exakt einmal mit einer realistischen, für alle Tage summierten Gesamtmenge.
Halte Namen und Mengen knapp, damit das vollständige JSON sicher in eine Antwort passt."""
        error = ""
        for _ in range(2):
            correction = (f"\nDer vorige Plan war ungültig ({error}). Liefere das vollständige, kompakte JSON erneut."
                          if error else "")
            response = self.router.call_with_fallback([{"role": "user", "content": prompt + correction}])
            try:
                plan = self._extract_json(str(response.get("content", "")))
                self._validate(plan, days)
                plan["people"] = people
                plan["generation_method"] = "local_model"
                break
            except (ValueError, json.JSONDecodeError) as exc:
                error = str(exc)
        else:
            plan = self._fallback_plan(days, people)
            self._validate(plan, days)
        plan_id = uuid.uuid4().hex[:12]
        pdf = self.render_pdf(plan, plan_id)
        with connection() as db:
            db.execute("INSERT INTO meal_plans(id,created_at,days,people,plan_json,pdf_path) VALUES(?,?,?,?,?,?)",
                       (plan_id, datetime.now(timezone.utc).isoformat(), days, people, json.dumps(plan, ensure_ascii=False), str(pdf)))
        return {"id": plan_id, "plan": plan, "pdf_path": str(pdf)}

    @staticmethod
    def _extract_json(text: str) -> dict:
        fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        candidate = fenced.group(1) if fenced else text[text.find("{"):text.rfind("}") + 1]
        if not candidate.strip().startswith("{"):
            raise ValueError("Das Modell lieferte kein JSON-Objekt.")
        value = repair_json(candidate)
        if not isinstance(value, dict):
            raise ValueError("Das Modell lieferte kein JSON-Objekt.")
        return value

    @staticmethod
    def _validate(plan: dict, expected_days: int) -> None:
        if not isinstance(plan.get("days"), list) or len(plan["days"]) != expected_days:
            raise ValueError("Das Modell lieferte nicht die erwartete Tagesanzahl.")
        for day in plan["days"]:
            if not all(key in day for key in ("day", "breakfast", "lunch", "dinner")):
                raise ValueError("Ein Tag ist unvollständig.")
            for key in ("breakfast", "lunch", "dinner"):
                meal = day[key]
                if not isinstance(meal, dict) or not isinstance(meal.get("ingredients"), list) or not meal.get("name"):
                    raise ValueError("Eine Mahlzeit hat kein gültiges Gericht oder keine Zutatenliste.")
                if any(not isinstance(item, dict) or not item.get("item") or not item.get("amount")
                       for item in meal["ingredients"]):
                    raise ValueError("Eine Zutat ist unvollständig.")
                if any("," in str(item["item"]) or "(" in str(item["item"]) for item in meal["ingredients"]):
                    raise ValueError("Zutaten müssen atomare Produktnamen ohne Listen oder Klammern sein.")
        if not isinstance(plan.get("shopping_list"), list):
            raise ValueError("Einkaufsliste fehlt.")
        if any(not isinstance(item, dict) or not all(item.get(key) for key in ("category", "item", "amount"))
               for item in plan["shopping_list"]):
            raise ValueError("Einkaufsliste enthält einen unvollständigen Eintrag.")
        meal_names = [str(day[key]["name"]).strip().casefold() for day in plan["days"]
                      for key in ("breakfast", "lunch", "dinner")]
        if len(set(meal_names)) < expected_days * 2:
            raise ValueError("Der Essensplan wiederholt zu viele Mahlzeiten.")
        day_menus = [tuple(str(day[key]["name"]).strip().casefold() for key in ("breakfast", "lunch", "dinner"))
                     for day in plan["days"]]
        if len(set(day_menus)) != len(day_menus):
            raise ValueError("Ein vollständiges Tagesmenü wurde wiederholt.")
        combined = " ".join(meal_names + [str(item["item"]).casefold() for day in plan["days"]
                                           for key in ("breakfast", "lunch", "dinner")
                                           for item in day[key]["ingredients"]])
        forbidden = sorted(term for term in MealPlanService.HIGH_CARB_TERMS if term in combined)
        if forbidden:
            raise ValueError("Nicht Low-Carb-konforme Zutaten: " + ", ".join(forbidden))
        ingredient_names = {str(item["item"]).strip().casefold() for day in plan["days"]
                            for key in ("breakfast", "lunch", "dinner") for item in day[key]["ingredients"]}
        shopping_names = {str(item["item"]).strip().casefold() for item in plan["shopping_list"]}
        missing = sorted(ingredient_names - shopping_names)
        if missing:
            raise ValueError("Einkaufsliste fehlen Zutaten: " + ", ".join(missing[:8]))

    @staticmethod
    def render_pdf(plan: dict, plan_id: str) -> Path:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        target = OUTPUT_DIR / f"essensplan_{plan_id}.pdf"
        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(name="JudeTitle", parent=styles["Title"], fontName="Helvetica-Bold",
                                  fontSize=22, leading=26, textColor=colors.HexColor("#16324F"), alignment=TA_CENTER, spaceAfter=12))
        styles.add(ParagraphStyle(name="Day", parent=styles["Heading2"], fontName="Helvetica-Bold",
                                  fontSize=15, textColor=colors.HexColor("#245B78"), spaceBefore=10, spaceAfter=6))
        styles.add(ParagraphStyle(name="Cell", parent=styles["BodyText"], fontName="Helvetica",
                                  fontSize=7.5, leading=9, spaceAfter=0))
        styles.add(ParagraphStyle(name="CellHeader", parent=styles["BodyText"], fontName="Helvetica-Bold",
                                  fontSize=8, leading=9, textColor=colors.white, spaceAfter=0))

        def footer(canvas, doc):
            canvas.saveState()
            canvas.setFont("Helvetica", 8)
            canvas.setFillColor(colors.HexColor("#52606D"))
            canvas.drawString(18 * mm, 12 * mm, "Jude Low-Carb-Essensplan")
            canvas.drawRightString(192 * mm, 12 * mm, f"Seite {doc.page}")
            canvas.restoreState()

        story = [Paragraph(escape(str(plan.get("title", "Low-Carb-Essensplan"))), styles["JudeTitle"]),
                 Paragraph(f"Für {plan.get('people', 1)} Person(en) - günstig - ohne Allergien", styles["BodyText"]), Spacer(1, 8)]
        labels = (("breakfast", "Frühstück"), ("lunch", "Mittagessen"), ("dinner", "Abendessen"))
        for day in plan["days"]:
            day_title = Paragraph(escape(str(day["day"])), styles["Day"])
            table_data = [[Paragraph(label, styles["CellHeader"]) for label in ("Mahlzeit", "Gericht", "Zutaten")]]
            for key, label in labels:
                meal = day[key]
                ingredients = ", ".join(f"{item.get('item', '')} {item.get('amount', '')}".strip() for item in meal.get("ingredients", []))
                table_data.append([Paragraph(escape(label), styles["Cell"]),
                                   Paragraph(escape(str(meal.get("name", ""))), styles["Cell"]),
                                   Paragraph(escape(ingredients), styles["Cell"])])
            table = Table(table_data, colWidths=[28 * mm, 55 * mm, 92 * mm], repeatRows=1)
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#16324F")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F7FA")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            story.append(KeepTogether([day_title, table, Spacer(1, 8)]))
        story.extend([PageBreak(), Paragraph("Einkaufsliste", styles["JudeTitle"])])
        shopping = [[Paragraph(label, styles["CellHeader"]) for label in ("Kategorie", "Produkt", "Menge")]]
        shopping += [[Paragraph(escape(str(item.get("category", ""))), styles["Cell"]),
                      Paragraph(escape(str(item.get("item", ""))), styles["Cell"]),
                      Paragraph(escape(str(item.get("amount", ""))), styles["Cell"])] for item in plan["shopping_list"]]
        table = Table(shopping, colWidths=[45 * mm, 85 * mm, 45 * mm], repeatRows=1)
        table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#16324F")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                                   ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
                                   ("FONTSIZE", (0, 0), (-1, -1), 7.5), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                   ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F7FA")]),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]))
        story.append(table)
        doc = SimpleDocTemplate(str(target), pagesize=A4, rightMargin=17 * mm, leftMargin=17 * mm, topMargin=16 * mm, bottomMargin=20 * mm,
                                title=str(plan.get("title", "Low-Carb-Essensplan")), author="Jude")
        doc.build(story, onFirstPage=footer, onLaterPages=footer)
        return target

    @staticmethod
    def _fallback_plan(days: int, people: int) -> dict:
        breakfasts = [
            ("Spinat-Feta-Omelett", {"Eier": (2, "Stück"), "Spinat": (60, "g"), "Feta": (40, "g")}),
            ("Joghurt mit Beeren und Walnüssen", {"Griechischer Joghurt": (150, "g"), "Beeren": (60, "g"), "Walnüsse": (20, "g")}),
            ("Rührei mit Pilzen", {"Eier": (2, "Stück"), "Champignons": (100, "g"), "Butter": (10, "g")}),
            ("Kräuterquark mit Gurke", {"Magerquark": (200, "g"), "Gurke": (100, "g"), "Kräuter": (10, "g")}),
            ("Avocado-Ei-Teller", {"Eier": (2, "Stück"), "Avocado": (0.5, "Stück"), "Tomaten": (100, "g")}),
            ("Chia-Pudding mit Beeren", {"Chiasamen": (30, "g"), "Mandelmilch": (150, "ml"), "Beeren": (50, "g")}),
            ("Hüttenkäse mit Paprika", {"Hüttenkäse": (200, "g"), "Paprika": (100, "g")}),
            ("Eiermuffins mit Käse", {"Eier": (2, "Stück"), "Paprika": (50, "g"), "Käse": (30, "g")}),
            ("Räucherlachs-Gurken-Teller", {"Räucherlachs": (80, "g"), "Gurke": (150, "g"), "Frischkäse": (30, "g")}),
            ("Shakshuka", {"Eier": (2, "Stück"), "Tomaten": (150, "g"), "Paprika": (80, "g")}),
        ]
        lunches = [
            ("Hähnchen-Feta-Salat", {"Hähnchenbrust": (150, "g"), "Blattsalat": (100, "g"), "Feta": (40, "g"), "Gurke": (80, "g")}),
            ("Thunfisch-Zucchini-Salat", {"Thunfisch": (120, "g"), "Zucchini": (150, "g"), "Tomaten": (100, "g")}),
            ("Puten-Salat-Wraps", {"Putenbrust": (150, "g"), "Salatblätter": (100, "g"), "Paprika": (80, "g")}),
            ("Griechischer Salat", {"Feta": (80, "g"), "Gurke": (150, "g"), "Tomaten": (150, "g"), "Oliven": (30, "g")}),
            ("Blumenkohl-Cremesuppe", {"Blumenkohl": (250, "g"), "Sahne": (40, "ml"), "Käse": (30, "g")}),
            ("Zucchini-Spaghetti mit Hack", {"Zucchini": (250, "g"), "Rinderhack": (150, "g"), "Tomaten": (120, "g")}),
            ("Rind-Brokkoli-Pfanne", {"Rindfleisch": (150, "g"), "Brokkoli": (200, "g"), "Sojasauce": (15, "ml")}),
            ("Lachs-Spinat-Pfanne", {"Lachsfilet": (150, "g"), "Spinat": (180, "g"), "Sahne": (30, "ml")}),
            ("Mozzarella-Tomaten-Teller", {"Mozzarella": (125, "g"), "Tomaten": (200, "g"), "Basilikum": (10, "g")}),
            ("Eiersalat mit Radieschen", {"Eier": (2, "Stück"), "Radieschen": (100, "g"), "Mayonnaise": (20, "g"), "Blattsalat": (80, "g")}),
        ]
        dinners = [
            ("Ofenlachs mit Brokkoli", {"Lachsfilet": (180, "g"), "Brokkoli": (250, "g"), "Zitrone": (0.25, "Stück")}),
            ("Hähnchen-Zucchini-Pfanne", {"Hähnchenbrust": (180, "g"), "Zucchini": (220, "g"), "Paprika": (100, "g")}),
            ("Gefüllte Paprika mit Hack", {"Paprika": (200, "g"), "Rinderhack": (160, "g"), "Käse": (40, "g")}),
            ("Putenbällchen mit Blumenkohlpüree", {"Putenhack": (170, "g"), "Blumenkohl": (250, "g"), "Butter": (10, "g")}),
            ("Rindfleisch mit grünen Bohnen", {"Rindfleisch": (170, "g"), "Grüne Bohnen": (220, "g"), "Sojasauce": (15, "ml")}),
            ("Zucchini-Feta-Auflauf", {"Zucchini": (250, "g"), "Feta": (80, "g"), "Eier": (1, "Stück")}),
            ("Kabeljau auf Rahmspinat", {"Kabeljau": (180, "g"), "Spinat": (220, "g"), "Sahne": (30, "ml")}),
            ("Schweinefilet mit Pilzen", {"Schweinefilet": (180, "g"), "Champignons": (180, "g"), "Sahne": (30, "ml")}),
            ("Tofu-Brokkoli-Wok", {"Tofu": (180, "g"), "Brokkoli": (220, "g"), "Sojasauce": (15, "ml")}),
            ("Hähnchen-Curry mit Blumenkohl", {"Hähnchenbrust": (180, "g"), "Blumenkohl": (250, "g"), "Kokosmilch": (80, "ml")}),
        ]
        names = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag",
                 "Tag 8", "Tag 9", "Tag 10"]
        totals: dict[tuple[str, str], float] = {}

        def meal(recipe):
            name, ingredients = recipe
            rows = []
            for item, (quantity, unit) in ingredients.items():
                total = quantity * people
                totals[(item, unit)] = totals.get((item, unit), 0) + total
                rows.append({"item": item, "amount": MealPlanService._format_amount(total, unit)})
            return {"name": name, "ingredients": rows}

        day_rows = [{"day": names[index], "breakfast": meal(breakfasts[index]),
                     "lunch": meal(lunches[index]), "dinner": meal(dinners[index])} for index in range(days)]
        categories = {
            "Eier": "Eier", "Hähnchenbrust": "Fleisch", "Putenbrust": "Fleisch", "Putenhack": "Fleisch",
            "Rinderhack": "Fleisch", "Rindfleisch": "Fleisch", "Schweinefilet": "Fleisch", "Lachsfilet": "Fisch",
            "Räucherlachs": "Fisch", "Thunfisch": "Fisch", "Kabeljau": "Fisch", "Tofu": "Protein",
        }
        dairy = {"Feta", "Käse", "Butter", "Sahne", "Magerquark", "Hüttenkäse", "Frischkäse", "Mozzarella", "Griechischer Joghurt"}
        shopping = []
        for (item, unit), quantity in sorted(totals.items()):
            category = categories.get(item, "Milchprodukte" if item in dairy else "Gemüse und Vorrat")
            shopping.append({"category": category, "item": item, "amount": MealPlanService._format_amount(quantity, unit)})
        return {"title": "Abwechslungsreicher Low-Carb-Essensplan", "people": people, "days": day_rows,
                "shopping_list": shopping, "generation_method": "curated_fallback"}

    @staticmethod
    def _format_amount(quantity: float, unit: str) -> str:
        if unit in {"g", "ml"} and quantity >= 1000:
            value, label = quantity / 1000, "kg" if unit == "g" else "l"
        else:
            value, label = quantity, unit
        number = f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")
        return f"{number} {label}"
