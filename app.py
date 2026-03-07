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

def _all_recipes() -> list[tuple[str, str]]:
    """Return [(filename_stem, recipe_name), ...] sorted by name."""
    results = []
    for path in sorted(RECIPES_DIR.glob("**/*.json")):
        try:
            recipe = load_recipe(path)
            results.append((path.stem, recipe.name))
        except Exception:
            pass
    return results


def _load_selected(stems: list[str]) -> list[tuple[str, object]]:
    recipes = []
    for stem in stems:
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
    all_recipes = _all_recipes()

    if not all_recipes:
        body = "<p>No recipe JSON files found in <code>./recipes/</code>.</p>"
        return _page(body)

    checkboxes = "".join(
        f'<label><input type="checkbox" name="recipe" value="{stem}" checked> {name}</label>'
        for stem, name in all_recipes
    )

    body = f"""
<form method="POST" action="{ROOT_PATH}/ingredients" id="main-form">
  <div style="display:flex;flex-direction:column;gap:.25rem">{checkboxes}</div>
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
        rows_html += f'<tr class="cat-head"><td colspan="4"><span>{cat_label.upper()}</span></td></tr>\n'
        for item in shopping[cat_label]:
            name_cls = "optional" if item["optional"] else ("staple" if item["staple"] else "")
            name_text = item["name"]
            if item["optional"]:
                name_text += " <em>(optional)</em>"
            elif item["staple"]:
                name_text += " <em>(staple)</em>"

            row_cls = "ing-row alt" if alt else "ing-row"
            alt = not alt

            for i, ln in enumerate(item["lines"]):
                key = f"{cat_label}|||{item['name']}|||{i}"
                # Name cell only on first line of each ingredient
                if i == 0:
                    rowspan = len(item["lines"])
                    name_cell = (
                        f'<td rowspan="{rowspan}" class="{name_cls}" '
                        f'style="border-right:1px solid #e8eef8">{name_text}</td>'
                    )
                else:
                    name_cell = ""
                rows_html += (
                    f'<tr class="{row_cls}">'
                    f'<td style="width:32px"><input type="checkbox" name="item" value="{key}" checked></td>'
                    f'{name_cell}'
                    f'<td class="qty">{ln["quantity"]}</td>'
                    f'<td class="recipe">{ln["recipe_name"]}</td>'
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