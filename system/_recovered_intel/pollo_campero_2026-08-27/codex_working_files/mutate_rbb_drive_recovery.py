import json
from pathlib import Path

ROOT = Path('/private/tmp/rbb_pollo_drive')
ACCOUNT = 'acct-pollo-campero'
OPP = 'opp-pollo-campero-dmb-payments'
AS_OF = '2026-08-27'
REVIEWER = 'human:Todd Vahlsing'


def fact(value, status, evidence_ids, confidence='high', scope=f'opportunity:{OPP}'):
    return {
        'value': value,
        'status': status,
        'evidence_ids': evidence_ids,
        'confidence': confidence,
        'as_of': AS_OF,
        'scope': scope,
        'last_reviewed_by': REVIEWER,
    }


def load(name):
    return json.loads((ROOT / name).read_text())


def save(name, value):
    (ROOT / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


account = load('account.json')
opp = next(x for x in account['opportunities'] if x['opportunity_id'] == OPP)
opp['stage'] = 'RFP active; revised proposal and completed pricing due 2026-09-04'
opp['procurement'] = {
    'previous_stage': 'RFI submitted',
    'current_stage': 'RFP / active competitive procurement',
    'rfi_response_date': '2026-08-21',
    'rfp_received_date': '2026-08-26',
    'response_deadline': '2026-09-04',
    'primary_procurement_contact_id': 'person-katherine-celeste-urbina-barillas',
    'required_deliverables': [
        'Revised proposal reflecting updated TDR requirements',
        'Completed SOW and pricing template',
        'All applicable costs, assumptions, optional services, exclusions and supporting details',
    ],
    'evidence_ids': ['ev-pollo-campero-0013', 'ev-pollo-campero-0014', 'ev-pollo-campero-0015'],
    'confidence': 'high',
    'as_of': AS_OF,
}
opp['rfi_commitment_baseline'] = {
    'source_evidence_id': 'ev-pollo-campero-0016',
    'status': 'submitted baseline; requires internal validation before conversion to priced commitments',
    'material_claims': [
        'Native pricing and order-confirmation integrations claimed for Genius POS, PAR Brink and NCR',
        'Oracle positioned as an API-enabled third-party integration subject to agreements and potential fees',
        'Near-real-time pricing and item availability supported subject to integration and display processing',
        'Dynamic upselling supported natively in Genius; third-party transaction-aware behavior depends on POS APIs',
        'Existing display reuse supported subject to model, age, condition and useful-life assessment',
        'Controller, controllerless and dual-display media-player options offered',
        'Installation, testing and go-live support described as included in the proposal',
        'Example support SLAs supplied',
        'Three-year warranty stated for quoted hardware',
    ],
}
opp['rfp_requirement_deltas'] = {
    'critical': [
        'Oracle and NCR integration must be validated by exact product/version and interface',
        'On-screen order confirmation requires lane mapping, data definition and acceptance criteria',
        'Transaction-aware dynamic upselling requires third-party POS validation or a qualified pilot position',
        'Vendor-facility storage, chain of custody, inventory reporting and redeployment operating model',
    ],
    'high': [
        'Warehouse Stream inventory inspection and compatibility assessment',
        'De-installation at closing or refreshed locations',
        'Managed content workflow, included volumes, turnaround SLAs and fees',
        'Installation inclusions/exclusions and site-readiness responsibility',
        'DMB-specific support, field service, replacement protocol and approved SLAs',
        'Warranty treatment for new versus reused equipment',
        'Billing frequency, deposits, milestones, invoice timing and pricing validity',
    ],
    'optional': ['In-store music', 'Guest Wi-Fi'],
    'evidence_ids': ['ev-pollo-campero-0014', 'ev-pollo-campero-0015', 'ev-pollo-campero-0016'],
}

for criterion in account.get('qualification', {}).get('criteria', []):
    if criterion.get('criterion') == 'Customer buying process is understood':
        criterion['current_read'] = 'Formal RFP received. Revised proposal and completed SOW/pricing are due 2026-09-04; evaluation scorecard, approvers and veto roles remain unverified.'
        criterion['next_step'] = 'Submit consolidated clarifications and confirm evaluation criteria, decision calendar, approvers and vetoes.'
        criterion['evidence_ids'] = sorted(set(criterion.get('evidence_ids', []) + ['ev-pollo-campero-0013', 'ev-pollo-campero-0015']))
        criterion['as_of'] = AS_OF

if not any(x.get('person_id') == 'person-katherine-celeste-urbina-barillas' for x in account.get('buying_influences', [])):
    account.setdefault('buying_influences', []).append({
        'person_id': 'person-katherine-celeste-urbina-barillas',
        'name': 'Katherine Celeste Urbina Barillas',
        'title': 'Purchasing Specialist',
        'location': 'CMI / Pollo Campero',
        'email': 'katherine.urbina@somoscmi.com',
        'role_etuc': fact('Procurement contact / U?', 'reported', ['ev-pollo-campero-0013'], 'high'),
        'influence': 'High process influence; economic and technical authority unverified',
        'mode': fact('Even Keel', 'hypothesis', ['ev-pollo-campero-0013'], 'low'),
        'personal_win': fact('Run a complete, comparable and timely procurement process.', 'hypothesis', ['ev-pollo-campero-0013'], 'low'),
        'business_result': 'Receive a complete revised proposal and priced SOW responsive to the updated requirements.',
        'competitive_preference': fact('Unknown', 'unknown', [], 'low'),
        'rating': fact(0, 'unknown', [], 'low'),
        'current_read': 'Primary procurement channel. Transmitted the updated TDR and SOW/pricing template and established the 2026-09-04 deadline.',
        'access': 'Direct email / formal procurement channel',
        'next_step': 'Send one consolidated clarification set and submit the revised proposal and pricing on time.',
        'owner': 'Todd / proposal lead',
    })

account['latest_review'] = {
    'blue_sheet_owner': 'Todd Vahlsing',
    'core_contributors': 'Amy McKay; assigned solution, integration, operations, support, finance, legal and executive sponsors',
    'last_reviewed': AS_OF,
    'review_status': 'RFP active / response build and validation in progress',
    'next_formal_review': 'Before pricing certification and before 2026-09-04 submission',
    'current_critical_test': 'Can Global Payments convert the RFI claims into approved, priced commitments for third-party integrations and full asset lifecycle services?',
}
account['updated_at'] = '2026-08-27T13:15:00Z'
save('account.json', account)

brand = load('brand_profile.json')
history = brand.setdefault('relationship_history', [])
history = [x for x in history if x.get('category') != 'current_pursuit']
history.append({
    'category': 'current_pursuit',
    'value': 'Global Payments/Genius submitted the DMB RFI response on 2026-08-21. Pollo Campero advanced the procurement to a formal RFP and delivered the updated TDR and SOW/pricing template on 2026-08-26. Revised response is due 2026-09-04.',
    'strategic_implication': 'Active competitive procurement. DMB remains the primary motion; Worldpay/payments and broader Genius consolidation are optional expansion paths, not prerequisites.',
    'status': 'confirmed',
    'confidence': 'high',
    'as_of': AS_OF,
    'scope': f'opportunity:{OPP}',
    'evidence_ids': ['ev-pollo-campero-0013', 'ev-pollo-campero-0014', 'ev-pollo-campero-0015', 'ev-pollo-campero-0016'],
    'last_reviewed_by': REVIEWER,
})
brand['relationship_history'] = history
brand.setdefault('open_questions', [])
new_questions = [
    'Which Oracle and NCR products and versions are deployed by location?',
    'What exact APIs, middleware and certifications are available for pricing, availability, order confirmation and transaction-aware upselling?',
    'What is the location-level display/player/mount/lane inventory, including age and ownership?',
    'Is transaction-aware dynamic upselling mandatory at launch or acceptable as a pilot/later phase?',
    'What storage duration, insurance value, reporting frequency and disposition rules apply to removed equipment?',
    'What content request volumes and standard, urgent and emergency turnaround expectations apply?',
    'What support hours, severity definitions, restoration targets and onsite dispatch times are required?',
]
for q in new_questions:
    if q not in brand['open_questions']:
        brand['open_questions'].append(q)
brand['freshness'] = {'last_updated': AS_OF, 'next_scheduled_review': 'Before 2026-09-04 submission or on material new intelligence'}
save('brand_profile.json', brand)

actions = load('actions.json')
new_actions = [
    ('0015', 'dependency', 'Validate NCR DMB/OCU integration', 'Product + Integration', 'Document supported NCR/Aloha versions, data flows, dependencies, certification, support and limitations.', 'Before response finalization', 'Written architecture approved.'),
    ('0016', 'dependency', 'Validate Oracle DMB integration', 'Product + Integration', 'Identify Oracle product/version, APIs, agreements, certification, effort, fees and support model.', 'Before response finalization', 'Written comply-with-assumptions position approved.'),
    ('0017', 'risk', 'Validate transaction-aware dynamic upselling', 'Product', 'Separate rules-based suggestive selling from live third-party POS transaction-event behavior; define pilot if needed.', 'Before response finalization', 'Capability position and acceptance criteria approved.'),
    ('0018', 'dependency', 'Define removed-asset lifecycle service', 'Operations + Field Services', 'Define de-installation, tagging, grading, custody, insurance, storage, reporting, testing, freight and redeployment.', 'Before pricing certification', 'Operating model, partner/capacity and unit costs approved.'),
    ('0019', 'commitment', 'Complete and validate SOW pricing', 'Finance + Sales Operations', 'Correct placeholder fee bases and populate every required/optional row with assumptions, exclusions and approved pricing.', '2026-09-04', 'Certified pricing workbook completed.'),
    ('0020', 'commitment', 'Submit consolidated clarification questions', 'Todd / proposal lead', 'Send one consolidated question set covering estate, POS, integrations, content, support, asset management and commercial terms.', 'Immediate', 'Questions sent to Katherine and logged.'),
    ('0021', 'dependency', 'Validate existing-display compatibility and warranty policy', 'Hardware + Support', 'Define supported Samsung/LG models, inspection thresholds, useful life, media-player architecture and reused-equipment SLA/warranty.', 'Before pricing certification', 'Compatibility and warranty matrix approved.'),
    ('0022', 'dependency', 'Define installation inclusions and exclusions', 'Implementation + Legal', 'Clarify standard labor, travel, lifts, after-hours work, electrical/network work, permits, structural/civil work and revisit rules.', 'Before pricing certification', 'Standard installation scope and responsibility matrix approved.'),
    ('0023', 'dependency', 'Define managed content service', 'Content Operations', 'Define intake, approvals, included request volumes, turnaround SLAs, emergency requests, creative scope and overage fees.', 'Before pricing certification', 'Managed-content service catalog and pricing approved.'),
    ('0024', 'commitment', 'Submit revised RFP response', 'Todd / proposal lead', 'Deliver revised proposal and completed SOW/pricing template through Katherine.', '2026-09-04', 'Submission confirmation retained as evidence.'),
]
existing_ids = {x['action_id'] for x in actions['actions']}
for suffix, typ, issue, owner, description, target, closure in new_actions:
    action_id = f'act-pollo-campero-{suffix}'
    if action_id in existing_ids:
        continue
    actions['actions'].append({
        'action_id': action_id,
        'opportunity_id': OPP,
        'type': typ,
        'issue': issue,
        'party': 'internal' if typ != 'commitment' or suffix != '0020' else 'Katherine Urbina',
        'description': description,
        'owner': owner,
        'target': target,
        'status': 'open',
        'blocker': 'Internal validation or customer clarification pending.',
        'evidence_ids': ['ev-pollo-campero-0013', 'ev-pollo-campero-0014', 'ev-pollo-campero-0015', 'ev-pollo-campero-0016'],
        'closure_criteria': closure,
        'rbb_loop_id': None,
        'mutation_status': 'proposed_add_loop',
    })
save('actions.json', actions)

source_index = load('source_index.json')
new_sources = [
    {
        'source_id': 'src-pollo-campero-0011',
        'durable_source_id': 'drive:180_wXodEG8RrSm3jk5EqfGcgqDAk1YtH',
        'type': 'submitted_rfi_response',
        'description': 'Global Payments/Genius submitted RFI response dated 2026-08-21.',
        'linked_evidence_ids': ['ev-pollo-campero-0016'],
    },
    {
        'source_id': 'src-pollo-campero-0012',
        'durable_source_id': 'drive:16vFQLGQQOJijZybkbbwQPRr0S1Zpji6d',
        'type': 'customer_rfp_tdr',
        'description': 'Pollo Campero TDR V3 received 2026-08-26.',
        'linked_evidence_ids': ['ev-pollo-campero-0014'],
    },
    {
        'source_id': 'src-pollo-campero-0013',
        'durable_source_id': 'drive:1rkSONTHZp2DCre3YBiNOnl60mXdt_GLt',
        'type': 'customer_sow_pricing_template',
        'description': 'Pollo Campero SOW and pricing workbook received 2026-08-26.',
        'linked_evidence_ids': ['ev-pollo-campero-0015'],
    },
    {
        'source_id': 'src-pollo-campero-0014',
        'durable_source_id': 'drive:1zSl2b1qWzzDwWPhAyggjr8mkKjMt8KGD',
        'type': 'email_transmittal_screenshot',
        'description': 'Katherine Urbina RFP transmittal establishing revised-response request and 2026-09-04 deadline.',
        'linked_evidence_ids': ['ev-pollo-campero-0013'],
    },
]
existing_sources = {x['source_id'] for x in source_index['sources']}
source_index['sources'].extend(x for x in new_sources if x['source_id'] not in existing_sources)
source_index['note'] = "Canonical RFI/RFP Drive identifiers were resolved on 2026-08-27. Older durable_source_id values prefixed 'unresolved:' remain placeholders only for the sources they name."
save('source_index.json', source_index)

evidence_path = ROOT / 'evidence.jsonl'
rows = [json.loads(line) for line in evidence_path.read_text().splitlines() if line.strip()]
existing_evidence = {x['evidence_id'] for x in rows}
new_evidence = [
    {
        'evidence_id': 'ev-pollo-campero-0013', 'account_id': ACCOUNT, 'opportunity_ids': [OPP],
        'source_type': 'customer_email', 'durable_source_id': 'drive:1zSl2b1qWzzDwWPhAyggjr8mkKjMt8KGD',
        'source_author': 'Katherine Celeste Urbina Barillas', 'participants': ['Todd Vahlsing'],
        'event_date': '2026-08-26', 'ingestion_date': AS_OF,
        'excerpt': 'The process transitioned from RFI to formal RFP. Pollo Campero requested a revised proposal and completed SOW/pricing template by Friday, September 4, 2026.',
        'extracted_claims': ['Procurement advanced from RFI to RFP', 'Revised proposal required', 'Completed SOW/pricing required', 'Response deadline is 2026-09-04'],
        'evidence_class': 'customer_confirmed', 'confidence': 'high', 'scope': f'opportunity:{OPP}',
        'limitations': 'Screenshot captures the transmittal; full email metadata should be linked if a durable Gmail message ID becomes available.',
        'contradiction_links': [], 'processing_version': 'recovery-2026-08-27',
    },
    {
        'evidence_id': 'ev-pollo-campero-0014', 'account_id': ACCOUNT, 'opportunity_ids': [OPP],
        'source_type': 'customer_rfp_tdr', 'durable_source_id': 'drive:16vFQLGQQOJijZybkbbwQPRr0S1Zpji6d',
        'source_author': 'Katherine Celeste Urbina Barillas / Pollo Campero', 'participants': [],
        'event_date': '2026-08-19', 'ingestion_date': AS_OF,
        'excerpt': 'TDR V3 defines an approximately 150-location U.S. DMB modernization with Oracle/NCR integration, order confirmation, near-real-time price and availability, dynamic upselling, full installation, de-installation, asset storage and support.',
        'extracted_claims': ['Approximately 150 U.S. locations', 'Oracle and NCR integration required', 'Order confirmation required on outdoor menu screens', 'De-installation and vendor-facility asset management required', 'Music and guest Wi-Fi may be considered'],
        'evidence_class': 'customer_confirmed', 'confidence': 'high', 'scope': f'opportunity:{OPP}',
        'limitations': 'Actual configurations vary by location and require site validation.', 'contradiction_links': [], 'processing_version': 'recovery-2026-08-27',
    },
    {
        'evidence_id': 'ev-pollo-campero-0015', 'account_id': ACCOUNT, 'opportunity_ids': [OPP],
        'source_type': 'customer_sow_pricing_template', 'durable_source_id': 'drive:1rkSONTHZp2DCre3YBiNOnl60mXdt_GLt',
        'source_author': 'Pollo Campero', 'participants': [], 'event_date': '2026-08-26', 'ingestion_date': AS_OF,
        'excerpt': 'The SOW requires line-level vendor responses, assumptions, billing basis and total fees for project management, surveys, hardware, integrations, content, installation, asset lifecycle, support, warranty and optional services.',
        'extracted_claims': ['Vendor response and pricing cells are blank', 'Required asset lifecycle includes storage and redeployment', 'Managed menu content is a required priced service', 'Commercial terms and vendor certification are required'],
        'evidence_class': 'customer_confirmed', 'confidence': 'high', 'scope': f'opportunity:{OPP}',
        'limitations': 'Prefilled fee types appear inconsistent and should not be treated as accepted commercial positions without clarification.', 'contradiction_links': [], 'processing_version': 'recovery-2026-08-27',
    },
    {
        'evidence_id': 'ev-pollo-campero-0016', 'account_id': ACCOUNT, 'opportunity_ids': [OPP],
        'source_type': 'submitted_rfi_response', 'durable_source_id': 'drive:180_wXodEG8RrSm3jk5EqfGcgqDAk1YtH',
        'source_author': 'Global Payments / Genius', 'participants': ['Todd Vahlsing', 'Michael Schwartz', 'Tracy Gallimore'],
        'event_date': '2026-08-21', 'ingestion_date': AS_OF,
        'excerpt': 'The submitted RFI provides the baseline solution, implementation and support claims, including NCR/native and Oracle/API-dependent integration positions, existing-equipment reuse, managed content options, example SLAs and a three-year hardware warranty statement.',
        'extracted_claims': ['NCR pricing and OCU integration described as native', 'Oracle integration remains API/discovery-dependent', 'Third-party transaction-aware upselling depends on available POS APIs', 'De-installation was explicitly to be determined', 'Three-year hardware warranty was stated'],
        'evidence_class': 'seller_submitted_commitment_baseline', 'confidence': 'high', 'scope': f'opportunity:{OPP}',
        'limitations': 'RFI is discussion-only under its legal disclaimer; product, operations, support, legal and pricing teams must validate claims before final RFP commitment.', 'contradiction_links': [], 'processing_version': 'recovery-2026-08-27',
    },
]
rows.extend(x for x in new_evidence if x['evidence_id'] not in existing_evidence)
evidence_path.write_text('\n'.join(json.dumps(x, ensure_ascii=False) for x in rows) + '\n')

receipt = {
    'event_id': 'mutation-pollo-campero-rfi-rfp-2026-08-27',
    'account_id': ACCOUNT,
    'opportunity_id': OPP,
    'mutation_date': AS_OF,
    'status': 'prepared_for_drive_write',
    'records_updated': ['account.json', 'brand_profile.json', 'actions.json', 'evidence.jsonl', 'source_index.json'],
    'records_unchanged': ['contradictions.json'],
    'sources_persisted': [
        'drive:180_wXodEG8RrSm3jk5EqfGcgqDAk1YtH', 'drive:16vFQLGQQOJijZybkbbwQPRr0S1Zpji6d',
        'drive:1rkSONTHZp2DCre3YBiNOnl60mXdt_GLt', 'drive:1zSl2b1qWzzDwWPhAyggjr8mkKjMt8KGD',
    ],
    'summary': 'Advanced the DMB opportunity to formal RFP, recorded the 2026-09-04 deadline, added Katherine, persisted source lineage, captured RFI commitments and RFP deltas, and added response actions.',
}
(ROOT / 'mutation_receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
