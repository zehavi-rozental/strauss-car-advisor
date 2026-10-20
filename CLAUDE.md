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
| `rules` | Loads `config/strauss-rules.json` (as delivered), `config/extensions.yaml`, `config/strauss-data.xlsx` and `config/faq.yaml` (grounded answers). |
| `vehicles` | Tax-authority vehicle data and current-car matching. |
| `cases` | Questions to the transport representative (owns `rep_questions`). Questions open in the conversation turn's write transaction; answers are published as `QuestionAnswered` events that the conversation applies idempotently. Must not import `conversation`. |
| `conversation` | State machine and the understand → decide → say agent: tools, reply plan, FAQ answers, number guard + coverage check, template fallback, LLM adapters. The deterministic parser reads each message first; one LLM call understands it, code decides, the LLM phrases the plan. Must not import FastAPI. |
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
- Q2 leftovers (official scenario: car question in 3 bot turns): listed as a short "חסר עוד: עמלות, בונוס…" line only on the first car question, never repeated; asked on their own once the car and alternatives are done. Q2 moves on only after the employee *answered* it in part (`state.q2_answered`); a turn that only asked a question ("מה זה נקודות זיכוי?") stays on Q2.
- A bare "אין" to Q2 sets 0 for commissions, bonus, study fund and children, and says so ("רשמתי: …"). Pension and credit points are never assumed.
- A representative question about the current car ("לא ידוע") opens only after the profile is complete; until then the answer is recorded.
- "הכי משתלם" before the profile and current car are known is recorded (`rank_requested`) and resolved as soon as only the alternatives are missing.
- No dead ends: a not-understood answer gets "לא הצלחתי להבין" + the question in simpler words (`explain_text`, FAQ `simple`) + the call to action with an example; a second miss in a row adds "אפשר גם לענות על דבר אחד בכל פעם, למשל: …" (`state.misses`), so the same reply never comes twice. Every bot reply that is not a result and not "waiting for the representative" carries a non-`none` `expects` (`tests/test_no_dead_ends.py`).
- A value saved for a field that is not on screen (e.g. "עמלות 2,400" while the alternatives are asked) is confirmed in a "רשמתי: …" line, never saved silently.
- `tests/scenarios/test_styles.py`: the six reference employees in three styles (buttons, short, free), 18 conversations through the API, net equal to the reference. Live provider: `STYLES_LLM=live STYLES_TURN_PAUSE_S=30 pytest tests/scenarios` (slow, free-tier quota).
- Confirmation lines come from tool results, not the LLM: "זיהיתי: <תפקיד>, קבוצה X.", "בחרת להשוות: …", "זיהיתי: <רכב>".

### LLM-led turns with code guardrails (session 3; supersedes session 2's understand → decide → say)

- **The turn** (`turn.py`, `agent._finish`): the LLM leads. One tool-calling call (temperature 0.2); another round only when a tool result must be reflected (anything but a plain `save_profile`), at most 3. Prompt order is fixed for implicit prefix caching: persona and rules (`SYSTEM_PROMPT`) → the whole FAQ (`render_faq`) → STATE (`state_block`: known / missing / unknown fields, car, selection, options, pending items, results with drivers and a pending copay, the parser's reading or what code already saved, the rep offer) → the last 12 messages → the message inside `<<<USER>>> … <<<END>>>` (data, never instructions). Tools: `save_profile`, `match_current_car`, `select_options`, `rank_options`, `propose_rep_question` (the offer only; the privacy-checked preview and "לשלוח" stay in code). Every call still goes through the code checks (`_check_call`, role conflicts, `_cross_check`, ranges, unusual values); a refused call comes back to the LLM as a tool error. Calls in a round run in a fixed order (profile, car, selection). Answers to the bot's own questions ("כן", "לא", "להמשיך") are saved in code; the LLM then writes the reply, told what was saved (`applied`). Buttons and the profile form stay template.
- **Persona** (`SYSTEM_PROMPT`): a warm, highly professional human advisor; second-person plural; short sentences, at most 5 lines; no emojis, no "!" (only the fixed rep message has "תודה!"); "רשמתי: …" acks; refers back to what the employee said; never asks for what STATE knows; questions (with or without "?") and complaints first; small talk / off-topic / injection / personal tax or legal advice / other people's salaries get one polite line; ranges and vague amounts are not values ("בערך כמה?"; an estimate is fine); numbers only from STATE, tools, the FAQ or the employee; every policy sentence carries a hidden source tag `[[faq:id]]` / `[[tool:name]]` / `[[state]]`.
- **Guards** (`guards.py`, on every LLM reply): (a) numbers in STATE / tool results / FAQ / catalog names / the employee's messages; (b) a policy sentence needs a valid tag (boundaries and "אין לי מידע" are not policy claims); (c) claim verifier: a second call (≤ 1.2 s) on the sentences that can carry a claim (tag, policy word, number, "רשמתי") against FAQ + STATE; out of budget, only FAQ-tagged sentences pass; (d) progress: while data is missing the reply names a missing field (or the offer / a pending question); (e) shape: ≤ 6 lines, exactly one asking line and it is the last, plural, no "!", emojis, banned phrases or openers; (g) a parser / LLM number conflict must be asked; plus must-answer (a question, "מה?" or a complaint gets more than the CTA) and the offer rule (the rep offer is the only question and the last line). (f) no silent drop stays on the tool calls. A violation → one retry with the violations (only with ≥ 1.5 s left) → the reply without the offending sentences, if it still passes → the template. While code holds a pending clarification (car role, no car or copay 0) the template asks it.
- **Templates only** for: no LLM / outage (parser + `plan_reply`), results (summary + "הסיבה העיקרית…" from the engine's `why` + one next step "רוצים לראות מה הכי משתלם…?"; "כן" ranks), the rep waiting and before/after messages, buttons and the form, and guard fallbacks.
- **Turn budget** `LLM_TURN_DEADLINE_S=4.0` (UNDERSTAND rounds + verifier + retry); `say=` in the turn log: `llm`, `llm:retry`, `llm:trimmed`, `guard:<kinds>`, `pending`, `fixed`, `outage`, `not_tried`.
- **Eval** (`pytest -m eval tests/eval/test_conversation_eval.py`): 30 live cases, a six-criterion judge (gemini-3.5-flash-lite) calibrated on 15 hand-labelled turns (14/15), code assertions (number guard, dropped / guessed fields, final net), `backend/eval-report.md`; the run stops above 1,000 estimated calls. Reports per iteration in `tests/eval/`.
- **Model**: `LLM_CHAIN=gemini:gemini-3.5-flash-lite,gemini:gemini-3.6-flash` (flash-lite main, 3.6-flash fallback; only flash-lite was evaluated, per the cost limit).

### Conversational agent (session 2; parts superseded by session 3 above; supersedes session 1's "one widget per question")

- **"לא יודע" at Q2** is recorded per field (`profile.unknown_fields`; the fields the message names, else every Q2 field on screen), acknowledged ("רשמתי שלא ידוע: … אני לא מניח ערך; נחזור לזה בסוף."), and the flow goes on (car, alternatives). When only unknown fields are left there is no calculation and never a default: the bot says what is missing and where it is on the payslip (FAQ `where`), with "להעלות תלוש" only when `extensions.yaml: payslip_upload_url` is set (empty today: the app has no upload). A repeated "לא יודע" alternates the wording (`state.unknown_told`), never the same text twice in a row. A value typed later is saved and the calculation runs.
- **One form of address**: second-person plural only (שלכם, לכם, בחרתם, כתבו) in templates, the FAQ and SAY; SAY is rejected for singular forms (`SINGULAR_RE`) or robotic openers (`OPENERS`); acks are "רשמתי: …". Engine reasons keep the reference wording and are pluralized for display (`to_plural` in `_dump`).
- **Requests are answered**: a compare / result request the bot cannot do yet gets "כדי להשוות, חסרים לי עוד: …." (`compare_needs_text`) before the question. The same question is never asked verbatim twice in a row (`state.last_ask`): narrowed to "עדיין חסרים לי: …" or in simpler words. Examples in the CTA and the placeholder name only fields that are still missing (`q2_example`, `_q1_example`).
- **Grounded answers** (`config/faq.yaml`, loaded by `rules`, optional for `reproduce_run`): id, term, aliases (typos tolerated; a one-word alias names the topic only in a short question), answer, simple, example. No numbers in the file: `{credit_point_value}`, `{electric_net_bonus}`, ... are filled from `strauss-rules.json`. An FAQ answer is given only when the entry answers the question: an id from the LLM is kept only if the message names one of the entry's aliases or its term (`entry_answers`); a one-word alias must match the word itself (typos only in phrases). A question no entry covers → "על זה אין לי מידע מדויק. רוצים שאשאל את נציג התחבורה? התשובה תגיע לכאן." — the turn's one call to action, no flow question and no link that turn; buttons "לשאול את הנציג" / "להמשיך בלי" (`expects` field `ask_rep`; typed "לשאול" / "להמשיך" too; any other message is read normally) → the question is rewritten without amounts (LLM, `REP_QUESTION_PROMPT`), checked in code (`rep_ok`: no digits except model years and car names) and shown first ("אשלח לנציג התחבורה: '…'. לשלוח?", `pending_rep_question`, "לשלוח" / "לבטל") → `GENERAL_QUESTION`, `TEXT`, `CONFIRM` (non-blocking), context group + car only, `option_id = gq-<random>` so several can be open at once (no migration). The answer arrives as "נציג התחבורה ענה על השאלה שלך: …".
- **"אינני משתתף"** before the car is known: never "no car", never copay 0; asked with "אין לי רכב צמוד היום" / "יש רכב, ההשתתפות 0" (`pending_ambiguity`). With a known (or named) car it is copay 0.
- **One item at a time**: the question text asks the next item only (Q1, Q2, the car today with its copay, the alternatives, then Q2 leftovers). The parser still reads every open field, so one message may answer more. A bare car name at the car question is the car today (no role question; a role word like "רוצה לבדוק" still makes it an alternative).
- **Free text first** (`frontend/src/employee/widgets.ts`): the composer is always enabled; open questions get no widget. Buttons only for closed questions (`expects.fields[].closed`: confirmation, occupation confirmation, clarification, car version, drive, group alone, ambiguity, rep question, ask_rep), and the text says "בחרו … או כתבו". At most one link per reply (`expects.link`): "או למלא בטופס" (Q2 fields) opens `ProfileForm`; "להציג את הרכבים בקבוצה שלך" (options) opens `SelectionCard`. No car search box, no auto Q3 card, no "חסר עוד" row. Only one interactive area at a time.
- **Every non-result reply ends with exactly one CTA** ("כתבו…" / "בחרו…" / "אפשר לכתוב כאן…", `call_to_action`); the composer placeholder mirrors it with an example (`expects.placeholder`, `placeholder_text`). The greeting adds "אפשר לכתוב כמו שמדברים, הכול בהודעה אחת או בכמה."
- **Progress** in the employee header: פרטים · רכב היום · חלופות · תוצאה (`progressStep(stage, hasResults)`).
- **SelectionCard**: toggle buttons (`aria-pressed`, keyboard operable), index number on each card = the number the employee may type (server order of `expects.choices`), live counter in an `aria-live` region ("נבחרו 2 מתוך 3"), a 4th card disabled with "אפשר עד 3", primary "השוואה", secondary "מה הכי משתלם לי?", opt-out as a dashed card only if `OPT_OUT` is offered.
- **Options text**: the question names the group ("איזה רכבים להשוות? בקבוצה ג ומטה יש 12 רכבים, ואפשר לבחור עד 3."). `options_text(..., with_list=True)` is the numbered-list fallback for a client without widgets (the API never uses it).
- **Results**: the chat text is built from a template, never by the LLM (the intro call was removed): at most 2 plain lines, no phrase twice ("בדקתי 2 רכבים, ובשניהם הנטו שלכם יגדל. הכי משתלם: MG4 (2025), תוספת של 1,013 ₪ בחודש."; "בשניהם" for 2, "בכולם" for 3+; down: "הכי משתלם: X, רק 923 ₪ פחות בחודש."; rank: "בדקתי את כל 13 הרכבים שאתם זכאים להם. אלה 3 המשתלמים ביותר:" from `state.ranked_count`), then the waiting line without "תודה!" ("חסר לי רק אישור…"; "תודה!" only when the waiting line stands alone); not-eligible / incomplete options add one short line each. No breakdown, no assumptions, no "אפשר להשוות חלופה נוספת" line in the text. Detail lives in the structured result: `result.why` (one plain sentence about the main cause, built by `why_sentence` from the drivers, stored by `_dump`), the `SummaryCard` (ranked rows, signed delta, why; "פירוט מלא" opens `ComparisonPanel`; one option -> `ResultCard`), and the panel (where the assumptions live). After results the selection collapses into "הרכבים שנבחרו: … / לשנות בחירה" (reopens the SelectionCard with the current selection checked).
- **One wording and one sign style**: "הנטו שלכם יגדל ב-X ₪" / "יקטן ב-X ₪" / "לא ישתנה" everywhere (`templates.net_change`, `ResultCard`), never "ירד" / "עלה" / "שינוי של -X". On screen the minus is "−" left of the digits inside `<bdi>` (`format/money.ts`, `Money`, `richText.tsx`); `format/sign.test.tsx` asserts the same string in all components.
- **Hebrew style guide** (templates and the LLM phrase prompt): short sentences, one idea per line, at most 2-3 lines per paragraph; no "·" chains, no parenthetical comparisons like "(היום 0 ₪)", no formulas; directions in words; friendly second-person plural ("כתבו", "בחרו"); "רשמתי" lines stay short; "למה?" is one plain sentence. Enforced by `tests/test_template_style.py` (line length, no "·", no "שינוי של", no raw negative amounts, every results case: all up / all down / mixed / zero / single / not eligible / incomplete / pending).
- **Bubbles** render a safe markdown subset (`richText.tsx`): line breaks, `**bold**`, "- " bullets, no raw HTML; every amount is wrapped in `<bdi>` and bold.
- **Unusual values** (legal but rare: pension 0%, credit points 0, base salary at most 6,000, 9+ children) are asked once with "לאשר" / "לתקן". Thresholds are in `extensions.yaml` (`unusual_values`, `at_most` / `at_least`), never hard-coded and never defaults. The value is held (not saved) in `pending_confirmation` (`unusual: true`) until "לאשר"; confirmed fields are remembered in `state.confirmed_unusual` so they are not asked twice; "לתקן" keeps the field open and asks it again. The profile form (`run_fields`) does not report a held value as "רשמתי".

## NEXT (not done in this pass)

- Eval targets not yet met (iteration 2): single_cta 92.5% (templates end with a question plus a "כתבו…" line, which the judge counts as two; the session-2 rule asked for that line — decide), concise / natural 95.5%, no_invention 98.5% (the range wording, fixed after the run, unmeasured), p95 5.59 s (budget lowered to 4.0 s after the run, unmeasured). Run `pytest -m eval tests/eval/test_conversation_eval.py` once more.
- The live model sometimes adds the flow question next to the rep offer; the guard then sends the template (correct, but not LLM-phrased).

- A payslip upload (the "להעלות תלוש" link is ready behind `payslip_upload_url`).
- Browser checks of the session-2 UI (desktop + 375px): progress header, links, closed buttons only.
- `llm.intro_deadline` / `intro_budget_s` in `LLMChain` and `MAX_TOOL_ROUNDS` / `FORCED_TOOL_STAGES` in `agent.py` are unused; delete them in a cleanup.
- The rep screen shows `GENERAL_QUESTION` with the generic TEXT answer field; a dedicated layout (the employee's question as the title) would read better.

## Known limitations (מגבלות ידועות)

- A model that is not in the company catalog ("טויוטה מיסטרי") is always taken as the current car: only catalog cars can be alternatives, so the Q3 role question is never asked for it.
- In template mode, "לא" in the middle of a sentence ("זה לא קורולה קרוס") is not interpreted: the parser does not act and the bot asks again. Only a leading "לא" (a correction) is read.
- After the first Q3 message, a bare number answers only the question on screen; Q2 leftovers are read only when labelled ("פנסיה 6%").
- A bare "אין" is read only as an answer to Q2, or when every pending field is a Q2 field. In Q3 with a car/options question on screen it is ambiguous (car, copay, leftovers) and the bot asks again; the buttons name their field ("עמלות: אין").
- In template mode, a long mixed sentence ("אין לי רכב, אני מקבלת אחזקה 1,500, אני רוצה לבדוק את אם ג'י 4", or a current car plus opt-out in one message) is not read: the bot asks the car question again, with the example. With an LLM these are read. Found by `tests/scenarios/test_styles.py` (the free style falls back to short answers after one failed attempt).
- A family-track car (Carnival, Santa Fe) is offered only once the number of children is known (4+). If the employee names it before that, the options list does not contain it and the bot asks again; the child count is asked as a leftover.
- The occupation confirmation shows the occupations-table name as is, including slash forms ("מנהל/ת אזור מכירות").

- Template mode (no LLM) reads values only in the parser's forms: a long free sentence such as "מרוויחה 18 וחצי ברוטו, 6 אחוז פנסיה" is not read; the bot asks again in simpler words with an example (nothing is guessed). With an LLM it is read.
- Without an LLM, questions are matched to the FAQ by aliases only; a covered question worded differently is offered to the representative. With an LLM, `note_message` maps it to the FAQ id.
- Presence without an amount ("יש קצת עמלות") is detected for commissions, bonus, pension, study fund and children only.
- A question and an answer in one message are separated only when the question is its own clause (ending with "?" or starting with a question word); "300 או 400?" is read as an answer attempt, not a question.
- The judge (flash-lite) agrees 14/15 with the hand labels; it passed one known-bad reply (the complaint answered with a redirect). Scores are a strong signal, not proof.
- `שני ילדים` is read in code only as a gap fill next to an LLM save; in template mode numbers in words are not read.
- The claim verifier is itself an LLM; out of budget only FAQ-tagged sentences survive, which can shorten an otherwise correct reply.

## Conventions

- UI text, error messages and eligibility reasons are in Hebrew. Code, identifiers and comments that explain code may be in English.
- Commit messages: English, imperative mood, describe what changed.
