# Automated CI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add GitHub Actions checks that test the backend and test and build the frontend on every push and pull request.

**Architecture:** A single workflow contains independent frontend and backend jobs so both quality checks run in parallel. Each job installs from the repository lockfile and uses the corresponding ecosystem cache; the workflow has read-only repository access and cancels superseded runs.

**Tech Stack:** GitHub Actions, Node.js 22, npm, Python 3.12, uv, Vitest, unittest

---

### Task 1: Add the CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Verify the workflow does not exist**

Run:

```powershell
if (Test-Path .github/workflows/ci.yml) { exit 0 } else { exit 1 }
```

Expected: FAIL because `.github/workflows/ci.yml` is absent.

- [ ] **Step 2: Add the workflow**

Create `.github/workflows/ci.yml` with push and pull request triggers, read-only
contents permission, per-ref concurrency cancellation, and independent jobs:

```yaml
name: CI

on:
  push:
  pull_request:

permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true

jobs:
  frontend:
    name: Frontend
    runs-on: ubuntu-latest
    timeout-minutes: 15
    defaults:
      run:
        working-directory: frontend
    steps:
      - name: Check out repository
        uses: actions/checkout@v7
      - name: Set up Node.js
        uses: actions/setup-node@v6
        with:
          node-version: 22
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - name: Install dependencies
        run: npm ci
      - name: Run tests
        run: npm test
      - name: Build
        run: npm run build

  backend:
    name: Backend
    runs-on: ubuntu-latest
    timeout-minutes: 20
    defaults:
      run:
        working-directory: backend
    steps:
      - name: Check out repository
        uses: actions/checkout@v7
      - name: Set up Python
        uses: actions/setup-python@v6
        with:
          python-version: "3.12"
      - name: Set up uv
        uses: astral-sh/setup-uv@v9.0.0
        with:
          enable-cache: true
          version: "0.12.3"
      - name: Install dependencies
        run: uv sync --locked --python 3.12
      - name: Run tests
        run: uv run --python 3.12 python -m unittest discover -s tests -v
```

- [ ] **Step 3: Validate workflow structure**

Run a YAML parser and Action workflow validator against
`.github/workflows/ci.yml`.

Expected: both commands exit with status 0 and report no errors.

- [ ] **Step 4: Run frontend checks**

Run:

```powershell
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run build
```

Expected: Vitest reports all tests passing and Vite completes the production build.

- [ ] **Step 5: Run backend checks**

Run:

```powershell
uv sync --locked --python 3.12
uv run --python 3.12 python -m unittest discover -s tests -v
```

Expected: unittest reports all tests passing with no failures or errors.

- [ ] **Step 6: Review the final diff**

Run:

```powershell
git diff --check
git diff -- .github/workflows/ci.yml docs/superpowers/specs/2026-08-12-automated-ci-design.md docs/superpowers/plans/2026-08-12-automated-ci.md
```

Expected: `git diff --check` exits with status 0, and the diff contains only the approved CI workflow and its documentation.
