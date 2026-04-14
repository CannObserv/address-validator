---
name: train-model
description: Interactive pipeline for training custom usaddress CRF models. Use when the user says "train model", "retrain usaddress", "fix parsing", or "/train-model". Walks through identify, label, train, test, deploy, observe, and contribute steps with operator confirmation at each gate.
compatibility: Designed for Claude. Requires Python 3.12, uv, parserator, pycrfsuite, usaddress, PostgreSQL (for candidate collection). Optional: ANTHROPIC_API_KEY for Claude-assisted labeling.
metadata:
  author: gregoryfoster
  version: "2.0"
  triggers: train model, retrain usaddress, fix parsing, train-model
---

# usaddress Model Training Pipeline — address-validator

Interactive, resumable pipeline for training custom usaddress CRF models,
deploying them, observing real-world performance, and contributing
improvements upstream. Issue #75.

## Steps

| # | Name | Script | Description |
|---|---|---|---|
| 1 | IDENTIFY | `scripts/model/identify.py` | Query candidates, review failures, export CSV |
| 2 | LABEL | `scripts/model/label.py` | Agent-assisted labeling with model+Claude diff |
| 3 | TRAIN | `scripts/model/train.py` | Run parserator train, write manifest |
| 4 | TEST | `scripts/model/test_model.py` | Regression suite + before/after comparison |
| 5 | DEPLOY | `scripts/model/deploy.py` | Copy model, validate, restart service |
| 6 | OBSERVE | `scripts/model/performance.py` | Collect real-world performance metrics |
| 7 | CONTRIBUTE | `scripts/model/contribute.py` | Fork PR + gated upstream PR |

## Invocation

```
/train-model                    # Interactive — prompt for step selection
/train-model --step identify    # Start/resume at specific step
/train-model --through deploy   # Automate steps 1–5, pause before observe
/train-model --step observe     # Resume at observation step
/train-model --step contribute  # Resume at contribution step
```

## Batch directory structure

All artifacts for a training batch live in a timestamped directory:

```
training/batches/2026_03_28-multi_unit/
├── candidates.csv          # exported from identify step
├── training-data.xml       # labeled training XML (parserator format)
├── test-data.xml           # labeled test XML
├── test-cases.csv          # test cases for test_model.py
├── rationale.md            # labeling basis documentation
├── manifest.json           # training run metadata
└── performance.md          # real-world performance report (after observation)
```

Cross-cutting content lives at `training/` root:
- `training/upstream/labeled.xml` — upstream usaddress training data
- `training/models/` — built model binaries (gitignored, reconstructible)

## Process

### Parse arguments

Extract any `--step <name>` or `--through <name>` from the user's invocation.

- `--step <name>`: start at that step, run interactively from there
- `--through <name>`: run all steps up to and including that step without pausing, then stop
- Neither: prompt the operator:
  ```
  Which steps would you like to run?
  Options: identify, label, train, test, deploy, observe, contribute, all
  (or 'identify through deploy' to automate multiple steps)
  ```

Always confirm before starting any execution.

---

### Step 1: IDENTIFY

**Purpose:** Find addresses where the parser needed recovery.

**Setup:** Load DB credentials:
```bash
source /etc/address-validator/.env 2>/dev/null || true
```

**Show summary:**
```bash
uv run python scripts/model/identify.py summary
```

**Ask operator:** Which failure type / pattern do you want to target? (e.g. `repeated_label_error`, `post_parse_recovery`)

**Export candidates:**
```bash
uv run python scripts/model/identify.py export --type <failure_type> \
  --out training/batches/<batch-dir>/candidates.csv
```

If no DB candidates, prompt operator to create a manual CSV or generate examples
directly in the conversation based on the target pattern.

**Review with operator:**
- Show a sample of the exported addresses
- Ask: "Do you want to add any manual examples?"

**Confirm** before proceeding to Step 2.

---

### Step 2: LABEL

**Purpose:** Generate labeled training XML for parserator.

**Create batch directory:**
```bash
mkdir -p training/batches/<YYYY_MM_DD-pattern_name>
```

**Labeling approach (choose based on context):**

1. **CLI labeling** (when `ANTHROPIC_API_KEY` is available):
   ```bash
   uv run python scripts/model/label.py candidates.csv \
     training/batches/<batch>/training-data.xml \
     --test-output training/batches/<batch>/test-data.xml
   ```

2. **Direct labeling in conversation** (preferred in skill context):
   - Analyze each address token-by-token
   - Assign labels based on the FGDC Address Standard and upstream training data
   - The agent can perform the same analysis as `label.py` directly

**Critical: research label semantics BEFORE labeling.** For each label assignment:
- Check the upstream training data (`training/upstream/labeled.xml`) for precedent
- Verify against the FGDC Address Standard hierarchy
- Count upstream label frequencies for the specific designator tokens

**XML format:** Parserator requires compact XML with space-separated elements:
```xml
<AddressCollection>
  <AddressString><AddressNumber>123</AddressNumber> <StreetName>MAIN</StreetName> ...</AddressString>
</AddressCollection>
```
Pretty-printed/indented XML will break parserator (whitespace becomes tokenized).

**Generate test cases CSV** (`test-cases.csv`) with columns:
`raw_address`, `expected_labels` (JSON), `description`, `should_fail_old_model`

**Document labeling rationale** in `training/batches/<batch>/rationale.md`:
- Governing standards (FGDC, USPS Pub 28, upstream training data)
- Per-pattern label assignments with evidence counts
- Patterns attempted but excluded, with root cause analysis
- Known edge cases and limitations

**Winnow training data iteratively.** After initial training + testing (Steps 3-4):
- Remove patterns that don't produce reliable model improvements
- Only ship training data for patterns that demonstrably work
- Document exclusions and root causes in the rationale

---

### Step 3: TRAIN

**Purpose:** Train a new CRF model.

**Ask for description:** "Provide a description for this training run"

**Run training:**
```bash
uv run python scripts/model/train.py \
  --name <pattern-name> \
  --description "<description>" \
  --session-dir training/batches/<batch>
```

The script automatically:
- Finds upstream training data in `training/upstream/`
- Finds custom XML in the batch directory
- Backs up the current model, trains, restores original
- Writes manifest to the batch directory

**Check upstream data:** If upstream training data is missing:
```bash
curl -sL https://raw.githubusercontent.com/datamade/usaddress/master/training/labeled.xml \
  -o training/upstream/labeled.xml --create-dirs
```

**Important CRF training constraints (learned from experience):**
- parserator deduplicates training examples via `set()` — oversampling identical examples has no effect
- 10-30 custom examples are insufficient to override strong upstream priors (100+ examples for a label)
- Generate many *distinct* addresses varying street names, numbers, cities, and designator combos
- Token features include previous/next token properties but NOT previous/next labels
- BLDG is already `SubaddressType` in upstream (12/12) — easiest to train new transitions from
- STE (106x OccupancyType), SUITE (58x), APT (29x) are nearly impossible to relabel as SubaddressType

---

### Step 4: TEST

**Purpose:** Verify the new model improves without regressions.

**Run tests:**
```bash
uv run python scripts/model/test_model.py \
  --model training/models/usaddr-<name>.crfsuite \
  --test-dir training/batches/<batch> \
  --run-pytest
```

**Quick verification before formal test run:** Test the model interactively:
```python
import pycrfsuite, usaddress
tagger = pycrfsuite.Tagger()
tagger.open('training/models/usaddr-<name>.crfsuite')
usaddress.TAGGER = tagger
usaddress.tag('<test address>')
```

**Iterate if needed:** If some patterns fail, return to Step 2 to winnow training
data and retrain. Only proceed with patterns that reliably pass.

---

### Step 5: DEPLOY

**GATE:** Always ask explicit confirmation before this step.

> "Ready to deploy model `usaddr-<name>.crfsuite` to production? Proceed? [y/N]"

**Deploy:**
```bash
uv run python scripts/model/deploy.py \
  --model training/models/usaddr-<name>.crfsuite \
  --restart \
  --smoke-test \
  --health-url http://localhost:8000/api/v1/health
```

**Set env var** (if not already set):
```bash
echo 'CUSTOM_MODEL_PATH=/path/to/src/address_validator/custom_model/usaddr-custom.crfsuite' \
  | sudo tee -a /etc/address-validator/env
```

**Commit the deployed model:**
```bash
git add -f src/address_validator/custom_model/usaddr-custom.crfsuite \
  training/batches/<batch>/
git commit -m "#<issue> feat: deploy custom usaddress model for <pattern-name>"
```

---

### Step 6: OBSERVE

**Purpose:** Collect real-world performance metrics after deployment. This step
runs over days/weeks — the operator pauses the pipeline here and returns later.

**Explain to operator:**
```
The model is now deployed. The audit_log records parse_type for every request
(Street Address vs Ambiguous). Over the next few days/weeks, real-world addresses
will flow through the parser. Addresses that previously triggered RepeatedLabelError
will now parse cleanly — the shift from Ambiguous to Street Address is the
performance signal.

Resume this step with /train-model --step observe when you have sufficient data.
```

**Check current metrics:**
```bash
source /etc/address-validator/.env
uv run python scripts/model/performance.py summary --since <deploy-date>
```

**Generate performance report** (when sufficient data collected):
```bash
uv run python scripts/model/performance.py report \
  --since <deploy-date> \
  --out training/batches/<batch>/performance.md
```

**Update manifest:**
The performance report path is recorded in manifest.json (`performance_file` field).

**Commit:**
```bash
git add training/batches/<batch>/performance.md
git commit -m "#<issue> docs: add performance report for <pattern-name> model"
```

---

### Step 7: CONTRIBUTE

**GATE:** Both sub-steps require explicit confirmation. Only proceed after
reviewing the performance report and confirming the model improves real-world
parsing.

**7a: Our fork** (unblocked):
```
Push training data to our usaddress fork? [y/N]
```
```bash
uv run python scripts/model/contribute.py --name <pattern-name> --stage fork
```

**7b: Upstream PR** (gated — only when confident):

Ask operator which fork branch the training data was pushed to (default: `main`).

```bash
uv run python scripts/model/contribute.py --name <pattern-name> \
  --stage upstream --branch <branch>
```

The script presents three choices: `[o]` open PR via `gh`, `[i]` show manual
instructions, `[n]` abort.

Include the performance report and rationale in the PR description.

---

## Error Handling

- If any step fails: report the error, ask if operator wants to retry or skip
- `--step <name>` allows resuming from any point
- Training data and manifests are never deleted automatically
- If `VALIDATION_CACHE_DSN` is not set, Step 1 will fail — prompt operator to set it or skip to Step 2 with a manual CSV

## Key Learnings

### CRF model constraints
- The usaddress CRF uses character-level features + bigram context, not label history
- Tokens with strong upstream label priors (100+ examples) are very hard to relabel
- BLDG-first patterns work because BLDG is already `SubaddressType` in upstream
- STE/SUITE/APT as outer designators fail because their `OccupancyType` prior is too strong
- Long numeric identifiers (3+ digits) after BLDG can confuse the CRF with AddressNumber

### Parserator quirks
- Pretty-printed XML breaks training (whitespace gets tokenized) — use compact format
- Training data is deduplicated via `set()` — oversampling identical examples has no effect
- The `train` CLI reconstructs the address from XML text content; element `tail` whitespace matters

### Label semantics
- Labels come from FGDC Address Standard, not USPS Pub 28
- SubaddressType = outer/building-level container; OccupancyType = inner/unit-level
- Always research upstream training data frequencies before labeling
- Document the basis for every label assignment in the rationale

### Route handler requirements
- Parse/standardize routes MUST be `async def` for ContextVar visibility (training candidate collection + parse_type audit)
- Sync `def` routes run in threadpool, which copies contextvars — writes invisible to middleware

## Related

- Design doc: `docs/plans/2026-03-27-usaddress-model-training-pipeline-design.md`
- Issue: #75
- Scripts: `scripts/model/` (identify, label, train, test_model, deploy, performance, contribute)
