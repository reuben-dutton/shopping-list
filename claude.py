"""
Script to convert a recipe JSON to a formatted PDF using ReportLab.
Uses Pydantic models for type safety and validation.
Handles recipe branching (multiple cooking methods/options).
"""
import re
import string
import json
import os
import typing

import reportlab
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak,
    Table, TableStyle, ListFlowable, ListItem, KeepTogether
)
from reportlab.lib import colors

import models
import measurements


GAP = 2


def load_recipe_from_json(json_path: str) -> models.Recipe:
    """Load a recipe from a JSON file with proper Unicode support."""
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return models.Recipe(**data)


def circled_number(n: int) -> str:
    """Return a circled digit character for numbers 1–20."""
    circled = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
    if 1 <= n <= len(circled):
        return circled[n - 1]
    return f"({n})"


def build_subrecipe_index(recipe: models.Recipe) -> dict[int, int]:
    """
    Return a mapping of id(SubRecipe) -> number (1-based),
    ordered by position in the ingredients list.
    """
    number = 1
    index = {}
    for ingredient in recipe.ingredients:
        if isinstance(ingredient, models.SubRecipe):
            index[id(ingredient)] = number
            number += 1
    return index


def annotate_instruction(instruction: str, subrecipe_index: dict[int, int], recipe: models.Recipe) -> str:
    """
    Scan the instruction text for sub-recipe names and append their
    circled number inline, e.g. "Meat Sauce" -> "Meat Sauce ①".
    Matches whole occurrences of the name (case-sensitive).
    """
    for ingredient in recipe.ingredients:
        if isinstance(ingredient, models.SubRecipe):
            number = subrecipe_index[id(ingredient)]
            marker = circled_number(number)
            pattern = re.escape(ingredient.name)
            replacement = f"{ingredient.name} {marker}"
            instruction = re.sub(pattern, replacement, instruction)
    return instruction


def add_subrecipe_section(story: list, subrecipe: models.SubRecipe, number: int, styles: dict):
    """Add a sub-recipe section (ingredients + steps) to the story."""
    # Sub-recipe title with circled number
    title = Paragraph(f"{circled_number(number)} {subrecipe.name}", styles['subrecipe_title_style'])
    story.append(title)

    # Prep window note
    if subrecipe.window == models.PrepWindow.NONE:
        window_text = "<i>Prepare alongside the rest of the recipe.</i>"
    else:
        window_text = f"<i>Can be prepared up to {subrecipe.window.value} ahead.</i>"
    story.append(Paragraph(window_text, styles['note_style']))

    story.append(Spacer(1, GAP))

    # Ingredients
    story.append(Paragraph("Ingredients", styles['heading_style']))
    ingredient_items = []
    for ingredient in subrecipe.ingredients:
        optional_suffix = " <i>(optional)</i>" if ingredient.optional else ""
        text = f"{ingredient.full_measurement} {ingredient.full_name}{optional_suffix} {ingredient.prep_notes}"
        item = Paragraph(text, styles['ingredient_style'])
        ingredient_items.append(ListItem(item, bulletColor=colors.HexColor('#E74C3C')))

    if ingredient_items:
        story.append(ListFlowable(ingredient_items, bulletType='bullet', start='circle'))

    story.append(Spacer(1, GAP))

    # Steps
    if subrecipe.steps:
        story.append(Paragraph("Instructions", styles['heading_style']))
        for idx, step in enumerate(subrecipe.steps, start=1):
            step_text = f"<b>{idx}.</b> {step.instruction}"
            story.append(Paragraph(step_text, styles['step_style']))

    story.append(Spacer(1, GAP * 4))


def add_main_recipe_section(
    story: list,
    recipe: models.Recipe,
    subrecipe_index: dict[int, int],
    styles: dict,
):
    """Add the main recipe section to the story."""
    story.append(Paragraph(recipe.name, styles['title_style']))
    story.append(Paragraph(
        f'<a href="{recipe.url}" color="#3498DB"><i>{recipe.source}</i></a>',
        styles['source_style']
    ))
    story.append(Paragraph(
        f'<i>{recipe.url}</i>',
        styles['source_style']
    ))
    story.append(Spacer(1, GAP))

    # --- Ingredients ---
    story.append(Paragraph("Ingredients", styles['heading_style']))

    ingredient_items = []
    for ingredient in recipe.ingredients:
        if isinstance(ingredient, models.Ingredient):
            optional_suffix = " <i>(optional)</i>" if ingredient.optional else ""
            text = f"{ingredient.full_measurement} {ingredient.full_name}{optional_suffix} {ingredient.prep_notes}"
            item = Paragraph(text, styles['ingredient_style'])
            ingredient_items.append(ListItem(item, bulletColor=colors.HexColor('#E74C3C')))
        elif isinstance(ingredient, models.SubRecipe):
            number = subrecipe_index[id(ingredient)]
            text = f"{circled_number(number)} <i>{ingredient.name}</i> <font color='#7F8C8D'>(see below)</font>"
            item = Paragraph(text, styles['ingredient_style'])
            ingredient_items.append(ListItem(item, bulletColor=colors.HexColor('#3498DB')))

    if ingredient_items:
        story.append(ListFlowable(ingredient_items, bulletType='bullet', start='circle'))

    story.append(Spacer(1, GAP))

    # --- Instructions ---
    if recipe.steps:
        story.append(Paragraph("Instructions", styles['heading_style']))

        step_counter = 1
        option_letters = list(string.ascii_lowercase)

        for step_item in recipe.steps:
            if isinstance(step_item, models.Branch):
                story.append(Spacer(1, GAP))

                for option_name, option_steps in step_item.options.items():
                    story.append(Paragraph(option_name, styles['branch_heading_style']))

                    for substep_idx, substep in enumerate(option_steps):
                        letter = option_letters[substep_idx] if substep_idx < len(option_letters) else str(substep_idx)
                        instruction = annotate_instruction(substep.instruction, subrecipe_index, recipe)
                        step_text = f"<b>{step_counter}{letter}.</b> {instruction}"
                        story.append(Paragraph(step_text, styles['branch_step_style']))

                    story.append(Spacer(1, GAP * 3))

                step_counter += 1

            elif isinstance(step_item, models.Step):
                instruction = annotate_instruction(step_item.instruction, subrecipe_index, recipe)
                step_text = f"<b>{step_counter}.</b> {instruction}"
                story.append(Paragraph(step_text, styles['step_style']))
                step_counter += 1

    story.append(Spacer(1, GAP * 3))


def create_recipe_pdf(recipe: models.Recipe, output_path: str):
    """Create a formatted PDF from a recipe with support for SubRecipes."""
    doc = SimpleDocTemplate(
        output_path,
        pagesize=reportlab.lib.pagesizes.letter,
        rightMargin=32,
        leftMargin=32,
        topMargin=18,
        bottomMargin=18
    )

    story = []
    base_styles = getSampleStyleSheet()

    styles = {
        'title_style': ParagraphStyle(
            'CustomTitle',
            parent=base_styles['Heading1'],
            fontSize=24,
            textColor=colors.HexColor('#2C3E50'),
            alignment=TA_CENTER,
            fontName='Helvetica-Bold'
        ),
        'subrecipe_title_style': ParagraphStyle(
            'SubrecipeTitle',
            parent=base_styles['Heading2'],
            fontSize=16,
            textColor=colors.HexColor('#7F8C8D'),
            fontName='Helvetica-Bold'
        ),
        'heading_style': ParagraphStyle(
            'CustomHeading',
            parent=base_styles['Heading2'],
            fontSize=14,
            textColor=colors.HexColor('#34495E'),
            fontName='Helvetica-Bold'
        ),
        'branch_heading_style': ParagraphStyle(
            'BranchHeading',
            parent=base_styles['Heading3'],
            fontSize=13,
            textColor=colors.HexColor('#34495E'),
            leftIndent=20,
            fontName='Helvetica-Bold'
        ),
        'ingredient_style': ParagraphStyle(
            'IngredientStyle',
            parent=base_styles['Normal'],
            fontSize=11,
            leftIndent=20,
            fontName='Helvetica'
        ),
        'step_style': ParagraphStyle(
            'StepStyle',
            parent=base_styles['Normal'],
            fontSize=11,
            leftIndent=20,
            fontName='Helvetica'
        ),
        'branch_step_style': ParagraphStyle(
            'BranchStepStyle',
            parent=base_styles['Normal'],
            fontSize=11,
            leftIndent=40,
            fontName='Helvetica'
        ),
        'source_style': ParagraphStyle(
            'SourceStyle',
            parent=base_styles['Normal'],
            fontSize=11,
            textColor=colors.HexColor('#3498DB'),
            alignment=TA_CENTER,
            fontName='Helvetica-Oblique',
            spaceAfter=2,
        ),
        'note_style': ParagraphStyle(
            'NoteStyle',
            parent=base_styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#7F8C8D'),
            leftIndent=20,
            fontName='Helvetica-Oblique'
        ),
    }

    subrecipe_index = build_subrecipe_index(recipe)

    # Main recipe first
    add_main_recipe_section(story, recipe, subrecipe_index, styles)

    # Sub-recipes below, in ingredient order
    for ingredient in recipe.ingredients:
        if isinstance(ingredient, models.SubRecipe):
            number = subrecipe_index[id(ingredient)]
            add_subrecipe_section(story, ingredient, number, styles)

    doc.build(story)
    print(f"PDF created successfully: {output_path}")


def main():
    """Main function to demonstrate usage."""
    recipe_dir = r'M:\dev\shopping-list\recipes\week_8'
    pdf_dir = r'M:\dev\shopping-list\pdfs\week_8'
    for filename in os.listdir(recipe_dir):
        if not filename.endswith('.json'):
            continue
        path = os.path.join(recipe_dir, filename)
        output_path = os.path.join(pdf_dir, filename.replace('.json', '.pdf'))
        recipe = load_recipe_from_json(path)
        create_recipe_pdf(recipe, output_path)


if __name__ == "__main__":
    main()