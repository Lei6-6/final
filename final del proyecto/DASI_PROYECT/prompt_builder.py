"""
prompt_builder.py
-----------------
All prompts sent to Ollama are constructed here.
Business logic modules must not contain raw prompt strings.
"""


def build_decision_prompt(
    inventory: dict[str, int],
    goal_needs: dict[str, int],
    target_resources: set[str],
    requested: dict[str, int],
    exchangeable: dict[str, int],
) -> str:
    """
    Build a decision prompt for responding to a resource request.

    The LLM is allowed to choose from the pre-filtered 'exchangeable'
    resources only. Code-side validation will re-check the output.
    """
    return f"""You are a resource-exchange agent in a multi-agent trading game.
Your task is to decide how to respond to an incoming resource request.

=== CURRENT STATE ===
Inventory (resources you own): {inventory}
Goal needs (still required to win): {goal_needs}
Blocked resources (must NOT be traded — still needed for goal): {sorted(target_resources)}

=== INCOMING REQUEST ===
Resources requested: {requested}

=== EXCHANGEABLE RESOURCES ===
(Non-target resources with sufficient stock — the only ones you may offer)
{exchangeable}

=== DECISION RULES ===
- You may ONLY include resources listed under EXCHANGEABLE RESOURCES.
- Quantities must be non-negative integers and must not exceed the exchangeable amounts.
- Blocked/target resources must NEVER appear in your response.
- Do NOT include resources that were not requested.
- Choose the decision that best serves completing your goal.

=== ALLOWED DECISIONS ===
"accept" — grant the full exchangeable request as-is
"offer"  — grant a partial subset (lower quantities or fewer resources)
"reject" — give nothing

=== OUTPUT FORMAT ===
Respond with valid JSON only. No extra text, no markdown, no comments.
{{
  "decision": "accept" | "offer" | "reject",
  "resources": {{"resource_name": quantity}},
  "reason": "brief one-sentence explanation"
}}
"""


def build_normalization_prompt(raw_text: str) -> str:
    """
    Build a prompt asking the LLM to parse a natural-language message into
    the unified internal format.
    """
    return f"""You are a message parser for a resource trading agent.
Convert the message below into structured JSON.

=== INPUT MESSAGE ===
"{raw_text}"

=== VALID MESSAGE KINDS ===
"request"  — sender is asking for resources
"delivery" — sender is sending resources to you
"accept"   — sender is accepting a previously proposed trade
"reject"   — sender is rejecting a trade
"unknown"  — intent cannot be determined

=== VALID RESOURCES ===
arroz, ladrillos, madera, piedra, queso, tela, trigo, oro

=== EXAMPLES ===
Input: "Necesito 2 arroz, te ofrezco 1 tela"
Output: {{"kind": "request", "resources": {{"arroz": 2}}, "from_agent": "unknown"}}

Input: "Te mando 3 madera como acordamos"
Output: {{"kind": "delivery", "resources": {{"madera": 3}}, "from_agent": "unknown"}}

=== OUTPUT FORMAT ===
Respond with valid JSON only. No extra text.
{{
  "kind": "request" | "delivery" | "accept" | "reject" | "unknown",
  "resources": {{"resource_name": quantity}},
  "from_agent": "agent name if clearly mentioned, else unknown"
}}

If you are not confident about the intent, use "unknown".
"""
