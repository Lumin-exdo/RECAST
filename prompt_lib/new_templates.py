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
3. BIOGRAPHICAL BACKGROUND: stable facts about who this person is
   (grew up somewhere, has a degree, has children, native language)
4. LASTING PREFERENCE OR HABIT: recurring patterns, values, constraints
   (doesn't eat meat, wakes up early, dislikes crowded places, exercises daily)

Do NOT extract:
- One-time events with no lasting consequence ("went to the cinema last weekend", "had lunch with a colleague")
- Pure requests, questions, or task instructions
- Hypotheticals and wishes ("if I were to...", "I wish...")
- Emotional reactions without a factual claim ("I'm so stressed today")
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
- Extract the minimal clause that conveys the meaningful personal fact
- If nothing qualifies, return {"statements": []}
"""


HYPOTHETICAL_FILTER_PROMPT = """Classify this user statement as FACTUAL, HYPOTHETICAL, or EMOTIONAL.

Statement: {statement}

FACTUAL: States a real current condition about the user's life, circumstances, or arrangements.
  Examples: "I just relocated for work", "I work at a startup now", "My boyfriend and I broke up"

HYPOTHETICAL: A thought experiment, wish, plan, or scenario not yet real.
  Examples: "If I were to move...", "I've been thinking about switching jobs", "I wish I could..."

EMOTIONAL: Primarily an emotional reaction without asserting a factual state change.
  Examples: "I'm so tired of my commute", "I love my new neighborhood" (without implying relocation)

Output JSON only:
{
  "type": "FACTUAL|HYPOTHETICAL|EMOTIONAL",
  "reason": "one sentence explanation"
}

Rules:
- When in doubt between FACTUAL and EMOTIONAL, prefer FACTUAL if a real-world state is implied
- A statement can imply a fact even if phrased emotionally ("I've been arguing with my boyfriend a lot lately" implies there IS a boyfriend)
- Statements beginning with "lately", "recently", "these days" strongly suggest current factual states
"""


TRIGGER_GATE_PROMPT = """Decide whether this user statement might require updating or invalidating existing memory about the user.

Statement: {statement}

Current user profile summary:
{global_impression}

Output JSON only:
{
  "should_trigger": true,
  "reason": "one sentence explanation"
}

Rules:
- should_trigger=true if the statement might change, contradict, or make obsolete any existing memory about the user
- should_trigger=true if the profile is empty (any factual personal statement is worth storing)
- should_trigger=false only for clearly irrelevant statements: weather comments, external world facts, task-only content with no personal state implication
- Common triggers: change of location, change of job/employer, change of relationship status, change of health, change of habits, change of living situation
- Even indirect statements can trigger: "adapting to life in a new city" implies a location change without naming the city
- Be generous with triggering — a false negative (missing an important update) is worse than a false positive
"""


IMPACT_HYPOTHESIS_PROMPT = """You are a detective trying to figure out what has CHANGED in this user's life.

New statement: {statement}

Current user profile summary:
{global_impression}

Your task — two steps:

STEP 1 — REASON FIRST (do not output this):
Ask yourself: if this statement is true, what does it IMPLY about the user's current reality?
Chain your reasoning outward. Think about:
- Physical environment: where must the user be? what can they access? what's impossible where they are?
- Legal/administrative status: what paperwork, rights, or obligations does this situation imply?
- Relationship/social structure: who is in or out of the user's life now?
- Financial/occupational reality: what income, schedule, or constraints does this imply?
- Physical capability: what can or can't the user do physically given this situation?
Then ask: what PREVIOUSLY STORED beliefs about this user would CONFLICT with that implied reality?

STEP 2 — OUTPUT hypotheses: things that USED TO BE TRUE but are now probably wrong.
Each hypothesis is a candidate memory to search for and check.

Output JSON only:
{
  "hypothetical_impacts": [
    "short statement describing what used to be true about the user but might now be outdated"
  ]
}

Rules:
- Generate 4-8 hypotheses. More is better. Being wrong is fine — the next step will verify.
- MANDATORY: include at least 2 non-obvious, multi-hop hypotheses. Surface-only is not enough.
  Bad (surface): "user recently changed jobs"
  Good (multi-hop): "user is not required to hold US citizenship or pass a security clearance"
  Bad (surface): "user went to a religious event"
  Good (multi-hop): "user identifies as [previous religion]" or "user is not a practicing Catholic"
- Hypotheses must be PAST-TENSE BELIEFS ("user used to X", "user was X", "user had X").
  NOT current observations. NOT future plans. Only things that might now be OUTDATED.
- Generate competing, contradictory hypotheses freely — they don't need to be consistent.
- Use the profile summary to target specific stored facts.
  If summary mentions a specific place and the statement implies a very different climate or geography → generate "user lives in [that place]".
  If summary mentions "permanent resident" and statement implies federal employment → generate "user is comfortable staying a permanent resident / not pursuing citizenship".
- Think about SYSTEMIC CONSEQUENCES, not just the immediate event:
  inheriting property from a family member → now a homeowner → "user rents their home" is now wrong
  federal job offer → federal jobs require security clearance → clearance requires citizenship → "user is not pursuing citizenship" is now wrong
  removing a hedge → the sound barrier it provided is gone → "noise from highway is blocked by vegetation" is now wrong
  parka from back of closet → cold climate → contradicts any stored belief about warm/humid climate
"""


ABDUCTIVE_JUDGMENT_PROMPT = """For each candidate memory, judge whether a user's new statement makes it outdated or less reliable.

New statement from user: {statement}

Supporting impact hypotheses (what this statement might invalidate):
{hypotheses}

Candidate memories to evaluate:
{candidates}

For each candidate, reason using abductive inference: if the user's statement is true, does this memory remain valid?
Multi-hop reasoning is expected — the statement and memory may share no keywords but still conflict.

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
- direct_invalidation: the memory is now almost certainly false or no longer applicable (confidence >= 0.65)
- weakens_support: the memory's reliability is reduced but cannot be conclusively invalidated (confidence 0.35-0.75)
- no_conflict: the memory is unaffected by this statement (confidence < 0.35 OR clearly independent)
- Only include judgments for direct_invalidation and weakens_support — skip no_conflict items
- inference_chain must trace the logical path: new fact → intermediate implication → why old memory fails
  Example: "user started a new job → no longer at previous employer → 'works at [old company]' memory is false"
- Confidence reflects certainty that the old memory is now invalid:
  0.9+: near-certain invalidation (e.g., new location named explicitly, old location named in memory)
  0.7-0.9: strong implied invalidation (e.g., "new city" without naming old city)
  0.5-0.7: moderate implied conflict (e.g., context suggests but doesn't confirm)
  0.35-0.5: weak signal, worth tracking in evidence pool
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
- Multiple weak signals (each 0.4-0.6) that all point the same direction should compound
- Consider recency: more recent evidence should weigh more heavily
- Consider coherence: consistent evidence from multiple angles is stronger than repeated similar evidence
- A single very strong piece of evidence (0.85+) alone can justify marking stale
"""


PREMISE_CHECK_PROMPT = """Check whether a user's question contains implicit assumptions that may be outdated given what we know.

User question: {query_text}

Current active memories (what we know to be currently true):
{active_memories}

Recently stale memories (what USED TO BE true but has changed):
{stale_memories}

Identify what the question implicitly assumes about the user's current state. Then check whether those assumptions are supported, contradicted, or unknown given the memory state.

Output JSON only:
{
  "presuppositions": [
    "what the question implicitly assumes"
  ],
  "premise_safe": true,
  "correction": "if premise_safe=false: what the user should know before we answer (one sentence)",
  "usable_active_facts": [
    "active memory contents directly relevant to answering"
  ],
  "outdated_facts": [
    "stale memory contents that the question may be assuming are still true"
  ]
}

Rules:
- premise_safe=true: active memories confirm the question's assumptions OR no assumptions are contradicted
- premise_safe=false: a stale memory shows the question is built on outdated information
- correction should be specific: name what changed, not just "things have changed"
- If no relevant memories exist at all, premise_safe=true (we don't know enough to say it's unsafe)
- Examples:
  - Question "which bus route do I take to work?" assumes user has a known commute/workplace
  - If we have stale memory "works at Company X in City A" and no new active workplace memory, premise may be unsafe
  - Question "should I bring an umbrella to work?" is location-dependent
"""


GLOBAL_IMPRESSION_UPDATE_PROMPT = """Update the user profile summary based on new information from this session.

Current profile summary:
{current_impression}

Memory changes that occurred this session:
{memory_changes}

New statements from user this session:
{new_statements}

Rewrite the profile summary to incorporate these changes. The summary must cover these four dimensions:
1. Who the user is (identity, life stage, background)
2. Current most important status (location, employment, health, relationships)
3. Most significant recent changes (what has shifted compared to before)
4. Core habits and preferences (routines, interests, values)

Output JSON only:
{
  "updated_impression": "the rewritten profile summary (max 500 characters)",
  "changed_dimensions": ["list of which dimensions changed: identity|status|changes|habits"]
}

Rules:
- Keep the summary under 500 characters total
- Write in third person ("The user...")
- Be concrete and specific — avoid vague generalizations
- Preserve accurate existing information that didn't change
- Only update dimensions where genuine changes occurred
- If current_impression is empty, build from scratch using only what's known
- Focus on facts, not speculation
"""


ANSWER_GENERATION_PROMPT = """Answer the user's question using available memory about them.

User question: {query_text}

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
- If we lack sufficient information to answer well, say so honestly and ask a clarifying question
- Keep the answer conversational and natural, as if you know this user well
- Be specific and practical — use the actual facts we have, don't give generic answers
"""
