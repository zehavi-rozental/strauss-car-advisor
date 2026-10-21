# Frontend contract for the redesign (`feature/premium-ui-redesign`)

Branch `fix/qa-sync-2026-10-21` changed these frontend files for QA fixes and STRAUSS DRIVE 360°. A redesign may
change any markup and style, but the behaviours below must survive the merge; each one has a test that fails if
it is lost (`npx vitest run`, `npx playwright test` against a server without an LLM).

## Data the screens receive (backend, additive and backward compatible)

| Field | Where | Meaning |
|---|---|---|
| `results_stale` | every turn, `GET /conversations/{id}`, `/updates` | the results were calculated before the last change: show them as history ("לא מעודכן"), never rank them |
| `option_map` | `GET /conversations/{id}`, `/updates` (null until asked) | 360°: `entries` (net order: calculated, waiting, not eligible), `excluded`, `counts`, `leaders` (several = tie, [] = none), `stale`, `group` |
| 422 `{detail, message_he, errors}` | `POST .../messages` with `fields` | every refused field by name; nothing was saved |
| `source: "payslip"` | request body with `fields` | values approved from a reviewed payslip |

## Behaviours to keep, by file

| File | Kind | What must stay | Test |
|---|---|---|---|
| `employee/useConversation.ts` | logic | `expects` keeps its identity when a poll re-sends the same question; `questionKey`; `sendFields` returns field errors; `resultsStale`, `optionMap` from view/updates | `qa-2026-10-21.spec.ts` (selection card while polling) |
| `employee/EmployeePage.tsx` | logic + layout | what a link opened belongs to `questionKey` (closes only on a new question); the interactive area stays mounted while sending (disabled, not removed); "גלו לי את כל האפשרויות" sends `MAP_REQUEST`; the map panel and the comparison from the map | selection card test (fails on the old reset), 360° journeys |
| `employee/PayslipUpload.tsx`, `payslipReview.ts` | logic + UI | one explicit choice per row; a conflict ("מהשיחה" / "מהתלוש") has no default and blocks confirming; the profile is read when the payslip is read, unreadable = nothing preselected; sends `source: "payslip"`; server errors shown per field | `payslipReview.test.ts`, payslip journeys |
| `employee/ProfileForm.tsx` | logic | per-field server errors; typed input kept; a failed prefill is said | rejected-form and prefill journeys |
| `employee/MessageList.tsx`, `SummaryCard.tsx`, `ComparisonPanel.tsx` | UI | stale results: the "לא מעודכן" note, dimmed, never ranked (also in the comparison panel); the 360° card slot (`mapCard`) | stale-result journey |
| `employee/optionMap.ts` | logic (no money math) | sections (calculated / waiting / not eligible), sort, filters from real values, compare at most 3 calculated, lead text from the server's `leaders` | `optionMap.test.ts` |
| `employee/OptionMapPanel.tsx` | UI | every entry listed (not only 3); status per entry; no car pictures; the net-only note; excluded cars with the reason | `option-map-360.spec.ts` (1280 and 375 px) |
| `rep/QuestionCard.tsx` | UI | no salary notice line; the note shown on an answered card; still no salary data | rep journeys |
| `api/types.ts`, `api/client.ts` | contract | the fields above; `ApiError.errors` | type check (`tsc -p tsconfig.app.json`) |
| `styles.css` | style | classes used by tests: `.stale-note`, `.result-stale`, `.map-entry`, `.map-lead`, `.map-card`, `.payslip-row[data-field]`, `.car-card` | the journeys above |

## Rules the redesign must not break

- No money is calculated in the browser except the annual line (the engine's monthly `delta_display` × 12, display only).
- Buttons that send are disabled while a message is in flight (no double actions).
- The map never selects cars; comparing from the map does not change `selected`.
- Hebrew, RTL, usable at 375 px with no horizontal scroll.
