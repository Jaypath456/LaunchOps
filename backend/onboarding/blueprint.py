"""Versioned demo workflow and server-owned evidence forms.

Fields deliberately collect references and confirmations, never passwords or ID numbers.
"""
BLUEPRINT = [
    ('profile', 'Verify employee profile', 'Confirm the employee, start date and team.', 'hr', 'approval', -7, []),
    ('contract', 'Confirm paperwork', 'Record the signed paperwork reference and review date.', 'hr', 'approval', -6, ['profile']),
    ('payroll', 'Confirm payroll setup', 'Record the internal payroll ticket without financial details.', 'hr', 'approval', -5, ['contract']),
    ('policy', 'Assign required policies', 'Choose the policy packet and record its distribution.', 'hr', 'approval', -4, ['profile']),
    ('equipment', 'Prepare equipment', 'Assign a device and confirm its delivery plan.', 'it', 'provisioning', -3, ['profile']),
    ('access', 'Provision work access', 'Confirm simulated account provisioning with a ticket reference.', 'it', 'provisioning', -2, ['profile']),
    ('mfa', 'Verify multi-factor authentication', 'Confirm MFA enrollment without recording recovery codes.', 'it', 'provisioning', -1, ['access']),
    ('endpoint', 'Verify device security', 'Confirm encryption and endpoint protection.', 'it', 'provisioning', 0, ['equipment']),
    ('it_handoff', 'Complete IT handoff', 'Confirm the new hire received device and support instructions.', 'it', 'provisioning', 1, ['mfa', 'endpoint']),
    ('mentor', 'Assign a mentor', 'Introduce the new hire to a team mentor.', 'manager', 'orientation', -1, ['profile']),
    ('welcome', 'Attend team orientation', 'Confirm when and where the orientation took place.', 'manager', 'orientation', 1, ['equipment', 'mentor']),
    ('goals', 'Set first-week goals', 'Document a concrete goal and owner.', 'manager', 'orientation', 2, ['welcome']),
    ('calendar', 'Schedule check-ins', 'Record the date of the first one-to-one meeting.', 'manager', 'orientation', 2, ['welcome']),
    ('manager_signoff', 'Manager check-in', 'Confirm goals and check-ins have been discussed.', 'manager', 'approval', 4, ['goals', 'calendar']),
    ('contact', 'Confirm contact preferences', 'Tell the team how to reach you at work.', 'employee', 'orientation', 1, ['profile']),
    ('handbook', 'Review team handbook', 'Read the handbook and confirm the version.', 'employee', 'training', 2, ['policy']),
    ('training', 'Complete security training', 'Read the linked track and pass every published assessment.', 'employee', 'training', 3, ['access']),
    ('receipt', 'Acknowledge equipment receipt', 'Confirm the device arrived and report its condition.', 'employee', 'provisioning', 3, ['it_handoff']),
    ('feedback', 'Share first-week feedback', 'Report any blockers or confirm you have none.', 'employee', 'orientation', 4, ['handbook', 'training']),
    ('ready', 'Confirm onboarding readiness', 'Review the completed tasks before closing onboarding.', 'hr', 'approval', 5,
     ['payroll', 'policy', 'it_handoff', 'manager_signoff', 'contact', 'feedback', 'receipt']),
]


def field(name, label, kind='text', **kwargs):
    return {'name': name, 'label': label, 'type': kind, 'required': True, **kwargs}


FORMS = {
    'profile': [field('verification', 'Verification method', 'select', options=['HR records', 'Video call', 'In person']), field('verified', 'I confirmed the employee and start date', 'checkbox')],
    'contract': [field('reference', 'Paperwork ticket reference'), field('review_date', 'Date paperwork was reviewed', 'date')],
    'payroll': [field('reference', 'Payroll setup ticket reference'), field('confirmed', 'Payroll team confirmed setup', 'checkbox')],
    'policy': [field('packet', 'Policy packet', 'select', options=['Standard', 'Remote', 'On site']), field('sent_date', 'Date packet was sent', 'date')],
    'equipment': [field('asset_tag', 'Device asset tag'), field('delivery_date', 'Delivery or collection date', 'date')],
    'access': [field('ticket', 'Access request ticket'), field('services', 'Services enabled (no credentials)')],
    'mfa': [field('method', 'MFA method', 'select', options=['Authenticator app', 'Security key']), field('verified', 'MFA enrollment tested', 'checkbox')],
    'endpoint': [field('asset_tag', 'Device asset tag'), field('encrypted', 'Disk encryption checked', 'checkbox'), field('protected', 'Endpoint protection checked', 'checkbox')],
    'it_handoff': [field('handoff_date', 'Handoff date', 'date'), field('support', 'Support channel shared')],
    'mentor': [field('mentor_name', 'Mentor name'), field('introduced', 'Introduction sent', 'checkbox')],
    'welcome': [field('session_date', 'Orientation date', 'date'), field('location', 'Meeting place or call link reference')],
    'goals': [field('goal', 'Specific first-week goal'), field('review_date', 'Goal review date', 'date')],
    'calendar': [field('checkin_date', 'First check-in date', 'date'), field('channel', 'Meeting location or channel')],
    'manager_signoff': [field('notes', 'Summary of the check-in'), field('confirmed', 'Goals and check-ins reviewed with the employee', 'checkbox')],
    'contact': [field('channel', 'Preferred work channel', 'select', options=['Work email', 'Team chat', 'Phone']), field('availability', 'Typical working hours or timezone')],
    'handbook': [field('version', 'Handbook version or link reference'), field('read', 'I reviewed the team handbook', 'checkbox')],
    'training': [field('confirmed', 'I completed the linked lessons and assessments', 'checkbox')],
    'receipt': [field('asset_tag', 'Device asset tag'), field('condition', 'Condition on receipt', 'select', options=['Good', 'Needs support'])],
    'feedback': [field('feedback', 'Feedback or blockers (write “None” if all is well)')],
    'ready': [field('summary', 'Final review summary'), field('confirmed', 'I checked all required tasks', 'checkbox')],
}

# These operations have no external integration. Their forms select demo inputs;
# services.py generates the result rather than trusting a user-supplied ticket.
SIMULATIONS = {'profile', 'contract', 'payroll', 'equipment', 'access', 'mfa', 'endpoint', 'it_handoff', 'receipt'}
HANDBOOK_VERSION = 'LaunchOps demo handbook v1'
HANDBOOK = [
    'Work communication: use the work channel and availability saved in your contact preferences.',
    'Support: report access, equipment and security problems to the IT team. Never share passwords, recovery codes, identity numbers or financial details here.',
    'First week: agree on a goal and check-in with your manager. Report blockers in first-week feedback; HR must resolve them before signoff.',
    'This is a demo policy packet, not an employment contract or a company policy certification.',
]
FORMS.update({
    'profile': [field('review', 'Review the displayed employee, team and start date', 'select', options=['Match demo profile'])],
    'contract': [field('document', 'Demo paperwork package', 'select', options=['Demo employment agreement v1'])],
    'payroll': [field('schedule', 'Simulated payroll schedule', 'select', options=['Monthly', 'Biweekly'])],
    'equipment': [field('device', 'Demo device', 'select', options=['Demo laptop', 'Demo workstation']), field('delivery_date', 'Planned delivery date', 'date')],
    'access': [field('bundle', 'Simulated access bundle', 'select', options=['Email and team chat', 'Email, team chat and engineering sandbox'])],
    'mfa': [field('method', 'Simulated MFA method', 'select', options=['Authenticator app', 'Security key'])],
    'endpoint': [field('baseline', 'Simulated security baseline', 'select', options=['Encryption and endpoint protection'])],
    'it_handoff': [field('support', 'Demo support channel', 'select', options=['IT help desk', 'Team IT channel'])],
    'policy': [field('packet', 'Publish a demo handbook packet in this workspace', 'select', options=['Standard', 'Remote', 'On site'])],
    'mentor': [field('mentor_name', 'Mentor name (local assignment; no message is sent)'), field('introduction', 'Introduction plan')],
    'welcome': [field('session_date', 'Orientation date (self-reported attendance)', 'date'), field('location', 'Meeting location'), field('summary', 'What was covered')],
    'goals': [field('goal', 'First-week deliverable'), field('success', 'How the employee and manager will assess success'), field('review_date', 'Goal review date', 'date')],
    'calendar': [field('checkin_date', 'Planned first check-in (no calendar invitation is sent)', 'date'), field('channel', 'Meeting location or channel')],
    'manager_signoff': [field('notes', 'Review of the displayed goal and check-in plan'), field('confirmed', 'I attest that I discussed this plan with the employee', 'checkbox')],
    'handbook': [field('read', 'I acknowledge the displayed demo handbook', 'checkbox')],
    'receipt': [field('condition', 'Simulated device receipt condition', 'select', options=['Good', 'Needs support'])],
    'feedback': [field('status', 'First-week status', 'select', options=['No blockers', 'Needs support']), field('feedback', 'Feedback or description of the blocker')],
    'ready': [field('summary', 'Review of the displayed evidence and any resolved blockers'), field('confirmed', 'I approve closing this demo workflow; simulations do not verify real-world readiness', 'checkbox')],
})
DESCRIPTIONS = {
    'profile': 'Review the displayed profile and run a simulated identity check. No identity is verified.',
    'contract': 'Run a demo paperwork review. No signature or legal document is verified.',
    'payroll': 'Run simulated payroll setup. No payroll account is created or verified.',
    'equipment': 'Reserve a simulated device and record its delivery plan. No physical device is provisioned.',
    'access': 'Create a simulated access bundle. No external account is created.',
    'mfa': 'Run a simulated MFA enrollment check. No real enrollment is tested.',
    'endpoint': 'Run simulated security checks on the assigned demo device.',
    'it_handoff': 'Publish the simulated device handoff and support channel for the employee.',
    'policy': 'Publish the versioned demo handbook in the employee workspace.',
    'mentor': 'Save a mentor assignment and introduction plan in this workspace.',
    'welcome': 'Record a self-reported orientation date, location and discussion summary.',
    'goals': 'Save a deliverable, success criteria and review date owned by this workflow’s employee and manager.',
    'calendar': 'Save a check-in plan. This does not create a calendar event.',
    'manager_signoff': 'Review saved plans and attest to a discussion; attendance is not independently verified.',
    'handbook': 'Read the displayed demo handbook and record your acknowledgment.',
    'receipt': 'Report the condition of the assigned simulated device; problems block final signoff.',
    'feedback': 'Save feedback and explicitly report blockers for HR to resolve.',
    'ready': 'Review all 19 tasks and resolved blockers, then close this demo workflow.',
}
BLUEPRINT = [(k, t, DESCRIPTIONS.get(k, d), r, kind, offset, deps) for k, t, d, r, kind, offset, deps in BLUEPRINT]
