"""
app.py — Litestar web UI for shopping list generation.

Workflow:
  1.  GET  /            → recipe selection page
  2.  POST /ingredients → ingredient checklist (selected recipes)
  3.  POST /pdf         → generate & download PDF (selected ingredients)

Run:
    pip install "litestar[standard]" reportlab pydantic uvicorn
    uvicorn app:app --reload
"""

import json
import tempfile
from pathlib import Path

from litestar import Litestar, get, post, Request
from litestar.response import Response
from litestar.enums import MediaType

from shopping_list import load_recipe, aggregate, build_pdf, CATEGORY_ORDER
from models import GroceryCategory

import os

RECIPES_DIR = Path("recipes")
ROOT_PATH = os.environ.get("ROOT_PATH", "").rstrip("/")

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _all_recipes_by_week() -> dict[str, list[tuple[str, str]]]:
    """Return {week_folder: [(relative_stem, recipe_name), ...]} sorted by week then name.

    Recipes directly in RECIPES_DIR (no subdirectory) are grouped under the
    empty-string key "" and shown first.
    relative_stem is the path relative to RECIPES_DIR without extension,
    e.g. "week_4/beef_with_broccoli".  This is used as the form value so that
    _load_selected can reconstruct the full path unambiguously.
    """
    groups: dict[str, list[tuple[str, str]]] = {}
    for path in sorted(RECIPES_DIR.glob("**/*.json")):
        try:
            recipe = load_recipe(path)
        except Exception:
            continue
        relative = path.relative_to(RECIPES_DIR)
        stem = str(relative.with_suffix(""))   # e.g. "week_4/beef_with_broccoli"
        week = relative.parts[0] if len(relative.parts) > 1 else ""
        groups.setdefault(week, []).append((stem, recipe.name))
    # Sort weeks naturally; un-grouped recipes ("") go first
    return dict(sorted(groups.items(), key=lambda kv: (kv[0] != "", kv[0])))


def _load_selected(stems: list[str]) -> list[tuple[str, object]]:
    recipes = []
    for stem in stems:
        # stem may be "week_4/beef_with_broccoli" or plain "beef_with_broccoli"
        path = RECIPES_DIR / f"{stem}.json"
        if path.exists():
            try:
                r = load_recipe(path)
                recipes.append((r.name, r))
            except Exception:
                pass
    return recipes


# ──────────────────────────────────────────────────────────────────────────────
# Inline HTML templates (no external template dir needed)
# ──────────────────────────────────────────────────────────────────────────────

_BASE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Shopping List</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{ font-family: system-ui, sans-serif; background: #f4f7fb; color: #1c3557;
          margin: 0; padding: 1.5rem 1rem 5rem; }}
  .card {{ background: #fff; border-radius: 10px; padding: 1.5rem;
           box-shadow: 0 2px 8px #0001; max-width: 800px; margin: 0 auto; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
           gap: .5rem; margin-bottom: 1rem; }}
  label {{ display: flex; align-items: center; gap: .5rem; cursor: pointer;
           padding: .35rem .5rem; border-radius: 6px; transition: background .15s; }}
  label:hover {{ background: #f0f5ff; }}
  input[type=checkbox] {{ accent-color: #3b7dd8; width: 1rem; height: 1rem; flex-shrink: 0; }}
  .optional {{ color: #888; font-style: italic; }}
  .staple   {{ color: #555; }}
  .qty      {{ font-weight: 600; color: #3b7dd8; white-space: nowrap; }}
  .part     {{ font-size: .75rem; color: #aaa; font-style: italic; }}
  .recipe   {{ font-size: .75rem; color: #888; }}
  table     {{ width: 100%; border-collapse: collapse; }}
  td        {{ padding: .3rem .5rem; font-size: .88rem; vertical-align: middle; }}
  tbody tr.ing-row     {{ background: #fff; }}
  tbody tr.ing-row.alt {{ background: #f4f7fb; }}
  .cat-head td {{ font-size: .75rem; font-weight: 700; letter-spacing: .08em;
                  color: #1c3557; padding: .7rem .5rem .25rem;
                  border-bottom: 1.5px solid #3b7dd8; background: #fff !important; }}
  /* fixed bottom toolbar */
  .toolbar {{ position: fixed; bottom: 0; left: 0; right: 0;
              background: #fff; border-top: 1px solid #dde4f0;
              box-shadow: 0 -2px 10px #0001;
              display: flex; align-items: center; gap: .75rem;
              padding: .75rem 1.5rem; flex-wrap: wrap; z-index: 100; }}
  .btn {{ padding: .5rem 1.2rem; border: none; border-radius: 7px;
          font-size: .875rem; cursor: pointer; font-weight: 600; line-height: 1; }}
  .btn-primary {{ background: #3b7dd8; color: #fff; }}
  .btn-primary:hover {{ background: #2f68b8; }}
  .btn-secondary {{ background: #e8eef8; color: #1c3557; }}
  .btn-secondary:hover {{ background: #d0dbf0; }}
  .btn-ghost {{ background: none; border: none; color: #3b7dd8; font-size: .8rem;
                cursor: pointer; text-decoration: underline; padding: 0; font-weight: 400; }}
  .notice {{ font-size: .8rem; color: #888; margin: 0 0 1rem; }}
  .inline-actions {{ display: flex; gap: .75rem; flex-wrap: wrap; margin-top: 1.25rem; }}
  /* week collapsible sections */
  details.week {{ border: 1px solid #dde4f0; border-radius: 8px; margin-bottom: .75rem; }}
  details.week[open] {{ box-shadow: 0 1px 4px #0001; }}
  details.week summary {{
    display: flex; align-items: center; justify-content: space-between;
    padding: .6rem .9rem; cursor: pointer; user-select: none;
    font-weight: 700; font-size: .9rem; color: #1c3557;
    list-style: none; gap: .5rem;
  }}
  details.week summary::-webkit-details-marker {{ display: none; }}
  details.week summary .week-chevron {{
    font-size: .7rem; color: #3b7dd8; transition: transform .2s; flex-shrink: 0;
  }}
  details.week[open] summary .week-chevron {{ transform: rotate(90deg); }}
  details.week summary .week-actions {{ display: flex; gap: .5rem; margin-left: auto; padding-right: .5rem; }}
  .week-recipes {{ padding: .25rem .75rem .75rem; display: flex; flex-direction: column; gap: .1rem; }}
</style>
</head>
<body>
<div class="card">
  {body}
</div>
<script>
function toggleAll(form, checked) {{
  form.querySelectorAll('input[type=checkbox]').forEach(cb => cb.checked = checked);
}}
function toggleWeek(weekId, checked) {{
  document.getElementById(weekId).querySelectorAll('input[type=checkbox]').forEach(cb => cb.checked = checked);
}}
</script>
</body>
</html>"""


def _page(body: str) -> Response:
    return Response(content=_BASE.format(body=body), media_type=MediaType.HTML)





# ──────────────────────────────────────────────────────────────────────────────
# Step 1 — Recipe selection
# ──────────────────────────────────────────────────────────────────────────────

@get("/")
async def index(request: Request) -> Response:
    by_week = _all_recipes_by_week()

    if not by_week:
        body = "<p>No recipe JSON files found in <code>./recipes/</code>.</p>"
        return _page(body)

    sections_html = ""
    for week, recipes in by_week.items():
        week_id = f"week-{week}" if week else "week-ungrouped"
        heading = week if week else "Other"
        checkboxes = "".join(
            f'<label><input type="checkbox" name="recipe" value="{stem}"> {name}</label>'
            for stem, name in recipes
        )
        sections_html += f"""
<details class="week" open id="{week_id}">
  <summary>
    <span>{heading}</span>
    <span class="week-actions">
      <button class="btn-ghost" type="button" onclick="event.preventDefault();toggleWeek('{week_id}', true)">all</button>
      <button class="btn-ghost" type="button" onclick="event.preventDefault();toggleWeek('{week_id}', false)">none</button>
    </span>
    <span class="week-chevron">&#9654;</span>
  </summary>
  <div class="week-recipes">{checkboxes}</div>
</details>"""

    body = f"""
<form method="POST" action="{ROOT_PATH}/ingredients" id="main-form">
  {sections_html}
</form>
<div class="toolbar">
  <button class="btn-ghost" type="button" onclick="toggleAll(document.getElementById('main-form'), true)">Select all</button>
  <button class="btn-ghost" type="button" onclick="toggleAll(document.getElementById('main-form'), false)">Deselect all</button>
  <button class="btn btn-primary" type="submit" form="main-form">Next &#8594;</button>
</div>"""
    return _page(body)


# ──────────────────────────────────────────────────────────────────────────────
# Step 2 — Ingredient checklist
# ──────────────────────────────────────────────────────────────────────────────

@post("/ingredients")
async def ingredients(request: Request) -> Response:
    form = await request.form()
    stems = form.getall("recipe")

    if not stems:
        body = f'<p>No recipes selected. <a href="{ROOT_PATH}/">Go back</a></p>'
        return _page(body)

    recipes = _load_selected(stems)
    if not recipes:
        body = f'<p>Could not load any recipes. <a href="{ROOT_PATH}/">Go back</a></p>'
        return _page(body)

    shopping, _ = aggregate(recipes)

    # Hidden inputs to carry recipe selection forward
    hidden_recipes = "".join(f'<input type="hidden" name="recipe" value="{s}">' for s in stems)

    # Build table rows — one checkbox per line (per recipe occurrence).
    # Checkbox value encodes "category|||ingredient_name|||line_index" so the
    # /pdf handler can reconstruct exactly which lines to keep.
    rows_html = ""
    alt = False  # alternate shading per ingredient group
    for cat in CATEGORY_ORDER:
        cat_label = cat.value
        if cat_label not in shopping:
            continue
        rows_html += f'<tr class="cat-head"><td colspan="5"><span>{cat_label.upper()}</span></td></tr>\n'
        for item in shopping[cat_label]:
            name_cls = "optional" if item["optional"] else ("staple" if item["staple"] else "")
            if item["optional"]:
                name_text_prefix = ""
                name_text_suffix = " <em>(optional)</em>"
            elif item["staple"]:
                name_text_prefix = ""
                name_text_suffix = " <em>(staple)</em>"
            else:
                name_text_prefix = ""
                name_text_suffix = ""
            name_text = item["name"] + name_text_suffix

            row_cls = "ing-row alt" if alt else "ing-row"
            alt = not alt

            lines = item["lines"]
            intrinsics_vary = any(ln.get("name_label") for ln in lines)
            # Check if all lines share the same part value — if so we can rowspan it
            parts = [ln.get("part") or "" for ln in lines]
            parts_uniform = len(set(parts)) == 1
            for i, ln in enumerate(item["lines"]):
                key = f"{cat_label}|||{item['name']}|||{i}"
                # Name cell: rowspan when all lines share the same name
                if intrinsics_vary:
                    label = ln.get("name_label") or item["name"]
                    name_cell = (
                        f'<td class="{name_cls}" '
                        f'style="border-right:1px solid #e8eef8">{label}{name_text_suffix}</td>'
                    )
                elif i == 0:
                    rowspan = len(lines)
                    name_cell = (
                        f'<td rowspan="{rowspan}" class="{name_cls}" '
                        f'style="border-right:1px solid #e8eef8">{name_text}</td>'
                    )
                else:
                    name_cell = ""
                # Part cell: rowspan when uniform across lines, per-line otherwise
                part = ln.get("part") or ""
                if parts_uniform and i == 0:
                    part_cell = (
                        f'<td rowspan="{len(lines)}" class="part">{part}</td>'
                    )
                elif parts_uniform:
                    part_cell = ""
                else:
                    part_cell = f'<td class="part">{part}</td>'
                recipe_cell = f'<td class="recipe">{ln["recipe_name"]}</td>'
                rows_html += (
                    f'<tr class="{row_cls}">'
                    f'<td style="width:32px"><input type="checkbox" name="item" value="{key}" checked></td>'
                    f'{name_cell}'
                    f'{part_cell}'
                    f'<td class="qty">{ln["quantity"]}</td>'
                    f'{recipe_cell}'
                    f'</tr>\n'
                )

    body = f"""
<p class="notice">* staple &nbsp;&nbsp; <em>italics</em> = optional</p>
<form method="POST" action="{ROOT_PATH}/pdf" id="main-form">
  {hidden_recipes}
  <input type="hidden" name="shopping_json" value="{_escape_attr(json.dumps(shopping))}">
  <table>
    <thead>
      <tr>
        <th style="width:32px"></th>
        <th style="text-align:left;font-size:.75rem;padding:.3rem .5rem">Ingredient</th>
        <th style="text-align:left;font-size:.75rem;padding:.3rem .5rem">Part</th>
        <th style="text-align:left;font-size:.75rem;padding:.3rem .5rem">Qty</th>
        <th style="text-align:left;font-size:.75rem;padding:.3rem .5rem">Recipe</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
</form>
<div class="toolbar">
  <a href="{ROOT_PATH}/"><button class="btn btn-secondary" type="button">&#8592; Back</button></a>
  <button class="btn-ghost" type="button" onclick="toggleAll(document.getElementById('main-form'), true)">Select all</button>
  <button class="btn-ghost" type="button" onclick="toggleAll(document.getElementById('main-form'), false)">Deselect all</button>
  <button class="btn btn-primary" type="submit" form="main-form">Download PDF</button>
</div>"""
    return _page(body)


def _escape_attr(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("'", "&#39;")


# ──────────────────────────────────────────────────────────────────────────────
# Step 3 — Generate PDF
# ──────────────────────────────────────────────────────────────────────────────

@post("/pdf")
async def pdf(request: Request) -> Response:
    form = await request.form()
    stems = form.getall("recipe")
    selected_keys = set(form.getall("item"))
    shopping_json: str = form.get("shopping_json") or "{}"

    shopping_full: dict = json.loads(shopping_json)

    # selected_keys are "category|||name|||line_index".
    # Rebuild a filtered shopping dict keeping only the checked lines.
    shopping_filtered: dict = {}
    for cat_label, items in shopping_full.items():
        kept = []
        for item in items:
            kept_lines = [
                ln for i, ln in enumerate(item["lines"])
                if f"{cat_label}|||{item['name']}|||{i}" in selected_keys
            ]
            if kept_lines:
                kept.append({**item, "lines": kept_lines})
        if kept:
            shopping_filtered[cat_label] = kept

    recipe_names = []
    for stem in stems:
        path = RECIPES_DIR / f"{stem}.json"
        if path.exists():
            try:
                r = load_recipe(path)
                recipe_names.append(r.name)
            except Exception:
                pass

    # Write PDF to a temp file then return bytes
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name

    build_pdf(shopping_filtered, recipe_names, tmp_path)

    pdf_bytes = Path(tmp_path).read_bytes()
    Path(tmp_path).unlink(missing_ok=True)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="shopping_list.pdf"'},
    )


# ──────────────────────────────────────────────────────────────────────────────
# App
# ──────────────────────────────────────────────────────────────────────────────

app = Litestar(
    route_handlers=[index, ingredients, pdf],
)