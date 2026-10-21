# Handoff to Kiro — 21/10 (from the deploy/render-demo branch)

**Status after integration (merge 50e2195 of 3b06848):** items 1 and 2 are done. The `base_salary` contract is
intact; bugs 1–6 are fixed and covered (API tests + browser journeys). Still open: storing the payslip origin
(item 1, last bullet). Two wording fixes were made during integration: "בואו נעבור על זה אחד-אחד" (plural) and
"הסיבה העיקרית בוויתור על רכב" (no hyphen before a Hebrew name); "ילד אחד" instead of "1 ילדים".

Two items for the conversation module (`feature/conversation-reliability`). Claude did not edit your files in
your branch; the contract change below exists only in `deploy/render-demo` so far.

## 1. Contract change: `base_salary` in `{fields}`

The payslip review can now send the base salary. The change on `deploy/render-demo`:

```python
# backend/app/modules/conversation/expects.py
FORM_FIELDS = ("base_salary", "commissions", "bonus", "pension_percent", "study_fund_percent", "credit_points",
               "children_under_23")      # base_salary: from a reviewed payslip (api ProfileFieldsIn)
_FORM_LABELS = {"base_salary": "שכר בסיס", "commissions": "עמלות", ...}   # + the label
```

- `app/api/routes.py ProfileFieldsIn` has `base_salary: Optional[Decimal]`.
- Validation: `value_ranges.base_salary` (3,000–200,000); outside → 422, nothing saved. Below 6,000 is held for
  "לאשר / לתקן" by the existing unusual-value rule.
- Saving goes through `run_fields` → `save_profile` as before; "רשמתי: שכר בסיס 18,000 ₪." comes from
  `recorded_text` (the label already existed in `_VALUE_LABELS`).
- Test changed: `tests/test_profile_fields.py::test_empty_or_unknown_fields_are_rejected` no longer uses
  `base_salary` as the unknown field (it uses `eligibility_group` / `current_copay`); new
  `test_the_base_salary_can_come_from_the_form`.
- **When merging:** keep both lines in `expects.py`. If you changed `FORM_FIELDS` too, the union of both.
- Open (needs your module): the payslip origin is not stored. The history shows "שכר בסיס 18,000 ₪, …" without
  "מהתלוש". Proposal: `MessageIn.source: "payslip"` → `send_fields(..., source)` → the stored user text starts
  with "מהתלוש (אושר): ". The API side is ready to pass it.

## 2. Conversation bugs found by tests (template mode, no LLM)

Reproduce with the API (`tests/conftest.make_client`, no LLM). Profile used:
`אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים`.

| # | Message | Where | What happens | Expected |
|---|---|---|---|---|
| 1 | "סליחה, טעיתי, שכר הבסיס שלי 16,000" / "טעיתי, השכר 16,000" | after a result (PRESENTED) | taken as the `_AUDIT_CUES` "you made a mistake" cue: "בדקתי: עד עכשיו רשמתי … שכר בסיס 18,500"; nothing saved | base 16,000 saved, result recalculated (+ "רשמתי") |
| 2 | "שכר בסיס 16,000" / "תיקון: שכר בסיס 16,000" / "השכר שלי 16,000 ולא 18,500" | after a result | "לא הצלחתי להבין" + the options question | same as 1 |
| 3 | "יש לי קיה נירו" | the car question (David, group ב) | "לא זיהיתי את דגם הרכב"; then the alternative "קרניבל" is saved as the **current** car | NIRO recognised ("נירו" alone works); a later catalog name at the options stage stays an alternative |
| 4 | "… פנסיה 7%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, **ילד אחד**" with "אני מנהלת פיתוח" | first message | "רשמתי" lists the salary fields, but the occupation is dropped silently; the next messages get "לא הצלחתי להבין" (asks for the group) | occupation saved (with "1 ילדים" it works) |
| 5 | "ויתור על הרכב" | while the group / occupation is still missing | "לא מצאתי את "ויתור על הרכב" בטבלת העיסוקים. זה התפקיד שלכם?" | recorded as the wish to opt out (or asked), never as an occupation |
| 6 | "לא ידוע לי" | Q2 | every Q2 field on screen is marked unknown (session-2 rule) | only the field the question named (`state.asked_field`), or ask which one |

- Test for 1–2: `backend/tests/test_ranking_copay_corrections.py::test_a_salary_correction_typed_after_the_result_reaches_the_calculation`
  is a **strict xfail**: it fails as soon as the bug is fixed — remove the mark then.
- The form correction (`{fields: {base_salary: 16000}}`) after a result works and recalculates
  (`test_a_corrected_salary_from_the_form_reaches_the_calculation`): the backend supports the behaviour; the
  gap is reading the typed message.
- Text detail: the result explanation says "הסיבה העיקרית ב-ויתור על רכב: …" ("ב-" before a Hebrew word).

## 3. What the backend guarantees (checked against an independent oracle)

- "הכי משתלם" ranks all eligible options (Noa: 13 = 12 cars in groups א–ג + opting out) and the top three equal
  the oracle; each ranked delta equals the same option chosen alone.
- A new car is calculated with its catalog copay, `copay_pending: true`, one `NEW_CAR_COPAY` CONFIRM question
  (choosing it again opens no second one); the representative's 300 instead of 250 lowers the net by exactly 50.
- Every breakdown adds up: net = cash − tax − NI − health − pension − study fund − copay − family share.

## 4. Integration plan (Claude, after you report done)

Merge `origin/feature/conversation-reliability` into `deploy/render-demo`, run the backend on SQLite and
PostgreSQL, `app.verify`, `lint-imports`, vitest, build, and the 33 Playwright tests (incl. 21 journeys), then
report the merged commit. No push or deploy without the owner's approval.
