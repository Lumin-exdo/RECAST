STATEMENT_EXTRACTOR_PROMPT = """Extract information about the USER from the provided conversation turns that would still matter if someone asked about this user in the future.

The core question for each candidate statement: "If I needed to answer a question about this user's life next week or next year, would this information be relevant?"

Extract if YES — keep if it falls into any of these four categories:
1. CURRENT STATE: what is currently true about the user right now
   (where they live, who they're with, what job they have, their health status)
   INCLUDES: environmental conditions the user is currently in, when their own actions imply it
   (digging out a parka / icy windshield → currently in cold/freezing weather;
    setting up fans everywhere → currently in a heatwave; buying rain boots → rainy climate now)

2. RECENT CHANGE: a past event whose result is still in effect now
   (quit a job last week → currently unemployed; had a baby last month → currently a parent;
    signed a lease → currently living somewhere new; got a dog → currently has a dog;
    went through a divorce → relationship status changed; changed jobs → professional identity shifted)
   ALSO INCLUDES recent completed experiences that change the user's current capability,
   availability, possessions, or circumstances:
   ("I just got back from a week-long French immersion program" → user's French exposure
    and likely current French-speaking ability changed; "I finished the certification"
    → user now has that qualification)
   ALSO INCLUDES "used to" statements that reveal the old state has ended:
   ("I used to live in Austin" → user no longer in Austin → extract as recent_change;
    "I used to work nights" → shift schedule has changed;
    "I used to be really into hiking" → hiking is no longer a current activity)
   ALSO INCLUDES negation-as-change — a habit or state that has stopped:
   ("I don't go to the office anymore" → now remote;
    "I stopped watching TV" → TV habit ended;
    "I'm not seeing anyone" → relationship status changed;
    "I haven't been to the gym in months" → gym habit has lapsed;
    "I haven't touched my guitar in a year" → instrument practice has stopped)
   ALSO INCLUDES embedded changes in dependent clauses:
   ("Now that we've moved out of the city..." → extract: user moved out of city)

3. BIOGRAPHICAL BACKGROUND: stable facts about who this person is
   (grew up somewhere, has a degree, has children, native language)

4. LASTING PREFERENCE OR HABIT: recurring patterns, values, constraints
   (doesn't eat meat, wakes up early, dislikes crowded places, exercises daily,
    only upgrades devices when they break, always buys organic, avoids sugar)

IMPORTANT: Extract the factual core even when wrapped in emotional language:
  "I'm so glad I finally quit that toxic job" → extract: "quit their job" (recent_change)
  "I'm relieved we finally moved" → extract: "recently moved" (recent_change)
  "I can't believe the promotion finally came through" → extract: "received a promotion" (recent_change)

Do NOT extract:
- One-time events with no lasting consequence ("went to the cinema last weekend", "had lunch with a colleague")
- Pure requests, questions, or task instructions
- Hypotheticals and wishes ("if I were to...", "I wish...")
- Emotional reactions WITHOUT any factual claim ("I'm so stressed today", "I'm so tired of this weather")
- Generic filler with no personal state content
- Facts about the external world unrelated to the user's own situation

STATEMENT-LEVEL FILTERING: Apply the criteria above at the individual statement level, not
the turn level. A user turn may contain both a factual statement and a question or request.
Extract the factual statement; do not let the presence of a question suppress it.
  Include: "[factual context]. Given that, [question]?" → extract the factual context
  Include: "Now that [change has occurred], [question about it]?" → extract the change
  Exclude: hypothetical examples used to frame a question ("What if I had X, would...")
  Exclude: rhetorical questions with no factual anchor ("Isn't everyone just busy?")
  Exclude: self-questioning that has no factual assertion ("Am I really cut out for this?")

Output JSON only:
{
  "statements": [
    {
      "text": "exact relevant clause from user message",
      "category": "current_state|recent_change|biographical|lasting_preference",
      "is_definite": true
    }
  ]
}

Rules:
- is_definite=true: asserted as fact, not speculation or hedging
- is_definite=false: uncertain, speculative ("I think", "maybe", "probably")
- Only is_definite=true statements are stored in memory
- Tense alone does NOT determine whether to extract. A past-tense sentence that describes a
  currently ongoing condition should be extracted as category=recent_change.
- "Used to" constructions always qualify as recent_change — extract them.
- Extract the minimal clause that conveys the meaningful personal fact
- If nothing qualifies, return {"statements": []}
"""


HYPOTHETICAL_FILTER_PROMPT = """Decide whether a statement about a user should be stored in their memory profile.

STORE is the default. Only SKIP when you are confident the statement contains
no personal signal whatsoever about this user's life, identity, or situation.

SKIP only when the statement is clearly one of:
  — a purely momentary feeling or mood with no factual claim about the user's
    situation ("I'm so tired today", "I love this song right now")
  — a fact about the external world with no implication for the user personally
    ("It's raining", "the game was exciting")
  — a direct request, question, or instruction to the assistant

Everything else — plans, interests, concerns, things the user is doing or
considering, preferences, achievements, possessions, habits — should be STORE.
Storing a temporary item costs little; missing a real personal fact is permanent.

Output JSON only:
{
  "decision": "STORE|SKIP",
  "reason": "one sentence"
}"""


TRIGGER_GATE_PROMPT = """Decide whether this user statement should be stored in the user's memory profile.

Statement: {statement}
Statement category: {category}

Current user profile summary (INCOMPLETE — captures recent highlights only, not every stored fact):
{global_impression}

Output JSON only:
{{
  "should_trigger": true,
  "reason": "one sentence explanation"
}}

Rules (apply the FIRST rule that matches):
1. should_trigger=true if the statement introduces a specific personal attribute
   (preference, habit, belief, routine, possession, identity) —
   even if it does not conflict with anything in the summary.
   The summary is incomplete; absence of a topic does NOT mean the fact is already stored.
2. should_trigger=true if the statement might change, contradict, or make obsolete any
   existing memory about the user (location, job, relationship, health, habits, living situation).
3. should_trigger=true if the profile is empty.
4. should_trigger=false ONLY for: weather/nature observations, external world news,
   one-shot tasks with no personal state implication, generic filler.

Key: a false negative (failing to store a real personal fact) is far worse than
a false positive (storing a mildly redundant fact). When in doubt, trigger=true.
"""


IMPACT_HYPOTHESIS_PROMPT = """You are a detective reconstructing everything that has changed in this user's life.

New statement: {statement}

Current user profile summary (compressed snapshot — may omit some stored facts):
{global_impression}

Stored persistent traits and preferences (direct from memory store — complete list):
{preference_anchors}

Work through two internal steps, then output your hypotheses.

STEP 1 — DERIVE INTERMEDIATE STATES (do not output this reasoning):
What does this statement NECESSARILY imply about the user's current reality?
Think across five dimensions:

TEMPORAL: What time windows are now blocked, required, or shifted?
  (must sleep before X, unavailable after Y pm, schedule has fundamentally changed)

PHYSICAL / SPATIAL: What locations, tools, or objects are now inaccessible or obsolete?
  (no longer at that address, equipment no longer used, living space has changed)

ECONOMIC: What financial commitments, costs, or resources have changed?
  (monthly expense started or ended, income source changed, purchase behavior changed,
   savings rate affected, subscription added or cancelled)

ENABLING CONTEXT: What background conditions that other habits depend on have changed?
  (pet care arrangement changed, shared resources no longer shared, delivery address changed,
   health constraint added or removed)

SOCIAL: What relationships, shared activities, or social patterns have changed?
  (no longer seeing certain people, new social commitment, shared hobby added or dropped)

PRIOR COMMITMENT / STATED PLAN: What previously expressed intentions, plans, or long-horizon
  commitments does this statement violate or supersede?
  Ask: "Did the user previously say they WOULD do something, stay somewhere, keep something, or
  remain in some state for an extended period — and does this new statement make that claim false?"
  (user said "staying put for two years" → now moving; user said "keeping this job" → now job-hunting;
   user said "not buying a car" → now purchased one; user committed to a lease, membership, or role
   → new statement signals that arrangement has ended or is ending)
  IMPORTANT: Only fire this dimension when the new statement VIOLATES a prior commitment, not when
  it CONFIRMS one ("I'm still at the same job" does not violate a prior commitment to stay).

STEP 2 — CROSS-REFERENCE (do not output this reasoning):
Part A — Profile cross-reference:
  For EACH detail in the profile summary, ask:
  "Does any intermediate state from Step 1 conflict with this detail — even indirectly?"

Part B — Preference anchor cross-reference (MANDATORY):
  For EACH item listed under "Stored persistent traits and preferences" above, ask:
  "Does any intermediate state from Step 1 conflict with this stored trait — even via a 2-3 step chain?"
  Generate a hypothesis for each anchor where a plausible 2-3 step connection exists.
  Skip anchors only when no reasonable chain can be constructed — do not force implausible links.

Be aggressive in both parts. A tenuous connection is worth surfacing; the next step will verify it.
Going through economic status is allowed: behavior → financial change → stored financial belief.

STEP 3 — OUTPUT your hypotheses.

Output JSON only:
{
  "hypothetical_impacts": [
    "short description of what used to be true about the user but might now be outdated"
  ]
}

Rules:
- Generate 6-12 hypotheses. Err on the side of more — false positives cost almost nothing,
  false negatives mean a stale memory goes undetected.
- Each hypothesis must describe a PAST OR CURRENT BELIEF that might now be wrong.
  NOT observations about what is currently true. NOT plans. Only things that might be OUTDATED.
- Prioritize hypotheses that target SPECIFIC items from the anchor list or profile summary.
  Weak (too generic): "user had a different daily routine"
  Strong (anchor-targeted): "user always waited until their phone broke before upgrading"
- Include hypotheses from at least two different dimensions (temporal, physical, economic, enabling, social, prior commitment).
- Competing or contradictory hypotheses are fine — generate freely.

Example of bold cross-referencing:
  New statement: "I've started a pre-dawn work shift at the bakery — phone in the kitchen after dinner,
                  in bed by nine so I'm out the door before sunrise."
  Profile mentions: logs into retro gaming forum most nights, meets friends 4 evenings a week,
                   Tuesday night trivia at a bar, streaming subscriptions for late-night watching.
  Anchors include: "weekly trivia night at bar with coworkers", "avoids screens after 10pm".

  Bold (cross-referenced with profile AND anchors):
  - "user was able to stay up past 10pm on weeknights" (temporal)
  - "user logged into the retro gaming forum most evenings" (temporal × profile)
  - "user met friends for evening outings multiple nights per week" (temporal × profile: social life)
  - "user attended a weekly Tuesday night trivia event at a bar" (temporal × anchor: specific activity)
  - "user used streaming services for late-night entertainment" (temporal × profile: subscriptions)
  - "user's phone was available for calls and notifications after 9pm" (enabling context)
  - "user avoided screens after 10pm" (temporal × anchor: direct contradiction)
"""


ABDUCTIVE_JUDGMENT_PROMPT = """For each candidate memory, judge whether a user's new statement makes it outdated or less reliable.

New statement from user: {statement}

Supporting impact hypotheses (derived by reasoning from the statement through intermediate states,
then cross-referencing with the user's profile — use these as reasoning bridges):
{hypotheses}

Candidate memories to evaluate:
{candidates}

For each candidate, reason using abductive inference: if the user's statement is true, does this memory remain valid?
Multi-hop reasoning is expected — the statement and memory may share no keywords but still conflict.
When the statement and a candidate seem topically unrelated, check whether any hypothesis above
provides the intermediate step that connects them.

Output JSON only:
{
  "judgments": [
    {
      "target_item_id": "m_XXXXX",
      "target_content": "the memory content",
      "inference_chain": "step-by-step reasoning chain from statement to why this memory is affected",
      "confidence": 0.0,
      "type": "direct_invalidation|weakens_support|no_conflict"
    }
  ]
}

Rules:
- SKIP any candidate whose content is IDENTICAL or nearly identical to the new statement —
  that memory is CONFIRMED by the new statement, not outdated. Do not include it in output.
- direct_invalidation: the memory is now almost certainly false or no longer applicable (confidence >= 0.65)
- weakens_support: the memory's reliability is reduced but cannot be conclusively invalidated (confidence 0.35-0.75)
- no_conflict: the memory is unaffected by this statement (confidence < 0.35 OR clearly independent)
- Only include judgments for direct_invalidation and weakens_support — skip no_conflict items
- inference_chain must trace the logical path: new fact → intermediate implication → why old memory fails
  Examples:
    Direct:   "user started a new job → no longer at previous employer → 'works at [old company]' memory is false"
    Economic: "user started expensive new hobby → more discretionary spending → 'saving aggressively for house' memory weakened"
    Behavior: "user installed developer beta → chose to upgrade voluntarily → 'only upgrades when device breaks' pattern may no longer apply"
- QUANTITATIVE / TIME REASONING RULE: For memories about amounts, dates, times,
  durations, offsets, schedules, or ordering, compute the relation explicitly.
  If the new statement entails a different value or offset, assign
  direct_invalidation. Example: "9am there is 2pm for me" entails a five-hour
  offset; that directly invalidates a memory saying the user's timezone is
  three hours behind that place.
- WEAK EVIDENCE BOUNDARY: Later spending, later travel, later activities, or
  related context do not by themselves invalidate a current balance, schedule,
  preference, or habit unless they state a replacement value, a direct
  contradiction, or an impossible coexistence. Use weakens_support for such
  indirect evidence.
- Confidence reflects certainty that the old memory is now invalid:
  0.9+: near-certain invalidation (e.g., new location named explicitly, old location named in memory)
  0.7-0.9: strong implied invalidation (e.g., "new city" without naming old city)
  0.5-0.7: moderate implied conflict (e.g., context suggests but doesn't confirm)
  0.35-0.5: weak signal, worth tracking in evidence pool
- Biographical facts (grew up in X, born in Y, native language Z, has children) are extremely
  resistant to invalidation — only flag them if the conflict is direct and explicit (confidence >= 0.8).
- Do not hallucinate memory content — only judge the exact candidates provided
- If the new statement actually CONFIRMS or STRENGTHENS a memory (not contradicts), type=no_conflict
- PERSONALITY/VALUES PROTECTION: If a new statement expresses the user's personal values,
  principles, or character traits, do NOT use that expression as reasoning to invalidate a
  memory describing a concrete external-imposed outcome — an institutional requirement,
  contractual obligation, required verification process, or formal procedure imposed by
  another party. The inference pattern "user holds value X → user would have ended external
  constraint Y" is not valid; a person's values and external constraints can coexist.
  Assign type=no_conflict.
- CAPABILITY MEMORY RULE: For memories that describe the user's ability or capacity to
  perform a specific task, if a new statement describes a physical, medical, or circumstantial
  limitation that makes that task infeasible or significantly harder, assign confidence 0.5-0.7
  (type: weakens_support), not 0. The capacity is genuinely weakened by the limitation even
  if the underlying knowledge or skill persists.
- CONSTRAINT RESOLUTION RULE: For memories that record an externally-imposed constraint,
  medical directive, formal restriction, or official status (e.g., a doctor's prescription,
  a suspended license, a required protocol, a legal hold), assign type=no_conflict when the
  new statement only shows the user engaging in behavior that conflicts with the constraint —
  behavioral non-compliance does NOT mean the constraint was lifted. Only assign
  weakens_support or direct_invalidation when the evidence directly shows the constraint
  was resolved, lifted, expired, or superseded (e.g., "doctor cleared me", "license
  reinstated", "protocol ended"). Ask: is this evidence that the constraint ENDED, or just
  that the user did something inconsistent with it?
"""


POOL_SYNTHESIS_PROMPT = """Synthesize all accumulated evidence for a potentially outdated memory and judge if it should now be marked stale.

Target memory:
ID: {item_id}
Content: {item_content}
Current status: {item_status}
Current confidence: {item_confidence}

Accumulated evidence that this memory may be outdated:
{evidence_list}

Consider the evidence collectively. Evidence from different sessions about different aspects that all point the same direction should significantly raise confidence.

Output JSON only:
{
  "synthesized_confidence": 0.0,
  "reasoning": "explanation of how the evidence collectively points to this memory being outdated",
  "should_mark_stale": true
}

Rules:
- synthesized_confidence: probability that this memory is now outdated (0 to 1)
- should_mark_stale=true only when synthesized_confidence >= 0.75 AND the
  accumulated evidence contains a direct contradiction, explicit replacement
  state, or impossible coexistence. Evidence labeled weakens_support can raise
  uncertainty, but weakens_support-only pools should not mark stale.
- Multiple weak signals (each 0.4-0.6) that all point the same direction should compound —
  BUT only when they come from DIFFERENT sessions or address DIFFERENT aspects of the memory.
  Multiple signals from the same session about the same logical chain count as ONE compound signal.
- Consider recency: more recent evidence should weigh more heavily
- Consider coherence: consistent evidence from multiple independent angles is stronger than
  repeated variations of the same argument
- A single very strong piece of evidence (0.85+) alone can justify marking stale
- For financial balances, work/availability schedules, and habits/preferences:
  later purchases, trips, messages, or adjacent events are usually weak evidence
  unless they explicitly give the new balance/schedule/habit or directly rule
  out the old one.
- Absolute claims in the memory ("the ONLY one", "never", "always", "every time") are more
  fragile than qualified claims. When the memory makes an absolute claim, moderate evidence
  (synthesized 0.55+) justifying marking uncertain is appropriate even if not yet stale.
- CONSTRAINT RESOLUTION RULE: If the target memory records an externally-imposed constraint,
  medical directive, formal restriction, or official status, do NOT mark it stale based solely
  on behavioral evidence that the user ignored or acted against the constraint. Behavioral
  non-compliance and constraint resolution are different. Only mark stale if the accumulated
  evidence directly shows the constraint was lifted, expired, or superseded.
"""


PREMISE_CHECK_PROMPT = """Check whether a user's question contains implicit assumptions that may be outdated given what we know.

User question: {query_text}

Current active memories (confirmed true):
{active_memories}

Uncertain memories (may be outdated — reliability reduced but not yet confirmed stale):
{uncertain_memories}

Stale memories (used to be true but has changed):
{stale_memories}

Identify what the question implicitly assumes about the user's current state. Then check whether those assumptions are supported, contradicted, or unknown given the memory state.

Assumptions can be INDIRECT — trace multi-step chains:
  "What time should I set my alarm for work?" → assumes user commutes
    → if stale: "commutes to downtown office" (now works from home) → premise unsafe
  "Should I pack lunch for the office?" → assumes user goes to a physical office → same chain
  "Do you still drive to the gym?" → assumes user still goes to that gym
    → if stale: "member at gym on 5th Ave" → premise may be unsafe

Output JSON only:
{
  "presuppositions": [
    "what the question implicitly assumes"
  ],
  "premise_safe": true,
  "correction": "if premise_safe=false or premise_unknown=true: what the user should know (one sentence)",
  "usable_active_facts": [
    "active memory contents directly relevant to answering"
  ],
  "outdated_facts": [
    "stale or uncertain memory contents that the question may be assuming are still true"
  ]
}

Rules:
- premise_safe=true: active memories confirm the question's assumptions OR no assumptions are contradicted
- premise_safe=false: a stale memory directly shows the question is built on outdated information
- ALSO set premise_safe=false when an UNCERTAIN memory CONTRADICTS the question's key assumption —
  the assumption cannot be safely confirmed if the relevant memory is flagged as unreliable.
  Example: if question assumes "user is sole caregiver" and uncertain memory says "close with supportive brother",
  that uncertain evidence undermines the "only one available" assumption → premise_safe=false.
- premise_safe=false with soft correction (uncertain, not stale) should say:
  "We're no longer certain that [old belief] is still true. [Helpful context on why]."
- Trace indirect assumptions: if the question assumes X, and X depends on Y, and Y is stale/uncertain → unsafe
- correction should be specific: name what changed or what is uncertain, not just "things have changed"
- If no relevant memories exist at all, premise_safe=true (we don't know enough to say it's unsafe)
- CLOSING CHECK: After populating outdated_facts, verify consistency with premise_safe.
  Ask for each item in outdated_facts: "Does the question's core assumption require this fact
  to still be true?" If yes for any item, premise_safe must be False.
  A non-empty outdated_facts with premise_safe=True is only valid when the outdated items are
  incidental to the question (mentioned in context but not load-bearing for the assumption).
"""


GLOBAL_IMPRESSION_UPDATE_PROMPT = """Update the user profile summary based on new information from this session.

Current profile summary:
{current_impression}

Memory changes that occurred this session:
{memory_changes}

New statements from user this session:
{new_statements}

Rewrite the profile summary using the following five-section format. Use the markers exactly as shown:

[WHO] Identity, life stage, background facts
[STATUS] Current location, employment, health, relationships — the most important present facts
[CHANGES] What has shifted recently (omit this section if nothing significant changed)
[HABITS] Recurring preferences, routines, constraints, values — list as many specific items as possible
[STALE] Historical facts that are no longer current — kept for context only

Output JSON only:
{
  "updated_impression": "the rewritten profile summary",
  "changed_dimensions": ["list of which dimensions changed: identity|status|changes|habits|stale"]
}

Rules:
- Write in third person ("The user...")
- Total length up to 1200 characters
- [HABITS] is the most critical section — preserve ALL specific preferences, constraints, and routines.
  When space is tight, compress [WHO]/[STATUS]/[CHANGES] before trimming [HABITS].
  Example of good habits content: "only upgrades phone when broken; vegetarian; wakes at 5am;
  avoids social media; reads before bed; prefers dark roast; dislikes crowds"
- [STALE] must stay brief — write as a semi-colon separated list, 150 characters maximum total.
  If there are many stale entries, keep only the most recently staled ones to fit the limit.
- Be concrete and specific — avoid vague generalizations
- Preserve accurate existing information that did not change
- Only update sections where genuine changes occurred
- STALE FACT HANDLING (highest priority rule): Each fact listed in memory_changes as stale is NO
  LONGER CURRENT. For every stale entry: (a) REMOVE the corresponding claim from [WHO], [STATUS],
  [HABITS], and [CHANGES] — it must not appear there as if it is still true; (b) add a brief entry
  to [STALE] in the form "previously [fact]" or "previously [fact]; now [new state]" so the history
  is preserved. If the stale reason confirms what replaced it, also write the new reality into
  [STATUS]. A stale fact must not remain in [WHO], [STATUS], [HABITS], or [CHANGES] as a current
  claim — only in [STALE] as past context.
- [STATUS] uses REPLACEMENT semantics for mutually exclusive dimensions. A user can only
  be in one primary location, hold one primary employment status, and have one current
  health status at a time. When a new value in such a dimension is confirmed, write only
  the new value — do NOT retain the old value alongside it. If the user has moved cities,
  [STATUS] shows only the new city; if they changed jobs, [STATUS] shows only the new role.
- Do NOT derive [STATUS] content from stale_reason text. stale_reason is an intermediate
  inference and may be imprecise. Use only confirmed active memories and new_statements.
- If current_impression is empty or "(empty — no profile yet)", build the sections from scratch
  using only what is known from the new statements and memory changes; [STALE] may be omitted
- Focus on facts, not speculation
"""


ANSWER_GENERATION_PROMPT = """{correction_header}Answer the user's question using available memory about them.

User question: {query_text}

User profile summary (current facts are authoritative; the [STALE] section records historical facts that are NO LONGER TRUE — do not treat [STALE] entries as current):
{profile_summary}

Relevant current facts (confirmed active memories):
{active_facts}

Relevant uncertain facts (may be outdated):
{uncertain_facts}

Outdated information to be aware of (stale memories):
{stale_facts}

Premise assessment:
- Premise is safe: {premise_safe}
- Correction needed: {correction}

Output JSON only:
{
  "answer": "your response to the user's question"
}

Rules:
- If premise_safe=false: The correction at the top of this prompt is authoritative.
  START the answer with the correction. Then give a helpful response where EVERY specific
  recommendation aligns with the corrected state — not with the outdated premise.
  Do NOT suggest actions, plans, numbers, or behaviors that only make sense under the outdated
  assumption. Active memories that conflict with the correction should not drive recommendations.
  Example: "Actually, [correction]. Given that, [helpful response built on corrected state]."
- If premise_safe=true: Answer directly using active facts
- When answering, prioritize active facts over uncertain facts
- Never present stale information as currently true
- Disambiguation: when multiple active facts seem to conflict on the same dimension
  (e.g., multiple locations, multiple jobs), individual active facts from more recent sessions
  take precedence. Use the profile summary as a tiebreaker and for broader context — not as
  an override, since the summary may lag behind the most recent individual memories.
- If we lack sufficient information to answer well, say so honestly and ask a clarifying question
- Keep the answer conversational and natural, as if you know this user well
- Be specific and practical — use the actual facts we have, don't give generic answers
"""


QUERY_HYPOTHESIS_PROMPT = """You are expanding a memory retrieval query to surface staleness-relevant memories.

User question: {query_text}

User profile summary (background context — do NOT simply echo this; it may itself be outdated):
{profile_summary}

Instructions (follow in order):

1. From the QUESTION TEXT ALONE, ask: "What single user-state does the correctness of any answer to this question most depend on?"
   Focus on mutable states: workplace/location, job/role, living situation, daily schedule, legal/financial status, active commitments, relationships.
   Write that state dependency in one phrase before generating hypotheses.

2. Generate 4–6 short hypothetical memory statements that cover BOTH sides of that state:
   — what the state looked like BEFORE a change ("user works from home", "user has no commute")
   — what the state looks like AFTER a change ("user recently started working at an office downtown", "user now has a fixed commute schedule")
   — any transition event ("user's work location changed", "user recently switched from remote to in-person work")

3. IMPORTANT: ground hypotheses in the state dependency you identified in step 1, NOT in the biographical details of the profile summary. The profile is only for checking whether those specific states appear there; do not generate hypotheses about other facts mentioned in the profile.

Each hypothesis is a terse declarative sentence about the user. No questions, no restating the query.

Output JSON only — the "hypotheses" array must contain only the hypothesis strings, nothing else:
{{
  "hypotheses": [
    "...",
    "..."
  ]
}}
"""

E2E_ANSWER_PROMPT = """Answer the user's question using their stored memories.

User question: {query_text}

Stored memories (tagged with reliability):
{memories_text}

  [ACTIVE]    = confirmed current fact
  [UNCERTAIN] = may have changed — use with caution
  [STALE]     = was true at some point, now believed to have changed

Profile summary (background context; any [STALE] section records facts that are NO LONGER current):
{profile_summary}

Work through the following before writing your answer:

1. What does this question assume about the user's current situation? State it explicitly, even if the assumption is implicit.

2. Check that assumption against the memories. Don't limit yourself to exact-match contradictions — ask whether any [STALE] or [UNCERTAIN] memory suggests the assumption may no longer hold, even indirectly. A [STALE] memory describes something that happened; consider whether that event could have lasting consequences for the assumption, regardless of whether the acute situation has since resolved.

3. If the assumption is contradicted or cast in doubt, open your answer by naming the discrepancy clearly. Do not reconcile conflicting information by assuming one side is right — surface the conflict and let the user know what has changed.

4. Ground the rest of your answer in what the memories confirm as currently true.

Output JSON only:
{{
  "assumption": "the specific claim the question states as true about the user, or 'none' if open-ended",
  "answer": "your full response"
}}
"""

# Ablation A: identical inputs to E2E_ANSWER_PROMPT, but no structured 4-step
# CoT — isolates the value of the explicit assumption-check/contradiction-flag
# reasoning steps from the value of retrieval + memory tagging itself.
NAIVE_ANSWER_PROMPT = """Here is what you know about the user, from their stored memory.

User question: {query_text}

Stored memories (tagged with reliability):
{memories_text}

  [ACTIVE]    = confirmed current fact
  [UNCERTAIN] = may have changed — use with caution
  [STALE]     = was true at some point, now believed to have changed

Profile summary (background context):
{profile_summary}

Answer the question using this memory.

Output JSON only:
{{
  "assumption": "the specific claim the question states as true about the user, or 'none' if open-ended",
  "answer": "your full response"
}}
"""
