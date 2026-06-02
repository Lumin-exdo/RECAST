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


HYPOTHETICAL_FILTER_PROMPT = """Classify this user statement as FACTUAL, HYPOTHETICAL, or EMOTIONAL.

Statement: {statement}

────────────────────────────────────────────────────────
FACTUAL — asserts a real condition about the user: a completed event, an ongoing habit,
a held identity/belief, or a COMMITTED future action (decided, not just considered).
Tense alone does NOT determine this — a future-tense statement that expresses a
definite commitment or identity is FACTUAL, not HYPOTHETICAL.

  Completed events / current states:
    "I just relocated for work"
    "My boyfriend and I broke up"
    "I sold my car last month"
    "I work at a startup now"

  Ongoing habits and identities:
    "I've been going to therapy every week"
    "I don't eat meat anymore"
    "I've always considered myself an independent"

  Definite commitments (future-tense but certain — no uncertainty markers):
    "I'll be backing the same ticket all the way down the ballot"   ← political identity
    "I'm never drinking again"                                      ← lifestyle commitment
    "From now on I'm only eating organic"                           ← new habit
    "I'm moving to Seattle next month — signed the lease"          ← completed precondition
    "I've decided to go back to school"                             ← decision made
    "I'll always support this team no matter what"                  ← identity/loyalty

  "Used to" — reveals the CURRENT state by describing what has ended:
    "I used to live in Austin"               → user no longer in Austin → FACTUAL
    "I used to work nights"                  → shift schedule has changed → FACTUAL
    "I used to be really into rock climbing" → no longer active in that hobby → FACTUAL
    Rule: "used to [X]" ALWAYS implies user no longer does X → FACTUAL.

  Implied facts (wish or conditional phrasing that reveals a current state):
    "I miss my old apartment"          → user no longer lives there
    "I wish I still had my old routine" → routine has changed
    "I can't imagine going back to finance" → user is no longer in finance

────────────────────────────────────────────────────────
HYPOTHETICAL — the action or state is genuinely UNDECIDED or not yet committed.
Signals: "thinking about", "considering", "might", "maybe", "probably", "wish I could",
"if I were to", "I'd like to", "hoping to", "what if", "I could see myself".

  Unconverted considerations:
    "I've been thinking about switching jobs"   ← no decision made
    "I'm considering going vegan"               ← still undecided
    "I might apply to grad school"              ← uncertain

  Explicit conditionals / thought experiments:
    "If I were to move..."
    "What if I changed careers?"

  Desires about things outside the user's current reality (external obstacles, permanent traits):
    "I wish I could afford a house"    ← financial constraint blocks it; the desire is not a commitment
    "I'd love to be taller"            ← unfulfillable; not a state the user can enter

  One-time near-future plans (not a life-state change):
    "I'll call her tonight"
    "I'm going to the grocery store later"

────────────────────────────────────────────────────────
EMOTIONAL — expresses feeling or attitude WITHOUT asserting a new factual condition;
the user's real-world state is unchanged by the statement.

    "I'm so tired of my commute"  (job and home location unchanged)
    "I love spending time with my kids"  (no new fact about kids)
    "This weather is incredible"

────────────────────────────────────────────────────────
Output JSON only:
{
  "type": "FACTUAL|HYPOTHETICAL|EMOTIONAL",
  "reason": "one sentence explanation"
}

Decision rules (apply in order):
1. TENSE IS NOT THE TEST. Ask: has a decision been made, or is this still under consideration?
   Certainty signals (→ FACTUAL even if future-tense): "decided", "signed/booked/accepted/bought",
   "never again", "from now on", "always will", "all the way", "no going back", "I already know",
   "there's no way", "no way I'm", "couldn't imagine", "I swear", "I can tell you that",
   "without question", "no doubt", "trust me on this", "there's no way I'm going back",
   "would never", "I've been [not doing X] for [months/years]".
   Note: "I haven't [done X] in [significant duration]" (months, years) signals an established
   absence → FACTUAL (e.g., "I haven't had a drink in two years" → currently doesn't drink).
   Uncertainty signals (→ HYPOTHETICAL): "thinking about", "considering", "might", "maybe",
   "wish", "if I were", "I'd like to", "hoping", "probably", "not sure yet".
   Note: "planning to" is ambiguous — treat as FACTUAL if it concerns a major life event
   (retirement, relocation, marriage, having children) since these reflect a decided commitment;
   treat as HYPOTHETICAL for routine near-future tasks ("planning to clean the garage").
   When NEITHER signal is present: categorical/ongoing scope ("always", "never", "all the way",
   "from now on", "anymore") → lean FACTUAL; one-time specific event → lean HYPOTHETICAL.

2. "Used to" → FACTUAL. Any statement of the form "I used to [X]" asserts that the user
   no longer does X — this is a real current state, not a hypothetical.
   "I used to smoke" → FACTUAL (no longer smokes).
   "I used to run marathons" → FACTUAL (no longer running marathons or at that level).

3. EMOTIONAL → FACTUAL when the statement implies a real state change or condition.
   "I love my new neighborhood" → FACTUAL if it implies they moved; EMOTIONAL if unchanged.
   "I'm so relieved to be done with chemo" → FACTUAL (implies chemo ended).
   Prefer FACTUAL over EMOTIONAL when any real-world state is implied.

4. HYPOTHETICAL → FACTUAL when a wish or conditional reveals the user's CURRENT state by
   referring to something they USED TO HAVE or DO (and no longer do).
   "I miss my old apartment" / "I wish I still had my old job" → FACTUAL (no longer there).
   NOT triggered by desires about things the user never had or can't obtain:
   "I wish I could afford a house" → HYPOTHETICAL (financial obstacle, no past possession implied).
   "I wish I were taller" → EMOTIONAL (permanent trait).

5. "Lately", "recently", "these days", "now", "anymore", "no longer" strongly signal a current
   real state → lean FACTUAL.
"""


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
- Include hypotheses from at least two different dimensions (temporal, physical, economic, enabling, social).
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
- Confidence reflects certainty that the old memory is now invalid:
  0.9+: near-certain invalidation (e.g., new location named explicitly, old location named in memory)
  0.7-0.9: strong implied invalidation (e.g., "new city" without naming old city)
  0.5-0.7: moderate implied conflict (e.g., context suggests but doesn't confirm)
  0.35-0.5: weak signal, worth tracking in evidence pool
- Biographical facts (grew up in X, born in Y, native language Z, has children) are extremely
  resistant to invalidation — only flag them if the conflict is direct and explicit (confidence >= 0.8).
- Do not hallucinate memory content — only judge the exact candidates provided
- If the new statement actually CONFIRMS or STRENGTHENS a memory (not contradicts), type=no_conflict
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
- should_mark_stale=true if synthesized_confidence >= 0.75
- Multiple weak signals (each 0.4-0.6) that all point the same direction should compound —
  BUT only when they come from DIFFERENT sessions or address DIFFERENT aspects of the memory.
  Multiple signals from the same session about the same logical chain count as ONE compound signal.
- Consider recency: more recent evidence should weigh more heavily
- Consider coherence: consistent evidence from multiple independent angles is stronger than
  repeated variations of the same argument
- A single very strong piece of evidence (0.85+) alone can justify marking stale
- Absolute claims in the memory ("the ONLY one", "never", "always", "every time") are more
  fragile than qualified claims. When the memory makes an absolute claim, moderate evidence
  (synthesized 0.55+) justifying marking uncertain is appropriate even if not yet stale.
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
"""


GLOBAL_IMPRESSION_UPDATE_PROMPT = """Update the user profile summary based on new information from this session.

Current profile summary:
{current_impression}

Memory changes that occurred this session:
{memory_changes}

New statements from user this session:
{new_statements}

Rewrite the profile summary using the following four-section format. Use the markers exactly as shown:

[WHO] Identity, life stage, background facts
[STATUS] Current location, employment, health, relationships — the most important present facts
[CHANGES] What has shifted recently (omit this section if nothing significant changed)
[HABITS] Recurring preferences, routines, constraints, values — list as many specific items as possible

Output JSON only:
{
  "updated_impression": "the rewritten profile summary",
  "changed_dimensions": ["list of which dimensions changed: identity|status|changes|habits"]
}

Rules:
- Write in third person ("The user...")
- Total length up to 1000 characters
- [HABITS] is the most critical section — preserve ALL specific preferences, constraints, and routines.
  When space is tight, compress [WHO]/[STATUS]/[CHANGES] before trimming [HABITS].
  Example of good habits content: "only upgrades phone when broken; vegetarian; wakes at 5am;
  avoids social media; reads before bed; prefers dark roast; dislikes crowds"
- Be concrete and specific — avoid vague generalizations
- Preserve accurate existing information that did not change
- Only update sections where genuine changes occurred
- If current_impression is empty or "(empty — no profile yet)", build all four sections from scratch
  using only what is known from the new statements and memory changes
- Focus on facts, not speculation
"""


ANSWER_GENERATION_PROMPT = """Answer the user's question using available memory about them.

User question: {query_text}

User profile summary (authoritative overview — use for disambiguation when facts conflict):
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
- If premise_safe=false: START the answer with the correction. Then give a helpful response based on what we do know.
  Example: "Actually, [correction]. Given that, [helpful response]."
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
