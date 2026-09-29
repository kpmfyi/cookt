import { useId } from "react";
import { useRestrictions } from "../lib/prefs";
import styles from "./Marks.module.css";

/*
 * Marks: the small pictures that say what a dish is without words.
 *   <Flag cuisine>    the country's flag emoji; a drawn flag in the same style for regional
 *                     cuisines that have no flag (Mediterranean, Asian, Cajun, …).
 *   <Mark kind value> an emoji for course, protein, diet or equipment; flat-colour art drawn
 *                     to emoji fidelity where no emoji exists (tofu, gluten-free, dairy-free,
 *                     the appliances).
 * Every mark carries its name for screen readers and as a tooltip, so the vocabulary is
 * learnable: the filter panel shows mark + word side by side.
 */

type Stripes = { dir: "h" | "v"; colors: string[]; weights?: number[] };
interface FlagSpec {
  stripes?: Stripes;
  fill?: string;
  canton?: string; // top-left quarter
  disc?: { color: string; r?: number; x?: number; y?: number };
  ring?: string; // hollow circle
  cross?: { color: string; outline?: string; nordic?: boolean };
  dot?: string; // small centre dot
  hoist?: string; // narrow band at the left edge
}

const RED = "#c8102e";
const WHITE = "#ffffff";
const NAVY = "#1f3a7a";
const GREEN = "#1f8a4c";

/* Country flag emoji by cuisine. */
const FLAG_EMOJI: Record<string, string> = {
  american: "🇺🇸", italian: "🇮🇹", mexican: "🇲🇽", indian: "🇮🇳", thai: "🇹🇭", greek: "🇬🇷", japanese: "🇯🇵",
  british: "🇬🇧", french: "🇫🇷", korean: "🇰🇷", irish: "🇮🇪", spanish: "🇪🇸", portuguese: "🇵🇹", moroccan: "🇲🇦",
  chinese: "🇨🇳", egyptian: "🇪🇬", swedish: "🇸🇪", peruvian: "🇵🇪", vietnamese: "🇻🇳", russian: "🇷🇺", european: "🇪🇺",
  german: "🇩🇪", turkish: "🇹🇷", lebanese: "🇱🇧", brazilian: "🇧🇷", argentinian: "🇦🇷", cuban: "🇨🇺", jamaican: "🇯🇲",
  filipino: "🇵🇭", indonesian: "🇮🇩", malaysian: "🇲🇾", ethiopian: "🇪🇹", polish: "🇵🇱", hungarian: "🇭🇺", canadian: "🇨🇦",
  australian: "🇦🇺", israeli: "🇮🇱", persian: "🇮🇷", iranian: "🇮🇷", pakistani: "🇵🇰", nepalese: "🇳🇵", dutch: "🇳🇱",
  belgian: "🇧🇪", swiss: "🇨🇭", austrian: "🇦🇹", danish: "🇩🇰", norwegian: "🇳🇴", finnish: "🇫🇮", scottish: "🏴󠁧󠁢󠁳󠁣󠁴󠁿",
};

const FLAGS: Record<string, FlagSpec> = {
  southern: { stripes: { dir: "h", colors: [RED, WHITE, RED, WHITE, RED, WHITE, RED] } },
  hawaiian: { stripes: { dir: "h", colors: [WHITE, RED, NAVY, WHITE, RED, NAVY] }, canton: NAVY },
  cajun: { stripes: { dir: "v", colors: ["#6a3d9a", "#e0b34a", GREEN] } },
  "tex-mex": { stripes: { dir: "v", colors: ["#006847", WHITE, "#ce1126"] }, canton: NAVY },
  "middle eastern": { stripes: { dir: "h", colors: ["#000000", WHITE, "#007a3d"] }, hoist: "#ce1126" },
  "latin american": { stripes: { dir: "h", colors: ["#74acdf", WHITE, "#74acdf"] }, dot: "#f6b40e" },
  mediterranean: { stripes: { dir: "h", colors: ["#2a6fb0", WHITE], weights: [2, 1] } },
  asian: { stripes: { dir: "v", colors: ["#b22222", "#e0b34a"], weights: [3, 1] } },
};

export function hasFlag(cuisine: string): boolean {
  const key = cuisine.toLowerCase();
  return key in FLAG_EMOJI || key in FLAGS;
}

function stripeRects({ dir, colors, weights }: Stripes) {
  const total = (weights ?? colors.map(() => 1)).reduce((a, b) => a + b, 0);
  let at = 0;
  return colors.map((color, i) => {
    const w = (weights?.[i] ?? 1) / total;
    const rect =
      dir === "h" ? (
        <rect key={i} x="0" y={at * 12} width="16" height={w * 12 + 0.02} fill={color} />
      ) : (
        <rect key={i} x={at * 16} y="0" width={w * 16 + 0.02} height="12" fill={color} />
      );
    at += w;
    return rect;
  });
}

/** The cuisine's flag: an emoji where the country has one, a drawn flag for regions. */
export function Flag({ cuisine, size = 20, className }: { cuisine: string; size?: number; className?: string }) {
  const id = useId();
  const key = cuisine.toLowerCase();
  const emoji = FLAG_EMOJI[key];
  if (emoji) return <Emoji char={emoji} label={cuisine} size={size} className={className} />;
  const spec = FLAGS[key];
  return (
    <svg
      className={[styles.flag, className].filter(Boolean).join(" ")}
      width={size}
      height={(size * 3) / 4}
      viewBox="0 0 16 12"
      role="img"
      aria-label={cuisine}
    >
      <title>{cuisine}</title>
      <clipPath id={id}><rect width="16" height="12" rx="1.5" /></clipPath>
      <g clipPath={`url(#${id})`}>
        {spec ? (
          <>
            <rect width="16" height="12" fill={spec.fill ?? WHITE} />
            {spec.stripes && stripeRects(spec.stripes)}
            {spec.hoist && <rect x="0" y="0" width="4" height="12" fill={spec.hoist} />}
            {spec.canton && <rect x="0" y="0" width="7" height="6.5" fill={spec.canton} />}
            {spec.cross && (
              <>
                {spec.cross.outline && <path d={spec.cross.nordic ? "M6 0v12M0 6h16" : "M8 0v12M0 6h16"} stroke={spec.cross.outline} strokeWidth="4" />}
                <path d={spec.cross.nordic ? "M6 0v12M0 6h16" : "M8 0v12M0 6h16"} stroke={spec.cross.color} strokeWidth={spec.cross.nordic ? 2.4 : 2.2} />
              </>
            )}
            {spec.disc && <circle cx={spec.disc.x ?? 8} cy={spec.disc.y ?? 6} r={spec.disc.r ?? 2.5} fill={spec.disc.color} />}
            {spec.ring && <circle cx="8" cy="6" r="2.4" fill="none" stroke={spec.ring} strokeWidth="1.1" />}
            {spec.dot && <circle cx="8" cy="6" r="1.2" fill={spec.dot} />}
          </>
        ) : (
          <rect width="16" height="12" className={styles.unknown} />
        )}
      </g>
      <rect width="16" height="12" rx="1.5" className={styles.edge} />
    </svg>
  );
}

/** Any emoji as a UI mark: sized, labelled for screen readers, in the platform's emoji face. */
export function Emoji({ char, label, size = 20, slashed = false, className, animate }: { char: string; label?: string; size?: number; slashed?: boolean; className?: string; animate?: "float" | "pop" | "spin" | "pulse" }) {
  return (
    <span
      className={[styles.emoji, slashed ? styles.slashed : "", animate ? styles[animate] : "", className].filter(Boolean).join(" ")}
      style={{ fontSize: size, width: size * 1.25, height: size * 1.25 }}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      title={label}
    >
      {char}
    </span>
  );
}

/** A hand-picked emoji is kept as a personal tag "emoji:🍝". */
export const EMOJI_TAG = "emoji:";

export function chosenEmoji(tags: Partial<Record<string, string[] | undefined>>): string | null {
  const tag = (tags.personal ?? []).find((t) => t.startsWith(EMOJI_TAG));
  return tag ? tag.slice(EMOJI_TAG.length) : null;
}

/** The one emoji that stands for a recipe: a chosen one, else its course, else its protein, else a plate. */
export function heroEmoji(tags: Partial<Record<string, string[] | undefined>>): string {
  const chosen = chosenEmoji(tags);
  if (chosen) return chosen;
  const course = tags.course?.[0];
  if (course && EMOJI.course[course]) return EMOJI.course[course];
  const protein = (tags.protein ?? []).find((p) => EMOJI.protein[p]);
  if (protein) return EMOJI.protein[protein];
  return "🍽️";
}

/* Emoji per tag value. A value missing here falls back to drawn art (ART) or nothing. */
const EMOJI: Record<string, Record<string, string>> = {
  course: {
    main: "🍽️", side: "🥣", soup: "🍲", salad: "🥗", "sauce/condiment": "🧂", bread: "🍞",
    dessert: "🍰", breakfast: "🍳", snack: "🍪", drink: "🥤",
  },
  protein: {
    chicken: "🍗", beef: "🥩", pork: "🥓", lamb: "🐑", fish: "🐟", shellfish: "🦐", eggs: "🥚", "beans/legumes": "🫘",
  },
  diet: { vegetarian: "🥦", vegan: "🌱" },
  equipment: {},
};

/* Emoji that mean the opposite when struck through: a red slash over the thing that's left out. */
const SLASHED: Record<string, Record<string, string>> = {
  diet: { "gluten-free": "🌾", "dairy-free": "🥛" },
};

/* Flat-colour art at emoji fidelity for the gaps, on a 24 grid. */
const ART: Record<string, Record<string, React.ReactNode>> = {
  protein: {
    "tofu/tempeh": (
      <>
        <path d="M3 9.5 12 5l9 4.5L12 14z" fill="#fbf7ec" />
        <path d="M3 9.5 12 14v7l-9-4.5z" fill="#e3dccb" />
        <path d="M21 9.5 12 14v7l9-4.5z" fill="#cfc6b1" />
        <path d="M3 9.5 12 5l9 4.5L12 14zM3 9.5 12 14v7l-9-4.5zM21 9.5 12 14v7l9-4.5z" fill="none" stroke="#9e9377" strokeWidth=".9" strokeLinejoin="round" />
        <path d="M6 12.5c1.5-.5 3-.5 4.5.3" fill="none" stroke="#b5ab90" strokeWidth=".8" strokeLinecap="round" />
      </>
    ),
  },
  equipment: {
    "instant pot": (
      <>
        <rect x="4" y="8" width="16" height="13" rx="2.5" fill="#3c4247" />
        <rect x="4" y="8" width="16" height="4" rx="2" fill="#b7bec4" />
        <rect x="3" y="6.5" width="18" height="2.5" rx="1.25" fill="#5b636a" />
        <rect x="9.5" y="3.5" width="5" height="3" rx="1" fill="#2b2f33" />
        <rect x="8" y="14" width="8" height="4" rx="1" fill="#1b1e21" />
        <circle cx="10" cy="16" r=".8" fill="#4fd1c5" />
        <circle cx="14" cy="16" r=".8" fill="#ff7a59" />
      </>
    ),
    "slow cooker": (
      <>
        <rect x="4" y="9" width="16" height="12" rx="2.5" fill="#c0392b" />
        <rect x="4" y="9" width="16" height="3" fill="#9c2f24" />
        <path d="M5 9a7 3.5 0 0 1 14 0z" fill="#bfe3f2" />
        <path d="M5 9a7 3.5 0 0 1 14 0" fill="none" stroke="#8fb9cc" strokeWidth=".9" />
        <rect x="10.5" y="4" width="3" height="1.8" rx=".9" fill="#5b636a" />
        <rect x="8" y="16" width="8" height="1.6" rx=".8" fill="#e6b7b1" />
      </>
    ),
    oven: (
      <>
        <rect x="3" y="3" width="18" height="18" rx="2" fill="#d9dde1" />
        <rect x="3" y="3" width="18" height="5" rx="2" fill="#aab2b9" />
        <circle cx="7" cy="5.5" r="1" fill="#2b2f33" />
        <circle cx="10.5" cy="5.5" r="1" fill="#2b2f33" />
        <circle cx="14" cy="5.5" r="1" fill="#2b2f33" />
        <rect x="5.5" y="10" width="13" height="8.5" rx="1" fill="#2b2f33" />
        <rect x="7" y="11.5" width="10" height="5.5" rx=".8" fill="#f2a53a" />
        <rect x="5.5" y="9" width="13" height="1.4" fill="#6c757d" />
      </>
    ),
    "sous vide": (
      <>
        <rect x="3" y="10" width="18" height="11" rx="2" fill="#bfe3f2" />
        <path d="M3 14c2-1 4 1 6 0s4 1 6 0 4 1 6 0v7H3z" fill="#7fc4e2" />
        <rect x="14" y="3" width="4" height="12" rx="1.5" fill="#2b2f33" />
        <rect x="14.8" y="4" width="2.4" height="2.4" rx=".5" fill="#4fd1c5" />
        <rect x="6" y="15.5" width="6" height="4" rx="1" fill="#e07b6b" />
      </>
    ),
    mixer: (
      <>
        <rect x="5" y="19" width="14" height="2.5" rx="1" fill="#6c757d" />
        <rect x="4" y="4" width="10" height="6" rx="2" fill="#e05252" />
        <rect x="14" y="4" width="4" height="4" rx="1.5" fill="#c43c3c" />
        <rect x="5" y="10" width="2" height="9" fill="#6c757d" />
        <path d="M11 10v6.5M11 10c-2 2-2 4 0 6.5 2-2.5 2-4.5 0-6.5" fill="none" stroke="#aab2b9" strokeWidth="1.1" strokeLinecap="round" />
        <path d="M8 19a4 3 0 0 0 8 0z" fill="#aab2b9" />
      </>
    ),
  },
};

export type MarkKind = "course" | "protein" | "diet" | "equipment";

/** A mark for a tag value; renders nothing for values without one (e.g. protein "none"). */
export function Mark({ kind, value, size = 20, label = true, className }: { kind: MarkKind; value: string; size?: number; label?: boolean; className?: string }) {
  const emoji = EMOJI[kind]?.[value];
  if (emoji) return <Emoji char={emoji} label={label ? value : undefined} size={size} className={className} />;
  const slashed = SLASHED[kind]?.[value];
  if (slashed) return <Emoji char={slashed} label={label ? value : undefined} size={size} slashed className={className} />;
  const art = ART[kind]?.[value];
  if (!art) return null;
  return (
    <svg
      className={[styles.art, className].filter(Boolean).join(" ")}
      width={size * 1.25}
      height={size * 1.25}
      viewBox="0 0 24 24"
      role={label ? "img" : undefined}
      aria-label={label ? value : undefined}
      aria-hidden={label ? undefined : true}
    >
      {label && <title>{value}</title>}
      {art}
    </svg>
  );
}

/** The marks that describe a dish: cuisine flag, course, protein, diet, equipment. */
export function DishMarks({ tags, cuisineName = false, courseName = false, size = 20, className }: {
  tags: Partial<Record<string, string[] | undefined>>;
  cuisineName?: boolean;
  courseName?: boolean;
  size?: number;
  className?: string;
}) {
  const cuisine = tags.cuisine?.[0];
  const course = tags.course?.[0];
  const showRestrictions = useRestrictions();
  const protein = (tags.protein ?? []).filter((p) => p !== "none");
  const diet = (tags.diet ?? []).filter((d) => showRestrictions || !d.endsWith("-free"));
  const equipment = tags.equipment ?? [];
  if (!cuisine && !course && !protein.length && !diet.length && !equipment.length) return null;
  return (
    <span className={[styles.row, className].filter(Boolean).join(" ")}>
      {cuisine && (
        <span className={styles.cuisine}>
          <Flag cuisine={cuisine} size={size} />
          {cuisineName && <span>{cuisine}</span>}
        </span>
      )}
      {course && (
        <span className={styles.cuisine}>
          <Mark kind="course" value={course} size={size} />
          {courseName && <span>{course}</span>}
        </span>
      )}
      {protein.map((p) => <Mark key={p} kind="protein" value={p} size={size} />)}
      {diet.map((d) => <Mark key={d} kind="diet" value={d} size={size} />)}
      {equipment.map((e) => <Mark key={e} kind="equipment" value={e} size={size} />)}
    </span>
  );
}
