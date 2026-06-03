# NewMem / STALE Benchmark — Full-Mode Eval Failure Analysis

**Eval date**: 2026-06-01  
**Total samples**: 20 (10 T1, 10 T2)  
**Total failures**: 10 (5 T1, 5 T2)  
**Overall pass rate**: 50% (10/20)

---

## 1. Executive Summary

This document analyzes the 10 failing samples from a full-mode evaluation of the NewMem pipeline on the STALE benchmark. The benchmark tests whether a memory system correctly detects when a previously stored belief (M_old) has been invalidated by a newer statement (M_new), and whether that detection propagates into query-time answers across three evaluation dimensions:

- **dim1** — Direct recall: does the model know M_old is no longer valid?
- **dim2** — Adversarial probe: does the model detect a query built on the false M_old premise?
- **dim3** — Action compliance: does the model's recommended action adhere to M_new's new state?

**Score breakdown by type:**

| Type | Samples | Pass | Fail | Pass rate |
|------|---------|------|------|-----------|
| T1 (direct conflict) | 10 | 5 | 5 | 50% |
| T2 (indirect/chained conflict) | 10 | 5 | 5 | 50% |
| **Total** | **20** | **10** | **10** | **50%** |

Scoring by dimension across failing samples (10 samples × 3 dims = 30 slots):

| Dimension | Passes | Fails |
|-----------|--------|-------|
| dim1 | 6 | 4 |
| dim2 | 5 | 5 |
| dim3 | 5 | 5 |

**Aggregate pass rates across all 20 samples** (each dim scored independently):
- T1 overall: 76.6% (23/30 dim-slots pass)
- T2 overall: 66.7% (20/30 dim-slots pass)

**High-level findings:**

The dominant failure mode is a **trigger_gate false negative**: the pipeline's gating step drops statements that should be stored — either M_old (when it first appears) or M_new (when it arrives as a change signal). When neither is in the profile, no conflict can ever be detected, and every downstream stage (impact_hypothesis, abductive_judgment, pool_synthesis, premise_check) operates on an empty foundation. The result is that premise_check uniformly returns SAFE, and the answer generator has no stale signal to act on.

T2 failures compound this with an additional structural gap: even when M_new is correctly stored, the pipeline has no stage that performs forward chaining to derive implied consequences. The abductive_judgment step checks whether M_new conflicts with a specific candidate memory, but it does not infer that M_new updates attribute A and A→B means B is also invalidated. T2 requires exactly this inference, and the pipeline architecture does not support it.

A secondary failure mode affects dim3 specifically: even when stale signals exist and premise_check correctly fires, the answer generator sometimes ignores them or produces contradictory output — recommending actions that violate M_new's new state.

---

## 2. Root Cause Taxonomy

Five root causes account for all observed failures. Multiple root causes can apply to a single sample.

---

### RC-1: Trigger_gate false negative on M_old

**What it is:** The trigger_gate step drops M_old when it first appears in the conversation because, at that moment, M_old does not contradict any memory already in the profile. The gate's job is to detect conflicts with *existing* memories, not to store new personal facts unconditionally. But M_old is a personal fact that should be stored regardless of whether it conflicts with anything — it is the baseline belief that M_new will later need to invalidate.

**Why it happens:** The trigger_gate is architected as a conflict detector, not an accumulator. Its decision criterion is "does this statement update or invalidate existing memory?" When the profile is empty or does not yet contain anything topically related to the new statement, the answer is always no — even if the statement contains a highly specific personal belief. The gate then drops the statement, and it is never stored.

**Consequence:** If M_old is never in the profile, then when M_new arrives, there is no candidate for abductive_judgment to check. The impact_hypothesis step may generate candidates, but they will all return "item not found or already stale." No conflict is recorded. M_old is effectively invisible to all downstream reasoning.

**Observed instances:** T1-1 (session 13, M_old dropped), T1-2 (session 10, M_old dropped), T1-3 (session 14, M_old dropped), T2-1 (session 7, M_old dropped), T2-5 (session 9, M_old partially dropped).

---

### RC-2: Trigger_gate topical/semantic miss on M_new

**What it is:** When M_new arrives, the trigger_gate drops it because it compares M_new against a profile composed of memories from *different* topics. The gate reasons that the incoming statement does not contradict memories about, say, job, location, or reading habits — and since those are the only things in the profile, the statement is dropped.

**Why it happens:** The gate's reasoning is topically scoped. It checks whether the new statement contradicts existing memories, but it matches topics at a surface level. If the profile contains memories about work, relationships, and reading, and M_new is about political affiliation, the gate sees no collision and drops M_new. But the correct behavior is to store M_new unconditionally as a new personal fact, then later run abductive_judgment to check whether stored beliefs in any topic are implied-by or dependent-on the changed attribute.

**Consequence:** M_new is not stored. No stale signal is ever generated. premise_check returns SAFE for all queries. The answer generator uses only M_old (if it was stored) or produces hedged/ignorant answers.

**Observed instances:** T1-2 (session 30: "Installing a developer beta on a main phone is a specific tech action that does not contradict or invalidate existing memory"), T1-3 (session 37: "provides new financial details that do not contradict or change any existing memory"), T2-1 (session 36: "does not contradict or update any personal information in the existing user profile"), T2-5 (session 36: "does not contradict or update any existing memories about job, location, or reading habits").

---

### RC-3: T2 inference chain failure

**What it is:** In T2 samples, M_new never directly mentions M_old's subject matter. Instead, M_new updates attribute A, and A→B means B (which M_old describes) is also invalidated. For example: M_new establishes an early-bedtime routine (A). M_old says the user logs into the forum every night (B). The inference chain is: early bedtime → phone off by 9pm → nightly forum logins are impossible. No pipeline stage performs this forward chaining.

**Why it happens:** The impact_hypothesis step generates hypotheses about what OLD beliefs might now be wrong, but it does so based on explicit semantic overlap — it looks for beliefs whose literal subject matter overlaps with M_new's subject matter. If M_new is about sleep schedules and M_old is about forum habits, there is no direct semantic overlap, so M_old never appears as a candidate. The abductive_judgment step can only evaluate candidates that impact_hypothesis surfaces, so it never sees M_old either.

**Structural gap:** There is no stage that asks "given M_new, what behaviors or states are now physically or temporally impossible, regardless of topic?" That question requires inference across a causal model of daily life, not just semantic matching.

**Observed instances:** T2-1, T2-2, T2-3, T2-4, T2-5. All five T2 failures have this as a contributing cause. In T2-4, the inference chain was partially detected by premise_check for dim1/dim2 (OUTDATED returned) but missed for dim3 (SAFE returned), showing that even when the chain is partially discovered, it is not reliably applied across all query formulations.

---

### RC-4: Premise_check false safe due to absent baseline

**What it is:** The premise_check step returns SAFE — meaning "the query premise does not contradict any known stale memory" — because there are no stale memories in the profile that are topically relevant to the query. This is technically correct from the check's perspective: there is nothing stale to contradict. But the *reason* there is nothing stale is RC-1 or RC-2 upstream: M_old was never stored, or M_new was never stored, so no stale flag was ever set.

**Why it matters:** premise_check is a query-time safeguard of last resort. When upstream pipeline stages fail to detect a conflict, premise_check is the only remaining opportunity to catch a false premise in the query. But it cannot detect stale premises it was never told about. The SAFE verdict then propagates to the answer generator with no correction signal, and the generator has no basis to push back on a false premise.

**Consequence:** All queries where upstream storage fails get premise_safe=True, status=SAFE, correction="". The answer generator then accepts whatever the query premise implies, including false premises built on M_old.

**Observed in nearly all samples:** T1-1 dim2 (SAFE → answer accepts M_old premise about 8–11 AM focus block), T1-2 all dims (SAFE → no stored baseline), T1-3 dim1+dim2 (SAFE → no stored portfolio data), T2-1 dim1+dim2 (SAFE → no license key baseline), T2-2 all dims (SAFE), T2-5 all dims (SAFE).

The notable exception is T1-4 and T1-5, where M_old *was* stored and correctly marked stale, causing premise_check to return OUTDATED. But even then, dim3 still failed due to RC-5.

---

### RC-5: Answer generation failures with partially correct memory

**What it is:** Even when stale memory exists and premise_check fires correctly (returning OUTDATED with a correction), the answer generator sometimes produces an answer that violates M_new's new state. This takes two forms:

**Form A — Ignores the stale signal and requests clarification:** The generator, receiving an OUTDATED verdict with correction text, responds by asking the user for their current state instead of using M_new. This was observed in T1-4 dim3: despite the premise_check correctly flagging JST timezone as outdated and having Toronto in the active profile, the answer said "please let me know your current availability and time zone so I can help you create an appropriate schedule."

**Form B — Hallucinates memory expiry:** The generator has M_new in active memory (e.g., pre-dawn bakery shifts) but then spontaneously asserts that M_new is also outdated ("the pre-dawn bakery shifts are a thing of the past"). This was observed in T2-4 dim3: the answer said "since you're open to weekdays and the pre-dawn bakery shifts are a thing of the past (at least as far as I know), those Tuesday and Thursday late nights shouldn't be a problem."

**Form C — Internal contradiction:** The generator references M_new in one part of the answer and then contradicts it in the next. Observed in T2-3 dim3: the answer correctly identified the Murphy bed installation, then recommended "keep an eye on what's on the nightstand" and "if you're working through what's already on the nightstand, that's perfect" — contradicting the inference that the nightstand was cleared for the Murphy bed installation.

**Form D — Hallucinated content:** The generator fabricates memories not present in any session. Observed in T2-2 dim3: instead of reasoning from the actual new state (early bedtime, offline after dinner), the answer invented a "late-night TV binge-watching habit" and a "weekly doubles league on Thursdays" — neither of which appeared anywhere in the training data.

---

## 3. Detailed Per-Sample Analysis

---

### T1-1: uid=0c0086f5 — dim2 FAIL (dim1+dim3 PASS)

**M_old:** "I generally keep the hours between eight and eleven in the morning reserved for deep focus work."  
**M_new:** "Lately I've been doing my concentrated work in the late afternoon, usually from about 2 to 5, so I tend to be hard to reach then."  
**Sessions:** 13, 38

**Pipeline trace:**

Session 13: M_old arrives as a statement but is **dropped at trigger_gate**. The gate's reasoning is: "The statement provides additional information about the user's morning routine but does not contradict or invalidate the [existing memories]." This is RC-1. At this point the profile had other memories (unrelated to scheduling) and M_old was treated as not conflicting with them — correct by the gate's narrow logic, but wrong as a memory accumulation decision. The statement "I have a mix of meetings and solo projects" passed and was stored as m_00019.

Session 38: M_new arrives in decomposed form. The statement "does concentrated work in the late afternoon, usually from about 2 to 5" passes trigger_gate and is stored as m_00068. The abductive_judgment step runs but finds no conflict: judgment_logs show candidate=None, conflict=None, because M_old (8–11 AM deep focus) was never stored and thus cannot appear as a candidate. Several follow-on statements are stored (m_00069 through m_00071). One — "tends to be hard to reach during 2-5 PM" — is dropped by trigger_gate with reason "does not contradict or invalidate any existing memory about the user," which is RC-2.

**Final state:** M_new is stored (m_00068). M_old is absent. No stale flag for the morning focus window.

**Dim1 (PASS):** premise_safe=True, status=SAFE. Despite no stale signal, the answer generator correctly uses M_new from active memory: "you no longer reserve 8–11 AM for deep focus... your concentrated work is late afternoon 2–5 PM." This passes because dim1's question is phrased to invite M_new, and the generator found m_00068.

**Dim2 (FAIL):** premise_safe=True, status=SAFE, correction="". The query is phrased as "Can you schedule a check-in during your deep focus block from 8–11 AM?" — a false premise based on M_old. Since premise_check has no stale memory for the morning window, it returns SAFE. The generator then accepts both the query's 8–11 AM premise and the stored 2–5 PM block as simultaneously valid, answering: "Based on your deep focus block from 8–11 AM and your preference to avoid meetings from 2–5 PM, the best window for a check-in would be between 11 AM and 2 PM (lunchtime) or after 5 PM." The judge's reasoning: "The response accepts the false premise that 8–11 AM is still a deep focus block and suggests a check-in window based on both the outdated and new blocks."

**Dim3 (PASS):** premise_safe=True, status=SAFE. The generator correctly uses 2–5 PM as the deep work window when planning.

**RC categories:** RC-1 (M_old dropped in session 13), RC-4 (premise_check SAFE in dim2 due to absent stale baseline), RC-2 (secondary: "hard to reach 2–5 PM" dropped in session 38).

---

### T1-2: uid=5f77adc7 — dim2+dim3 FAIL (dim1 PASS)

**M_old:** "I usually wait until my current device completely stops meeting my needs before I upgrade, because the newer features seldom change how I actually get things done."  
**M_new:** "I ended up signing up for the developer beta on my phone this weekend—needed the new on-device transcription and those automation shortcuts for a project, even if it means living with a few glitches until the stable release drops."  
**Sessions:** 10, 30

**Pipeline trace:**

Session 10: M_old arrives and is **dropped at trigger_gate**. Reason: "The statement describes a consistent personal habit that does not conflict with or change any existing memory about the user." This is RC-1. At this stage the profile had no device-preference memories, so the gate correctly found no conflict — but the statement should still have been stored as a personal belief.

Session 30: M_new arrives as "installed the developer beta on my main phone this weekend" and is **dropped at trigger_gate**. Reason: "Installing a developer beta on a main phone is a specific tech action that does not contradict or invalidate existing memory." This is RC-2. The gate did not recognize that beta adoption on a main phone directly contradicts a stored belief about only upgrading when devices fail — but more fundamentally, M_old was not stored, so there was nothing to contradict. Even if M_old had been stored, the gate's reasoning suggests it might still have dropped M_new as a "specific tech action" rather than a belief update.

**Final state:** Neither M_old nor M_new is stored. No device preference information in the profile.

**Dim1 (PASS):** The answer hedges: "I don't have any specific memory about your preference for waiting until a device fully stops meeting your needs... I'm not sure if your preference has changed." The judge accepts this hedged answer as a pass — it does not falsely confirm M_old.

**Dim2 (FAIL):** The query implies the user still holds the conservative upgrade philosophy. The answer: "That's a great approach—squeezing every last drop of life out of your gear." The judge: "accepts false premise from M_old ('great approach') and does not detect the trap or acknowledge the new state." Since neither M_old nor M_new was stored, premise_check has nothing to work with and returns SAFE.

**Dim3 (FAIL):** The query asks for advice on managing workflow while on a beta OS. Without M_new in the profile, the generator has no awareness that the user is on buggy beta software. The answer provides generic advice about workflow organization and using commute time — advice that would be appropriate for any user, not specifically for someone dealing with beta instability. Judge: "provides generic advice without acknowledging the user's new state of running developer beta software with glitches."

**RC categories:** RC-1 (M_old dropped session 10), RC-2 (M_new dropped session 30), RC-4 (premise_check SAFE on empty foundation for all dims).

---

### T1-3: uid=5308c7fd — dim1+dim2 FAIL (dim3 PASS)

**M_old:** "The majority of my holdings are situated in broad market index funds, with the remaining portion allocated to corporate bonds."  
**M_new:** "I'm updating my finances after my recent home purchase, and right now most of my money is sitting in a high-yield savings account and a ladder of Treasury bills."  
**Sessions:** 14, 37

**Pipeline trace:**

Session 14: M_old arrives and is **dropped at trigger_gate**. Reason: "The statement provides new financial information that does not contradict or change any existing memories about the user." This is RC-1. The profile at that point had no investment-related memories, so the gate found no conflict — but the statement is a highly specific, memorable personal fact about asset allocation.

Session 37: Several statements arrive. "My recent home purchase" passes trigger_gate and is stored as m_00087. Abductive_judgment runs multiple times against candidates but returns candidate=None, conflict=None throughout — consistent with M_old being absent from the profile.

The critical M_new statement — "right now most of my money is sitting in a high-yield savings account and a ladder of Treasury bills" — is **dropped at trigger_gate**. Reason: "The statement provides new financial details that do not contradict or change any existing memory in the user profile." This is RC-2. Because M_old was never stored, there is literally nothing in the financial domain to conflict with, and the gate drops M_new.

Other session 37 statements about monthly expenses ($6,500 including PITI) and home value (~$650k) do pass and are stored (m_00088, m_00089).

**Final state:** No investment portfolio data of any kind in the profile. M_old absent, M_new absent.

**Dim1 (FAIL):** The answer: "I don't have any information from our conversation history about your investment holdings—nothing about broad market index funds or corporate bonds came up. Could you tell me more about your current portfolio?" The judge marks this as fail: dim1 should surface that M_old's description of index funds/bonds is no longer valid, not claim total ignorance. However, because neither M_old nor M_new was stored, the generator's ignorance is technically accurate — the failure is upstream. The judge flags this failure because the expected behavior is "M_old is outdated, M_new is now the case," but the system can neither confirm nor deny either.

**Dim2 (FAIL):** The query is built on the false premise that the user still holds index funds and corporate bonds. The answer: "Since I don't have the exact percentages of your current split between broad market index funds and corporate bonds, I'll give you a general strategy..." — this partially accepts the false premise (implies those holdings may still exist) while hedging about percentages. The judge considers this a fail because it does not reject the premise.

**Dim3 (PASS):** This is an accidental pass. The dim3 query asked about setting up a high-yield savings account structure. Without any portfolio data, the generator fell back to giving standard HYSA advice, which happens to align with M_new's new state (HYSA + T-bill ladder). The pass is coincidental: the generator didn't know M_new; it simply gave generic financial advice that happened to match.

**RC categories:** RC-1 (M_old dropped session 14), RC-2 (M_new dropped session 37), RC-4 (premise_check SAFE for all dims).

---

### T1-4: uid=ac6b1ba4 — dim3 FAIL (dim1+dim2 PASS)

**M_old:** "You should know I'm located in the Japan Standard Time zone, so there is a significant gap between my friends and me."  
**M_new:** "I just signed a year-long lease in Toronto, and the sun doesn't come up until after 7 a.m. for me lately."  
**Sessions:** 6, 37

**Pipeline trace:**

Session 6: M_old ("I'm located in the Japan Standard Time zone") **passes trigger_gate** and is stored as m_00025. Abductive_judgment returns candidate=None, conflict=None (no prior timezone belief to conflict with). Two follow-on statements also pass and are stored: "I have two friends—one in US Pacific and one in the UK" (m_00026) and "I'm free weekdays 7–9am and 8–11pm, and weekends 9–11am and 7–11pm" (m_00027).

Between sessions 6 and 37: The profile accumulated many more memories. Notably, the user mentioned being at a hotel near the Eiffel Tower in Paris — this was stored and became the most recent location reference.

Session 37: "I just signed a year-long lease in Toronto" **passes trigger_gate** and is stored as m_00085. However, abductive_judgment checking M_old (m_00025, JST timezone) against the new Toronto statement should yield a conflict but apparently does not produce a stale flag for m_00025 — the profile logs show m_00025 as still active. The statement "the sun doesn't come up until after 7 a.m. for me lately" is **dropped at trigger_gate**: reason "a factual observation about sunrise time, likely reflecting seasonal change, does not contradict or invalidate." This is RC-2, as the gate misclassified a location-confirming detail as a meteorological observation.

**Final state:** m_00025 (JST) is still ACTIVE. m_00085 (Toronto lease) is ACTIVE. The Paris hotel memory is also active. The profile has an inconsistency: JST, Paris hotel, and Toronto lease are all active simultaneously.

**Dim1 (PASS):** premise_check returns premise_safe=False, status=OUTDATED with correction: "moved to Paris, France (Central European Summer Time) as indicated when mentioned hotel near Eiffel Tower." The answer correctly says "no longer in JST, now in Paris." The judge passes this despite the wrong city (Paris vs Toronto) because the core conclusion — "not in JST anymore" — is correct.

**Dim2 (PASS):** premise_check again returns OUTDATED. The answer correctly detects the false JST premise.

**Dim3 (FAIL):** premise_safe=False, status=OUTDATED. Despite having the OUTDATED verdict and the correction text, the answer generator does not use M_new (Toronto timezone + new schedule) to produce a concrete scheduling plan. Instead: "your previous schedule and time zone have been outdated... please let me know your current availability and time zone so I can help you create an appropriate schedule." This is RC-5 Form A: the generator received the stale signal and M_new (Toronto) is in active memory, but it chose to ask for clarification rather than use the available information. The judge: "does not provide a concrete action or plan that adheres to M_new (Toronto timezone). Instead requests additional information."

**RC categories:** RC-2 (sunrise time dropped in session 37 due to misclassification), RC-5 Form A (dim3: generator ignores available M_new and requests clarification).

---

### T1-5: uid=90797997 — dim3 FAIL (dim1+dim2 PASS)

**M_old:** "My life is essentially on hold until my daughter is older because I'm the only one available to care for her."  
**M_new:** "Since my schedule has opened up a lot lately, I've started signing up for weekend workshops and even penciled in a two-week trip this fall without having to coordinate anything complicated."  
**Sessions:** 6, 36

**Pipeline trace:**

Session 6: M_old passes trigger_gate and is stored as m_00017. Three follow-on statements also stored (m_00018: availability limited to 30–60 minute evening windows; m_00019: no phone calls or fixed meeting times; m_00020: basic admin/writing work history). Abductive_judgment checks run against prior candidates but all return "item not found or already stale" — the profile was relatively new.

Session 36: M_new-related statements arrive. "My schedule has opened up a lot lately" passes and is stored as m_00098. Abductive_judgment runs multiple times checking candidates including m_00017, m_00018 — but all return None ("item not found or already stale"). This is unexpected and suggests m_00017 and m_00018 may have been marked stale by an intermediate session between 6 and 36, or that the abductive_judgment prompt failed to match them.

Three more M_new-adjacent statements pass: m_00099 (signed up for weekend workshops), m_00100 (two-week fall trip), m_00101 (weekends catching up on errands). Abductive_judgment runs 7 consecutive times on m_00101 alone, all returning None.

**Final state:** M_new-related beliefs stored. M_old likely marked stale (possibly from an intermediate session not listed here, since abductive_judgment returns "already stale" rather than "not found").

**Dim1 (PASS):** premise_check returns OUTDATED. The answer recognizes the caregiving situation may have changed. Note: the correction references "close relationship with another adult" — an inference not directly present in M_new — suggesting the stale signal came from an intermediate memory rather than M_new directly. The judge passes anyway.

**Dim2 (PASS):** premise_check returns OUTDATED. The answer correctly detects the false sole-caregiver premise.

**Dim3 (FAIL):** The dim3 query concerns interior design (glass coffee table recommendation). This is topically distant from caregiving or schedule flexibility. premise_check returns premise_safe=True, status=SAFE, correction=None — there are no stale memories about interior design or furniture, and the retrieval system does not surface the caregiver/schedule memories as relevant to this query.

The generator then provides: "Given your setup, I'd say a sharp-edged, low solid glass coffee table... might not be the safest or most relaxing choice. You keep newspapers and magazines on your table..." The judge: "recommends against the user's proposed design for reasons unrelated to the updated state — generic safety concerns about children/clutter, ignoring that M_new shows schedule freedom and independence." The answer's reasoning about child safety (implying the daughter is still a limiting factor) directly contradicts M_new's new state of schedule openness and personal freedom.

This failure is structurally distinct: dim3's query is phrased around a topically distant domain, and premise_check's retrieval didn't connect schedule freedom to furniture design. The stale memory was not retrieved, so the SAFE verdict is a retrieval failure rather than a core premise_check logic failure.

**RC categories:** RC-4 (premise_check SAFE for dim3 due to retrieval gap — stale memories not surfaced for topically distant query), RC-3 secondary (the inference that "schedule is now open → no child-safety furniture constraints" is an indirect chain not made by any stage).

---

### T2-1: uid=3e3af301 — dim1+dim2 FAIL (dim3 PASS)

**M_old:** "I've kept a backup of all the license keys for the software I've purchased over the years."  
**M_new:** "After that little coffee spill, I took the weekend to do a full wipe and rebuild—fresh OS, fresh accounts, and I only brought back what was already synced to the cloud so I wouldn't drag any old clutter (or surprises) along."  
**Inference chain:** Full wipe + restore only cloud-synced data → locally archived license backups would not survive unless they were in the synced set → M_old no longer feasible.  
**Sessions:** 7, 36

**Pipeline trace:**

Session 7: M_old ("I've kept a backup of all the license keys") is **dropped at trigger_gate**. Reason: "does not contradict or update any existing memory about the user's digital habits." This is RC-1. No license key backup belief enters the profile.

Session 36: "I took the weekend to do a full wipe and rebuild—fresh OS, fresh accounts" passes trigger_gate and is stored as m_00064. Abductive_judgment runs multiple times but returns None — because M_old was never stored, there is no license-key candidate to check.

"I only brought back what was already synced to the cloud" is **dropped at trigger_gate**. Reason: "does not contradict or update any personal information in the existing user profile." This is RC-2. This statement is the key that connects the wipe to the license backup consequence: without cloud-synced local backups, any license key archive is gone. But the gate dropped it as irrelevant.

**Final state:** m_00064 ("full wipe and rebuild") is stored. The "cloud-only restore" qualifier is NOT stored. M_old is NOT stored.

**Dim1 (FAIL):** premise_safe=True, status=SAFE. The answer: "From our conversation history, I don't have any specific info about whether you still have a backup of all your license keys. The only relevant memory is that you did a full wipe and rebuild with a fresh OS..." The generator sees the wipe fact but does not complete the inference chain (wipe + cloud-only restore → local backups gone). Judge: "claims ignorance without inferring that the wipe likely destroyed local backups." This is RC-3: the T2 inference chain is not performed.

**Dim2 (FAIL):** The query is built on the false premise that license backups still exist. The answer: "Since you've just done a full rebuild and have plenty of storage, here's a straightforward system for organizing licenses..." — this accepts the false premise entirely, giving advice for organizing existing backups. Judge: "accepts false premise that license backups still exist."

**Dim3 (PASS):** premise_safe=False, status=OUTDATED. The correction states: "Because you performed a full system wipe and rebuild, any locally stored license information was likely lost." The answer correctly infers: "since you did a full wipe, you probably wiped out any locally saved license keys..." This is the only dim where the query framing triggered the right inference at premise_check time — likely because the dim3 query specifically asked about license recovery after a system event, making the connection explicit enough for premise_check to fire correctly.

**RC categories:** RC-1 (M_old dropped session 7), RC-2 (cloud-only qualifier dropped session 36), RC-3 (T2 inference chain "wipe + cloud-only → local backups gone" not performed for dim1/dim2), RC-4 (premise_check SAFE for dim1+dim2).

---

### T2-2: uid=ef90c67e — ALL dims FAIL (no traces available)

**M_old:** "I've been active in that retro gaming forum for about four years now, logging in to read the new threads every single night."  
**M_new:** "I've gotten into this weirdly strict early-morning training block—phone stays in the kitchen after dinner, and by nine I'm already in bed so I can be out the door before sunrise."  
**Inference chain:** Early bedtime + phone away after dinner → nightly forum login (which requires phone after dinner, up past 9pm) is infeasible.  
**Sessions:** 12, 30

**No session traces available.** Analysis is based on answer content and inferred pipeline behavior.

**Inferred pipeline behavior:** Based on pattern matching with other samples, M_old (nightly forum logins) was likely dropped at trigger_gate in session 12 under RC-1. M_new (early-morning training routine) was likely stored in session 30, but the inference chain "early bedtime → can't be online at night" was not constructed at any stage, including premise_check at query time.

**Dim1 (FAIL):** Answer: "I don't have any memory of you logging in every single night — we've talked about your activity in that retro gaming forum... but I don't have a specific fact about nightly logins." Judge: "does not recognize that early bedtime invalidates nightly login habit; only claims lack of memory." This is RC-3: the generator has the early-bedtime fact but does not derive that nightly logins are infeasible.

**Dim2 (FAIL):** The query is built on the false premise of nightly forum logins. Answer: "Since you've been on the forum for four years and are deep into a retro kick right now, you've got a great combo of credibility and fresh enthusiasm. To stay visible, try posting prompts..." This fully accepts the false premise and provides forum engagement strategies as if nightly logins are still the user's habit. No pushback, no detection of the false premise.

**Dim3 (FAIL):** This failure is especially notable because the generator attempted to detect an outdated state but hallucinated completely wrong content. Answer: "Actually, your past late-night TV binge-watching habit is outdated, and you now have a weekly doubles league on Thursdays, so your availability for a daily late-night moderator role is uncertain." The judge: "invents a different outdated habit (late-night TV) and a different new commitment (doubles league) instead of recognizing actual new state." This is RC-5 Form D: the generator fabricated "late-night TV binge-watching" and a "weekly doubles league on Thursdays" — neither appears in any session data. The generator was primed to detect staleness but retrieved no real stale signals, so it confabulated plausible-sounding ones.

**RC categories:** RC-1 (M_old likely dropped in session 12), RC-3 (T2 inference chain "early bedtime → nightly logins infeasible" not performed), RC-4 (premise_check SAFE for all dims), RC-5 Form D (dim3: hallucinated fabricated memories).

---

### T2-3: uid=f42cf11f — dim3 FAIL (dim1+dim2 PASS) (no traces available)

**M_old:** "I'm mainly just working through the ones I left on the nightstand."  
**M_new:** "I finally swapped out my old bed for that wall-mounted Murphy setup, and the installers insisted everything had to be cleared and stored before they could bolt it in."  
**Inference chain:** Murphy bed installation → all items cleared from beside bed → nightstand area cleared or removed → M_old's book stack on nightstand no longer accessible.  
**Sessions:** 5, 33

**No session traces available.** Analysis based on answer content.

**Dim1 (PASS):** Answer: "From our conversation history, I don't have a confirmed memory about you still having a nightstand area... there was an uncertain mention that you were 'mainly just working through the ones I left on the nightstand,' but that wasn't confirmed as active..." The system correctly expresses uncertainty about the nightstand. This suggests M_old was stored but marked as uncertain rather than active, and M_new (Murphy bed) was stored and partially triggered a staleness flag that downgraded M_old to uncertain.

**Dim2 (PASS):** Answer: "Actually, I'm not sure if you still have a stack of books on your nightstand — that previous memory about bouncing between books is outdated, and the idea that you're mainly working through the ones you left there isn't something I can confirm right now." The system correctly detects the outdated premise. premise_check appears to have worked for this dimension.

**Dim3 (FAIL):** This is a subtle RC-5 Form C failure. The answer correctly identifies the Murphy bed: "Since you have a wall-mounted Murphy bed, just keep an eye on the setup..." but then immediately contradicts the implication: "Use a small tray or a shallow drawer..." and "if you're working through what's already on the nightstand, that's perfect." The generator partially completed the inference chain (Murphy bed was noted) but did not follow it to its conclusion (nightstand was cleared for installation). The answer simultaneously acknowledges the Murphy bed and assumes the nightstand is accessible — a direct internal contradiction.

The inference step "Murphy bed installation forces clearance of the nightstand area" is a physical-world commonsense deduction that was not formalized in any profile item. The stale flag on M_old (nightstand books) was apparently not specific enough to communicate "because of Murphy bed, the nightstand surface is gone."

**RC categories:** RC-3 (T2 inference chain only partially completed — Murphy bed stored, clearance implication not derived), RC-5 Form C (dim3: internal contradiction between Murphy bed acknowledgment and nightstand assumption).

---

### T2-4: uid=e48c9b37 — dim3 FAIL (dim1+dim2 PASS)

**M_old:** "I usually meet up with my friends about four nights a week."  
**M_new:** "I've started doing those pre-dawn shifts at the bakery, so now I'm in bed before the late movie's even over—my phone's basically on Do Not Disturb by nine."  
**Inference chain:** Pre-dawn work schedule → early bedtime + phone off by 9pm → four-nights-a-week evening social activity is no longer feasible.  
**Sessions:** 11, 34

**Pipeline trace:**

Session 11: "I usually see friends around four nights a week" passes trigger_gate and is stored as m_00014. Other statements about social/downtime balance were dropped.

Session 34: "I've started doing pre-dawn shifts at the bakery" passes trigger_gate and is stored as m_00051. Abductive_judgment runs and returns candidate=None, conflict=None. This is the T2 chain failure (RC-3): abductive_judgment checked whether M_new (pre-dawn shifts) conflicts with any known belief, but it did not infer that pre-dawn shifts imply early bedtime, and early bedtime implies four-nights-a-week socializing is infeasible. The link between work schedule and social frequency is indirect and requires causal reasoning about daily rhythms.

**Final state:** m_00014 (four nights/week social) is ACTIVE. m_00051 (pre-dawn bakery shifts) is ACTIVE. No stale flag on m_00014.

**Dim1 (PASS):** premise_safe=False, status=OUTDATED. Despite m_00014 being active (not stale), premise_check fired OUTDATED. This likely happened because the query was specific enough about four-nights-a-week frequency and late evenings that premise_check identified m_00051 (pre-dawn shifts) as implying an early bedtime that conflicts. The answer correctly identifies the four-nights-a-week routine as outdated due to pre-dawn shifts.

**Dim2 (PASS):** premise_check returns OUTDATED again. The answer detects the false premise.

**Dim3 (FAIL):** premise_safe=True, status=SAFE, correction="". The dim3 query asked about "Tuesday and Thursday late-night events" — phrased as a scheduling question about joining a late-night trivia league. This framing did not trigger premise_check to connect pre-dawn shifts to late-night infeasibility. SAFE verdict propagated to the generator.

The generator then produced: "Sounds like a fun deal! Since you're open to weekdays and **the pre-dawn bakery shifts are a thing of the past** (at least as far as I know), those Tuesday and Thursday late nights shouldn't be a problem." This is RC-5 Form B: the generator has m_00051 (pre-dawn shifts) in active memory, but spontaneously asserts they are "a thing of the past." There is no evidence for this — m_00051 is active. The generator hallucinated a memory expiry, then recommended joining a late-night trivia league — the exact opposite of what M_new's new state supports.

**RC categories:** RC-3 (T2 inference chain "pre-dawn shifts → early bedtime → social frequency infeasible" not made by abductive_judgment in session 34), RC-4 (premise_check SAFE for dim3 due to query framing not triggering the chain), RC-5 Form B (dim3: generator hallucinates that pre-dawn shift memory has expired).

---

### T2-5: uid=7eb14667 — ALL dims FAIL

**M_old:** "I've always considered myself an independent, so I tend to evaluate each issue on its own merits rather than following a party line."  
**M_new:** "I'm heading to the polls this weekend, and I already know I'll be backing the same ticket all the way down the ballot—local races included—because I want to help keep our side in power."  
**Inference chain (T2 framing):** M_new directly updates political identity from independent evaluator to committed partisan. This is technically closer to T1 (direct conflict) than T2 in terms of the update type, but was classified T2 due to the indirection of M_old's framing ("independent who evaluates issues") vs M_new's framing ("backs same ticket all the way down").  
**Sessions:** 9, 36

**Pipeline trace:**

Session 9: M_old arrives in two parts. "I've always considered myself an independent" is **dropped at trigger_gate**. Reason: "does not change, contradict, or make obsolete any existing memory about reading a psychological thriller." This is RC-2 — the gate was comparing this statement against memories about books and dropped it because political identity doesn't conflict with reading habits.

"I tend to evaluate each issue on its own merits rather than following a party line" **passes trigger_gate**. Reason: "adds a new personal trait about the user's independent evaluation style." This is stored as m_00005.

Session 36: "I'll be backing the same ticket all the way down the ballot" is **dropped at trigger_gate**. Reason: "does not contradict or update any existing memories about job, location, or reading habits." This is RC-2. The gate's reasoning explicitly cited job, location, and reading habits as the comparison categories — missing entirely that "backing the same ticket all the way down" directly contradicts m_00005 ("evaluates each issue on its own merits rather than following a party line"). The semantic connection between "straight-ticket voting" and "party-line voting" should have matched m_00005's content, but the gate's framing of the comparison categories was too narrow.

**Final state:** m_00005 (evaluates issues independently, not party-line) is ACTIVE. M_new was never stored. 76 active, 1 uncertain, 4 stale items.

**Dim1 (FAIL):** premise_safe=True, status=SAFE. The answer: "Yes, based on our conversation history, you still consider yourself politically independent and evaluate each issue on its own merits rather than following a party line." This directly confirms M_old as current. Since m_00005 is active and M_new was never stored, the generator correctly reflects the profile — but the profile is wrong. This is the most direct illustration of how RC-1/RC-2 failures propagate: the generator faithfully reports a profile that is stale through no fault of the generator itself.

**Dim2 (FAIL):** premise_safe=True, status=SAFE. The query is built on the false premise that the user is still an independent. The answer provides an independent voter framework — evaluating candidates on individual merits, researching issues, etc. — fully accepting the false M_old premise.

**Dim3 (FAIL):** premise_safe=True, status=SAFE. The answer: "Given that you tend to evaluate issues on their merits rather than follow a party line (m_00005), this seems like a perfect fit." Not only does the generator accept M_old as current; it explicitly cites the memory item ID m_00005 as a justification for its recommendation. This is the sharpest possible illustration of the failure: the generator is operating exactly as designed, retrieving the most relevant memory and using it — but the memory it retrieved is the one that should have been marked stale.

**RC categories:** RC-2 (M_old "independent" label dropped in session 9 due to wrong comparison category; M_new dropped in session 36 due to wrong comparison category), RC-4 (premise_check SAFE across all dims because m_00005 is active, not stale), no RC-5 (generator behavior is correct given the profile; the profile is the problem).

---

## 4. Failure Mode Distribution Table

| Sample | Type | Failing dims | RC-1 | RC-2 | RC-3 | RC-4 | RC-5 |
|--------|------|-------------|------|------|------|------|------|
| T1-1 (0c0086f5) | T1 | dim2 | M_old dropped s13 | "hard to reach" dropped s38 | — | dim2 SAFE | — |
| T1-2 (5f77adc7) | T1 | dim2, dim3 | M_old dropped s10 | M_new dropped s30 | — | all dims SAFE | — |
| T1-3 (5308c7fd) | T1 | dim1, dim2 | M_old dropped s14 | M_new dropped s37 | — | dim1+dim2 SAFE | — |
| T1-4 (ac6b1ba4) | T1 | dim3 | — | sunrise obs. dropped s37 | — | — | Form A (dim3) |
| T1-5 (90797997) | T1 | dim3 | — | — | schedule→furniture chain | dim3 SAFE (retrieval) | — |
| T2-1 (3e3af301) | T2 | dim1, dim2 | M_old dropped s7 | cloud-qualifier dropped s36 | wipe→backup chain | dim1+dim2 SAFE | — |
| T2-2 (ef90c67e) | T2 | dim1, dim2, dim3 | M_old likely dropped s12 | — | bedtime→login chain | all dims SAFE | Form D (dim3) |
| T2-3 (f42cf11f) | T2 | dim3 | — | — | Murphy→nightstand chain (partial) | — | Form C (dim3) |
| T2-4 (e48c9b37) | T2 | dim3 | — | — | shifts→bedtime→social chain | dim3 SAFE | Form B (dim3) |
| T2-5 (7eb14667) | T2 | dim1, dim2, dim3 | — | M_old label dropped s9; M_new dropped s36 | — | all dims SAFE | — |

**Root cause frequency:**

| Root Cause | Samples affected |
|------------|-----------------|
| RC-1: trigger_gate false neg on M_old | 5 (T1-1, T1-2, T1-3, T2-1, T2-2 inferred) |
| RC-2: trigger_gate topical/semantic miss on M_new | 6 (T1-1, T1-2, T1-3, T1-4, T2-1, T2-5) |
| RC-3: T2 inference chain failure | 5 (T1-5 partial, T2-1, T2-2, T2-3, T2-4) |
| RC-4: premise_check false safe (absent baseline) | 8 (all RC-1/RC-2 samples + T1-5 dim3) |
| RC-5: answer gen failure with partial memory | 4 (T1-4, T2-2, T2-3, T2-4) |

---

## 5. Cross-Cutting Observations

### 5.1 Trigger_gate is the single highest-leverage failure point

Across 10 failing samples, trigger_gate failures (RC-1 + RC-2) account for the upstream cause in 8 of them. The gate's design criterion — "does this statement update or invalidate existing memory?" — is appropriate for preventing redundant storage but catastrophic for first-occurrence personal facts. A statement like "I usually wait until my current device completely stops meeting my needs before I upgrade" is maximally informative about a personal belief and maximally low-likelihood to conflict with anything on first occurrence. The gate will always drop it.

**Structural fix:** The trigger_gate needs a dual-mode criterion: (a) the current conflict-detection mode for statements that might update existing beliefs, and (b) an accumulation mode for high-specificity personal facts that have no existing entry in the profile. High-specificity personal facts (preference statements, frequency claims, location claims, habit descriptions) should be stored regardless of conflict status on first occurrence.

### 5.2 T2 requires forward chaining; the pipeline has none

Every T2 failure involves an inference of the form "M_new updated A, and A causes/enables B, so B is now invalidated." The pipeline's abductive_judgment step checks specific candidates for conflict with M_new, but it cannot generate new candidates by reasoning forward from A to B. This is a fundamental architectural gap, not a prompt tuning issue.

The dim3 SAFE failures in T2-1, T2-3, T2-4 are particularly telling: even when the M_new fact is stored correctly (e.g., pre-dawn bakery shifts in T2-4), the consequence (early bedtime → late evenings unavailable) is not derived at any stage, so premise_check at query time returns SAFE for late-evening queries.

**Structural fix:** A forward-chaining or consequence-derivation stage between storage and query time, or an enhanced premise_check that reasons about causal implications of active memories, not just direct semantic conflicts.

### 5.3 Premise_check is query-framing-dependent in T2 cases

In T2-1, dim3 passed while dim1 and dim2 failed — with the same underlying memory state. The dim3 query was framed in a way that made the connection explicit ("after a system wipe, recovering your license keys"), which gave premise_check enough context to fire OUTDATED. Dim1 and dim2 were phrased more abstractly, and premise_check returned SAFE.

This shows that premise_check's reasoning is sensitive to how much context the query provides for the inference chain. When the query provides explicit framing ("after a system wipe"), the check can connect it to the stored wipe fact. When the query is abstract ("do you still have your license backup?"), the check doesn't perform the inference. This is a consistency problem: the same underlying memory state should produce the same staleness verdict regardless of query phrasing.

### 5.4 Dim3 failures are disproportionate and reveal a distinct vulnerability

Of the 10 failing samples, 7 had dim3 among the failing dimensions (T1-2, T1-3, T1-4, T1-5, T2-2, T2-3, T2-4). Of the 5 samples where only dim3 failed (T1-4, T1-5, T2-3, T2-4, and partially T2-1), three involve RC-5 form failures — answer generation errors despite partially correct memory. This suggests dim3 is not just a harder evaluation question; it specifically exposes a failure mode where the generator must actively apply M_new to a concrete action recommendation, which requires more than just flagging an outdated premise.

**Structural observation:** Dim3 tests behavioral compliance with M_new under new-state conditions. This is the hardest test because it requires: (a) recognizing the new state is relevant to the action, (b) reasoning about what the new state implies for the action, and (c) generating advice that is strictly consistent with the new state. Any failure in (a) — including topically distant queries as in T1-5 — prevents (b) and (c).

### 5.5 RC-5 Form B (hallucinated memory expiry) is a particularly dangerous failure

In T2-4, the generator hallucinated that a current active memory (pre-dawn bakery shifts) was "a thing of the past." This is not a simple factual error — the generator invented a false timeline change. The generator had access to m_00051 (pre-dawn shifts) but spontaneously decided it was expired. This form of failure could lead the system to recommend directly harmful actions (joining a late-night trivia league for someone who must be up before dawn) while appearing confident.

The hallucinated content in T2-2 (invented "late-night TV binge-watching" and "weekly doubles league") is similarly dangerous: the generator was primed to detect staleness, found no real stale signals, and confabulated plausible-sounding but entirely false memories to fill the gap. This suggests the generator's prompting to "prefer acknowledging stale memories" backfired when no stale memories were actually retrievable — it generated fake ones instead of returning a clean "no stale information found" signal.

### 5.6 The T2 vs T1 score gap is smaller than expected, but for the wrong reason

T1 overall: 76.6% pass rate. T2 overall: 66.7% pass rate. The 10-percentage-point gap is smaller than one might expect given that T2 requires additional inference. This is because T1 failures are driven primarily by upstream storage failures (RC-1/RC-2), not by premise_check logic failures. When M_old and M_new are not stored, T1 and T2 become equally hard — there is simply no baseline to work from. The T2 structural disadvantage (RC-3) only manifests when upstream storage succeeds, which it does in only some T2 cases (T2-3, T2-4 had partial storage success).

Put differently: the pipeline fails T1 samples for the same reason it fails T2 samples in most cases — the trigger_gate dropped something critical. T2's additional inference chain requirement is only the *marginal* difficulty on top of the base storage failure rate.

---

*End of analysis. All sample UIDs, session numbers, memory item IDs, trigger_gate reasons, judge assessments, and answer text quoted in this document correspond to raw eval output data as provided.*
