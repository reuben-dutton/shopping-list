#!/usr/bin/env python3
"""
shopping_list.py
Reads recipe JSON files from ./recipes/*.json, parses them into Recipe model
objects, aggregates all ingredients into a shopping list, and renders a PDF
via reportlab.

Usage:
    python shopping_list.py [--recipes-dir recipes] [--output shopping_list.pdf]

Dependencies:
    pip install reportlab pydantic
    models.py and measurements.py must be on sys.path (e.g. same directory).
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from models import Branch, Ingredient, Recipe, Step, SubRecipe
from models import GroceryCategory
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
)


# ── JSON loading ──────────────────────────────────────────────────────────────

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
    return Recipe(name=raw["name"], ingredients=ingredients, steps=steps)


# ── Shopping list aggregation ─────────────────────────────────────────────────

def collect_ingredients(recipe: Recipe) -> list[Ingredient]:
    """Flatten all ingredients including those inside sub-recipes."""
    result = []
    for item in recipe.ingredients:
        if isinstance(item, SubRecipe):
            result.extend(item.ingredients)
        else:
            result.append(item)
    return result


def float_to_string(value: float) -> str:
    """Format a float cleanly; map common decimals to fraction strings."""
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


# ── Unit normalisation (imperial weight → metric only) ────────────────────────

# Only imperial weight units are converted; everything else is left as-is.
_IMPERIAL_WEIGHT_TO_G: dict[str, float] = {
    "oz": 28.3495,
    "lb": 453.592,
}

# Metric weight units that omit the space (e.g. "500g", "1.2kg")
_COMPACT_UNITS = {"g", "kg"}


def _normalise(amount: float, unit: str | None) -> tuple[float, str | None]:
    """
    Convert imperial weight (oz, lb) → metric (g, kg).
    All other units — including all volume units — are left unchanged.
    """
    if unit in _IMPERIAL_WEIGHT_TO_G:
        g = amount * _IMPERIAL_WEIGHT_TO_G[unit]
        if g >= 1000:
            return g / 1000, "kg"
        return g, "g"
    return amount, unit


def _format_quantity(amount: float, unit: str | None) -> str:
    s = float_to_string(amount)
    if unit is None:
        return s
    if unit in _COMPACT_UNITS:
        return f"{s}{unit}"
    return f"{s} {unit}"



# Preferred display order for grocery categories
CATEGORY_ORDER = [
    GroceryCategory.PRODUCE,
    GroceryCategory.MEAT_SEAFOOD,
    GroceryCategory.DAIRY_EGGS,
    GroceryCategory.PASTA_GRAINS,
    GroceryCategory.CANNED_JARRED,
    GroceryCategory.CONDIMENTS,
    GroceryCategory.DRY_GOODS_SPICES,
    GroceryCategory.OILS_WINE,
    GroceryCategory.BAKERY,
    GroceryCategory.FROZEN,
    GroceryCategory.OTHER,
]


def aggregate(
    recipes: list[tuple[str, Recipe]]
) -> tuple[dict[str, list[dict]], dict[str, int]]:
    """
    Aggregates ingredients for the shopping list, grouped by grocery category.

    Single-recipe ingredients: amount is summed across all occurrences in that
    recipe and displayed as a single quantity line.

    Multi-recipe ingredients: never aggregated across recipes. Each recipe
    contributes its own quantity line, displayed stacked in the qty/recipe columns.

    Returns:
      - {category_label: [item, ...]} where each item has:
          name        – display name (variety + name, with intrinsics prepended)
          lines       – list of {quantity, recipe_name} dicts (one per recipe)
          optional    – bool
          staple      – bool
      - {recipe_name: 0-based index}
    sorted: alpha within each category, staples and optionals last.
    """
    recipe_index: dict[str, int] = {name: i for i, (name, _) in enumerate(recipes)}

    # Structure: ing_key → recipe_name → unit → amount
    #            ing_key → "_meta" → {name, variety, optional, staple, intrinsics, category}
    IngKey = str  # normalised ingredient name (lower-stripped)
    per_recipe: dict[IngKey, dict] = {}

    for recipe_name, recipe in recipes:
        for ing in collect_ingredients(recipe):
            norm_amount, norm_unit = _normalise(
                ing.measurement.amount, ing.measurement.unit
            )
            ing_key = ing.name.strip().lower()

            if ing_key not in per_recipe:
                per_recipe[ing_key] = {
                    "_meta": {
                        "name": ing.name,
                        "variety": ing.variety,
                        "optional": True,
                        "staple": True,
                        "intrinsics": [],
                        "category": ing.category,
                    }
                }

            meta = per_recipe[ing_key]["_meta"]
            if not ing.optional:
                meta["optional"] = False
            if not ing.staple:
                meta["staple"] = False
            if meta["variety"] is None and ing.variety is not None:
                meta["variety"] = ing.variety
            for intrinsic in ing.intrinsics:
                if intrinsic not in meta["intrinsics"]:
                    meta["intrinsics"].append(intrinsic)

            if recipe_name not in per_recipe[ing_key]:
                per_recipe[ing_key][recipe_name] = {}
            unit_bucket = per_recipe[ing_key][recipe_name]
            unit_bucket[norm_unit] = unit_bucket.get(norm_unit, 0.0) + norm_amount

    categories: dict[str, list] = defaultdict(list)

    for ing_key, data in per_recipe.items():
        meta = data["_meta"]
        recipe_names_present = [r for r in data if r != "_meta"]
        recipe_names_sorted = sorted(recipe_names_present, key=lambda r: recipe_index[r])

        # Build display name: "intrinsic1, intrinsic2 variety name"
        intrinsics_prefix = ", ".join(meta["intrinsics"])
        name_parts = [p for p in [intrinsics_prefix or None, meta["variety"], meta["name"]] if p]
        display_name = " ".join(name_parts)

        # Build quantity lines
        if len(recipe_names_sorted) == 1:
            recipe_name = recipe_names_sorted[0]
            unit_buckets = data[recipe_name]
            lines = [
                {"quantity": _format_quantity(amt, unit), "recipe_name": recipe_name}
                for unit, amt in unit_buckets.items()
            ]
        else:
            lines = []
            for recipe_name in recipe_names_sorted:
                unit_buckets = data[recipe_name]
                for unit, amt in unit_buckets.items():
                    lines.append({
                        "quantity": _format_quantity(amt, unit),
                        "recipe_name": recipe_name,
                    })

        category_label = meta["category"].value
        categories[category_label].append({
            "name": display_name,
            "lines": lines,
            "optional": meta["optional"],
            "staple": meta["staple"],
        })

    # Sort within each category: required first, then staples, then optional; alpha within each tier
    def sort_key(item):
        tier = 2 if item["optional"] else (1 if item["staple"] else 0)
        return (tier, item["name"].lower())

    for cat in categories:
        categories[cat].sort(key=sort_key)

    # Return in preferred aisle order, skipping empty categories
    ordered_labels = [c.value for c in CATEGORY_ORDER]
    shopping = {k: categories[k] for k in ordered_labels if k in categories}
    return shopping, recipe_index


# ── PDF generation ────────────────────────────────────────────────────────────

NAVY    = colors.HexColor("#1C3557")
BLUE    = colors.HexColor("#3B7DD8")
GREY    = colors.HexColor("#888888")
ALT_ROW = colors.HexColor("#F4F7FB")
MID     = colors.HexColor("#555555")
LIGHT   = colors.HexColor("#999999")
RULE    = colors.HexColor("#CCCCCC")



def build_pdf(
    shopping: dict[str, list[dict]],
    recipe_names: list[str],
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

    s_title       = style("Title",      fontSize=18, textColor=NAVY,  fontName="Helvetica-Bold",   spaceAfter=8)
    s_section     = style("Section",    fontSize=9,  textColor=NAVY,  fontName="Helvetica-Bold",   spaceBefore=10, spaceAfter=2, leading=12)
    s_item        = style("Item",       fontSize=8,  textColor=colors.black, fontName="Helvetica", leading=11)
    s_item_opt    = style("ItemOpt",    fontSize=8,  textColor=colors.black, fontName="Helvetica-Oblique", leading=11)
    s_qty         = style("Qty",        fontSize=8,  textColor=BLUE,  fontName="Helvetica-Bold",   leading=11)
    s_recipe_one  = style("RecipeOne",  fontSize=7,  textColor=MID,   fontName="Helvetica",        leading=10)
    s_recipe_item = style("RecipeItem", fontSize=7,  textColor=MID,   fontName="Helvetica",        leading=10)
    s_footer      = style("Footer",     fontSize=7,  textColor=LIGHT, fontName="Helvetica",        alignment=TA_CENTER)

    # qty | name | recipe(s)
    col_w = [usable_width * 0.12, usable_width * 0.50, usable_width * 0.38]

    story = []

    # ── Title ──────────────────────────────────────────────────────────────────
    story.append(Paragraph("* staple   · ** optional", s_footer))
    story.append(HRFlowable(width="100%", thickness=1.5, color=BLUE, spaceBefore=6, spaceAfter=6))

    def qty_recipe_cell(lines: list[dict]) -> tuple:
        """
        Returns (qty_cell, recipe_cell) — each is either a single Paragraph
        (one line) or a list of Paragraphs (multiple lines, stacked).
        """
        if len(lines) == 1:
            return (
                Paragraph(lines[0]["quantity"], s_qty),
                Paragraph(lines[0]["recipe_name"], s_recipe_one),
            )
        qty_paras    = [Paragraph(ln["quantity"],    s_qty)        for ln in lines]
        recipe_paras = [Paragraph(ln["recipe_name"], s_recipe_item) for ln in lines]
        return qty_paras, recipe_paras

    for category, items in shopping.items():
        story.append(Paragraph(category.upper(), s_section))

        rows = []
        for item in items:
            opt    = item["optional"]
            staple = item["staple"] and not opt
            if opt:
                prefix = "** "
                n_style = s_item_opt
            elif staple:
                prefix = "* "
                n_style = s_item
            else:
                prefix = ""
                n_style = s_item

            name_text = prefix + item["name"]
            qty_cell, recipe_cell = qty_recipe_cell(item["lines"])

            rows.append([
                qty_cell,
                Paragraph(name_text, n_style),
                recipe_cell,
            ])

        tbl = Table(rows, colWidths=col_w)
        tbl.setStyle(TableStyle([
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING",   (0, 0), (-1, -1), 3),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 3),
            ("ROWBACKGROUNDS",(0, 0), (-1, -1), [ALT_ROW, colors.white]),
            ("LINEBELOW",     (0, -1), (-1, -1), 0.5, RULE),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 2))

    # Footer
    total = sum(len(v) for v in shopping.values())
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=0.5, color=RULE, spaceAfter=4))
    story.append(Paragraph(
        f"{total} items across {len(recipe_names)} recipe(s)",
        s_footer,
    ))

    doc.build(story)
    print(f"✓ PDF written to: {output_path}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a shopping list PDF from recipe JSON files."
    )
    parser.add_argument(
        "--recipes-dir", default="recipes",
        help="Directory containing recipe JSON files (default: ./recipes)",
    )
    parser.add_argument(
        "--output", default="shopping_list.pdf",
        help="Output PDF path (default: shopping_list.pdf)",
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

    shopping, recipe_index = aggregate(recipes)
    build_pdf(shopping, [name for name, _ in recipes], args.output)


if __name__ == "__main__":
    main()