---
name: train-model
description: Interactive pipeline for training custom usaddress CRF models. Use when the user says "train model", "retrain usaddress", "fix parsing", or "/train-model". Walks through identify, label, train, test, deploy, and contribute steps with operator confirmation at each gate.
compatibility: Designed for Claude. Requires Python 3.12, uv, parserator, pycrfsuite, usaddress, PostgreSQL (for candidate collection). Optional: ANTHROPIC_API_KEY for Claude-assisted labeling.
metadata:
  author: gregoryfoster
  version: "1.0"
  triggers: train model, retrain usaddress, fix parsing, train-model
---

# usaddress Model Training Pipeline — address-validator

Interactive, resumable pipeline for training custom usaddress CRF models,
deploying them, and contributing improvements upstream. Issue #75.

## Steps

| # | Name | Script | Description |
|---|---|---|---|
| 1 | IDENTIFY | `scripts/model/identify.py` | Query candidates, review failures, export CSV |
| 2 | LABEL | `scripts/model/label.py` | Agent-assisted labeling with model+Claude diff |
| 3 | TRAIN | `scripts/model/train.py` | Run parserator train, write manifest |
| 4 | TEST | `scripts/model/test_model.py` | Regression suite + before/after comparison |
| 5 | DEPLOY | `scripts/model/deploy.py` | Copy model, validate, restart service |
| 6 | CONTRIBUTE | `scripts/model/contribute.py` | Fork PR + gated upstream PR |

## Invocation

```
/train-model                    # Interactive — prompt for step selection
/train-model --step identify    # Start/resume at specific step
/train-model --through train    # Automate steps 1–3, pause before deploy
/train-model --step contribute  # Resume at contribution step
```

## Process

### Parse arguments

Extract any `--step <name>` or `--through <name>` from the user's invocation.

- `--step <name>`: start at that step, run interactively from there
- `--through <name>`: run all steps up to and including that step without pausing, then stop
- Neither: prompt the operator:
  ```
  Which steps would you like to run?
  Options: identify, label, train, test, deploy, contribute, all
  (or 'identify through train' to automate multiple steps)
  ```

Always confirm before starting any execution.

---

### Step 1: IDENTIFY

**Purpose:** Find addresses where the parser needed recovery.

**Setup:** Load DB credentials:
```bash
source /etc/address-validator/env 2>/dev/null || true
```

**Show summary:**
```bash
uv run python scripts/model/identify.py summary
```

**Ask operator:** Which failure type / pattern do you want to target? (e.g. `repeated_label_error`, `post_parse_recovery`)

**Export candidates:**
```bash
uv run python scripts/model/identify.py export --type <failure_type> --out training/candidates.csv
```

**Review with operator:**
- Show a sample of the exported addresses
- Ask: "Do you want to add any manual examples?" If yes, ask them to append lines to the CSV

**Confirm** before proceeding to Step 2.

---

### Step 2: LABEL

**Purpose:** Generate labeled training XML with model+Claude comparison.

**Determine names:** Ask operator for a short pattern name (e.g. `multi-unit`) and confirm the output paths:
- Training XML: `training/data/<pattern-name>.xml`
- Test XML: `training/test_cases/<pattern-name>.xml` (optional, ~20% split)

**Run labeling:**
```bash
uv run python scripts/model/label.py training/candidates.csv \
  training/data/<pattern-name>.xml \
  --test-output training/test_cases/<pattern-name>.xml
```

The script is interactive — for each address it shows model labels vs Claude labels side-by-side and asks the operator to choose.

**After labeling:** Show the output XML path. Confirm it looks correct.

**If no DB candidates:** Prompt the operator to create a manual CSV:
```
Tip: if you have no DB candidates, create training/candidates.csv manually:
raw_address
"995 9TH ST BLDG 201 ROOM 104 T, SAN FRANCISCO, CA 94130-2107"
```

---

### Step 3: TRAIN

**Purpose:** Train a new CRF model.

**Ask for description:** "Provide a description for this training run (e.g. 'Multi-unit designator handling — BLDG + ROOM patterns'):"

**Run training:**
```bash
uv run python scripts/model/train.py \
  --name <pattern-name> \
  --description "<description>"
```

**Verify success:** Check exit code. If non-zero, show the error and ask the operator how to proceed (retry, skip, or abort).

**Show manifest path** after success.

---

### Step 4: TEST

**Purpose:** Verify the new model improves without regressions.

**Run tests:**
```bash
uv run python scripts/model/test_model.py \
  --model training/models/usaddr-<name>.crfsuite \
  --run-pytest
```

**If a previous manifest exists**, run comparison:
```bash
uv run python scripts/model/test_model.py \
  --model training/models/usaddr-<name>.crfsuite \
  --compare-manifest training/manifests/<previous-id>.json \
  --run-pytest
```

**Review results:** All tests must pass before proceeding. If failures, ask operator: retry after fixing, or abort.

---

### Step 5: DEPLOY

**GATE:** Always ask explicit confirmation before this step.

> "Ready to deploy model `usaddr-<name>.crfsuite` to production? This will copy the model to `src/address_validator/custom_model/usaddr-custom.crfsuite` and can optionally restart the service. Proceed? [y/N]"

**Deploy:**
```bash
uv run python scripts/model/deploy.py \
  --model training/models/usaddr-<name>.crfsuite \
  --restart \
  --smoke-test
```

**Remind operator:**
```
Ensure CUSTOM_MODEL_PATH=<absolute-path>/src/address_validator/custom_model/usaddr-custom.crfsuite
is set in /etc/address-validator/env
```

**Commit the deployed model:**
```bash
git add src/address_validator/custom_model/usaddr-custom.crfsuite training/manifests/
git commit -m "#75 feat: deploy custom usaddress model for <pattern-name>"
```

---

### Step 6: CONTRIBUTE

**GATE:** Both sub-steps require explicit confirmation.

**6a: Our fork** (unblocked):
```
Push training data to our usaddress fork? [y/N]
```
```bash
uv run python scripts/model/contribute.py --name <pattern-name> --stage fork
```

**6b: Upstream PR** (gated — only when confident):
```
Open a PR to datamade/usaddress? Only proceed when training data is correct and complete. [y/N]
```
```bash
uv run python scripts/model/contribute.py --name <pattern-name> --stage upstream
```

---

## Error Handling

- If any step fails: report the error, ask if operator wants to retry or skip
- `--step <name>` allows resuming from any point
- Training data and manifests are never deleted automatically
- If `VALIDATION_CACHE_DSN` is not set, Step 1 will fail — prompt operator to set it or skip to Step 2 with a manual CSV

## Related

- Design doc: `docs/plans/2026-03-27-usaddress-model-training-pipeline-design.md`
- Issue: #75
- Scripts: `scripts/model/` (identify, label, train, test_model, deploy, contribute)
