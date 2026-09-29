# Recipe style guide

The house standard for recipe text in cookt, distilled from the catalog's best recipes: J. Kenji
López-Alt's (23 in the catalog, plus most Serious Eats and NYT Cooking entries). They write for a
competent home cook: exact amounts, one clear action per sentence, a sensory cue for doneness, and
nothing that isn't needed to cook the dish.

This file is the source for the copy-edit prompt (`backend/cookt/copyedit.py`). The model only
*proposes* edits. Nothing changes until someone approves it on the Review page (`/review`), and an
approved edit can be undone from there or from the recipe's revision history.

## Title

- Name the dish plainly: "Roasted Carrots with Harissa and Crème Fraîche".
- Drop owner prefixes ("Kenji", "IP", "TDDC"); the credit line already names the author.
- Drop version suffixes ("II", "III"), hype ("The Best", "Amazing", "Easy", "Perfect") and
  emoji. Keep words that describe the dish or method ("One-Pot", "Slow-Cooker", "No-Knead").
- Title case.

## Ingredients

- Order within a line: quantity, unit, ingredient, then a comma and the prep.
  `1 medium shallot, thinly sliced (about 1/4 cup)`
- **Never change a quantity or unit.** Keep metric equivalents that are already there. A line may
  be restated only if the amount is unchanged ("1 (8-ounce) package shredded cheddar" →
  "8 ounces cheddar cheese, shredded").
- Spell out units: teaspoon, tablespoon, cup, ounce, pound (not tsp., tbsp., c., oz., lbs.).
- Generic names, not brands: "chicken bouillon paste", not a trademark. Keep a brand only when it
  changes the measurement or chemistry (Diamond Crystal vs. Morton kosher salt measure very
  differently by volume).
- Lowercase common nouns; capitalize proper names (Parmigiano-Reggiano, Dijon mustard, Shaoxing wine).
- "Optional" goes last: `1/2 cup chopped walnuts, optional`.
- Remove duplicate lines and section names that were imported as ingredients; a section name
  becomes a heading.
- Keep "(see note)" only when that note exists.

## Method

- Imperative and present tense: "Heat the oil in a large skillet over medium-high heat until
  shimmering."
- Give the vessel, the heat level and a doneness cue, then the time as a guide:
  "Cook, stirring occasionally, until softened and lightly browned, about 6 minutes."
- Temperatures as `375°F`; keep internal temperatures and any existing °C.
- Cut filler: step labels ("Get the oven ready:"), anecdotes ("I usually…"), apologies,
  cheerleading ("Enjoy!"), references to photos, videos, links or FAQs that aren't part of this
  recipe.
- Steps name ingredients the way the ingredient list does. If a step uses something the list
  doesn't have (or calls thighs "quarters"), fix the step when the intent is unambiguous.
- Repair broken imports: sentences split across two steps, "Step 1" prefixes, stray HTML text.
- Fix technique only when it is plainly wrong (garlic sautéed 5 minutes before a long high-heat
  cook, baking soda "dissolved" in a teaspoon of milk and stirred into wet batter separately, cold
  pan for a sear). Explain the reason. Never change what the dish is, and never invent times or
  temperatures the recipe doesn't imply.

## Notes

- Keep what helps someone cook: substitutions, sourcing unusual ingredients, make-ahead and
  storage, equipment alternatives.
- Remove anecdotes, marketing, orphan fragments ("Before you start:"), notes that repeat the
  ingredient list or method, and "Prep: 10 mins"-style lines when the recipe's time fields already
  hold that value.
- Import warnings stay: they flag recipes whose text never came across.

## Section headings

- Short noun phrases: "Dressing", not "FOR DRESSING" or "For the dressing:".
