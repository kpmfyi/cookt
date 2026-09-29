// Convert between the recipe document and editable text blocks, keeping ids for unchanged lines.
import type { Ingredient, IngredientSection, InstructionSection, RecipeDocument, Step } from "./types";

export interface IngredientBlock { id: string; heading: string; text: string }
export interface StepBlock { id: string; heading: string; text: string }

let counter = 0;
const newId = (prefix: string) => `${prefix}_${Date.now().toString(36)}${(counter++).toString(36)}`;

export function ingredientBlocks(doc: RecipeDocument): IngredientBlock[] {
  return doc.ingredient_sections.map((section) => ({
    id: section.id,
    heading: section.heading ?? "",
    text: section.ingredients.map((item) => item.source_text).join("\n"),
  }));
}

export function stepBlocks(doc: RecipeDocument): StepBlock[] {
  return doc.instruction_sections.map((section) => ({
    id: section.id,
    heading: section.heading ?? "",
    text: section.steps.map((step) => step.text).join("\n\n"),
  }));
}

export function toIngredientSections(blocks: IngredientBlock[], previous: RecipeDocument): IngredientSection[] {
  const pool = new Map<string, Ingredient[]>();
  for (const section of previous.ingredient_sections) {
    for (const item of section.ingredients) pool.set(item.source_text, [...(pool.get(item.source_text) ?? []), item]);
  }
  return blocks
    .map((block) => ({
      id: block.id,
      heading: block.heading.trim() || null,
      ingredients: block.text
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean)
        .map((line) => {
          const reuse = pool.get(line)?.shift();
          return reuse ?? { id: newId("i"), source_text: line };
        }),
    }))
    .filter((section) => section.ingredients.length > 0);
}

export function toInstructionSections(blocks: StepBlock[], previous: RecipeDocument): InstructionSection[] {
  const pool = new Map<string, Step[]>();
  for (const section of previous.instruction_sections) {
    for (const step of section.steps) pool.set(step.text, [...(pool.get(step.text) ?? []), step]);
  }
  return blocks
    .map((block) => ({
      id: block.id,
      heading: block.heading.trim() || null,
      steps: block.text
        .split(/\n\s*\n/)
        .map((para) => para.replace(/\s*\n\s*/g, " ").trim())
        .filter(Boolean)
        .map((text) => pool.get(text)?.shift() ?? { id: newId("step"), text }),
    }))
    .filter((section) => section.steps.length > 0);
}

export { newId };
