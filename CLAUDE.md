# Strauss Car Advisor — guidelines for working on this codebase

A Hebrew chatbot that tells an employee how changing their company car changes their monthly net pay.
Open Code AI Hackathon 2026, Strauss challenge. Submission: 2026-10-21. Presentation: 2026-10-28.

Full specification: https://claude.ai/code/artifact/59517376-30d7-4cf7-ad98-ed5c97cf7946 (section 14 is the work plan).

## Commands (run from `backend/`)

```bash
pip install -e ".[dev]"
python -m app.modules.vehicles.import_csv   # load the tax-authority CSV into SQLite
python -m app.verify                        # must report 12/12
pytest                                      # all tests must pass
lint-imports                                # module boundaries must hold
```

## Architecture

Modular monolith (ports & adapters). Every module can later be extracted into a service.

| Module | Responsibility |
| --- | --- |
| `calculation` | Pure engine. Must not import the database, FastAPI or other modules (enforced by `lint-imports`). |
| `rules` | Loads `config/strauss-rules.json` (as delivered), `config/extensions.yaml` and `config/strauss-data.xlsx`. |
| `vehicles` | Tax-authority vehicle data and current-car matching. |
| `cases` | Questions to the transport representative. |
| `conversation` | State machine and LLM agent. |
| `shared` | Money, database, settings. |

A module imports another module **only** through that module's `public.py`.

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
- Pension and study fund are computed on base + commissions + bonus. Pension credit = 35% × min(pension, 7% × 9,500).
- The employee's copay does not reduce the taxable use value.
- The employee's share of the family-track gap is added on top of the copay.
- Copay for a new car: calculate immediately with the catalog default and mark it "pending representative approval" (`CATALOG_DEFAULT_CONFIRM_ASYNC`).
- Current-car matching: company catalog first, then the tax file. Several matches with different use values → the employee chooses from up to 5.

## Conventions

- UI text, error messages and eligibility reasons are in Hebrew. Code, identifiers and comments that explain code may be in English.
- Commit messages: English, imperative mood, describe what changed.
