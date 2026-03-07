import enum
import typing

import pydantic

import measurements


class PrepWindow(str, enum.Enum):
    NONE = 'none'
    TWO_HOUR = '2 hours'
    HALF_DAY = 'half a day'
    DAY = '1 day'
    TWO_DAY = 'two days'
    WEEK = '1 week'


class GroceryCategory(str, enum.Enum):
    PRODUCE          = 'Produce'
    MEAT_SEAFOOD     = 'Meat & Seafood'
    DAIRY_EGGS       = 'Dairy & Eggs'
    PASTA_GRAINS     = 'Pasta & Grains'
    CANNED_JARRED    = 'Canned & Jarred'
    CONDIMENTS       = 'Condiments & Sauces'
    DRY_GOODS_SPICES = 'Dry Goods & Spices'
    OILS_WINE        = 'Oils, Vinegars & Wine'
    FROZEN           = 'Frozen'
    BAKERY           = 'Bakery'
    OTHER            = 'Other'


class Ingredient(pydantic.BaseModel):
    # e.g. grams
    # note that you a JSON representation does not need anything except unit and amount
    # the rest is auto-assigned via discriminated union
    measurement: measurements.Measurement
    # e.g. tomatoes
    name: str
    # e.g. breast
    part: typing.Optional[str] = None
    # e.g. roma (tomato), red (onion)
    variety: typing.Optional[str] = None
    # intrinsic characteristics
    # anything that could feasibly be purchased in a store, but not feasibly done at home
    # e.g. RSPCA-approved, gluten-free, minced (in some cases), canned, dry (in some cases), etc.
    intrinsics: list[str]
    # preparation
    # anything that could be feasibly done at home
    # e.g. cut, trimmed, diced, etc.
    preparation: list[str]

    # note that intrinsics and preparation are generally mutually exclusive

    # grocery store section this ingredient belongs to
    category: GroceryCategory

    # when the ingredient is marked as optional
    optional: bool
    # when the ingredient is likely to be used for multiple recipes
    # e.g. spices
    staple: bool

    @property
    def full_name(self) -> str:
        """Format the ingredient name with variety and part if present."""
        return " ".join(
            item for item in
            [
                self.variety if self.variety else "",
                self.name,
                self.part,
                " - " + ", ".join(self.intrinsics) if self.intrinsics else ""
            ]
            if item is not None
        )

    @property
    def full_measurement(self) -> str:
        return repr(self.measurement)

    @property
    def prep_notes(self) -> str:
        if len(self.preparation) == 0:
            return ""
        if len(self.preparation) == 1:
            return f"({self.preparation[0]})"
        if len(self.preparation) == 2:
            return f"({self.preparation[0]} and {self.preparation[1]})"
        if len(self.preparation) == 3:
            return f"({self.preparation[0]}, {self.preparation[1]} and {self.preparation[2]})"
        raise Exception('Too many preparation notes')


# a single instruction for a recipe
class Step(pydantic.BaseModel):
    instruction: str


# useful in situations where the recipe has multiple options for completion (but we only pick one)
# do not use this in cases where we have two separate parts of the recipe running concurrently
'''
    e.g. {
        'Bake the ...': [...],
        'Fry the ...': [...]
    }
'''
class Branch(pydantic.BaseModel):
    options: dict[str, list[Step]]

# sub recipes don't have sub recipes themselves, nor do they have branches
# a group of ingredients and steps should only be coalesced into a sub-recipe if
# it makes preparatory sense to do so (e.g. spice mix, marinade, pre-prepared sauce)
class SubRecipe(pydantic.BaseModel):
    name: str
    ingredients: list[Ingredient]
    steps: list[Step]
    
    # some sub-recipes can be prepared ahead of time e.g. 2 hours, 1 day, a week
    window: PrepWindow


class Recipe(pydantic.BaseModel):
    name: str
    # e.g. Taste.com.au
    source: typing.Optional[str] = None
    # e.g. https://www.taste.com.au/recipes/potato-leek-soup-2/56c16a16-d92c-475c-92cc-4b8eecd6c0c5
    url: typing.Optional[str] = None
    # list of ingredients
    # a subrecipe can also be included in the case of sub-groups e.g. a spice mix, a sauce, a dressing
    # note that a recipe should include at least one step. recipes should not be used as an ingredient
    # just to group items together
    # additionally, the main body of the recipe should not be a sub-recipe
    ingredients: list[Ingredient | SubRecipe]
    # steps in order
    # we can conceptualize this as a simple DAG
    # we may have forks in the recipe, but they can join back at some point
    steps: list[Branch | Step]



'''
    NOTES:
        1) Combine ingredients, unless they're in different subrecipes. This would include 'egg white' and 'egg yolk' (one egg)
'''