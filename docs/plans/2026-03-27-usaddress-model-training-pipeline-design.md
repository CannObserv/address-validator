# Custom usaddress Model Training & Deployment Pipeline

**Date:** 2026-03-27
**Status:** Approved
**Issue:** #75

## Summary

Skill-driven, interactive pipeline for identifying address parsing deficiencies in the `usaddress` CRF model, training improved models, deploying them to production, and contributing improvements upstream. Issue #72 (multi-unit designators) is the inaugural test case; subsequent patterns (city recovery heuristics) are future candidates.

## Motivation

The `usaddress` library uses a CRF model that occasionally mislabels tokens — particularly multi-unit designators, unit fragments in city names, and other edge patterns. We currently compensate with post-parse recovery heuristics (`_collect_ambiguous_components`, `_recover_unit_from_city`, `_recover_identifier_fragment_from_city`). These heuristics are effective but brittle — the better long-term fix is retraining the underlying model.

The upstream project accepts training data PRs but moves slowly (PR #403 open since Oct 2025). We need the ability to deploy fixes ahead of upstream releases while still contributing back.

## Architecture

```
Operator invokes /train-model
    |
    +- Step 1: IDENTIFY  -- query model_training_candidates table, review failures
    +- Step 2: LABEL     -- agent-assisted labeling (model + Claude + diff)
    +- Step 3: TRAIN     -- parserator train with manifest tracking
    +- Step 4: TEST      -- regression suite + before/after model comparison
    +- Step 5: DEPLOY    -- commit model, set CUSTOM_MODEL_PATH, restart
    +- Step 6: CONTRIBUTE -- fork PR to our fork, then gated upstream PR

scripts/model/
    +- identify.py      -- query candidates table, export CSV
    +- label.py         -- generate draft labels (model + Claude), produce diff
    +- train.py         -- run parserator train, write manifest
    +- test.py          -- rebuild old model from manifest, run comparison
    +- deploy.py        -- copy model to deployment path, validate load
    +- contribute.py    -- assemble upstream PR (XML + test data + description)

training/
    +- data/*.xml           -- our labeled training data (version-controlled)
    +- test_cases/*.csv     -- regression inputs with expected parses
    +- manifests/*.json     -- training run records (inputs, usaddress version, timestamp)
    +- models/              -- (gitignored) built .crfsuite files
    +- candidates.jsonl     -- (gitignored) local export cache from DB
```

## Decisions

### Model deployment strategy: toggle (option C)

Default to the bundled upstream model. Env var `CUSTOM_MODEL_PATH` opts into a locally-trained model. This gives us safety of upstream defaults with the ability to deploy fixes ahead of their release cycle.

### Skill structure: hybrid (option C)

One orchestrating skill (`/train-model`) calls discrete scripts under `scripts/model/` for each deterministic step. The skill handles sequencing, documentation, and operator interaction; the scripts handle reproducible technical work.

### Skill interaction model: interactive with automation

- Operator prompted to confirm which steps to run
- `--through <step>` flag for full automation up to a target step
- `--step <step>` for retry/resume at a particular step
- Step 6 (contribute) is always explicitly gated

### Training artifact storage: data tracked, binaries reconstructed

- Training XML and test CSVs are version-controlled in `training/`
- `.crfsuite` model binaries are gitignored (reconstructible from manifests)
- Only the deployed model is committed: `src/address_validator/custom_model/usaddr-custom.crfsuite`
- Manifests record exact inputs for deterministic reconstruction (CRFsuite + L-BFGS is deterministic given identical inputs)

### Agent-assisted labeling: both + diff (option C)

- Run `usaddress.parse()` on each address -> model labels
- Run Claude labeling from address structure knowledge -> Claude labels
- Present side-by-side diff showing disagreements
- Operator resolves each disagreement
- Output: labeled XML in upstream training format

### Candidate collection: separate DB table (option B)

Decoupled from audit. Fire-and-forget insert when `RepeatedLabelError` or post-parse recovery fires.

### Upstream contribution: two-stage (option C)

Improvements go to our fork first (unblocked). Gated step assembles focused, single-pattern-family PRs to `datamade/usaddress` matching their expectations.

### Model loading: lifespan hook (option A)

Load custom model during FastAPI startup, swap `usaddress.TAGGER`. Parser code unchanged.

## Candidate Collection Table

New table: `model_training_candidates`

| Column | Type | Purpose |
|---|---|---|
| `id` | SERIAL PK | -- |
| `raw_address` | TEXT | original input |
| `failure_type` | TEXT | `repeated_label_error`, `post_parse_recovery` |
| `parsed_tokens` | JSONB | usaddress token output before recovery |
| `recovered_components` | JSONB | our post-recovery output (if applicable) |
| `created_at` | TIMESTAMPTZ | -- |
| `status` | TEXT | `new` / `reviewed` / `labeled` / `rejected` |
| `notes` | TEXT | operator notes |

**Collection points in `parser.py`:**

1. `RepeatedLabelError` catch block (line 408) — `failure_type = 'repeated_label_error'`, `parsed_tokens` = `exc.parsed_string`, `recovered_components` = output of `_collect_ambiguous_components()`
2. `_recover_unit_from_city()` / `_recover_identifier_fragment_from_city()` — `failure_type = 'post_parse_recovery'`, `parsed_tokens` = pre-recovery component values, `recovered_components` = post-recovery values

Insert is fire-and-forget (same pattern as audit), gated by `get_engine()` availability.

## Model Loading

```python
# In FastAPI lifespan hook
custom_model_path = os.environ.get("CUSTOM_MODEL_PATH")
if custom_model_path:
    path = Path(custom_model_path)
    if path.exists():
        import pycrfsuite
        tagger = pycrfsuite.Tagger()
        tagger.open(str(path))
        usaddress.TAGGER = tagger
        logger.info("loaded custom usaddress model: %s", path)
    else:
        logger.warning("CUSTOM_MODEL_PATH=%s not found, using bundled model", path)
```

`parser.py` continues calling `usaddress.tag()` unchanged — the swap is transparent.

## Env Vars

| Variable | Values | Default |
|---|---|---|
| `CUSTOM_MODEL_PATH` | filesystem path to `.crfsuite` file | -- (uses bundled model) |

## Skill: `/train-model`

### Invocation

```
/train-model                    # interactive -- prompt for step selection
/train-model --step identify    # start/resume at specific step
/train-model --through train    # automate steps 1-3, pause before deploy
/train-model --step contribute  # resume at contribution step
```

### Step 1 — IDENTIFY (`scripts/model/identify.py`)

- Query `model_training_candidates` where `status = 'new'`
- Group by `failure_type`, show frequency and example addresses
- Operator selects which pattern family to address
- Export selected candidates to CSV
- Operator confirms or adds manual examples

### Step 2 — LABEL (`scripts/model/label.py`)

- Input: CSV of raw addresses
- Run `usaddress.parse()` on each -> model labels
- Run Claude labeling -> Claude labels
- Produce side-by-side diff showing disagreements
- Operator resolves each disagreement
- Output: `training/data/<pattern-name>.xml` (training data)
- Output: `training/test_cases/<pattern-name>.csv` (test data — different addresses, same patterns)

### Step 3 — TRAIN (`scripts/model/train.py`)

- Combine upstream training XML (from installed usaddress or our fork) with our custom XML
- Run `parserator train <all-training-files> usaddress`
- Write manifest to `training/manifests/<id>.json`
- Output: new `.crfsuite` in `training/models/` (gitignored)

### Step 4 — TEST (`scripts/model/test.py`)

- Rebuild previous model from its manifest (deterministic reconstruction)
- Run both models against `training/test_cases/*.csv`
- Assert: old model fails on target pattern, new model succeeds
- Assert: no regressions on existing test cases
- Run project test suite (`uv run pytest`)
- Produce evaluation report

### Step 5 — DEPLOY (`scripts/model/deploy.py`)

- Copy `.crfsuite` to `src/address_validator/custom_model/usaddr-custom.crfsuite`
- Update manifest `deployed: true`
- Operator sets `CUSTOM_MODEL_PATH` in `/etc/address-validator/env`
- Restart service (`sudo systemctl restart address-validator`)
- Smoke test: parse original failing addresses via live API

### Step 6 — CONTRIBUTE (`scripts/model/contribute.py`)

Two independently gated sub-steps:

**6a: Our fork**
- Push training XML + test XML to our usaddress fork
- Unblocked — can run immediately after step 5

**6b: Upstream PR**
- Assemble focused PR per pattern family to `datamade/usaddress`
- PR body includes: problem statement, before/after `usaddress.parse()` output, training XML, test XML, linked issue
- One pattern family per PR (matches upstream preference from merged PR #386)
- Always requires explicit operator confirmation

## Manifest Format

```json
{
  "id": "2026-03-27-multi-unit",
  "description": "Multi-unit designator handling (BLDG + ROOM)",
  "usaddress_version": "0.5.16",
  "training_files": [
    "upstream:training/labeled.xml",
    "upstream:training/multi_word_state_addresses.xml",
    "custom:training/data/multi-unit-designators.xml"
  ],
  "test_files": [
    "training/test_cases/multi-unit-designators.csv"
  ],
  "created_at": "2026-03-27T12:00:00Z",
  "deployed": false,
  "upstream_pr": null
}
```

## Training Data Format

Follows upstream convention exactly:

```xml
<AddressCollection>
  <AddressString>
    <AddressNumber>995</AddressNumber>
    <StreetName>9TH</StreetName>
    <StreetNamePostType>ST</StreetNamePostType>
    <OccupancyType>BLDG</OccupancyType>
    <OccupancyIdentifier>201</OccupancyIdentifier>
    <SubaddressType>ROOM</SubaddressType>
    <SubaddressIdentifier>104 T</SubaddressIdentifier>
    <PlaceName>SAN FRANCISCO</PlaceName>
    <StateName>CA</StateName>
    <ZipCode>94130-2107</ZipCode>
  </AddressString>
</AddressCollection>
```

## Dependencies

- `parserator` — add as dev dependency (`uv add --dev parserator`)
- `pycrfsuite` — already transitive via usaddress
- Our usaddress fork — created when first contribution is ready (step 6a)

## Migration

One Alembic migration for `model_training_candidates` table.

## Future Patterns

The post-parse recovery heuristics identify patterns that should eventually be trained into the model, eliminating the need for runtime correction:

- Unit designators mislabeled as `PlaceName` (currently fixed by `_recover_unit_from_city`)
- Single-letter identifier fragments absorbed into city (currently fixed by `_recover_identifier_fragment_from_city`)
- Multi-unit designators causing `RepeatedLabelError` (currently fixed by `_collect_ambiguous_components`)

As each pattern is successfully trained, the corresponding heuristic can be simplified or removed — the `model_training_candidates` table's before/after data directly informs which patterns to target next.

## Upstream PR Expectations

Based on analysis of merged PRs (esp. #386):

- One pattern family per PR
- Files: `training/<name>.xml` + `measure_performance/test_data/<name>.xml`
- PR body: problem statement, before/after parse output, linked issue, testing steps
- No `.crfsuite` binary — maintainers retrain from all XML
- No feature extraction code changes expected
