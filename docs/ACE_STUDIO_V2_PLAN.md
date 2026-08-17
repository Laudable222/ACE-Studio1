# ACE Studio v2 — Architecture & Feature Plan

> A local-first **Global Alpha Studio** for WorldQuant BRAIN consultants.
> Goal: not to gate features behind a paywall, but to **maximise every user's research
> success rate** while keeping each user's research genuinely their own. Premium is
> reframed as **donation-ware**: users who reach a high, verified success rate are
> invited to donate from **$2**, and donating devices unlock the advanced tooling.

---

## 0. Guiding principles

1. **AI-first, template-second.** Templates exist, but every generation path is driven
   by an LLM research step. The system never ships the same templates to two users.
2. **Uniqueness by construction.** Per-device seed + per-user research history + a
   diversity engine guarantee that generated ideas diverge between users and over time.
3. **Success is the product.** The app is judged by users' submittable-alpha rate, not
   by feature count. Every screen exists to raise that rate.
4. **Local and private.** Everything runs on the user's machine. No user PII is collected;
   entitlement is bound to hardware, not to an account.
5. **No page ever scrolls.** Layouts are compact, responsive, and auto-scale to the
   user's OS zoom/screen so nothing is cut off.
6. **Never limit capability.** Power features (research papers, cross-region transfer,
   deep single-field extraction) are always available; donation unlocks *convenience and
   scale*, never the ability to do good research.

---

## 1. Tech stack & repository layout

```
ace-studio/
├─ run.bat                     # one click: starts backend + frontend, opens browser
├─ run.sh                      # same for macOS/Linux
├─ backend/                    # FastAPI (Python)
│  ├─ app/
│  │  ├─ main.py               # app factory, routers, static-serve of built frontend
│  │  ├─ core/                 # config, logging, device-id, crypto, scheduler
│  │  ├─ brain/                # ace_lib wrappers, session_manager, rate-aware client
│  │  ├─ db/                   # SQLAlchemy models, migrations (Alembic), DuckDB analytics
│  │  ├─ knowledge/            # dataset/field similarity graph, embeddings, provenance
│  │  ├─ research/             # LLM research engine, strategy library, paper ingestion
│  │  ├─ generation/           # template + expression generators, diversity engine
│  │  ├─ simulation/           # regular + super orchestration, metrics gate
│  │  ├─ analytics/            # correlation, portfolio, insights, success-rate engine
│  │  ├─ entitlement/          # tier verification, signed licences, donation flow
│  │  └─ modules/…             # feature modules, each self-contained (router+service+schema)
│  └─ pyproject.toml
├─ frontend/                   # React + Vite + TypeScript
│  ├─ src/
│  │  ├─ app/                  # router, layout shell (sidebar/header), theme
│  │  ├─ features/             # one folder per screen/domain (data, research, templates…)
│  │  ├─ components/           # shared UI (compact primitives, charts, editors)
│  │  ├─ lib/                  # api client, hooks, autoscale, autocomplete engine
│  │  └─ styles/               # design tokens (the current orange/dark palette)
│  └─ vite.config.ts
└─ data/                       # per-device sqlite db, caches, saved prompts, licences
```

**`run.bat`** (root): creates/activates the venv, `pip install`, `npm install` if needed,
starts `uvicorn` (backend) and either `vite` (dev) or serves the built `dist/` (prod) from
FastAPI, then opens the browser. One command, zero terminal knowledge required.

**Module contract.** Each backend module exposes `router`, `service`, `schema`, `models`
and registers itself; adding a feature = adding a module, never editing a monolith. The
frontend mirrors this with a `features/<name>/` folder (route, panels, hooks, api).

---

## 2. The intelligent database (the "brain")

A rich local store (SQLite for transactional data + DuckDB for analytics) that **learns
from every fetch** and makes the whole studio smarter over time.

### 2.1 Core tables
- `datasets(id, region, delay, universe, category, name, description, coverage,
  value_score, alpha_count, fetched_at, embedding)`
- `fields(id, dataset_id, region, delay, type, description, coverage, crowding,
  is_virgin, prefix, embedding, first_seen_at)`
- `operators(name, scope, category, signature, arity, params, definition)`
- `expressions(id, text, kind, operators[], fields[], categories[], region, delay,
  created_by_module, prompt_id, research_id, seed)`
- `simulations(id, expression_id, config, alpha_id, status, error, ran_at)`
- `metrics(simulation_id, sharpe, fitness, turnover, returns, margin, drawdown,
  self_corr, prod_corr, powerpool_corr, is_tests_json, passed_gate, gate_reasons)`
- `research_sessions(id, category, region, goal, papers[], transcript, hypotheses,
  produced_expressions[], produced_templates[], created_at)`
- `prompts(id, name, category, scope, body, datasets[], created_at, win_rate)`
- `provenance(entity, entity_id, source, prompt_id, research_id, seed, model)`
- `usage(operator, field, category, region, count, avg_abs_fitness, last_used)`
- `device(id, fingerprint, tier, licence_blob, donated_at)`

### 2.2 Similarity & transfer intelligence
After each dataset/field fetch the **knowledge module** runs (in the background):
- **Cross-region dataset similarity.** Embed dataset name+description (local sentence
  model, no external calls) and link datasets that represent the *same concept across
  regions* (e.g. `news18` USA ↔ its EUR/ASI analogue). Stored as a
  `dataset_similarity(a, b, score, same_concept)` graph.
- **Cross-region field mapping.** For linked datasets, map fields by prefix + type +
  description similarity → `field_equivalence(a, b, score)`. This powers **"research this
  idea in every region where the field exists."**
- **Category graph.** Track which categories co-occur safely vs which raise overfitting
  risk, feeding the overfitting guard (§4.5).
- **Crowding & virginity intelligence.** Surface under-explored (virgin, low alpha_count)
  fields per region so users are steered toward original territory.

### 2.3 Per-user uniqueness
- Every device gets a **research seed** (from the device fingerprint + install nonce).
- Generators mix the seed into prompt framing, operator sampling order, and hypothesis
  selection, so two users with identical inputs still get **different** ideas.
- The `usage` table biases generation *away* from operators/fields/categories this user
  has already leaned on → forced diversity that is personal to each user.

---

## 3. Screens & routes (React Router)

No screen scrolls; each is a compact, panelled workspace. SuperAlpha is fully separated.

| Route | Screen | Purpose |
|---|---|---|
| `/` | **Command Center** | KPIs: success rate, submittable count, diversity score, session, quota; recent runs; "next best action". |
| `/data` | **Data Explorer** | Datasets + fields with prefix filter, virginity/crowding, cross-region twins, presets. |
| `/knowledge` | **Knowledge Graph** | Visual map of dataset/field similarity across regions; click to research a concept everywhere it exists. |
| `/research` | **Research Lab** | LLM research over a category/dataset/region; ingest research papers (whole or chosen pages); produce hypotheses → **push to Generate or Templates**. |
| `/strategies` | **Strategy Atlas** | Per-category strategy explorer (Analyst, Broker, Earnings, … as in the categories screen). LLM-explored, never hardcoded. |
| `/templates` | **Template Studio** | Editor with live syntax check, Tab autocomplete+signatures, multi-line, LLM suggestion (diverse, N-ops, no bucket/vec_*), saved templates, best-templates. |
| `/generate` | **Generation** | Single- and multi-field LLM generation; deep single-field signal extraction; ≤2-category multi-field rule; per-user uniqueness. |
| `/simulate` | **Simulation** | Regular alphas; multi-universe/neutralization; metrics gate preview; user-chosen tags. |
| `/super` | **SuperAlpha** (separated) | Selection building/counting, combos, sweep, its own results. |
| `/results` | **Results & Analytics** | Filter/sort/CSV, PnL sparklines, per-alpha drill-down, gate pass/fail per metric. |
| `/portfolio` | **Correlation & Portfolio** | Pairwise correlation, max-uncorrelated set, self/prod/powerpool gate, submit set (user-chosen). |
| `/operators` | **Operator Atlas** | Every operator with signature, family, your usage & avg-fitness, diversity nudges. |
| `/regions` | **Region & Universe Atlas** | Market factors per region/universe; delay-0 focus; what transfers across regions. |
| `/prompts` | **Prompt Library** | Dataset-focused prompts, saved & versioned, with win-rate. |
| `/settings` | **Settings** | Keys, model per provider + Test key, tags, appearance/scale, session. |
| `/support` | **Support** | File issues; plain-language error inbox. |
| `/tier` | **Success & Donation** | Live success-rate scorecard; when thresholds are met, invite to donate ($2+) and unlock. |

**Sidebar (complex):** grouped nav (Research / Build / Run / Analyse / System), live
session + quota badges, diversity meter, quick actions, collapsible, keyboard-navigable.

**Header (complex):** global context (region·universe·delay·instrument) as editable chips,
global command palette (⌘K) to jump anywhere or run actions, active-jobs tray with live
progress, provider/model indicator, tier badge, appearance/scale control, notifications.

---

## 4. Research & generation engine

### 4.1 Research Lab (the heart)
A dedicated LLM research workspace, decoupled from generation:
- Pick a **category / dataset(s) / region**; state a goal.
- Optionally attach **research papers** (PDF): choose *whole paper* or *specific pages*;
  the system extracts economic mechanisms, then proposes signals/templates/expressions
  grounded in the *provided datasets*.
- The model returns **hypotheses** (mechanism, expected sign, horizon, fields, region
  caveats), a transcript, and candidate expressions/templates.
- One click **pushes** chosen hypotheses to **Generate** or **Template Studio** — research
  and building are separate steps, so users think first, generate second.

### 4.2 Per-category strategy exploration (never hardcoded)
For each data category (Analyst, Broker, Earnings, Fundamental, Imbalance, Insiders,
Institutions, Macro, Model, News, Option, Other, Price Volume, Risk, Sentiment, Short
Interest, Social Media) the Strategy Atlas asks the LLM to **explore** the strategy space
live, seeded per-user, rather than shipping a fixed list. Results are cached per user but
re-explorable, so the space keeps widening.

### 4.3 Deep single-field signal extraction
A first-class mode that extracts signal from **one field** in *every* structural way, not
just "wrap in a couple of operators":
- arithmetic (differences, ratios, spreads vs its own transforms),
- time-series (momentum, reversal, decay, volatility, seasonality),
- cross-sectional (rank, zscore, scale, winsorize, normalize),
- group-relative (industry/sector/… neutralisation and comparison),
- logical/conditional (regime gates, if_else, sign, clamp),
- transformations composed to a target complexity.
The LLM is prompted to cover these mechanisms with diversity, not repeat one shape.

### 4.4 Multi-field with a hard 2-category ceiling
- Multi-field generation is available via LLM too.
- **Strict rule, enforced in the prompt AND by a validator:** a multi-field expression may
  draw fields from **at most two distinct data categories**. The generator is given the
  category of every field; any expression mixing 3+ categories is rejected pre-simulation.

### 4.5 Overfitting & region guards
- The **overfitting guard** blocks category combinations known to co-inflate in-sample fit
  (from the category graph, §2.2). Enforced in prompts and as a hard reject.
- **Region-aware research.** Prompts include market-structure context per region (liquidity,
  microstructure, delay conventions, holiday/settlement effects). **Delay-0 research gets
  special treatment** (see §5) because delay-0 alphas hinge on Sharpe.
- **Cross-region transfer.** When a field has equivalents in other regions (§2.2), the user
  can replay a winning idea across all of them in one action.

### 4.6 Diversity engine
- Tracks operator/field/category usage per user (`usage` table).
- Down-weights recently/over-used operators and fields at generation time.
- Emits a **diversity score** (breadth of operators × fields × categories × regions) shown
  in the header, and nudges the user toward under-used, virgin, or cross-region territory.

### 4.7 Dataset-focused prompts, saved & versioned
- Auto-write a rich prompt from the selected datasets' descriptions and field mix.
- Save, name, version, and track each prompt's win-rate → Prompt Library.

---

## 5. Success-rate engine & the metrics gate

A single, explicit gate decides whether an alpha is a **verified success**. An alpha
passes only if **all** hold (magnitudes, so negatives pass in absolute value):

- `|Sharpe| ≥ 1.58` and `|Fitness| ≥ 1.0` (as today),
- `Turnover < 0.70`,
- `self_correlation < 0.70`, `prod_correlation < 0.70`, `powerpool_correlation < 0.70`,
- all in-sample submission checks **PASS** (no FAIL),
- **Delay-0 rule:** for delay 0, Sharpe is the binding metric and is checked strictly;
  delay-0 research prompts and the gate both foreground it.

The engine computes a rolling **success rate** = verified successes / evaluated alphas,
per region/delay/category, and surfaces it on `/tier` and the Command Center. This number
is what invites a donation — the app asks for support only when it has demonstrably helped.

---

## 6. Tags & winners (user-chosen)
- Replace the fixed `ace_tag` / `ace_winner`. On first run the user **chooses their tag(s)**;
  every marked alpha uses those. For "winners," the user chooses the winner tag and the
  fitness threshold. Nothing is hardcoded.

---

## 7. Donation-ware entitlement (hidden in plain sight)

Reframed from "premium" to **support-when-it-works**. Design goals: no PII, device-bound,
tamper-resistant, offline.

- **Device identity.** Derive a stable fingerprint from hardware/OS signals the user cannot
  trivially change (machine GUID, CPU/board identifiers, MAC-derived salt), hashed to an
  opaque `device_id`. No personal data leaves the machine.
- **Signed entitlement.** A donation returns a **licence blob = Ed25519 signature** over
  `{device_id, tier, issued_at}` from a key **only the author holds**. The app ships the
  **public key** and verifies offline. Because entitlement is a signature the user cannot
  forge, it can't be faked by editing config.
- **Hidden in plain sight.** Verification is woven through normal code paths (not a single
  removable `if premium:`): multiple modules independently check the signature and derive
  behaviour from it, with checks obfuscated and redundant so removing one does nothing.
- **Honest limits.** Any purely local gate is ultimately bypassable by a determined user who
  controls the machine — so the model **leans on goodwill**: the gate is real but light, the
  ask is small ($2+), and it triggers only after the app has produced verified success. This
  matches your intent: raise success first, then invite support.
- **Tiers by donation, unlocked per device.** Donating unlocks scale/convenience
  (bigger sweeps, more concurrent research, cross-region batch, paper ingestion volume) —
  never the ability to do good research, which stays free.

---

## 8. UI system: compact, adaptive, no-scroll, on-brand

- **Design tokens** keep the current **dark + orange** palette but with a refined, modern
  component set (compact inputs, dense tables, chips, meters, panels).
- **Never-scroll layout.** Every screen is a fixed shell (sidebar + header + content grid)
  where panels flex to fit the viewport; long content scrolls **inside a panel**, never the
  page.
- **Auto-scale.** On load, measure viewport vs a design baseline and set a root `zoom`/font
  scale so that at **125%+ OS zoom or large screens** the app *shrinks itself* to avoid
  scrolling. A manual scale control lives in the header.
- **Best-in-class editor.** The expression/template editor keeps Tab autocomplete with live
  operator signatures, live bracket/lint checks, multi-line join, and adds inline
  parameter hints and one-click LLM repair.

---

## 9. Persistence & offline resilience
- All research, prompts, presets, results, usage, and licences persist in the local DB, so
  closing the app loses nothing and the "brain" keeps compounding.
- A **run history / job queue** persists long sweeps so they can be paused, resumed, and
  revisited.
- BRAIN calls stay conservative (cache datasets/fields/operators, respect Retry-After,
  pause polling when hidden — already done in v1) and add a **usage/quota meter**.

---

## 10. Delivery phases

1. **Scaffold** — repo layout, `run.bat`, FastAPI+Vite skeleton, design tokens, layout
   shell (sidebar/header), no-scroll + auto-scale, migrate existing endpoints as modules.
2. **Knowledge DB** — models, migrations, dataset/field embedding + similarity, provenance,
   usage tracking.
3. **Research Lab + Strategy Atlas** — LLM research, paper ingestion, push-to-generate.
4. **Generation v2** — deep single-field extraction, ≤2-category multi-field, diversity
   engine, dataset-focused prompt library, per-user uniqueness.
5. **Simulation + Success gate** — metrics gate, delay-0 focus, user-chosen tags/winners.
6. **Analytics** — results analytics, correlation/portfolio, cross-region transfer.
7. **Entitlement** — device id, signed licences, donation flow, tiering.
8. **Polish** — command palette, animations, keyboard nav, empty/error states (all
   plain-language), packaging.

---

### Open questions to confirm before build
1. Local embedding model choice (bundled sentence-transformer vs a small ONNX model) for
   offline dataset/field similarity.
2. Exact numeric gate thresholds per region/delay (confirm the delay-0 Sharpe cutoff).
3. Donation rail ($2+) — which processor, and how the signed licence is returned offline.
4. Whether SuperAlpha gets its own success gate variant (its universes/neutralizations
   differ from regular).
5. How much of v1's SQLite (`ace_studio.db`) to migrate vs start clean.
