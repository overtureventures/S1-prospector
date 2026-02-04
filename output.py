"""
Output module for writing results to Google Sheets or CSV.
"""

import os
import csv
from typing import List, Dict
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Google Sheets imports (optional)
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False
    logger.warning("gspread not available - Google Sheets output disabled")


def write_to_csv(investors: List[Dict], filename: str):
    """
    Write investor data to a CSV file.
    """
    if not investors:
        logger.warning("No investors to write")
        return
    
    # Define column order
    columns = [
        'investor_name',
        'company_ipo',
        'filing_date',
        'ownership_pct',
        'shares',
        'entity_type',
        'in_crm',
        'crm_status',
        'crm_last_activity',
        'crm_notes',
        'foundation_contacts',
        'linkedin_search_url'
    ]
    
    filepath = f"/home/claude/{filename}"
    
    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(investors)
    
    logger.info(f"Wrote {len(investors)} investors to {filepath}")
    
    # Also copy to outputs for user access
    output_path = f"/mnt/user-data/outputs/{filename}"
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(investors)
    
    logger.info(f"Copied to {output_path}")


def write_to_google_sheet(investors: List[Dict], sheet_id: str, run_date: str):
    """
    Write investor data to a Google Sheet.
    
    Requires:
    - GOOGLE_CREDENTIALS_PATH env var pointing to service account JSON
    - Sheet must be shared with the service account email
    """
    if not GSPREAD_AVAILABLE:
        logger.error("gspread not installed - cannot write to Google Sheets")
        write_to_csv(investors, f"s1_investors_{run_date}.csv")
        return
    
    if not investors:
        logger.warning("No investors to write")
        return
    
    creds_path = os.getenv('GOOGLE_CREDENTIALS_PATH', 'credentials.json')
    
    try:
        # Authenticate with Google
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
        client = gspread.authorize(creds)
        
        # Open the spreadsheet
        spreadsheet = client.open_by_key(sheet_id)
        
        # Create a new worksheet for this run, or use existing
        worksheet_name = f"Run {run_date}"
        
        try:
            worksheet = spreadsheet.worksheet(worksheet_name)
            worksheet.clear()
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(
                title=worksheet_name,
                rows=len(investors) + 10,
                cols=15
            )
        
        # Prepare headers
        headers = [
            'Investor Name',
            'IPO Company',
            'Filing Date',
            'Ownership %',
            'Shares',
            'Entity Type',
            'In CRM?',
            'CRM Status',
            'Last Activity',
            'CRM Notes',
            'Foundation Contacts',
            'LinkedIn Search'
        ]
        
        # Prepare rows
        rows = [headers]
        for inv in investors:
            rows.append([
                inv.get('investor_name', ''),
                inv.get('company_ipo', ''),
                inv.get('filing_date', ''),
                inv.get('ownership_pct', ''),
                inv.get('shares', ''),
                inv.get('entity_type', ''),
                'Yes' if inv.get('in_crm') else 'No',
                inv.get('crm_status', ''),
                inv.get('crm_last_activity', ''),
                inv.get('crm_notes', ''),
                inv.get('foundation_contacts', ''),
                inv.get('linkedin_search_url', '')
            ])
        
        # Write to sheet
        worksheet.update('A1', rows)
        
        # Format header row
        worksheet.format('A1:L1', {
            'textFormat': {'bold': True},
            'backgroundColor': {'red': 0.9, 'green': 0.9, 'blue': 0.9}
        })
        
        # Auto-resize columns
        worksheet.columns_auto_resize(0, 11)
        
        logger.info(f"Wrote {len(investors)} investors to Google Sheet '{worksheet_name}'")
        
        # Also update the summary worksheet
        update_summary_sheet(spreadsheet, investors, run_date)
        
    except Exception as e:
        logger.error(f"Error writing to Google Sheets: {e}")
        # Fallback to CSV
        write_to_csv(investors, f"s1_investors_{run_date}.csv")


def update_summary_sheet(spreadsheet, investors: List[Dict], run_date: str):
    """
    Update a summary worksheet with aggregate stats.
    """
    try:
        # Get or create summary sheet
        try:
            summary = spreadsheet.worksheet('Summary')
        except gspread.WorksheetNotFound:
            summary = spreadsheet.add_worksheet(title='Summary', rows=100, cols=10)
            summary.update('A1', [['Date', 'Total Investors', 'In CRM', 'New', 'Foundations', 'Family Offices', 'Funds']])
            summary.format('A1:G1', {'textFormat': {'bold': True}})
        
        # Calculate stats
        total = len(investors)
        in_crm = sum(1 for i in investors if i.get('in_crm'))
        new = total - in_crm
        foundations = sum(1 for i in investors if i.get('entity_type') == 'foundation')
        family_offices = sum(1 for i in investors if i.get('entity_type') == 'family_office')
        funds = sum(1 for i in investors if i.get('entity_type') == 'fund')
        
        # Append new row
        summary.append_row([run_date, total, in_crm, new, foundations, family_offices, funds])
        
        logger.info("Updated summary sheet")
        
    except Exception as e:
        logger.error(f"Error updating summary sheet: {e}")


def format_for_email(investors: List[Dict]) -> str:
    """
    Format investor data for email notification.
    """
    lines = []
    lines.append("S-1 Prospector Weekly Report")
    lines.append("=" * 40)
    lines.append("")
    
    # Summary
    total = len(investors)
    in_crm = sum(1 for i in investors if i.get('in_crm'))
    new = total - in_crm
    
    lines.append(f"Total investors found: {total}")
    lines.append(f"Already in CRM: {in_crm}")
    lines.append(f"New prospects: {new}")
    lines.append("")
    
    # New prospects (prioritize these)
    new_prospects = [i for i in investors if not i.get('in_crm')]
    if new_prospects:
        lines.append("NEW PROSPECTS")
        lines.append("-" * 40)
        for inv in new_prospects[:20]:  # Limit to top 20
            lines.append(f"• {inv['investor_name']} ({inv['entity_type']})")
            lines.append(f"  IPO: {inv['company_ipo']} | {inv['ownership_pct']}%")
            if inv.get('foundation_contacts'):
                lines.append(f"  Contacts: {inv['foundation_contacts']}")
            lines.append("")
    
    # Existing contacts with new exits
    existing = [i for i in investors if i.get('in_crm')]
    if existing:
        lines.append("")
        lines.append("EXISTING CONTACTS WITH NEW EXITS")
        lines.append("-" * 40)
        for inv in existing[:10]:
            lines.append(f"• {inv['investor_name']}")
            lines.append(f"  IPO: {inv['company_ipo']} | Status: {inv.get('crm_status', 'N/A')}")
            lines.append("")
    
    return "\n".join(lines)
