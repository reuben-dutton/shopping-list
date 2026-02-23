#!/usr/bin/env python3
"""
prep_schedule.py
Reads recipe JSON files from ./recipes/*.json, finds all sub-recipes with a
non-NONE prep window, and renders a single-page prep schedule PDF.

Each sub-recipe block shows:
  - Sub-recipe name + parent recipe
  - How far ahead it can be prepared
  - Ingredients used
  - Steps

Usage:
    python prep_schedule.py [--recipes-dir recipes] [--output prep_schedule.pdf]

Dependencies:
    pip install reportlab pydantic
    models.py and measurements.py must be on sys.path (e.g. same directory).
"""

import argparse
import json
import sys
from pathlib import Path

from models import Branch, Ingredient, PrepWindow, Recipe, Step, SubRecipe
from measurements import MeasurementBase

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    KeepTogether,
)


# ── JSON loading (shared with shopping_list.py) ───────────────────────────────

def _is_subrecipe(obj: dict) -> bool:
    return (
        obj.get("_type") == "SubRecipe"
        or ("ingredients" in obj and "steps" in obj and "measurement" not in obj)
    )


def _is_branch(obj: dict) -> bool:
    return obj.get("_type") == "Branch" or "options" in obj


def _parse_ingredient(obj: dict) -> Ingredient:
    return Ingredient(**{k: v for k, v in obj.items() if k != "_type"})


def _parse_subrecipe(obj: dict) -> SubRecipe:
    obj = {k: v for k, v in obj.items() if k != "_type"}
    obj["ingredients"] = [_parse_ingredient(i) for i in obj["ingredients"]]
    obj["steps"] = [Step(**s) for s in obj["steps"]]
    return SubRecipe(**obj)


def _parse_step_or_branch(obj: dict) -> Step | Branch:
    if _is_branch(obj):
        obj = {k: v for k, v in obj.items() if k != "_type"}
        return Branch(options={
            label: [Step(**s) for s in steps]
            for label, steps in obj["options"].items()
        })
    return Step(**obj)


def load_recipe(path: Path) -> Recipe:
    raw = json.loads(path.read_text())
    ingredients = [
        _parse_subrecipe(i) if _is_subrecipe(i) else _parse_ingredient(i)
        for i in raw["ingredients"]
    ]
    steps = [_parse_step_or_branch(s) for s in raw["steps"]]
    return Recipe(
        name=raw["name"],
        source=raw.get("source"),
        url=raw.get("url"),
        ingredients=ingredients,
        steps=steps,
    )


# ── Data collection ───────────────────────────────────────────────────────────

def collect_prep_subrecipes(
    recipes: list[tuple[str, Recipe]]
) -> list[tuple[str, SubRecipe]]:
    """
    Return all (recipe_name, subrecipe) pairs where the sub-recipe has a
    prep window other than NONE, preserving recipe order.
    """
    result = []
    for recipe_name, recipe in recipes:
        for item in recipe.ingredients:
            if isinstance(item, SubRecipe) and item.window != PrepWindow.NONE:
                result.append((recipe_name, item))
    return result


# ── Ingredient formatting ─────────────────────────────────────────────────────

def float_to_string(value: float) -> str:
    if value == int(value):
        return str(int(value))
    frac_parts = {
        0.125: "1/8", 0.25: "1/4", 0.333: "1/3", 0.375: "3/8",
        0.5: "1/2", 0.625: "5/8", 0.667: "2/3", 0.75: "3/4", 0.875: "7/8",
    }
    whole = int(value)
    remainder = value - whole
    for fval, fstr in frac_parts.items():
        if abs(value - fval) < 0.005:
            return fstr
        if whole > 0 and abs(remainder - fval) < 0.005:
            return f"{whole} {fstr}"
    return f"{value:.2f}".rstrip("0").rstrip(".")


_COMPACT_UNITS = {"g", "kg"}
_IMPERIAL_WEIGHT_TO_G = {"oz": 28.3495, "lb": 453.592}


def _normalise(amount: float, unit: str | None) -> tuple[float, str | None]:
    if unit in _IMPERIAL_WEIGHT_TO_G:
        g = amount * _IMPERIAL_WEIGHT_TO_G[unit]
        return (g / 1000, "kg") if g >= 1000 else (g, "g")
    return amount, unit


def format_ingredient(ing: Ingredient) -> tuple[str, str]:
    """Return (quantity_str, name_str) for an ingredient."""
    amount, unit = _normalise(ing.measurement.amount, ing.measurement.unit)
    s = float_to_string(amount)
    if unit is None:
        qty = s
    elif unit in _COMPACT_UNITS:
        qty = f"{s}{unit}"
    else:
        qty = f"{s} {unit}"

    intrinsics_prefix = ", ".join(ing.intrinsics)
    name_parts = [p for p in [intrinsics_prefix or None, ing.variety, ing.name] if p]
    name = " ".join(name_parts)
    if ing.preparation:
        name += f" ({', '.join(ing.preparation)})"
    return qty, name


# ── PDF generation ────────────────────────────────────────────────────────────

NAVY    = colors.HexColor("#1C3557")
BLUE    = colors.HexColor("#3B7DD8")
GREY    = colors.HexColor("#888888")
ALT_ROW = colors.HexColor("#F4F7FB")
MID     = colors.HexColor("#555555")
LIGHT   = colors.HexColor("#999999")
RULE    = colors.HexColor("#CCCCCC")
TEAL    = colors.HexColor("#2A7F6F")


def build_pdf(
    prep_items: list[tuple[str, SubRecipe]],
    output_path: str,
) -> None:
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=10 * mm, rightMargin=10 * mm,
        topMargin=12 * mm,  bottomMargin=12 * mm,
    )

    base = getSampleStyleSheet()
    usable_width = A4[0] - 20 * mm

    def style(name, **kw) -> ParagraphStyle:
        return ParagraphStyle(name, parent=base["Normal"], **kw)

    s_subrecipe   = style("SubRecipe",  fontSize=11, textColor=NAVY,  fontName="Helvetica-Bold",   spaceAfter=1,  leading=14)
    s_meta        = style("Meta",       fontSize=8,  textColor=TEAL,  fontName="Helvetica-Oblique",spaceAfter=6,  leading=11)
    s_label       = style("Label",      fontSize=8,  textColor=NAVY,  fontName="Helvetica-Bold",   spaceBefore=6, spaceAfter=2, leading=11)
    s_ing_qty     = style("IngQty",     fontSize=8,  textColor=BLUE,  fontName="Helvetica-Bold",   leading=11)
    s_ing_name    = style("IngName",    fontSize=8,  textColor=colors.black, fontName="Helvetica", leading=11)
    s_step_num    = style("StepNum",    fontSize=8,  textColor=BLUE,  fontName="Helvetica-Bold",   leading=11)
    s_step_text   = style("StepText",   fontSize=8,  textColor=colors.black, fontName="Helvetica", leading=11)
    s_footer      = style("Footer",     fontSize=7,  textColor=LIGHT, fontName="Helvetica",        alignment=TA_CENTER)

    ing_col_w  = [usable_width * 0.15, usable_width * 0.85]
    step_col_w = [usable_width * 0.07, usable_width * 0.93]

    story = []

    for idx, (recipe_name, sr) in enumerate(prep_items):
        block = []

        # ── Sub-recipe heading ────────────────────────────────────────────────
        block.append(Paragraph(sr.name, s_subrecipe))
        block.append(Paragraph(
            f"{recipe_name}   ·   prepare up to {sr.window.value} ahead",
            s_meta,
        ))
        block.append(HRFlowable(width="100%", thickness=0.75, color=BLUE, spaceAfter=4))

        # ── Ingredients ───────────────────────────────────────────────────────
        block.append(Paragraph("INGREDIENTS", s_label))
        ing_rows = []
        for ing in sr.ingredients:
            qty, name = format_ingredient(ing)
            ing_rows.append([
                Paragraph(qty,  s_ing_qty),
                Paragraph(name, s_ing_name),
            ])

        ing_tbl = Table(ing_rows, colWidths=ing_col_w)
        ing_tbl.setStyle(TableStyle([
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING",   (0, 0), (-1, -1), 3),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 3),
            ("ROWBACKGROUNDS",(0, 0), (-1, -1), [ALT_ROW, colors.white]),
        ]))
        block.append(ing_tbl)

        # ── Steps ─────────────────────────────────────────────────────────────
        block.append(Paragraph("STEPS", s_label))
        step_rows = []
        for i, step in enumerate(sr.steps, 1):
            step_rows.append([
                Paragraph(str(i), s_step_num),
                Paragraph(step.instruction, s_step_text),
            ])

        step_tbl = Table(step_rows, colWidths=step_col_w)
        step_tbl.setStyle(TableStyle([
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING",   (0, 0), (-1, -1), 3),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 3),
            ("ROWBACKGROUNDS",(0, 0), (-1, -1), [colors.white, ALT_ROW]),
            ("LINEBELOW",     (0, -1), (-1, -1), 0.5, RULE),
        ]))
        block.append(step_tbl)

        # Keep each sub-recipe block together; add spacing between blocks
        story.append(KeepTogether(block))
        if idx < len(prep_items) - 1:
            story.append(Spacer(1, 10))

    # ── Footer ────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=0.5, color=RULE, spaceAfter=4))
    story.append(Paragraph(
        f"{len(prep_items)} pre-prep item(s)",
        s_footer,
    ))

    doc.build(story)
    print(f"✓ PDF written to: {output_path}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a pre-prep schedule PDF from recipe JSON files."
    )
    parser.add_argument(
        "--recipes-dir", default="recipes",
        help="Directory containing recipe JSON files (default: ./recipes)",
    )
    parser.add_argument(
        "--output", default="prep_schedule.pdf",
        help="Output PDF path (default: prep_schedule.pdf)",
    )
    args = parser.parse_args()

    recipes_dir = Path(args.recipes_dir)
    if not recipes_dir.is_dir():
        print(f"Error: '{recipes_dir}' is not a directory.", file=sys.stderr)
        sys.exit(1)

    json_files = sorted(recipes_dir.glob("*.json"))
    if not json_files:
        print(f"Error: no JSON files found in '{recipes_dir}'.", file=sys.stderr)
        sys.exit(1)

    recipes: list[tuple[str, Recipe]] = []
    for path in json_files:
        try:
            recipe = load_recipe(path)
            recipes.append((recipe.name, recipe))
            print(f"  Loaded: {recipe.name}  ({path.name})")
        except Exception as exc:
            print(f"  Warning: skipping '{path.name}' — {exc}", file=sys.stderr)

    if not recipes:
        print("Error: no recipes loaded successfully.", file=sys.stderr)
        sys.exit(1)

    prep_items = collect_prep_subrecipes(recipes)
    if not prep_items:
        print("No sub-recipes with a prep window found. Nothing to output.")
        sys.exit(0)

    print(f"  Found {len(prep_items)} pre-prep item(s).")
    build_pdf(prep_items, args.output)


if __name__ == "__main__":
    main()