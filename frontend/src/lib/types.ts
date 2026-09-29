export interface MeasurementVariant { display_text: string; quantity?: string | null; unit?: string | null }
export interface Ingredient {
  id: string;
  source_text: string;
  quantity?: string | null;
  unit?: string | null;
  name?: string | null;
  note?: string | null;
  us?: MeasurementVariant | null;
  metric?: MeasurementVariant | null;
}
export interface IngredientSection { id: string; heading?: string | null; ingredients: Ingredient[] }
export interface Step { id: string; text: string }
export interface InstructionSection { id: string; heading?: string | null; steps: Step[] }
export interface RecipeDocument {
  schema_version: 2;
  title: string;
  yield_text?: string | null;
  notes: string[];
  personal_notes?: string | null;
  legacy_source?: boolean;
  ingredient_sections: IngredientSection[];
  instruction_sections: InstructionSection[];
}
export interface ImageRef { id: string; src: string; thumb: string; width?: number; height?: number; step_id?: string }
export type TagKind = "cuisine" | "course" | "protein" | "diet" | "equipment" | "personal";
export interface NextTimeNote { id: string; text: string; person_id?: string | null; created_at: string }
export interface NutritionSummary {
  source: "publisher" | "computed";
  confidence?: "high" | "medium" | "low" | null;
  kcal?: number | null;
  protein_g?: number | null;
  carbs_g?: number | null;
  fat_g?: number | null;
  fiber_g?: number | null;
  sodium_mg?: number | null;
}
export interface Recipe {
  id: string;
  slug: string;
  title: string;
  credit?: string | null;
  description?: string | null;
  source_url?: string | null;
  source_site?: string | null;
  source_author?: string | null;
  prep_minutes?: number | null;
  cook_minutes?: number | null;
  total_minutes?: number | null;
  servings?: number | null;
  image: ImageRef | null;
  step_images: ImageRef[];
  tags: Partial<Record<TagKind, string[]>>;
  favorites: string[];
  cooked: { count: number; last: string } | null;
  next_time: NextTimeNote[];
  nutrition: NutritionSummary | null;
  created_at: string;
  updated_at: string;
  version: number;
  document: RecipeDocument;
}
export interface Person { id: string; name: string }
export interface Catalog { version: string; recipes: Recipe[]; people: Person[] }
