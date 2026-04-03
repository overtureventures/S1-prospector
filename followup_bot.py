"""
Follow-Up Bot

Takes a processed Granola note context dict, looks up the LP in Affinity,
calls Claude with the full fundraising tactics prompt, and posts the result
to #fundraising-bot on Slack, tagging the GP who had the call.
"""

import os
import re
import logging
import requests
from typing import Dict, Optional
from affinity import AffinityClient

logger = logging.getLogger(__name__)

FUNDRAISING_BOT_CHANNEL = "C0AQHP58A0Z"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
SLACK_API_URL = "https://slack.com/api"

# ── Fundraising tactics context injected into every Claude prompt ────────────
TACTICS_CONTEXT = """
You are the fundraising intelligence system for Overture Ventures, a climate-focused
early-stage venture fund raising Fund II.

MALTBY BUCKET CLASSIFICATION:
- REAL SHOT: notes contain "fits our mandate" / "next steps" / "send materials" / "interested"
- NEEDS WORK: notes contain "interesting" / "still getting our arms around" / "TBD" / no clear next step
- STRUCTURAL NO: notes contain "already in X funds" / "capacity issue" / "timing" / "I like you but..."

FOLLOW-UP ACTION BY BUCKET:
- REAL SHOT: Deal update or fund momentum email. Include specific next step (IC materials, reference
  call, data room link). Cadence: every 2 to 3 days.
- NEEDS WORK: "Show don't tell" email. Frame as "You mentioned X, here is a specific proof point."
  Conclude with a proposed next meeting. Cadence: every 2 to 3 weeks.
- STRUCTURAL NO: No-ask value-add only. No pitch reference. Goal is warmth for future fund cycle.
  Cadence: quarterly.

LP TYPE PERSONALIZATION:
- Institutional (endowment, pension, foundation): Formal written comms. IC-ready materials.
  Never pressure. Identify and support the internal champion.
- Family office: Personal and direct. Text or personal email preferred. Co-invest access is high value.
- HNWI: Personal conviction and relationship. Regular reciprocal gestures.
- Fund of funds: Deep transparency. Proprietary sourcing proof. Differentiation is everything.

WARM-UP RULE: Never make a capital ask without 2 to 3 value-add touchpoints first.
If a capital ask seems premature based on the call notes, flag it.

CADENCE:
- Real Shot: every 2 to 3 days
- Warm / Needs Work: every 2 to 3 weeks
- Cool: monthly
- Structural No: quarterly, value-add only

NEVER:
- End a follow-up without a proposed next step
- Send a generic check-in with no added value
- Re-pitch. Add new evidence or new value only.
- Make a capital ask without warming up first.

OVERTURE FUND II CONTEXT:
- Climate-focused early-stage venture (pre-seed and seed)
- Thesis sectors: Energy Transition, Resilience, Industrial Transformation
- Portfolio includes: Harbinger (EV trucks, potential IPO), Antora (thermal batteries),
  Earth Force (US Forest Service default platform), Halcyon (AI energy data), Kerrigan
  (robotic orchestration), Glacier (robotics recycling), BurnBot (wildfire prevention),
  and 30+ others
- Fund II target: institutional-grade raise, first close completed
"""

CLAUDE_SYSTEM_PROMPT = f"""{TACTICS_CONTEXT}

Your job: Given a Granola meeting note from an LP call and the LP's CRM data, produce:

1. MALTBY BUCKET: One of REAL SHOT / NEEDS WORK / STRUCTURAL NO with a one-sentence reason.

2. ACTION ITEMS: A short numbered list of what the GP owes from this call. Be specific.
   Reference exact things discussed in the notes. No vague items.

3. SUGGESTED FOLLOW-UP MESSAGE: Write a draft message the GP can send within 24 hours.
   Rules:
   - Write like a senior partner, not a marketing tool
   - Use the LP's name
   - Reference something specific from the call
   - Connect to a relevant portfolio company or thesis point if it fits naturally
   - End with a clear, specific next step
   - No hyphens anywhere
   - No AI sounding openers or closers
   - No "I hope this finds you well" or equivalent
   - If LP is institutional, keep it formal. If family office or HNWI, keep it personal.
   - If this is a STRUCTURAL NO, do not pitch. Offer a value add only.

4. FOLLOW-UP CADENCE: State the recommended next contact window based on the bucket.

Format your response with these exact section headers:
MALTBY BUCKET
ACTION ITEMS
SUGGESTED FOLLOW-UP MESSAGE
FOLLOW-UP CADENCE
"""


class FollowUpBot:
    def __init__(self):
        self.slack_token = os.getenv("SLACK_BOT_TOKEN", "").strip()
        self.anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        self.affinity_key = os.getenv("AFFINITY_API_KEY", "").strip()
        self.affinity_list_name = os.getenv("AFFINITY_LIST_NAME", "Fundraising")
        self._slack_user_cache: Dict[str, str] = {}

    # ── Slack helpers ────────────────────────────────────────────────────────

    def _lookup_slack_user_id(self, email: str) -> Optional[str]:
        """Resolve a Granola owner email to a Slack user ID for tagging."""
        if email in self._slack_user_cache:
            return self._slack_user_cache[email]

        if not self.slack_token:
            return None

        try:
            r = requests.get(
                f"{SLACK_API_URL}/users.lookupByEmail",
                headers={"Authorization": f"Bearer {self.slack_token}"},
                params={"email": email},
                timeout=10,
            )
            data = r.json()
            if data.get("ok"):
                user_id = data["user"]["id"]
                self._slack_user_cache[email] = user_id
                return user_id
            else:
                logger.warning(f"Could not find Slack user for {email}: {data.get('error')}")
                return None
        except requests.RequestException as e:
            logger.error(f"Slack user lookup failed for {email}: {e}")
            return None

    def _post_to_slack(self, message: str) -> bool:
        if not self.slack_token:
            logger.warning("SLACK_BOT_TOKEN not set. Skipping post.")
            return False

        try:
            r = requests.post(
                f"{SLACK_API_URL}/chat.postMessage",
                headers={
                    "Authorization": f"Bearer {self.slack_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "channel": FUNDRAISING_BOT_CHANNEL,
                    "text": message,
                    "unfurl_links": False,
                    "unfurl_media": False,
                },
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            if not data.get("ok"):
                logger.error(f"Slack post error: {data.get('error')}")
                return False
            logger.info("Slack message posted successfully")
            return True
        except requests.RequestException as e:
            logger.error(f"Slack post failed: {e}")
            return False

    # ── Affinity lookup ──────────────────────────────────────────────────────

    def _get_lp_crm_data(self, lp_name: str) -> Dict:
        """
        Try to find the LP in Affinity and return pipeline context.
        Returns an empty dict if Affinity is not configured or no match found.
        """
        if not self.affinity_key:
            logger.info("AFFINITY_API_KEY not set. Skipping CRM lookup.")
            return {}

        try:
            client = AffinityClient(self.affinity_key)
            client.load_fundraising_list(self.affinity_list_name)
            match = client.find_match(lp_name)
            if match:
                logger.info(f"Affinity match for '{lp_name}': {match['name']} ({match['status']})")
                return match
            else:
                logger.info(f"No Affinity match found for '{lp_name}'")
                return {}
        except Exception as e:
            logger.error(f"Affinity lookup failed for '{lp_name}': {e}")
            return {}

    # ── Claude call ──────────────────────────────────────────────────────────

    def _call_claude(self, user_prompt: str) -> Optional[str]:
        if not self.anthropic_key:
            logger.error("ANTHROPIC_API_KEY not set.")
            return None

        try:
            r = requests.post(
                ANTHROPIC_API_URL,
                headers={
                    "x-api-key": self.anthropic_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 1500,
                    "system": CLAUDE_SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": user_prompt}],
                },
                timeout=60,
            )
            r.raise_for_status()
            data = r.json()
            content_blocks = data.get("content", [])
            text = " ".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")
            return text.strip() if text else None
        except requests.RequestException as e:
            logger.error(f"Claude API call failed: {e}")
            return None

    # ── Main entry point ─────────────────────────────────────────────────────

    def process_note(self, note_context: Dict) -> bool:
        """
        Full pipeline for one note:
        1. Extract LP name from meeting title
        2. Look up LP in Affinity
        3. Call Claude with all context
        4. Post to Slack tagging the GP
        """
        title = note_context.get("title", "Untitled meeting")
        owner_email = note_context.get("owner_email", "")
        owner_name = note_context.get("owner_name", "Unknown")
        summary = note_context.get("summary", "")
        transcript = note_context.get("transcript_text", "")
        created_at = note_context.get("created_at", "")

        logger.info(f"Processing note: '{title}' (owner: {owner_name})")

        # Extract LP name from title (typically "LP Name <> Overture" or "Call with LP Name")
        lp_name = self._extract_lp_name(title)

        # CRM lookup
        crm_data = self._get_lp_crm_data(lp_name) if lp_name else {}
        crm_context = ""
        if crm_data:
            crm_context = f"""
CRM DATA FOR THIS LP:
- Organization: {crm_data.get('name', 'N/A')}
- Pipeline status: {crm_data.get('status', 'Unknown')}
- Last activity: {crm_data.get('last_activity', 'Unknown')}
- Notes on file: {crm_data.get('notes', 'None')}
"""

        # Build user prompt for Claude
        user_prompt = f"""
Meeting title: {title}
Date: {created_at}
GP on the call: {owner_name} ({owner_email})

MEETING SUMMARY FROM GRANOLA:
{summary}

TRANSCRIPT EXCERPT:
{transcript[:3000] if transcript else "No transcript available."}

{crm_context}

LP NAME IDENTIFIED: {lp_name or "Could not identify from title"}

Based on the above, provide the four sections: MALTBY BUCKET, ACTION ITEMS,
SUGGESTED FOLLOW-UP MESSAGE, and FOLLOW-UP CADENCE.
"""

        # Call Claude
        logger.info("Calling Claude for follow-up analysis...")
        analysis = self._call_claude(user_prompt)

        if not analysis:
            logger.error("Claude returned no response. Skipping Slack post.")
            return False

        # Build Slack message
        slack_user_id = self._lookup_slack_user_id(owner_email)
        gp_tag = f"<@{slack_user_id}>" if slack_user_id else owner_name

        lp_display = lp_name or title
        date_display = created_at[:10] if created_at else "today"

        slack_message = (
            f"*Follow-Up Brief: {lp_display}* | {date_display}\n"
            f"Call owner: {gp_tag}\n"
            f"{'─' * 48}\n"
            f"{analysis}"
        )

        return self._post_to_slack(slack_message)

    # ── LP name extraction ───────────────────────────────────────────────────

    def _extract_lp_name(self, title: str) -> Optional[str]:
        """
        Parse meeting title to extract the LP or organization name.
        Handles common formats:
          "BlackRock <> Overture"
          "Call with Wellcome Trust"
          "Overture / Stanford Endowment"
          "First meeting: Harvard Management Company"
        """
        title_clean = title.strip()

        # Pattern: X <> Y or X / Y or X | Y  — return the non-Overture side
        for sep in [" <> ", " / ", " | ", " — ", " - "]:
            if sep in title_clean:
                parts = title_clean.split(sep, 1)
                for part in parts:
                    part = part.strip()
                    if "overture" not in part.lower():
                        return part
                return parts[0].strip()

        # Pattern: "Call with X" / "Intro with X" / "Meeting with X"
        prefixes = [
            "call with ", "intro with ", "intro call with ",
            "meeting with ", "first meeting with ", "first meeting:",
            "catch up with ", "catch-up with ", "follow up with ",
            "diligence call with ", "lp call with ",
        ]
        title_lower = title_clean.lower()
        for prefix in prefixes:
            if title_lower.startswith(prefix):
                return title_clean[len(prefix):].strip().rstrip(":")

        # Pattern: "X: follow up" or "X: intro" — take the first segment
        if ":" in title_clean:
            candidate = title_clean.split(":")[0].strip()
            if len(candidate) > 3 and "overture" not in candidate.lower():
                return candidate

        # Fallback: return full title as the LP identifier so Affinity can attempt a match
        logger.info(f"Could not parse LP name from title '{title}'. Using full title for CRM lookup.")
        return title_clean
