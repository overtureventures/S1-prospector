import os
import re
import logging
import threading
from datetime import datetime, timedelta
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
import requests
import schedule
import time
import pytz

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN")
AFFINITY_API_KEY = os.environ.get("AFFINITY_API_KEY")
AFFINITY_LIST_ID = os.environ.get("AFFINITY_LIST_ID")
NUDGE_CHANNEL_ID = os.environ.get("NUDGE_CHANNEL_ID")  # #deal-nudges channel ID

# Owner name to Slack ID mapping
OWNER_SLACK_MAP = {
    "Emma McDonagh": "U02SC43GEH4",
    "Shomik Dutta": "U03HP4WKP62",
    "Allison Hinckley": "U07S6CLHPL1",
    "Leila Pirbay": "U08840SFVN1",
}

# Slack ID to Affinity Person ID mapping
SLACK_TO_AFFINITY_PERSON = {
    "U02SC43GEH4": 217635093,   # Emma McDonagh
    "U03HP4WKP62": 217635937,   # Shomik Dutta
    "U07S6CLHPL1": 217637423,   # Allison Hinckley
    "U08840SFVN1": 217635950,   # Leila Pirbay
}

# Stage nudge thresholds (in days)
STAGE_THRESHOLDS = {
    "First Meeting": 14,   # 2 weeks
    "Engaged": 21,         # 3 weeks
    "Need to Pass": 14,    # 2 weeks
    "On Hold": 84,         # 12 weeks
}

# Affinity field IDs
STATUS_FIELD_ID = 4927710
OWNERS_FIELD_ID = 4927712
PASS_REASON_FIELD_ID = 4944316
MISSED_STATUS_VALUE_ID = 20689035

app = App(token=SLACK_BOT_TOKEN)

AFFINITY_BASE_URL = "https://api.affinity.co"


class AffinityClient:
    def __init__(self, api_key):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.auth = ("", api_key)
        self.session.headers.update({"Content-Type": "application/json"})

    def search_organization(self, term):
        """Search for an organization by name or domain."""
        response = self.session.get(
            f"{AFFINITY_BASE_URL}/organizations",
            params={"term": term}
        )
        response.raise_for_status()
        data = response.json()
        return data.get("organizations", [])

    def get_list_entries(self, list_id):
        """Get all list entries for a list."""
        response = self.session.get(
            f"{AFFINITY_BASE_URL}/lists/{list_id}/list-entries"
        )
        response.raise_for_status()
        return response.json()

    def get_field_values(self, organization_id):
        """Get field values for an organization."""
        response = self.session.get(
            f"{AFFINITY_BASE_URL}/field-values",
            params={"organization_id": organization_id}
        )
        response.raise_for_status()
        return response.json()

    def get_list_fields(self, list_id):
        """Get all fields for a list to find the stage field."""
        response = self.session.get(
            f"{AFFINITY_BASE_URL}/lists/{list_id}"
        )
        response.raise_for_status()
        return response.json().get("fields", [])

    def create_organization(self, name, domain=None):
        """Create a new organization in Affinity."""
        data = {"name": name}
        if domain:
            data["domain"] = domain
        logger.info(f"Creating organization with data: {data}")
        response = self.session.post(
            f"{AFFINITY_BASE_URL}/organizations",
            json=data
        )
        response.raise_for_status()
        return response.json()

    def add_to_list(self, list_id, organization_id):
        """Add an organization to a list (deal pipeline)."""
        logger.info(f"Adding org {organization_id} to list {list_id}")
        response = self.session.post(
            f"{AFFINITY_BASE_URL}/lists/{list_id}/list-entries",
            json={"entity_id": organization_id}
        )
        response.raise_for_status()
        return response.json()

    def get_organization(self, org_id):
        """Get a specific organization by ID."""
        response = self.session.get(
            f"{AFFINITY_BASE_URL}/organizations/{org_id}"
        )
        response.raise_for_status()
        return response.json()

    def get_field_value_changes(self, field_id):
        """Get field value changes for tracking when stages changed."""
        response = self.session.get(
            f"{AFFINITY_BASE_URL}/field-value-changes",
            params={"field_id": field_id}
        )
        response.raise_for_status()
        return response.json()

    def get_list_entry_field_values(self, list_entry_id):
        """Get field values for a specific list entry."""
        response = self.session.get(
            f"{AFFINITY_BASE_URL}/field-values",
            params={"list_entry_id": list_entry_id}
        )
        response.raise_for_status()
        return response.json()

    def set_field_value(self, field_id, entity_id, list_entry_id, value):
        """Set a field value for a list entry."""
        logger.info(f"Setting field {field_id} to {value} for entity {entity_id}, list entry {list_entry_id}")
        payload = {
            "field_id": field_id,
            "entity_id": entity_id,
            "list_entry_id": list_entry_id,
            "value": value
        }
        logger.info(f"Payload: {payload}")
        response = self.session.post(
            f"{AFFINITY_BASE_URL}/field-values",
            json=payload
        )
        if not response.ok:
            logger.error(f"Affinity error response: {response.text}")
        response.raise_for_status()
        return response.json()


affinity = AffinityClient(AFFINITY_API_KEY)


def extract_company_info(text):
    """Extract company name and domain from message text."""
    # Try to extract URL/domain
    url_pattern = r'https?://(?:www\.)?([a-zA-Z0-9-]+\.[a-zA-Z0-9.-]+)'
    domain_pattern = r'\b([a-zA-Z0-9-]+\.(?:com|io|co|ai|org|net|app|vc|xyz|tech|dev))\b'
    
    domain = None
    url_match = re.search(url_pattern, text)
    if url_match:
        domain = url_match.group(1)
    else:
        domain_match = re.search(domain_pattern, text)
        if domain_match:
            domain = domain_match.group(1)
    
    # Clean up the company name
    company_name = text.strip()
    company_name = re.sub(r'https?://(?:www\.)?', '', company_name)
    company_name = re.sub(r'\([^)]*\)', '', company_name)
    # Remove missed/miss/missing keywords
    company_name = re.sub(r'\b(missed|miss|missing|we|this|one|was|a)\b', '', company_name, flags=re.IGNORECASE)
    company_name = company_name.strip(' -–—:/')
    
    # If we have a domain, use it as the search term
    if domain:
        # Remove trailing paths from domain
        domain = domain.split('/')[0]
        # Use domain as company name if text is just a URL
        if not company_name or company_name == domain:
            company_name = domain.split('.')[0].title()
    
    return company_name, domain


def get_stage_name(organization_id, list_id):
    """Get the current stage name for an organization in a list."""
    try:
        # Get list fields to find the stage/status field
        fields = affinity.get_list_fields(list_id)
        stage_field_id = None
        stage_options = {}
        
        for field in fields:
            field_name = field.get("name", "").lower()
            if field_name in ["stage", "status", "deal stage"]:
                stage_field_id = field.get("id")
                # Build mapping of dropdown option IDs to names
                for option in field.get("dropdown_options", []):
                    stage_options[option["id"]] = option["text"]
                break
        
        if not stage_field_id:
            return "Unknown"
        
        # Get field values for this organization
        field_values = affinity.get_field_values(organization_id)
        
        for fv in field_values:
            if fv.get("field_id") == stage_field_id:
                value = fv.get("value")
                if isinstance(value, dict) and "text" in value:
                    return value["text"]
                elif isinstance(value, int) and value in stage_options:
                    return stage_options[value]
                return str(value) if value else "Not set"
        
        return "Not set"
    except Exception as e:
        logger.error(f"Error getting stage: {e}")
        return "Unknown"


def check_org_in_list(organization_id, list_id):
    """Check if an organization is already in the specified list."""
    try:
        org = affinity.get_organization(organization_id)
        list_entries = org.get("list_entries", [])
        for entry in list_entries:
            if entry.get("list_id") == int(list_id):
                return True, entry
        return False, None
    except Exception as e:
        logger.error(f"Error checking org in list: {e}")
        return False, None


def get_list_entry_details(org_id, list_id):
    """Get owner names and pass reason for an org in a list."""
    try:
        org = affinity.get_organization(org_id)
        list_entries = org.get("list_entries", [])
        
        for entry in list_entries:
            if entry.get("list_id") == int(list_id):
                list_entry_id = entry.get("id")
                field_values = affinity.get_list_entry_field_values(list_entry_id)
                
                owners = []
                pass_reasons = []
                
                for fv in field_values:
                    # Get owners
                    if fv.get("field_id") == OWNERS_FIELD_ID:
                        person_id = fv.get("value")
                        if person_id:
                            owner_name = get_owner_name_from_id(person_id)
                            if owner_name:
                                owners.append(owner_name)
                    
                    # Get pass reason
                    if fv.get("field_id") == PASS_REASON_FIELD_ID:
                        value = fv.get("value")
                        if isinstance(value, dict) and "text" in value:
                            pass_reasons.append(value["text"])
                        elif value:
                            pass_reasons.append(str(value))
                
                return owners, pass_reasons
        
        return [], []
    except Exception as e:
        logger.error(f"Error getting list entry details: {e}")
        return [], []


def process_company(search_term, domain=None, is_missed=False, slack_user_id=None):
    """Check if company exists in deal pipeline. If yes, return current stage. If no, add it."""
    try:
        # Search using domain if available, otherwise use name
        term = domain if domain else search_term
        logger.info(f"Searching for: {term}")
        
        orgs = affinity.search_organization(term)
        logger.info(f"Found {len(orgs)} organizations")
        
        organization = None
        if orgs:
            # Find best match
            for org in orgs:
                org_domain = org.get("domain", "")
                org_name = org.get("name", "").lower()
                if domain and org_domain and domain.lower() in org_domain.lower():
                    organization = org
                    break
                if search_term.lower() in org_name:
                    organization = org
                    break
            if not organization:
                organization = orgs[0]
        
        if organization:
            org_id = organization["id"]
            org_name = organization["name"]
            logger.info(f"Found organization: {org_name} (ID: {org_id})")
            
            # Check if already in deal pipeline
            in_list, entry = check_org_in_list(org_id, AFFINITY_LIST_ID)
            
            if in_list:
                # Already in pipeline - get current stage, owner, and pass reason
                stage = get_stage_name(org_id, AFFINITY_LIST_ID)
                owners, pass_reasons = get_list_entry_details(org_id, AFFINITY_LIST_ID)
                
                message = f"*{org_name}* is already in the deal pipeline.\n📊 Current stage: *{stage}*"
                
                if owners:
                    message += f"\n👤 Owner: {', '.join(owners)}"
                
                if stage == "Passed" and pass_reasons:
                    message += f"\n❌ Pass reason: {', '.join(pass_reasons)}"
                
                return {
                    "status": "exists",
                    "company": org_name,
                    "stage": stage,
                    "message": message
                }
            else:
                # Org exists but not in pipeline - add it
                list_entry = affinity.add_to_list(AFFINITY_LIST_ID, org_id)
                
                # Set owner if we have a slack user mapping
                if slack_user_id and slack_user_id in SLACK_TO_AFFINITY_PERSON:
                    try:
                        affinity_person_id = SLACK_TO_AFFINITY_PERSON[slack_user_id]
                        affinity.set_field_value(OWNERS_FIELD_ID, org_id, list_entry["id"], affinity_person_id)
                        logger.info(f"Set owner to person {affinity_person_id}")
                    except Exception as e:
                        logger.error(f"Error setting owner: {e}")
                
                # If marked as missed, set the status
                if is_missed:
                    try:
                        affinity.set_field_value(STATUS_FIELD_ID, org_id, list_entry["id"], MISSED_STATUS_VALUE_ID)
                        return {
                            "status": "added",
                            "company": org_name,
                            "message": f"😢 Added *{org_name}* to the deal pipeline as *Missed*."
                        }
                    except Exception as e:
                        logger.error(f"Error setting missed status: {e}")
                
                return {
                    "status": "added",
                    "company": org_name,
                    "message": f"✅ Added *{org_name}* to the deal pipeline as a new lead."
                }
        else:
            # Create new organization and add to pipeline
            logger.info(f"Creating new organization: {search_term}")
            new_org = affinity.create_organization(search_term, domain)
            org_id = new_org["id"]
            org_name = new_org["name"]
            logger.info(f"Created organization: {org_name} (ID: {org_id})")
            
            list_entry = affinity.add_to_list(AFFINITY_LIST_ID, org_id)
            
            # Set owner if we have a slack user mapping
            if slack_user_id and slack_user_id in SLACK_TO_AFFINITY_PERSON:
                try:
                    affinity_person_id = SLACK_TO_AFFINITY_PERSON[slack_user_id]
                    affinity.set_field_value(OWNERS_FIELD_ID, org_id, list_entry["id"], affinity_person_id)
                    logger.info(f"Set owner to person {affinity_person_id}")
                except Exception as e:
                    logger.error(f"Error setting owner: {e}")
            
            # If marked as missed, set the status
            if is_missed:
                try:
                    affinity.set_field_value(STATUS_FIELD_ID, org_id, list_entry["id"], MISSED_STATUS_VALUE_ID)
                    return {
                        "status": "created",
                        "company": org_name,
                        "message": f"😢 Created *{org_name}* and added to the deal pipeline as *Missed*."
                    }
                except Exception as e:
                    logger.error(f"Error setting missed status: {e}")
            
            return {
                "status": "created",
                "company": org_name,
                "message": f"✅ Created *{org_name}* and added to the deal pipeline as a new lead."
            }
            
    except requests.exceptions.HTTPError as e:
        error_text = e.response.text if hasattr(e.response, 'text') else str(e)
        logger.error(f"Affinity API error: {error_text}")
        return {
            "status": "error",
            "message": f"❌ Error processing company: {e.response.status_code} - {error_text}"
        }
    except Exception as e:
        logger.error(f"Error processing company: {e}")
        return {
            "status": "error",
            "message": f"❌ Error processing company: {str(e)}"
        }


def get_deals_needing_nudge():
    """Get all deals that have been in a stage longer than the threshold."""
    try:
        # Get list fields to find Status and Owners fields
        fields = affinity.get_list_fields(AFFINITY_LIST_ID)
        status_field_id = None
        owners_field_id = None
        status_options = {}
        
        for field in fields:
            field_name = field.get("name", "").lower()
            if field_name in ["status", "stage"]:
                status_field_id = field.get("id")
                for option in field.get("dropdown_options", []):
                    status_options[option["id"]] = option["text"]
            elif field_name == "owners":
                owners_field_id = field.get("id")
        
        if not status_field_id:
            logger.error("Could not find Status field")
            return []
        
        # Get all list entries
        list_entries = affinity.get_list_entries(AFFINITY_LIST_ID)
        
        deals_to_nudge = []
        now = datetime.now(pytz.UTC)
        
        for entry in list_entries:
            entity_id = entry.get("entity_id")
            list_entry_id = entry.get("id")
            created_at = entry.get("created_at")
            
            # Get field values for this entry
            field_values = affinity.get_list_entry_field_values(list_entry_id)
            
            current_status = None
            status_updated_at = None
            owners = []
            
            for fv in field_values:
                if fv.get("field_id") == status_field_id:
                    value = fv.get("value")
                    if isinstance(value, dict) and "text" in value:
                        current_status = value["text"]
                    elif isinstance(value, int) and value in status_options:
                        current_status = status_options[value]
                    status_updated_at = fv.get("updated_at") or fv.get("created_at")
                
                elif fv.get("field_id") == owners_field_id:
                    # Owner field value is a person ID, need to resolve name
                    owner_value = fv.get("value")
                    if owner_value:
                        owners.append(owner_value)
            
            # Check if this status needs a nudge
            if current_status and current_status in STAGE_THRESHOLDS:
                threshold_days = STAGE_THRESHOLDS[current_status]
                
                # Parse the date when status was set
                if status_updated_at:
                    try:
                        status_date = datetime.fromisoformat(status_updated_at.replace('Z', '+00:00'))
                    except:
                        status_date = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                else:
                    status_date = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                
                days_in_stage = (now - status_date).days
                
                if days_in_stage >= threshold_days:
                    # Get org details
                    try:
                        org = affinity.get_organization(entity_id)
                        org_name = org.get("name", "Unknown")
                        
                        weeks_in_stage = days_in_stage // 7
                        week_text = f"{weeks_in_stage} week" + ("s" if weeks_in_stage != 1 else "")
                        
                        deals_to_nudge.append({
                            "org_id": entity_id,
                            "org_name": org_name,
                            "status": current_status,
                            "days_in_stage": days_in_stage,
                            "week_text": week_text,
                            "owners": owners,
                            "link": f"https://overture.affinity.co/companies/{entity_id}"
                        })
                    except Exception as e:
                        logger.error(f"Error getting org {entity_id}: {e}")
        
        return deals_to_nudge
        
    except Exception as e:
        logger.error(f"Error getting deals needing nudge: {e}")
        return []


def get_owner_name_from_id(person_id):
    """Get person name from Affinity person ID."""
    try:
        response = affinity.session.get(f"{AFFINITY_BASE_URL}/persons/{person_id}")
        response.raise_for_status()
        person = response.json()
        first_name = person.get("first_name", "")
        last_name = person.get("last_name", "")
        return f"{first_name} {last_name}".strip()
    except:
        return None


def send_nudge_messages():
    """Check for deals needing nudges and send Slack messages."""
    logger.info("Running nudge check...")
    
    if not NUDGE_CHANNEL_ID:
        logger.error("NUDGE_CHANNEL_ID not set")
        return
    
    deals = get_deals_needing_nudge()
    logger.info(f"Found {len(deals)} deals needing nudges")
    
    for deal in deals:
        # Determine who to tag
        slack_mention = ""
        
        if deal["owners"]:
            # Get first owner's name and map to Slack ID
            owner_name = get_owner_name_from_id(deal["owners"][0])
            if owner_name and owner_name in OWNER_SLACK_MAP:
                slack_id = OWNER_SLACK_MAP[owner_name]
                slack_mention = f"<@{slack_id}> "
        
        message = f"{slack_mention}{deal['org_name']} has been in \"{deal['status']}\" for {deal['week_text']}. Link: {deal['link']}"
        
        try:
            app.client.chat_postMessage(
                channel=NUDGE_CHANNEL_ID,
                text=message
            )
            logger.info(f"Sent nudge for {deal['org_name']}")
        except Exception as e:
            logger.error(f"Error sending nudge for {deal['org_name']}: {e}")


def run_scheduler():
    """Run the scheduler in a separate thread."""
    # Schedule nudge check at 9am PT on Tuesdays
    pacific = pytz.timezone('America/Los_Angeles')
    schedule.every().tuesday.at("09:00").do(send_nudge_messages)
    
    logger.info("Scheduler started - nudges will run Tuesdays at 9am PT")
    
    while True:
        schedule.run_pending()
        time.sleep(60)  # Check every minute


@app.event("message")
def handle_message(event, say, client):
    """Handle messages posted to #dealflow channel."""
    if event.get("subtype") in ["bot_message", "message_changed", "message_deleted"]:
        return
    
    channel_id = event.get("channel")
    text = event.get("text", "").strip()
    
    # Check for manual nudge test command
    if text.lower() == "!nudge-test":
        say(text="🔄 Running nudge check...")
        send_nudge_messages()
        say(text="✅ Nudge check complete!")
        return
    
    try:
        channel_info = client.conversations_info(channel=channel_id)
        channel_name = channel_info["channel"]["name"]
        
        if channel_name != "dealflow":
            return
    except Exception as e:
        logger.error(f"Error getting channel info: {e}")
        return
    
    if not text:
        return
    
    # Only process messages that contain a URL
    url_pattern = r'https?://[^\s]+'
    if not re.search(url_pattern, text):
        return
    
    # Check if this is a "missed" deal BEFORE extracting company info
    missed_pattern = r'\b(missed|miss|missing)\b'
    is_missed = bool(re.search(missed_pattern, text.lower()))
    logger.info(f"Processing message: {text} (is_missed: {is_missed})")
    
    company_name, domain = extract_company_info(text)
    logger.info(f"Extracted - Name: {company_name}, Domain: {domain}")
    
    if not company_name and not domain:
        return
    
    user_id = event.get("user")
    
    result = process_company(company_name, domain, is_missed=is_missed, slack_user_id=user_id)
    
    say(text=f"<@{user_id}> {result['message']}")


@app.event("app_mention")
def handle_mention(event, say):
    """Handle direct mentions of the bot."""
    say(
        text="👋 I'm monitoring #dealflow for company names. Post a company name there and I'll automatically add it to Affinity!",
        thread_ts=event.get("ts")
    )


if __name__ == "__main__":
    # Start scheduler in background thread
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    logger.info("Starting Slack bot with nudge scheduler...")
    handler.start()
