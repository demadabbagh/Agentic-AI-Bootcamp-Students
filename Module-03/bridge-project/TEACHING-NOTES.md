# The Vendor Onboarding Desk — teaching notes

**Instructor only.** Set after Module 3, due before the next session.
Budget ~5–6 hours of student time.

---

## Why this project exists

Students asked, in these words: *"what's the purpose of learning this if we're
not building real agents?"* They were right to ask. A calculator agent teaches
the mechanics and none of the judgement.

This one queries two registries that procurement and compliance teams genuinely
use, about real companies, and the answers change as the registries change.
**The reasoning is the deliverable, not the output.**

It also retro-justifies Module 3. On Saturday the honest answer to *"what does
planning buy me when ReAct is cheaper?"* was "not much, on a two-step sum."
`MAX_LOOKUPS = 9` is that answer made real.

### LangGraph only — and why that is deliberate

The Module 2 bridge shipped both a CrewAI and a LangGraph solution. This one is
LangGraph only, on purpose.

Everything this project teaches is **control the student holds themselves**: a
budget checked before each lookup, a loop-back edge they draw, a router that
reads without writing. CrewAI runs the agent loop internally — you found that
yourself at `crew_agent_executor.py:330 _invoke_loop_react()`. That abstraction
is why CrewAI is good for delegation and wrong here; a student choosing it would
fight the framework for control it deliberately hides, and the budget lesson
would never become visible.

There is also a concrete reuse argument: `screen` hands one supplier to the
ReAct graph they compiled in the Module 3 lab. In CrewAI they would have nothing
to reuse.

**If a student asks anyway**, the honest answer is a good teaching moment:
*"you could, and the interesting part is what you would lose."* Let them argue
it — but do not let them spend Thursday on it.

---

## Ground truth, measured 30 Aug 2026

Re-run `verify_tools.py` on the morning of the session — registries move.

| supplier | LEI register | sanctions (top score) | defensible verdict |
|---|---|---|---|
| **Maersk A/S** | 5 candidates, **1 exact**, ACTIVE / ISSUED | 0.71 noise | **APPROVE** |
| **Siemens AG** | 5 candidates, **0 exact** | 0.71 noise | **CONDITIONS** |
| **Almarai Company** | **NO_RECORDS** (real company) | **0.81 noise** | **CONDITIONS** |
| **Zorblax Trading FZE** | **NO_RECORDS** (invented) | 0.77 noise | **REJECT** |
| **Al Noor Cart Trading Company** | 1 exact, ACTIVE / ISSUED | 0.78 noise | **APPROVE** |
| **C & V Works ApS** | 1 exact, ACTIVE / **LAPSED** | 0.71 noise | **CONDITIONS** |
| **Al Wasel and Babel General Trading LLC** | NO_RECORDS | **1.00 — real match, IRAQ2** | **REJECT** |

### The four traps, and which supplier carries each

**1 · "The register found it, so that's our supplier."** — `Siemens AG`
returns *Siemens Energy AG, Siemens Healthineers AG, Siemens Mobility AG* and
two more. **None is named exactly "Siemens AG".** An agent that takes
`data[0]` approves a company nobody asked about, and sounds certain doing it.
This is the most common real failure in supplier onboarding.

**2 · "Not in the register means it doesn't exist."** — Three suppliers return
`NO_RECORDS` and they deserve **three different verdicts**. An LEI is mainly
required of entities in regulated financial markets; Almarai is a major Saudi
food company with no LEI at all. The register alone can never separate Almarai
from Zorblax — only a second tool can. *This is what forces the ReAct loop to
be real rather than decorative.*

**3 · "A high similarity score means sanctioned."** — Every legitimate supplier
scores 0.71–0.81 against something, purely from shared words like "Trading" or
"Company". **Almarai scores 0.81 against `HARA COMPANY [SDGT/NPWMD/IRGC]`** —
an alarming-looking hit on a legitimate dairy business. The one true match
scores **1.00**. A threshold of 0.80 rejects a real supplier; 0.90 is
defensible. Students must pick a number and justify it.

**4 · "It's in the register and active, so it's fine."** — `C & V Works ApS`
has entity status **ACTIVE** and registration status **LAPSED**. Two different
fields, and they disagree. Nothing in the course notes explains what a lapsed
LEI means; they have to go and look it up.

### The budget arithmetic

Sanctions screening runs for all seven and is free. Of the remaining work:
`Al Wasel` is settled by the sanctions hit alone (0 lookups), the other six need
one register lookup each (6), and the two `NO_RECORDS` cases need one search
each (2) — **8 of 9**. One unit of slack. A duplicated lookup costs a supplier.

---

## What the reference solution does

`solution-code/vendor_desk_solution.py`. Run it before the session — ~2 minutes,
about 21 model calls.

```
triage → screen → decide → (loop) → memo
            │
            └── hands ONE supplier to a compiled ReAct sub-agent
```

`screen` calls `SCREENER.invoke()` — **the same manager/worker move as the
Module 3 solution.** No hand-written loop anywhere. If a student's `screen`
contains `for s in suppliers:`, point them at Module 3 line 203.

**Verified stable:** two consecutive runs produced identical verdicts for all
seven suppliers, 9 lookups each. That is not guaranteed for *their* code, and
the brief asks them to run it twice and report any drift.

### Two things I had to fix that they will hit too

**Duplicate lookups.** The model re-issued the same `web_search` with slightly
different wording, burning budget for an identical answer. Prompting didn't fix
it; a **deterministic guard** did — one search per supplier, enforced in code.

> **Teach this.** When a model misbehaves in a way you can *detect*, write the
> check — don't argue with it in the prompt.

**Triage ranked by brand recognition.** The first version put "Siemens AG" and
"Maersk A/S" last because they are *famous* — so the ambiguity trap never fired.
Fame is not evidence, and a big group name is **more** ambiguous, not less,
because large groups have many similarly-named subsidiaries. The fix was in the
triage prompt. Watch for this in their submissions; it is a subtle and very
human bias to encode.

---

## What to actually read in their submission

`MEMO.md` shows whether the plumbing worked. **`NOTES.md` is the assessment.**

| looking for | weak | strong |
|---|---|---|
| **(a) budget trade-off** | "I ran out of lookups" | "I skipped Maersk because it returned an exact register match with clean screening, so a second source would only confirm what I had — the residual risk is that I never checked whether the pinned entity is the trading arm" |
| **(b) the threshold** | "I used 0.9 because it seemed right" | quotes the 0.81 on Almarai and the 1.00 on Al Wasel, and says what being wrong costs in each direction |
| **(c) near-miss** | "it worked fine" | names a trap, quotes what it output first, explains the fix |

**(b) is the one that separates them.** Anyone can pick a number. The student
who says *"0.81 on a legitimate dairy company is why I didn't use 0.80, and I
accept that a sophisticated alias below 0.90 would get through"* is thinking
like a compliance analyst.

Also check: **did they run it twice?** Verdicts drift between runs because the
*plan* changes. Noticing that unprompted is a strong signal.

---

## Failures you'll be asked about

| symptom | cause |
|---|---|
| `LengthFinishReasonError` mid-run | the model failed to close a valid structured object. **Not their logic.** The reference wraps every model call in `ask()` — one retry, then a safe fallback. Expect several students to hit this on Thursday night; it is worth mentioning when you set the project |
| only one candidate ever returned | they returned `data[0]` from `gleif_lookup`. The disambiguation is the agent's job, not the tool's — this hides trap 1 inside the plumbing |
| the filter appears to be ignored | square brackets not percent-encoded. **The request still succeeds**, which is what makes it nasty — they get unfiltered results and think it works |
| everything is `INSUFFICIENT` | treating `NO_RECORDS` and `LOOKUP_UNAVAILABLE` as the same thing |
| everything is `REJECT` | no second tool. Without a web search there is no way to tell Almarai from Zorblax |
| budget gone by supplier three | ReAct with no plan — the Module 3 lesson landing |
| agent never stops | budget counter in the router. Routers are read-only |
| `HTTP 400` from the LLM | `args: dict` in a schema again |
| tools work, graph doesn't | they skipped `verify_tools.py`. Make them run it |

---

## If you want to make it harder

Strong students will finish early. Don't add suppliers — add **ambiguity**:

- Rana replies: *"Procurement says they've used Zorblax before and it was fine."*
  Now a human assertion contradicts the evidence. What does the agent do with it?
- Give them 340 suppliers and the same nine lookups. The honest answer is that
  per-supplier investigation is the wrong architecture — you need tiering and
  sampling. That paragraph is a genuinely senior insight.

---

## Files

| file | student sees it |
|---|---|
| `INSTRUCTIONS.md` | ✅ the brief · 4 traps · rubric |
| `REQUEST.md` | ✅ the incoming email |
| `registry_tools.py` | ✅ `sanctions_screen` written, `gleif_lookup` is theirs |
| `verify_tools.py` | ✅ 10 checks against known-answer companies |
| `search_tools.py` | ✅ unchanged from Module 2 |
| `starter-code/` | ✅ scaffold with TODOs |
| `solution-code/` | ❌ hold back until they submit |

**Note on the repo:** `.gitignore` line 5 is `Module-*/solution-code/`, which
does **not** match the nested `bridge-project/solution-code/` path — the Module 2
bridge solutions are tracked in Cloud for exactly this reason. If you push this
folder, the solution goes with it unless you exclude it explicitly.
