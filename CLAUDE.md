# Strauss Car Advisor — guidelines for working on this codebase

A Hebrew chatbot that tells an employee how changing their company car changes their monthly net pay.
Open Code AI Hackathon 2026, Strauss challenge. Submission: 2026-10-21. Presentation: 2026-10-28.

Full specification: https://claude.ai/code/artifact/59517376-30d7-4cf7-ad98-ed5c97cf7946 (section 14 is the work plan).

## Commands (run from `backend/`)

```bash
pip install -e ".[dev]"
alembic upgrade head                        # create/upgrade the schema (alembic owns all tables)
python -m app.modules.vehicles.import_csv   # load the tax-authority CSV into SQLite
python -m app.verify                        # must report 12/12
pytest                                      # all tests must pass
pytest -m eval                              # live LLM eval (skipped when no LLM_CHAIN entry is available)
python -m app.llm_probe                     # one tiny request per LLM_CHAIN entry: status + latency (no keys printed)
lint-imports                                # module boundaries must hold
uvicorn app.api.main:create_app --factory   # run the API (no LLM key → template mode)
```

Schema changes: edit the models, then `alembic revision --autogenerate -m "..."`; `tests/test_migrations.py` fails on drift. Keep `alembic.ini` ASCII (alembic reads it in the locale encoding).

## Architecture

Modular monolith (ports & adapters). Every module can later be extracted into a service.

| Module | Responsibility |
| --- | --- |
| `calculation` | Pure engine. Must not import the database, FastAPI or other modules (enforced by `lint-imports`). |
| `rules` | Loads `config/strauss-rules.json` (as delivered), `config/extensions.yaml` and `config/strauss-data.xlsx`. |
| `vehicles` | Tax-authority vehicle data and current-car matching. |
| `cases` | Questions to the transport representative (owns `rep_questions`). Questions open in the conversation turn's write transaction; answers are published as `QuestionAnswered` events that the conversation applies idempotently. Must not import `conversation`. |
| `conversation` | State machine and LLM agent: tools, number guard, template fallback, LLM adapters. The deterministic parser reads each message first; the LLM is called only for what it cannot read in full. Must not import FastAPI. |
| `shared` | Money, database, settings. |

`app/api` is the HTTP layer (cookie, rate limits, serialization) and the composition root (`deps.py`, `main.py`).

Endpoints:
- Employee (session cookie): `POST /api/conversations`, `POST /api/conversations/{id}/messages`, `GET /api/conversations/{id}`, `GET /api/conversations/{id}/updates?after=<seq>` (polling), plus `GET /api/catalog`, `/api/occupations`, `/api/health`.
- `POST .../messages` body is exactly one of `{text}` | `{choice, field?}` | `{choices, field?}` | `{fields: {commissions?, bonus?, pension_percent?, study_fund_percent?, credit_points?, children_under_23?}}`. `fields` is the Q2 form / "תיקון נתונים": validated against `value_ranges` (422, nothing saved), saved by `save_profile` directly (no reader, no LLM, at any stage, a result is recalculated), reply = "רשמתי: …" + the next question.
- Transport representative (header `X-Rep-Token` = `REP_ACCESS_TOKEN`, optional `X-Rep-Name`): `GET /api/rep/questions?status=OPEN`, `GET /api/rep/questions/{id}`, `POST /api/rep/questions/{id}/answer` (200 / 401 / 404 / 409 / 422). The rep sees only the question context, never salary or profile data; answer values and notes are never logged.

A module, and `app/api`, imports another module **only** through that module's `public.py`.

## Rules that must not be broken

1. **Money is `Decimal` only.** No floats in calculations. Round for display only (`to_shekel`), never in intermediate steps.
2. **The LLM never calculates.** It extracts data, calls tools and phrases replies. Every number shown to the employee must come from the engine result or the employee's profile.
3. **The state machine in code drives the conversation**, not the LLM.
4. **A missing monetary value yields `INCOMPLETE`**, never an invented default.
5. **Data changes need no code changes.** New rules arrive through `config/` files.
6. **Privacy:** never store ID numbers, never log salaries or message content; the representative sees only context (group, car, suggested amount).
7. **API keys live only in `backend/.env`**, which is git-ignored.
8. After every change, `pytest`, `python -m app.verify` (12/12) and `lint-imports` must pass.

## Settled decisions (do not reopen without a reason)

- Tax-authority key: tax year + registration year + **vehicle type** + manufacturer code + model code. ATTO 3 and TANG share manufacturer and model codes.
- Drive codes in the tax file: 0 petrol, 1 hybrid, 2 plug-in, 3 electric. The column header in the file is misleading.
- From the rules file (`strauss-rules.json`, the source of truth; not our own decisions any more):
  - Pension and study fund base: `netMethod.contributionsBase` (`gross` = base + commissions + bonus).
  - Pension credit: `netMethod.pensionCredit` = rate × min(pension, maxContributionRate × incomeCeiling) = 35% × min(pension, 7% × 9,500).
  - Copay: `netMethod.participation` — a net deduction that does not reduce the taxable use value (`reducesValueOfUse: false`).
  - Family track: exactly the cars in `familyTrack.cars`; each car's monthly cost comes from the catalog (`priceSource: catalogue` — Carnival 6,000, Santa Fe 5,600).
  - Rounding: `netMethod.rounding` — none during the calculation, whole shekels for display only.
  - Alternatives a pilot can switch to in the rules file alone: `contributionsBase: base` (base salary only), `electric.method: gross` (flat gross component), `participation.reducesValueOfUse: true` (copay reduces the taxable use value). Any other value fails loading with a Hebrew error.
- `extensions.yaml` keeps only what the rules file does not say (e.g. the employee's share of the family-track gap is added on top of the copay). A field that moved to the rules file fails loading if it is left there.
- Copay for a new car: calculate immediately with the catalog default and mark it "pending representative approval" (`CATALOG_DEFAULT_CONFIRM_ASYNC`).
- Current-car matching: company catalog first, then the tax file. Several matches with different use values → the employee chooses from up to 5.
- Q2 leftovers (official scenario: car question in 3 bot turns): listed as a short "חסר עוד: עמלות, בונוס…" line only on the first Q3 message (the client hides that line and shows a compact row with a "השלמה" button that opens the profile form), never repeated; asked on their own once the car and alternatives are done.
- A bare "אין" to Q2 sets 0 for commissions, bonus, study fund and children, and says so ("רשמתי: …"). Pension and credit points are never assumed.
- A representative question about the current car ("לא ידוע") opens only after the profile is complete; until then the answer is recorded.
- "הכי משתלם" before the profile and current car are known is recorded (`rank_requested`) and resolved as soon as only the alternatives are missing.
- No dead ends: the same question asked right after a not-understood answer keeps its text and adds "buttons + an exact example" (`templates.retry_hint`, `EXAMPLES`). Every bot reply that is not a result and not "waiting for the representative" carries a non-`none` `expects` (`tests/test_no_dead_ends.py`).
- A value saved for a field that is not on screen (e.g. "עמלות 2,400" while the alternatives are asked) is confirmed in a "רשמתי: …" line, never saved silently.
- `tests/scenarios/test_styles.py`: the six reference employees in three styles (buttons, short, free), 18 conversations through the API, net equal to the reference. Live provider: `STYLES_LLM=live STYLES_TURN_PAUSE_S=30 pytest tests/scenarios` (slow, free-tier quota).
- Confirmation lines come from tool results, not the LLM: "זיהיתי: <תפקיד>, קבוצה X.", "בחרת להשוות: …", "זיהיתי: <רכב>".

### Interaction model and message style (session 1 of the UX pass)

- **One primary input per question**, derived from `expects` only (`frontend/src/employee/widgets.ts`, `widgetsFor`): choice up to 8 -> buttons, more -> searchable select; Q2 (all fields are profile numbers) -> `ProfileForm`; `car_search` -> search box with result buttons; `multi_choice` -> `SelectionCard`. It is drawn under the latest bot message and disappears while a turn is sent (the user bubble is the read-only summary); older messages never carry widgets, so no stale button is clickable. Typing always stays possible; the composer placeholder is a short per-question hint.
- **Q3 is partial-aware**: "הרכב שלך היום" (search) and "רכבים להשוואה" (SelectionCard) are two labelled sections of one card; each is shown only while its field is open in `expects`.
- **SelectionCard**: toggle buttons (`aria-pressed`, keyboard operable), index number on each card = the number the employee may type (server order of `expects.choices`), live counter in an `aria-live` region ("נבחרו 2 מתוך 3"), a 4th card disabled with "אפשר עד 3", primary "השוואה", secondary "מה הכי משתלם לי?", opt-out as a dashed card only if `OPT_OUT` is offered.
- **Options text**: when `expects` carries choices the server text is the short question only ("איזה רכבים להשוות? אפשר לבחור עד 3."). `options_text(..., with_list=True)` is the numbered-list fallback for a client without widgets (the API never uses it).
- **Results**: the chat text is built from a template, never by the LLM (the intro call was removed): at most 2 plain lines ("בדקתי 3 רכבים. בכולם הנטו שלך יקטן, הכי פחות ב-X. הנטו שלך יקטן ב-923 ₪ בחודש." + "ממתין לאישור נציג על ההשתתפות."); not-eligible / incomplete options add one short line each. No breakdown, no assumptions, no "אפשר להשוות חלופה נוספת" line in the text. Detail lives in the structured result: `result.why` (one plain sentence about the main cause, built by `why_sentence` from the drivers, stored by `_dump`), the `SummaryCard` (ranked rows, signed delta, why; "פירוט מלא" opens `ComparisonPanel`; one option -> `ResultCard`), and the panel (where the assumptions live). After results the selection collapses into "הרכבים שנבחרו: … / לשנות בחירה" (reopens the SelectionCard with the current selection checked).
- **One wording and one sign style**: "הנטו שלך יגדל ב-X ₪" / "יקטן ב-X ₪" / "לא ישתנה" everywhere (`templates.net_change`, `ResultCard`), never "ירד" / "עלה" / "שינוי של -X". On screen the minus is "−" left of the digits inside `<bdi>` (`format/money.ts`, `Money`, `richText.tsx`); `format/sign.test.tsx` asserts the same string in all components.
- **Hebrew style guide** (templates and the LLM phrase prompt): short sentences, one idea per line, at most 2-3 lines per paragraph; no "·" chains, no parenthetical comparisons like "(היום 0 ₪)", no formulas; directions in words; friendly second-person plural ("כתבו", "בחרו"); "רשמתי" lines stay short; "למה?" is one plain sentence. Enforced by `tests/test_template_style.py` (line length, no "·", no "שינוי של", no raw negative amounts, every results case: all up / all down / mixed / zero / single / not eligible / incomplete / pending).
- **Bubbles** render a safe markdown subset (`richText.tsx`): line breaks, `**bold**`, "- " bullets, no raw HTML; every amount is wrapped in `<bdi>` and bold.
- **Unusual values** (legal but rare: pension 0%, credit points 0, base salary at most 6,000, 9+ children) are asked once with "לאשר" / "לתקן". Thresholds are in `extensions.yaml` (`unusual_values`, `at_most` / `at_least`), never hard-coded and never defaults. The value is held (not saved) in `pending_confirmation` (`unusual: true`) until "לאשר"; confirmed fields are remembered in `state.confirmed_unusual` so they are not asked twice; "לתקן" keeps the field open and asks it again. The profile form (`run_fields`) does not report a held value as "רשמתי".

## NEXT (not done in this pass)

- Run the browser checks (desktop + 375px) and the live LLM styles run (`STYLES_LLM=live STYLES_TURN_PAUSE_S=5`), deliberately skipped in session 1.
- `llm.intro_deadline` / `intro_budget_s` in `LLMChain` are now unused (the results intro was removed); delete them in a cleanup.

## Known limitations (מגבלות ידועות)

- A model that is not in the company catalog ("טויוטה מיסטרי") is always taken as the current car: only catalog cars can be alternatives, so the Q3 role question is never asked for it.
- In template mode, "לא" in the middle of a sentence ("זה לא קורולה קרוס") is not interpreted: the parser does not act and the bot asks again. Only a leading "לא" (a correction) is read.
- After the first Q3 message, a bare number answers only the question on screen; Q2 leftovers are read only when labelled ("פנסיה 6%").
- A bare "אין" is read only as an answer to Q2, or when every pending field is a Q2 field. In Q3 with a car/options question on screen it is ambiguous (car, copay, leftovers) and the bot asks again; the buttons name their field ("עמלות: אין").
- In template mode, a long mixed sentence ("אין לי רכב, אני מקבלת אחזקה 1,500, אני רוצה לבדוק את אם ג'י 4", or a current car plus opt-out in one message) is not read: the bot asks the car question again, with the example. With an LLM these are read. Found by `tests/scenarios/test_styles.py` (the free style falls back to short answers after one failed attempt).
- A family-track car (Carnival, Santa Fe) is offered only once the number of children is known (4+). If the employee names it before that, the options list does not contain it and the bot asks again; the child count is asked as a leftover.
- The occupation confirmation shows the occupations-table name as is, including slash forms ("מנהל/ת אזור מכירות").

## Conventions

- UI text, error messages and eligibility reasons are in Hebrew. Code, identifiers and comments that explain code may be in English.
- Commit messages: English, imperative mood, describe what changed.
