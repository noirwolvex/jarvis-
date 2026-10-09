"""Owned ticket-queue fixture for a real-model, opt-in browser qualification.

The fixture records effects independently of the planner's claims. It contains
only local drafts and never connects to a real ticketing or messaging service.
"""
from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

NOTE = 'Handoff ready — تمت المراجعة بنجاح'
REFERENCE = 'ATLAS-HANDOFF-1042'
SELECTED_ID = 'ATL-1042'
CHECK_CODE = 'ATL-1042-CHECK-82'
RECEIPT = 'DRAFT-ATLAS-1042'
TICKETS = (
    {'id': 'ATL-1049', 'project': 'Atlas', 'priority': 0, 'status': 'BLOCKED', 'title': 'Blocked deployment'},
    {'id': 'NOVA-3000', 'project': 'Nova', 'priority': 0, 'status': 'READY', 'title': 'Unrelated incident'},
    {'id': SELECTED_ID, 'project': 'Atlas', 'priority': 1, 'status': 'READY', 'title': 'Release handoff'},
    {'id': 'ATL-1001', 'project': 'Atlas', 'priority': 2, 'status': 'READY', 'title': 'Documentation follow-up'},
    {'id': 'ATL-1033', 'project': 'Atlas', 'priority': 1, 'status': 'DONE', 'title': 'Completed review'},
)


def fresh_state() -> dict:
    return {'saves': [], 'scrolls': 0, 'dismissed': False, 'visits': [], 'filters': [], 'previews': []}


def objective() -> str:
    return (
        'In the already-connected local ticket queue, use the queue filters to show READY tickets for project Atlas. '
        'Find and open the highest-priority matching ticket: P0 is highest, then P1, then P2. '
        'Exclude blocked/completed tickets and other projects. Dismiss the handoff notice if present and wait '
        'for the delayed handoff form to become editable. Replace the Handoff note placeholder with exactly '
        f'{NOTE!r}. In the Reference panel iframe, replace Handoff reference with exactly {REFERENCE!r}. '
        'Scroll down to the draft controls and save the handoff draft exactly once. Verify the receipt, '
        'then open Preview handoff and verify the selected ticket and both exact texts. '
        'Create an output folder in the provided workspace and write output/result.md containing the ticket ID, '
        'project, priority, status, verification code shown on the ticket, draft receipt, and both exact texts. '
        'Copy the report to output/result-backup.md and read both files to verify that they match. '
        'Keep one browser tab. Report completion only after all results are verified. '
        'This is an isolated local test: only the connected browser and this workspace are available. '
        'Use surface=browser for interaction tools. Do not access other apps, files or websites.'
    )


def independent_checks(state: dict, workspace: Path, tab_count: int) -> dict[str, bool]:
    report = workspace / 'output/result.md'
    backup = workspace / 'output/result-backup.md'
    text = report.read_text(encoding='utf-8') if report.is_file() else ''
    saved = state['saves'][0] if len(state['saves']) == 1 else {}
    expected = {'ticket': SELECTED_ID, 'note': NOTE, 'reference': REFERENCE}
    return {
        'filtered_queue': any(item == {'project': 'Atlas', 'status': 'READY'} for item in state['filters']),
        'correct_ticket': saved.get('ticket') == SELECTED_ID,
        'unicode_note': saved.get('note') == NOTE,
        'iframe_reference': saved.get('reference') == REFERENCE,
        'saved_once': len(state['saves']) == 1,
        'popup_dismissed': state['dismissed'] is True,
        'scrolled': state['scrolls'] > 0,
        'preview_visited': '/preview' in state['visits'],
        'preview_contains_saved_values': any(item == expected for item in state['previews']),
        'report_complete': all(value in text for value in
                               (SELECTED_ID, 'Atlas', 'P1', 'READY', CHECK_CODE, RECEIPT, NOTE, REFERENCE)),
        'backup_identical': bool(text) and backup.is_file() and backup.read_bytes() == report.read_bytes(),
        'one_tab': tab_count == 1,
    }


class TicketFixture(BaseHTTPRequestHandler):
    @property
    def state(self) -> dict:
        return self.server.fixture_state

    def log_message(self, *_):
        pass

    def respond(self, content: str, content_type: str = 'text/html; charset=utf-8'):
        data = content.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 16384:
                raise ValueError('Invalid body size')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('Expected an object')
        except (ValueError, UnicodeDecodeError):
            self.send_error(400)
            return
        if self.path == '/save':
            if data.get('ticket') not in {ticket['id'] for ticket in TICKETS}:
                self.send_error(400)
                return
            self.state['saves'].append(data)
            self.respond(json.dumps({'receipt': RECEIPT}), 'application/json')
        elif self.path == '/event':
            if data.get('event') == 'scroll':
                self.state['scrolls'] += 1
            if data.get('event') == 'dismiss':
                self.state['dismissed'] = True
            self.respond('{}', 'application/json')
        else:
            self.send_error(404)

    def do_GET(self):
        parsed = urlparse(self.path)
        self.state['visits'].append(parsed.path)
        if parsed.path == '/':
            query = parse_qs(parsed.query)
            project = query.get('project', [''])[0]
            status = query.get('status', [''])[0]
            self.state['filters'].append({'project': project, 'status': status})
            rows = [ticket for ticket in TICKETS
                    if (not project or project.casefold() in ticket['project'].casefold())
                    and (not status or ticket['status'] == status)]
            content = (
                '<h1>Ticket queue</h1><p>Priority order: P0 highest, then P1, then P2. '
                'Only READY tickets can be handed off.</p>'
                '<form action="/" method="get"><label>Project search '
                '<input name="project" type="search" aria-label="Project search" value="'
                + html.escape(project, quote=True) + '"></label> '
                '<label>Status filter <select name="status" aria-label="Status filter">'
                + ''.join('<option value="' + value + '"' + (' selected' if value == status else '') + '>'
                          + (value or 'All statuses') + '</option>' for value in ('', 'READY', 'BLOCKED', 'DONE'))
                + '</select></label> <button type="submit">Apply filters</button></form>'
                '<p role="status">' + str(len(rows)) + ' matching tickets</p>'
                '<table><tr><th>Ticket</th><th>Project</th><th>Priority</th><th>Status</th><th>Title</th><th>Action</th></tr>'
                + ''.join('<tr><td>' + ticket['id'] + '</td><td>' + ticket['project'] + '</td><td>P'
                          + str(ticket['priority']) + '</td><td>' + ticket['status'] + '</td><td>'
                          + ticket['title'] + '</td><td><a href="/ticket/' + ticket['id'] + '">Open '
                          + ticket['id'] + '</a></td></tr>' for ticket in rows)
                + '</table>'
            )
        elif parsed.path == '/reference-frame':
            content = ('<label>Handoff reference<input id="reference" aria-label="Handoff reference" '
                       'value="Replace reference"></label>')
        elif parsed.path.startswith('/ticket/'):
            selected = next((ticket for ticket in TICKETS if ticket['id'] == parsed.path.rsplit('/', 1)[-1]), None)
            if selected is None:
                self.send_error(404)
                return
            content = (
                '<h1>Ticket handoff</h1><p>Ticket: ' + selected['id'] + '</p><p>Project: ' + selected['project']
                + '</p><p>Priority: P' + str(selected['priority']) + '</p><p>Status: ' + selected['status']
                + '</p><p>Verification code: ' + CHECK_CODE + '</p>'
                '<dialog id="notice" aria-label="Handoff notice"><p>Only a local draft will be saved. '
                'The form becomes editable shortly.</p><button id="dismiss">Dismiss handoff notice</button></dialog>'
                '<label>Handoff note<textarea id="note" aria-label="Handoff note" disabled>'
                'Replace handoff placeholder</textarea></label>'
                '<iframe title="Reference panel" id="reference-frame" src="/reference-frame"></iframe>'
                '<p id="ready" role="status">Loading handoff form...</p>'
                '<div style="height:1200px">Draft controls are below. Scroll down after entering the note and reference.</div>'
                '<button id="save">Save handoff draft</button><p id="receipt" role="status"></p>'
                '<a href="/preview">Preview handoff</a>'
                '<script>const ticket=' + json.dumps(selected['id']) + ';'
                "const notice=document.querySelector('#notice');notice.showModal();"
                "document.querySelector('#dismiss').onclick=()=>{notice.close();fetch('/event',"
                "{method:'POST',body:JSON.stringify({event:'dismiss'})})};"
                "setTimeout(()=>{document.querySelector('#note').disabled=false;"
                "document.querySelector('#ready').textContent='Handoff form ready'},850);"
                "let scrolled=false;addEventListener('scroll',()=>{if(scrollY>300&&!scrolled){scrolled=true;"
                "fetch('/event',{method:'POST',body:JSON.stringify({event:'scroll'})})}});"
                "document.querySelector('#save').onclick=async()=>{const payload={ticket,"
                "note:document.querySelector('#note').value,"
                "reference:document.querySelector('iframe').contentDocument.querySelector('#reference').value};"
                "const r=await fetch('/save',{method:'POST',body:JSON.stringify(payload)});"
                "const result=await r.json();document.querySelector('#receipt').textContent='Draft saved: '+result.receipt;"
                "document.querySelector('#save').disabled=true;};</script>"
            )
        elif parsed.path == '/preview':
            content = '<h1>Handoff preview</h1>'
            if self.state['saves']:
                saved = dict(self.state['saves'][-1])
                self.state['previews'].append(saved)
                ticket = next(item for item in TICKETS if item['id'] == saved['ticket'])
                values = (saved['ticket'], ticket['project'], f"P{ticket['priority']}", ticket['status'],
                          CHECK_CODE, RECEIPT, saved.get('note', ''), saved.get('reference', ''))
                content += ''.join('<p>' + html.escape(str(value)) + '</p>' for value in values)
            else:
                content += '<p>No handoff draft saved</p>'
            content += '<a href="/">Return to ticket queue</a>'
        else:
            self.send_error(404)
            return
        self.respond('<!doctype html><meta charset="utf-8"><title>JARVIS Atlas ticket qualification</title>'
                     '<style>body{font:16px sans-serif;padding:20px}td,th{padding:8px;text-align:left}'
                     'textarea{display:block;width:500px;height:70px}iframe{width:500px;height:85px}'
                     'dialog{max-width:400px}button,input,select{font:inherit}</style>' + content)
