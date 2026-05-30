STATEMENT_EXTRACTOR_PROMPT = """Extract factual current-state claims about the USER from the provided conversation turns.

Only extract statements that:
- Describe the user's current real-world state (location, job, relationships, health, habits, living situation, etc.)
- Are stated as current facts, not hypotheticals, desires, or past events with no present relevance
- Are the user's own statements (not assistant responses)

Do NOT extract:
- Pure requests or questions
- Statements about external world facts unrelated to the user's personal state
- Purely hypothetical sentences ("if I...", "what if...")
- Pure future intentions not yet realized ("I'm planning to...", "I want to...")
- Emotional reactions without factual content ("I'm feeling stressed about work")
- Generic conversational filler

Output JSON only:
{
  "statements": [
    {
      "text": "exact relevant clause from user message",
      "temporal_scope": "current|past|future",
      "is_definite": true
    }
  ]
}

Rules:
- temporal_scope=current: describes ongoing or recently established state still in effect now
- temporal_scope=past: describes something that ended before now
- temporal_scope=future: describes something not yet happened
- is_definite=true: stated as fact, not speculation
- is_definite=false: uncertain, speculative, or hedged
- Only current + definite statements are relevant for memory
- Keep the text minimal but semantically complete (enough to understand the claim without the surrounding conversation)
- If no relevant statements exist in the session, return {"statements": []}
"""


HYPOTHETICAL_FILTER_PROMPT = """Classify this user statement as FACTUAL, HYPOTHETICAL, or EMOTIONAL.

Statement: {statement}

FACTUAL: States a real current condition about the user's life, circumstances, or arrangements.
  Examples: "I moved to Austin last month", "I work at a startup now", "My boyfriend and I broke up"

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


IMPACT_HYPOTHESIS_PROMPT = """Generate hypothetical memory search queries based on what might be INVALIDATED by this user statement.

Statement: {statement}

Current user profile summary:
{global_impression}

Think: if this statement is true, what aspects of the user's life that we might have stored in memory could now be outdated?
Generate search queries that would find those potentially-outdated memory entries.

Output JSON only:
{
  "hypothetical_impacts": [
    "short statement describing what might now be outdated or false"
  ]
}

Rules:
- Generate 2-5 impact hypotheses
- Each hypothesis should be a factual-sounding statement about what USED TO BE TRUE but might not be anymore
- Use the profile summary to make hypotheses specific to this user's known situation
- Think beyond direct semantic overlap — consider downstream implications:
  - Location change → commute habits, daily routines, local services, social circle
  - Job change → income level, daily schedule, professional identity, commute
  - Relationship change → living situation, weekend activities, future plans
  - Health change → physical activities, daily routine, work capacity
- If profile is empty, generate generic hypotheses based on common personal facts
- Examples of good hypotheses:
  - "user lives in [city X]" (if statement implies relocation)
  - "user is single/has no romantic partner" (if statement mentions a partner)
  - "user works at [company X]" (if statement implies job change)
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
  Example: "user moved to Austin → no longer in Seattle → 'lives in Seattle' memory is false"
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
