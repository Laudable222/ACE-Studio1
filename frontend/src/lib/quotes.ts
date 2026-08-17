// A curated, on-brand quote bank shown as a daily reminder ribbon. No LLM, no network:
// instant, offline-safe, free, and always in-voice. Selection is DETERMINISTIC per
// (user-seed + calendar day) so a given person sees one stable quote all day, two people
// almost always see different ones, and it rotates on its own each day.

export type QuoteTheme =
  | "boldness" | "choices" | "research" | "teamwork" | "together";

export interface Quote { text: string; author?: string; theme: QuoteTheme; }

// ~70 lines across the themes you asked for: being bold / cold under fire, choices &
// opportunity cost, research rigor, helping each other, and shining together.
export const QUOTES: Quote[] = [
  // ── Boldness / conviction under uncertainty ───────────────────────────────
  { text: "Be cold on the process, warm on the people.", theme: "boldness" },
  { text: "Conviction is what's left after the doubt has had its say.", theme: "boldness" },
  { text: "The market pays for the trade you were brave enough to size.", theme: "boldness" },
  { text: "Fortune favours the prepared mind.", author: "Louis Pasteur", theme: "boldness" },
  { text: "Stay calm when the curve turns red — that's when edge is earned.", theme: "boldness" },
  { text: "Bold is not loud. Bold is doing the hard test anyway.", theme: "boldness" },
  { text: "Risk comes from not knowing what you're doing.", author: "Warren Buffett", theme: "boldness" },
  { text: "A ship in harbour is safe, but that is not what ships are built for.", author: "John A. Shedd", theme: "boldness" },
  { text: "Cold blood, clear head, clean signal.", theme: "boldness" },
  { text: "Courage is grace under pressure.", author: "Ernest Hemingway", theme: "boldness" },
  { text: "The best alpha you'll ever run is the one you didn't flinch on.", theme: "boldness" },
  { text: "Doubt kills more ideas than failure ever will.", theme: "boldness" },

  // ── Choices / opportunity cost ────────────────────────────────────────────
  { text: "Every alpha you run is one you chose over all the others.", theme: "choices" },
  { text: "The cost of any choice is the best idea you set aside for it.", theme: "choices" },
  { text: "Say no on purpose so your yes means something.", theme: "choices" },
  { text: "Ideas are cheap; the discipline to pick one is not.", theme: "choices" },
  { text: "You can do anything, but not everything — choose your one thing today.", theme: "choices" },
  { text: "The right question, chosen early, beats a hundred late answers.", theme: "choices" },
  { text: "Focus is deciding what NOT to research.", theme: "choices" },
  { text: "A good decision made now beats a perfect one made never.", theme: "choices" },
  { text: "Kill your darlings fast; the dataset won't miss them.", theme: "choices" },
  { text: "Simplicity is the ultimate sophistication.", author: "Leonardo da Vinci", theme: "choices" },
  { text: "Choose the hypothesis you can be wrong about cleanly.", theme: "choices" },

  // ── Research rigor / the craft ────────────────────────────────────────────
  { text: "In-sample is a story; out-of-sample is the truth.", theme: "research" },
  { text: "Torture the data long enough and it will confess to anything.", author: "Ronald Coase", theme: "research" },
  { text: "One hypothesis at a time — count every test you run.", theme: "research" },
  { text: "It is the mark of a truly intelligent person to be moved by statistics.", author: "George Bernard Shaw", theme: "research" },
  { text: "The plural of anecdote is not data.", theme: "research" },
  { text: "If it doesn't survive the holdout, it didn't happen.", theme: "research" },
  { text: "Doubt is the origin of wisdom.", author: "René Descartes", theme: "research" },
  { text: "Trust the process — but verify the correlation.", theme: "research" },
  { text: "An approximate answer to the right question is worth more than an exact answer to the wrong one.", author: "John Tukey", theme: "research" },
  { text: "Elegance in an alpha is a signal you understood the data.", theme: "research" },
  { text: "First make it correct, then make it robust, then make it fast.", theme: "research" },
  { text: "The signal is quiet. Turn down the noise, not the standards.", theme: "research" },
  { text: "Overfitting is flattery from your own data.", theme: "research" },
  { text: "Curiosity is the engine of achievement.", author: "Ken Robinson", theme: "research" },

  // ── Teamwork / helping each other ─────────────────────────────────────────
  { text: "Alone we go fast; together we go far.", theme: "teamwork" },
  { text: "Lift as you climb — someone once did it for you.", theme: "teamwork" },
  { text: "Share the idea that failed; it saves a teammate a week.", theme: "teamwork" },
  { text: "None of us is as smart as all of us.", author: "Ken Blanchard", theme: "teamwork" },
  { text: "A rising tide lifts all boats.", theme: "teamwork" },
  { text: "Great research is a relay, not a solo sprint.", theme: "teamwork" },
  { text: "Teach what you just learned while it's still fresh.", theme: "teamwork" },
  { text: "The strength of the team is each member; the strength of each member is the team.", author: "Phil Jackson", theme: "teamwork" },
  { text: "Ask early, ask often — questions are free, blind alleys are not.", theme: "teamwork" },
  { text: "Credit is not a scarce resource. Give it away.", theme: "teamwork" },
  { text: "Help someone else's alpha pass and yours gets sharper too.", theme: "teamwork" },
  { text: "Coming together is a beginning, staying together is progress, working together is success.", author: "Henry Ford", theme: "teamwork" },

  // ── Shine together / collective edge ──────────────────────────────────────
  { text: "Let's shine together. ✦", theme: "together" },
  { text: "Your edge grows when you hand a piece of it to someone else.", theme: "together" },
  { text: "We don't compete for the light — we make more of it.", theme: "together" },
  { text: "Small wins, shared daily, become a culture.", theme: "together" },
  { text: "The best portfolios are built by the best communities.", theme: "together" },
  { text: "Talent wins games, teamwork wins championships.", author: "Michael Jordan", theme: "together" },
  { text: "Be the teammate you wish you had on your first day.", theme: "together" },
  { text: "Progress compounds — yours and everyone's beside you.", theme: "together" },
  { text: "Good work speaks; great work lifts the room.", theme: "together" },
  { text: "Shine bright, then hold the torch for the next researcher.", theme: "together" },
  { text: "The whole team is stronger than the sum of its alphas.", theme: "together" },
  { text: "Happy research — and let's shine together. ✦", theme: "together" },

  // ── More boldness / cold under fire ───────────────────────────────────────
  { text: "Nerve is a skill. Practise it on small bets first.", theme: "boldness" },
  { text: "The drawdown is a test of temperament, not of the thesis.", theme: "boldness" },
  { text: "Ice in the veins, fire in the curiosity.", theme: "boldness" },
  { text: "Whatever you can do, or dream you can, begin it. Boldness has genius in it.", author: "Goethe", theme: "boldness" },
  { text: "You miss 100% of the shots you don't take.", author: "Wayne Gretzky", theme: "boldness" },
  { text: "Act on the signal, not on the fear.", theme: "boldness" },
  { text: "The comfort zone is a beautiful place, but nothing ever grows there.", theme: "boldness" },
  { text: "Hold your nerve when the p-value tempts you to quit early.", theme: "boldness" },

  // ── More choices / focus ──────────────────────────────────────────────────
  { text: "The best researchers are ruthless about what they ignore.", theme: "choices" },
  { text: "A narrow question is a gift to your future self.", theme: "choices" },
  { text: "You have exactly one attention. Spend it like capital.", theme: "choices" },
  { text: "Perfect is the enemy of shipped.", theme: "choices" },
  { text: "Decide with the data you have, not the data you wish you had.", theme: "choices" },
  { text: "Two good paths? Pick one fully rather than both halfway.", theme: "choices" },
  { text: "The idea you protect from testing is the one costing you most.", theme: "choices" },
  { text: "Prune early, harvest often.", theme: "choices" },

  // ── More research rigor ───────────────────────────────────────────────────
  { text: "Correlation is a hint, not a verdict.", theme: "research" },
  { text: "Measure twice, neutralise once.", theme: "research" },
  { text: "The plot that surprises you is the one worth trusting.", theme: "research" },
  { text: "Reproducibility is respect — for your future self and your team.", theme: "research" },
  { text: "No matter how beautiful, a theory that fails the holdout is wrong.", theme: "research" },
  { text: "Slow is smooth, smooth is robust.", theme: "research" },
  { text: "Log the failed run; it's half of tomorrow's insight.", theme: "research" },
  { text: "Turnover is the tax on your conviction — pay it deliberately.", theme: "research" },
  { text: "The map is not the territory; the backtest is not the market.", theme: "research" },
  { text: "Curiosity, then discipline. In that order, every time.", theme: "research" },

  // ── More teamwork / helping each other ────────────────────────────────────
  { text: "The question you're afraid to ask is the one the whole team needs.", theme: "teamwork" },
  { text: "Debug together; the second pair of eyes is free alpha.", theme: "teamwork" },
  { text: "A good review makes the work better and the person bigger.", theme: "teamwork" },
  { text: "Leave the codebase kinder than you found it.", theme: "teamwork" },
  { text: "Generosity with knowledge compounds faster than any signal.", theme: "teamwork" },
  { text: "If you want to go deep, bring someone with you.", theme: "teamwork" },
  { text: "Celebrate a teammate's alpha like it's your own — soon it will be.", theme: "teamwork" },

  // ── More shine together ───────────────────────────────────────────────────
  { text: "One lamp lights another and loses nothing.", theme: "together" },
  { text: "Build the ladder, then hold it steady for the next climber.", theme: "together" },
  { text: "The team that shares its edge keeps discovering new ones.", theme: "together" },
  { text: "We win quietly, together, one honest test at a time.", theme: "together" },
  { text: "Your best work is a light for someone still finding theirs.", theme: "together" },
  { text: "Rise by lifting others.", author: "Robert Ingersoll", theme: "together" },
  { text: "Strong alone, unstoppable together.", theme: "together" },

  // ── Third wave ────────────────────────────────────────────────────────────
  { text: "Discipline is choosing between what you want now and what you want most.", theme: "choices" },
  { text: "The market rewards patience more often than speed.", theme: "boldness" },
  { text: "Every no is a yes to something you care about more.", theme: "choices" },
  { text: "Great signals hide in the questions no one else is asking.", theme: "research" },
  { text: "Fall in love with the problem, not your first solution.", theme: "research" },
  { text: "Nobody remembers the idea you almost tried.", theme: "boldness" },
  { text: "Standards are a gift you give the whole team.", theme: "teamwork" },
  { text: "The quiet win today is the compounding of yesterday's care.", theme: "together" },
  { text: "Precision beats prediction.", theme: "research" },
  { text: "Backtest the humility, not just the returns.", theme: "research" },
  { text: "Bring your best on the boring days; that's where edge hides.", theme: "boldness" },
  { text: "Two researchers, one whiteboard, zero egos.", theme: "teamwork" },
  { text: "Do the small thing well and the big thing follows.", theme: "choices" },
  { text: "A clean expression is a kindness to whoever reads it next.", theme: "teamwork" },
  { text: "Signal is rare; steal none of the credit for it.", theme: "together" },
  { text: "Test the idea you fear is wrong — that's where learning lives.", theme: "research" },
  { text: "Momentum is built one honest commit at a time.", theme: "together" },
  { text: "When in doubt, shrink the bet and keep the nerve.", theme: "boldness" },
  { text: "The plan survives contact with the data, or it wasn't a plan.", theme: "research" },
  { text: "Choose depth over breadth until depth pays, then repeat.", theme: "choices" },
  { text: "Answer a teammate's question and you sharpen your own thinking.", theme: "teamwork" },
  { text: "We are lanterns, not spotlights — light the whole room.", theme: "together" },
  { text: "The bravest research is the kind you might be wrong about publicly.", theme: "boldness" },
  { text: "Fewer, better ideas — run them all the way through.", theme: "choices" },
  { text: "Kindness scales. So does knowledge. Compound both.", theme: "together" },
];

// A stable, per-browser seed. If we ever expose the BRAIN username we can pass it in; until
// then a persistent random id makes the pick "different for different users" on the same day.
export function userSeed(): string {
  const KEY = "ace2:userSeed";
  try {
    let s = localStorage.getItem(KEY);
    if (!s) { s = Math.random().toString(36).slice(2) + Date.now().toString(36); localStorage.setItem(KEY, s); }
    return s;
  } catch {
    return "anon";
  }
}

// Local calendar hour, e.g. "2026-07-31-14" — the rotation key. Changes every hour so the
// reminder refreshes through the day rather than staying fixed.
export function hourKey(d = new Date()): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}-${String(d.getHours()).padStart(2, "0")}`;
}

// Deterministic 32-bit string hash (FNV-1a style) — same input, same index, no storage.
function hash(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193); }
  return h >>> 0;
}

// The quote for a given device in a given hour. Stable within the hour (no flicker on reload),
// rotates every hour, and — because the seed is a per-device random id — appears in a different
// order on every device. A second mix term keeps consecutive hours from feeling predictable.
export function pickQuote(seed = userSeed(), bucket = hourKey()): Quote {
  const i = hash(`${seed}|${bucket}`) ^ hash(`${bucket}|${seed}`);
  return QUOTES[(i >>> 0) % QUOTES.length];
}

// Short, friendly label per theme for the ribbon's tag on the right end.
export const THEME_LABEL: Record<QuoteTheme, string> = {
  boldness: "Bold", choices: "Choices", research: "Rigor", teamwork: "Team", together: "Shine",
};
