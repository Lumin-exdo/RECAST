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

BEHAVIORAL PATTERNS: When the statement describes a persistent pattern of how OTHERS
behave toward the user, extract the BEHAVIORAL FACT — what they concretely do — not
the user's emotional reaction.

  "Whenever I bring up my side project at the neighborhood meetups, everyone quickly
   changes the subject"
  → Extract: "social group consistently avoids engaging with user's side project
    at neighborhood gatherings" (current_state)
  NOT: "user feels rejected about side project"

  "My coworkers stopped including me in the informal Friday lunches after the
   department moved floors"
  → Extract: "coworkers no longer include user in informal lunch gatherings since
    the department relocated" (current_state)
  NOT: "user feels left out at work"

ONLY apply this when:
  — The behavior is a PERSISTENT PATTERN ("keep", "always", "never", "anymore",
    "every time", habitual present tense, "no longer")
  — It is SPECIFIC and OBSERVABLE (not vague generalizations like "everyone hates me")
  — It reveals something meaningful about the user's SOCIAL ENVIRONMENT or STATUS

Do NOT extract:
- One-time events with no lasting consequence ("went to the cinema last weekend", "had lunch with a colleague")
- Pure requests, questions, or task instructions
- Hypotheticals and wishes ("if I were to...", "I wish...")
- Emotional reactions WITHOUT any factual claim ("I'm so stressed today", "I'm so tired of this weather")
- Generic filler with no personal state content
- Facts about the external world unrelated to the user's own situation

TURN-LEVEL vs STATEMENT-LEVEL:
The exclusion rule "Do NOT extract Pure requests, questions" applies at the STATEMENT
level, not the TURN level. A user turn may contain both factual claims and questions.
Extract factual claims from ALL parts of the turn — do not return an empty list for a
turn simply because it also contains a question or request.

EXTRACT (factual clause embedded in a question-containing turn):
  Turn: "My lease in Portland ended last month and I moved in with my sister temporarily.
        Any advice for making shared living work?"
  → Extract: "user's lease in Portland ended; user is now living with sister" (recent_change)

  Turn: "Since I started my apprenticeship at the bakery three weeks ago,
        what kind of work shoes should I get?"
  → Extract: "user started an apprenticeship at a bakery three weeks ago" (recent_change)

  Turn: "Given my new standing desk setup I finally got installed, what ergonomic
        habits should I build?"
  → Extract: "user now has a standing desk setup" (current_state)

DO NOT EXTRACT (assumption inside a rhetorical or self-questioning form):
  Turn: "Is it true that I still prefer dark roast coffee?"
  → No extraction — user is questioning their own preference, not asserting it.
  Turn: "Why do I always procrastinate? Can I change this?"
  → No extraction — "why do I" is rhetorical, not a factual assertion.

Rule of thumb: if the factual content is in a subordinate clause introduced by
"since", "now that", "given that", "after", "because" — extract it.
If the entire sentence is a question about whether the user still has a trait/state
("Is it true that...", "Do I still...", "Am I still...") — do NOT extract.

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

LEGAL / OFFICIAL STATUS: When the statement mentions signing or submitting a specific
document, completing a formal procedure at an official institution (bank, tax authority,
government office, court, clerk's office, immigration office), or undergoing a formal
assessment or registration:
  Step A — Identify the TYPE: financial document, civic/identity form, legal instrument,
    professional certification, or enrollment/registration process.
  Step B — Reason about what STATUS CHANGE this transaction typically FINALIZES:
    Financial document at bank or financial institution → may mean change in account
      arrangement, loan status, declared income source, or financial beneficiary
    Government office paperwork → may mean change in immigration status, marital status,
      property ownership, civic enrollment, or legal standing
    Legal instrument at official office → may mean change in a legal obligation,
      contract status, or asset arrangement
    Enrollment/registration → may mean change in educational status, professional
      license, or organizational membership
  Step C — Cross-reference with stored biographical/status memories: does any stored
    memory assume a status (citizenship, residency, financial obligation, legal standing,
    professional license) that this transaction might now challenge?
  NOTE: Routine visits without a specific document signing (deposits, withdrawals,
    asking questions) do NOT warrant this inference.

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
- recent_change memories (shown with category tag "recent_change") describe events that have
  already occurred whose outcome is currently in effect. To invalidate a recent_change memory,
  the new statement must explicitly contradict the factual outcome of that event. Behavioral
  activity that is consistent with and expected given the new state is NOT evidence of
  invalidation: researching cat food brands after adopting a cat, looking for vegan restaurants
  after switching diet, setting up services after moving — these are expected concurrent
  activities, not signals that the change was reversed. Do not flag recent_change items based
  solely on indirect behavioral inference.
- A statement about the user's CHARACTER, VALUES, or PERSONAL PRINCIPLES (e.g., "I value
  self-reliance", "I believe in personal responsibility", "I prefer to handle things myself")
  does NOT invalidate a recent_change memory that describes a specific institutional,
  contractual, or externally-imposed outcome. Values and real-world outcomes can coexist: a
  person who values autonomy may still be subject to a formal review process they didn't
  choose; a person who prefers independence may still be bound by a condition imposed at an
  institutional meeting. Only a direct statement that the specific situation ended or was
  formally resolved justifies invalidating such a recent_change.
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

Current active memories (confirmed true; session number = when recorded):
{active_memories}

Uncertain memories (may be outdated — reliability reduced but not yet confirmed stale):
{uncertain_memories}

Stale memories (used to be true but has since changed; format: created session C → staled session S):
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
- ALSO set premise_safe=false when two ACTIVE memories imply mutually exclusive states on the
  SAME life dimension (relationship/companionship, employment, location, health status), AND the
  query's assumption relies on one side of the conflict. Use the session number as a tiebreaker:
  the higher-session active memory reflects more recently recorded information — treat it as the
  current state. Only flag genuine mutual exclusivity — states that CANNOT both be simultaneously
  true for this user right now. Unrelated dimensions do not count.
  Example: active "employed at a software company (session 12)" + active "started a year-long
  sabbatical to write a novel (session 19)" → mutually exclusive on employment dimension → query
  that assumes ongoing employment is unsafe; session 19 memory takes precedence.
  Correction should name both conflicting facts and identify which (higher session) appears current.
- premise_safe=false with soft correction (uncertain, not stale) should say:
  "We're no longer certain that [old belief] is still true. [Helpful context on why]."
- Trace indirect assumptions: if the question assumes X, and X depends on Y, and Y is stale/uncertain → unsafe
- correction GROUNDING RULES (P4):
  (1) Ground correction in the content of ACTIVE memories. Paraphrase what the active
      memories say — do NOT copy stale_reason verbatim. stale_reason is an abductive
      inference trace that may be imprecise or wrong; active memories are authoritative.
  (2) A single direct-inference step from an active memory to the conclusion is allowed:
      active: "user's new job ends at 9pm" → correction may say "new schedule affects
      evenings." Do NOT chain through multiple unstated steps.
  (3) Prefer the active memory MOST DIRECTLY related to the stale dimension.
  (4) If no active memory explains the change: correction = "We know that [paraphrase of
      stale fact] is no longer current, but we don't have information about what changed."
      Do NOT invent a reason.
  (5) NEVER echo stale_reason text verbatim into correction.
- If no relevant memories exist at all, premise_safe=true (we don't know enough to say it's unsafe)
- Scrutinize the stale_reason before including a memory in outdated_facts: only include it if
  the stale_reason cites DIRECT evidence of reversal (an explicit new statement that undoes the
  memory's content, or the user directly stating the situation has ended). If the stale_reason
  is based on indirect inference — a personality trait implying the person "would not accept" a
  condition, or concurrent behavior implying a situation "must have" resolved — treat the stale
  status as uncertain. In that case, do not assert the memory as definitively past; instead,
  reflect the ambiguity in the correction. Pay particular attention to stale memories whose
  content describes a formal obligation, institutional constraint, or event-driven outcome:
  those require explicit reversal evidence, not inference.
- Use the session gap (stale session − created session) as a signal: a memory staled within
  1–2 sessions of creation is suspicious — the staling may reflect a premature inference rather
  than a genuine reversal. Apply extra scrutiny to the stale_reason in those cases.

CLOSING DEPENDENCY CHECK (mandatory — apply after populating outdated_facts):
After identifying all presuppositions and populating outdated_facts, perform this
final check for EACH item in outdated_facts:

  Ask: "Does the question's central assumption DIRECTLY DEPEND on this item being
        still true — such that if this item were false, the question's core premise
        would be undermined?"

If YES for ANY item: premise_safe MUST be False.

"Directly depends" includes:
  — The question explicitly asks about the same behavior/state as the stale item
    ("what do you usually cook for your weekly meal prep?" depends on the meal-prepping habit)
  — The question's action would be pointless or misleading without the stale item
    ("what's the best route for your morning commute?" depends on the user still commuting)
  — A 1-step inference: "what evening activities might you enjoy?" depends on evenings
    being free, if the stale item is "evenings were mostly available"

"Does NOT directly depend" (premise_safe may still be True):
  — Stale item is tangentially related but the question remains valid without it
  — The question is about a different dimension than the stale item
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

[STATUS] OVERWRITE RULE — MANDATORY:
[STATUS] is a current-state snapshot — it holds exactly ONE value per dimension.
Never append an old and new value together in the same dimension.
The four core [STATUS] dimensions: (1) Location / where user lives, (2) Employment /
job / income source, (3) Health / medical condition, (4) Relationship / household status.

When memory_changes lists a stale record whose content belongs to one of these four
dimensions:
  → IDENTIFY which dimension it belongs to
  → SEARCH new_statements for the updated value for that dimension
  → REPLACE the old [STATUS] content for that dimension — do NOT keep the old value
  → If no replacement value is found: write "(currently unknown)" for that dimension

Source rule: new [STATUS] content must come from new_statements.
NEVER use stale_reason as the source — it is an inference trace and may be wrong.

What does NOT trigger [STATUS] replacement: devices, subscriptions, memberships,
or one-time visits/trips. Only records whose content is about the four core dimensions
above should update [STATUS].
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
- Disambiguation:
  (1) When correction is non-empty (premise_safe=false): the correction is AUTHORITATIVE
      for the specific dimension it addresses (e.g., if correction addresses location, use
      correction's location over any conflicting profile_summary location claim). For all
      other dimensions NOT addressed by correction, profile_summary remains valid.
      Correction OVERRIDES profile_summary on its own dimension — not the other way around.
  (2) When no correction, but multiple active facts conflict on the same dimension:
      individual active facts from more recent sessions take precedence.
  (3) Profile_summary is a compressed, potentially lagging snapshot. It should NEVER
      override correction or recent individual active facts. Use it for context only.
- If we lack sufficient information to answer well, say so honestly and ask a clarifying question
- Keep the answer conversational and natural, as if you know this user well
- Be specific and practical — use the actual facts we have, don't give generic answers
"""
