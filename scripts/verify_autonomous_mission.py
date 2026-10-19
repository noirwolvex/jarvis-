"""Opt-in model-driven qualification using only an owned browser and workspace.

Unlike verify_browser_control, this runs the configured real planner. The test
runner supplies an objective, not a tool sequence, and checks independent state.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import subprocess
import tempfile
import threading
import time
from dataclasses import replace
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Import the production builder before overriding the isolated workspace, so its
# normal dotenv loading cannot redirect test files to the user's actual project.
from core.full_access_bridge import build_full_access_agent, run_agent_mission
# The bridge imports its agent lazily; load its dotenv side effect before setting
# the test workspace, otherwise build_full_access_agent can overwrite that root.
from core import agent as _agent_runtime
from core import chrome_cdp
from core.mission_control import MissionControl
from scripts.jarvis_runtime import _stop_dashboard
from scripts import autonomous_ticket_fixture
from core.universal_interaction import _browser_hotkey

NOTE = 'JARVIS validation — مرحبا'
AUDIT = 'reviewed-r4'
STATE = {'saves': [], 'scrolls': 0, 'dismissed': False, 'visits': []}
RECORDS = {
    'orion-r5': ('Draft', 'No approval: this is an unapproved draft'),
    'vega-r9': ('Approved', 'VEGA-R9-APPROVED-81'),
    'orion-r4': ('Approved', 'ORION-R4-APPROVED-57'),
    'orion-r3': ('Approved', 'ORION-R3-APPROVED-26'),
}


def run_with_quota_recovery(agent, goal, emit, control, *, max_resumes=2):
    """Keep this qualification's live browser/journal through bounded minute resets."""
    outcome = run_agent_mission(agent, goal, emit=emit, mission_control=control)
    for _ in range(max_resumes):
        delay = getattr(agent, '_provider_pause_retry_seconds', None)
        if (outcome.get('status') != 'waiting_user'
                or not str(outcome.get('result', '')).startswith('AI_PROVIDER_RATE_LIMITED:')
                or not isinstance(delay, (int, float)) or isinstance(delay, bool)
                or not math.isfinite(delay) or not 0 < delay <= 61
                or agent._is_stopped() or control.cancelled):
            break
        if emit:
            emit(_agent_runtime.AgentEvent('status',
                f'Qualification waiting {delay:g}s for the provider minute reset; resuming the same mission without replay.'))
        deadline = time.monotonic() + delay
        while time.monotonic() < deadline:
            if agent._is_stopped() or control.cancelled:
                return {**outcome, 'status': 'cancelled', 'ok': False, 'requires_user_action': False,
                        'result': 'CANCELLED: Qualification stopped during provider cooldown'}
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        if agent._is_stopped() or control.cancelled:
            return {**outcome, 'status': 'cancelled', 'ok': False, 'requires_user_action': False,
                    'result': 'CANCELLED: Qualification stopped during provider cooldown'}
        metrics = agent.orchestrator.current.metrics
        metrics['qualification_quota_resumes'] = metrics.get('qualification_quota_resumes', 0) + 1
        metrics['provider_cooldown_ms'] = metrics.get('provider_cooldown_ms', 0) + round(delay * 1000)
        outcome = run_agent_mission(agent, goal, emit=emit, mission_control=control, resume_current=True)
    return outcome


def five_stage_objective() -> str:
    return (
        'Complete these five processes in order in the already-connected local browser. '
        'Use exactly five meaningful task-plan steps and preserve them until completion. '
        '1. Inspect the release library, choose the highest APPROVED revision of project Orion '
        '(exclude drafts and other projects), open it and read its approval code. '
        '2. Dismiss the review notice if present, wait for the form to be editable, focus Review note, '
        f'use Ctrl+A to select its placeholder, and replace it with exactly {NOTE!r}. '
        f'Enter exactly {AUDIT!r} in Audit label inside the Audit panel iframe. Verify both field values. '
        '3. Scroll down to the draft controls, save the draft exactly once, and verify its receipt. '
        '4. Open Review summary and verify the chosen record, approval code, receipt, and both exact texts. '
        '5. Create output/result.md in the provided workspace containing all five verified values, '
        'copy it to output/result-backup.md, then read both files and verify that they match exactly. '
        'Keep one browser tab. Report completion only after all five processes are verified. '
        'This is an isolated local test; only the connected browser and this workspace are available. '
        'Use surface=browser. Do not access other apps, files or websites.'
    )


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, content, content_type='text/html; charset=utf-8'):
        data = content.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path == '/save':
            STATE['saves'].append(data)
            self.respond(json.dumps({'receipt': 'DRAFT-0042'}), 'application/json')
        elif self.path == '/event':
            if data.get('event') == 'scroll':
                STATE['scrolls'] += 1
            if data.get('event') == 'dismiss':
                STATE['dismissed'] = True
            self.respond('{}', 'application/json')
        elif self.path == '/typing-event':
            if data.get('event') == 'input':
                STATE['typed_value'] = data.get('value')
            if data.get('event') == 'select-all' and data.get('selected') is True:
                STATE['select_all_observed'] = True
            self.respond('{}', 'application/json')
        else:
            self.send_error(404)

    def do_GET(self):
        STATE['visits'].append(self.path)
        if self.path == '/typing':
            content = '''<h1>Draft editor</h1><label>Review note<textarea aria-label="Review note">Replace this placeholder</textarea></label>
            <p>Local unsent draft. Nothing is submitted from this page.</p><script>
            const editor=document.querySelector('textarea');
            const report=data=>fetch('/typing-event',{method:'POST',body:JSON.stringify(data)});
            editor.addEventListener('input',()=>report({event:'input',value:editor.value}));
            editor.addEventListener('keyup',e=>{if(e.key.toLowerCase()==='a'&&e.ctrlKey)
              report({event:'select-all',selected:document.activeElement===editor&&editor.selectionStart===0&&editor.selectionEnd===editor.value.length})});
            </script>'''
        elif self.path == '/':
            content = '''<h1>Release library</h1><p>Choose the highest approved revision for the requested project.</p>
            <table><tr><th>Project</th><th>Revision</th><th>Status</th><th>Action</th></tr>
            <tr><td>Orion</td><td>5</td><td>Draft</td><td><a href="/document/orion-r5">Open Orion revision 5</a></td></tr>
            <tr><td>Vega</td><td>9</td><td>Approved</td><td><a href="/document/vega-r9">Open Vega revision 9</a></td></tr>
            <tr><td>Orion</td><td>4</td><td>Approved</td><td><a href="/document/orion-r4">Open Orion revision 4</a></td></tr>
            <tr><td>Orion</td><td>3</td><td>Approved</td><td><a href="/document/orion-r3">Open Orion revision 3</a></td></tr></table>'''
        elif self.path == '/frame':
            content = '<label>Audit label<input id="audit" aria-label="Audit label"></label>'
        elif self.path.startswith('/document/'):
            record = self.path.rsplit('/', 1)[-1]
            status, approval = RECORDS.get(record, ('Unknown', 'No approval'))
            content = '''<h1>Document review</h1><p id="record">Record: RECORD</p>
            <p>Status: STATUS</p><p>Approval code: APPROVAL</p><a href="/">Return to library</a>
            <dialog open aria-label="Review notice"><p>This local draft is editable; wait for the editor to load.</p>
            <button onclick="document.querySelector('dialog').close();fetch('/event',{method:'POST',body:JSON.stringify({event:'dismiss'})})">Dismiss notice</button></dialog>
            <label>Review note<textarea id="note" aria-label="Review note" disabled>Replace this placeholder</textarea></label>
            <iframe id="audit-frame" title="Audit panel" src="/frame"></iframe>
            <p id="ready">Loading editor...</p><div style="height:1400px">Scroll to the draft controls below.</div>
            <button id="save">Save draft</button><p id="saved" role="status"></p><a href="/summary">Review summary</a>
            <script>
            const editor=document.querySelector('#note');
            const reportInput=data=>fetch('/typing-event',{method:'POST',body:JSON.stringify(data)});
            editor.addEventListener('input',()=>reportInput({event:'input',value:editor.value}));
            editor.addEventListener('keyup',e=>{if(e.key.toLowerCase()==='a'&&e.ctrlKey)
              reportInput({event:'select-all',selected:document.activeElement===editor&&editor.selectionStart===0&&editor.selectionEnd===editor.value.length})});
            setTimeout(()=>{document.querySelector('#note').disabled=false;document.querySelector('#ready').textContent='Editor ready'},900);
            let scrolled=false;
            addEventListener('scroll',()=>{if(scrollY>300&&!scrolled){scrolled=true;fetch('/event',{method:'POST',body:JSON.stringify({event:'scroll'})})}});
            document.querySelector('#save').onclick=async()=>{
              const payload={record:'RECORD',note:document.querySelector('#note').value,audit:document.querySelector('iframe').contentDocument.querySelector('#audit').value};
              const r=await fetch('/save',{method:'POST',body:JSON.stringify(payload)});const saved=await r.json();
              document.querySelector('#saved').textContent='Draft saved: '+saved.receipt;document.querySelector('#save').disabled=true;
            };
            </script>'''.replace('RECORD', record).replace('STATUS', status).replace('APPROVAL', approval)
        elif self.path == '/summary':
            content = '<h1>Draft summary</h1>' + (''.join(
                '<p>' + html.escape(str(value)) + '</p>' for value in [
                    STATE['saves'][-1]['record'], STATE['saves'][-1]['note'], STATE['saves'][-1]['audit'],
                    RECORDS.get(STATE['saves'][-1]['record'], ('Unknown', 'No approval'))[1],
                    'DRAFT-0042']) if STATE['saves'] else '<p>No draft saved</p>')
            content += '<a href="/">Return to library</a>'
        else:
            self.send_error(404)
            return
        self.respond('<!doctype html><meta charset="utf-8"><title>JARVIS autonomous qualification</title>'
                     '<style>body{font:18px sans-serif;padding:30px}td,th{padding:12px}textarea{display:block;width:500px;height:80px}dialog{position:fixed;top:20px}</style>' + content)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=('release', 'tickets', 'typing', 'five-stage'), default='release',
                        help='Real-planner mission to run in an isolated local browser (default: release).')
    parser.add_argument('--reasoning-effort', choices=('minimal', 'low', 'medium', 'high'),
                        help='Override AI reasoning effort only for this test process; leaves .env unchanged.')
    parser.add_argument('--quota-resumes', type=int, choices=range(6), default=2,
                        help='Maximum same-mission continuations after validated minute quotas (0-5).')
    parser.add_argument('--deadline-seconds', type=int, default=240,
                        help='Total qualification deadline including provider cooldowns (30-600 seconds).')
    options = parser.parse_args(argv)
    if not 30 <= options.deadline_seconds <= 600:
        parser.error('--deadline-seconds must be between 30 and 600')
    STATE.clear()
    STATE.update({'saves': [], 'scrolls': 0, 'dismissed': False, 'visits': []})
    state = autonomous_ticket_fixture.fresh_state() if options.scenario == 'tickets' else STATE
    fixture = autonomous_ticket_fixture.TicketFixture if options.scenario == 'tickets' else Fixture
    output = Path('.jarvis/qualifications') / datetime.now().strftime(
        f'autonomous-{options.scenario}-%Y%m%d-%H%M%S-%f')
    output = output.resolve()
    workspace = output / 'workspace'
    workspace.mkdir(parents=True)
    os.environ['JARVIS_WORKSPACE'] = str(workspace)
    os.environ['JARVIS_FULL_ACCESS_MAX_TURNS'] = '35'
    if options.reasoning_effort:
        os.environ['AI_REASONING_EFFORT'] = options.reasoning_effort
    server = ThreadingHTTPServer(('127.0.0.1', 0), fixture)
    server.fixture_state = state
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    process = runtime = agent = timer = None
    outcome = {'scenario': options.scenario, 'reasoning_effort': os.getenv('AI_REASONING_EFFORT', '') or 'provider_default',
               'quota_resume_limit': options.quota_resumes, 'deadline_seconds': options.deadline_seconds}
    events = []
    cleanup_errors = []
    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix='jarvis-autonomous-browser-') as directory:
            try:
                profile = Path(directory)
                process = subprocess.Popen([
                    chrome_cdp._chrome_executable(), '--headless=new', '--remote-debugging-port=0',
                    '--remote-debugging-address=127.0.0.1', f'--user-data-dir={profile}',
                    '--no-first-run', '--no-default-browser-check', '--disable-background-networking', 'about:blank',
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                deadline = time.monotonic() + 15
                while True:
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('Isolated Chrome did not start')
                    try:
                        port = int((profile / 'DevToolsActivePort').read_text().splitlines()[0])
                        break
                    except (OSError, ValueError, IndexError):
                        time.sleep(0.05)
                runtime = chrome_cdp._runtime()
                runtime.call('connect', endpoint=f'http://127.0.0.1:{port}', session_type='managed')
                runtime.call('page', operation='goto', url=base + ('/typing' if options.scenario == 'typing' else '/'))
                agent = build_full_access_agent()
                if agent.workspace_context.root != workspace or not agent.orchestrator.trace_dir.is_relative_to(workspace):
                    raise RuntimeError('Qualification workspace isolation was not retained')
                # Capability restriction is for this qualification process only.
                # Preserve the production handlers, validation, planner and journal.
                browser_tools = {
                    'interaction_inspect', 'interaction_scene', 'interaction_resolve', 'interaction_click',
                    'interaction_type', 'interaction_hotkey', 'interaction_scroll', 'interaction_focus',
                    'interaction_wait', 'browser_semantic_snapshot', 'browser_read_page', 'browser_check_challenge',
                    'chrome_tabs', 'chrome_current_tab',
                    'browser_semantic_action', 'browser_semantic_scroll', 'browser_wait_state',
                }
                file_tools = {'directory_create', 'file_copy', 'write_file', 'read_file', 'list_directory'}
                for name in list(agent.tools._tools):
                    if name not in browser_tools | file_tools and not name.startswith(('task_', 'workflow_')):
                        agent.tools._tools.pop(name)
                        agent.tools._validators.pop(name, None)
                for required in ('browser_semantic_snapshot', 'browser_semantic_action',
                                 'browser_semantic_scroll', 'browser_wait_state'):
                    if required not in agent.tools._tools:
                        raise RuntimeError('Qualification removed a required backend: ' + required)

                def restrict_browser(handler, name):
                    def guarded(**arguments):
                        if not runtime.call('page', operation='url').startswith(base + '/'):
                            raise RuntimeError('Qualification browser left its owned origin')
                        if name.startswith('interaction_'):
                            if arguments.get('surface', 'browser') not in {'auto', 'browser'}:
                                raise RuntimeError('Qualification cannot target the personal desktop')
                            arguments['surface'] = 'browser'
                        return handler(**arguments)
                    return guarded
                for name in browser_tools:
                    spec = agent.tools._tools[name]
                    agent.tools.register(replace(spec, handler=restrict_browser(spec.handler, name)))

                answered = set()
                def confirmation(snapshot):
                    pending = snapshot.get('pendingConfirmation')
                    if not pending or pending['id'] in answered:
                        return
                    answered.add(pending['id'])
                    args = json.loads(pending['details'])
                    target = args.get('browser_target') or args.get('target') or {}
                    # This authored fixture contains only local draft/navigation
                    # controls. Consent is confined to that exact origin and the
                    # new result file, including snapshot-node target forms. The
                    # production action guard still rejects stale/missing versions.
                    allowed = (pending['tool'] in {'interaction_click', 'browser_semantic_action'}
                               and isinstance(target, dict) and bool(target)
                               and args.get('surface', 'browser') == 'browser'
                               and runtime.call('page', operation='url').startswith(base + '/'))
                    if pending['tool'] == 'write_file':
                        destination = (workspace / str(args.get('path', ''))).resolve()
                        allowed = destination == workspace / 'output/result.md' and not destination.exists()
                    if not allowed:
                        print('TEST_CONFIRMATION_REJECTED ' + pending['tool'], flush=True)
                    control.command('confirm' if allowed else 'reject', pending['id'])
                control = MissionControl(confirmation, confirmation_timeout=5)

                goal = autonomous_ticket_fixture.objective() if options.scenario == 'tickets' else (
                    'In the already-connected browser release library, find the highest APPROVED revision of project Orion '
                    '(ignore drafts and other projects). Open that record. Dismiss the review notice if present and wait '
                    'for the editor to be ready. In Review note, use Ctrl+A to select the existing placeholder and '
                    f'replace it with exactly {NOTE!r}. In the Audit panel iframe, enter {AUDIT!r} into Audit label. '
                    'Scroll down to the draft controls and save the draft exactly once. Verify its receipt, then open '
                    'Review summary and verify the saved record and both texts. Create an output folder in the provided '
                    'workspace and write output/result.md containing the selected record, its approval code, receipt, '
                    'and the exact two texts. Copy it to output/result-backup.md and read both files to verify they match. '
                    'Keep one browser tab. Report completion only after every result is verified. '
                    'This is an isolated local test: only the connected browser and this workspace are available. '
                    'Use surface=browser for interaction tools. Do not access other apps, files or websites.'
                )
                if options.scenario == 'typing':
                    goal = (f'In the already-connected test browser, select all of the existing Review note text using Ctrl+A, '
                            f'then replace it with exactly {NOTE!r}. Verify the exact editor text. Leave it as an unsent draft. '
                            'Only use this connected browser; do not open other apps, websites or files.')
                elif options.scenario == 'five-stage':
                    goal = five_stage_objective()
                (output / 'mission.txt').write_text(goal, encoding='utf-8')
                def progress(event):
                    item = {'seconds': round(time.perf_counter() - started, 2), 'kind': event.kind,
                            'tool': event.tool, 'message': str(event.message)}
                    events.append(item)
                    print(json.dumps({**item, 'message': item['message'][:900]}, ensure_ascii=False), flush=True)
                timer = threading.Timer(options.deadline_seconds, agent.request_stop)
                timer.start()
                print('LIVE_AUTONOMOUS_TEST_STARTED ' + str(output), flush=True)
                outcome.update(run_with_quota_recovery(agent, goal, progress, control, max_resumes=options.quota_resumes))
                from core.process_control import set_cancellation
                set_cancellation(lambda: False)  # Test ended; allow only independent readback and cleanup.
                # Independent result checks inspect fixture state and real file bytes,
                # never the model's final prose or task_verify claims.
                text = (workspace / 'output/result.md').read_text(encoding='utf-8') if (workspace / 'output/result.md').is_file() else ''
                copy = workspace / 'output/result-backup.md'
                checks = autonomous_ticket_fixture.independent_checks(
                    state, workspace, len(runtime.call('tabs'))
                ) if options.scenario == 'tickets' else {
                    'correct_record': len(STATE['saves']) == 1 and STATE['saves'][0]['record'] == 'orion-r4',
                    'unicode_note': len(STATE['saves']) == 1 and STATE['saves'][0]['note'] == NOTE,
                    'iframe_text': len(STATE['saves']) == 1 and STATE['saves'][0]['audit'] == AUDIT,
                    'saved_once': len(STATE['saves']) == 1, 'popup_dismissed': STATE['dismissed'],
                    'scrolled': STATE['scrolls'] > 0, 'summary_visited': '/summary' in STATE['visits'],
                    'report_complete': all(value in text for value in ['orion-r4', NOTE, AUDIT, 'ORION-R4-APPROVED-57', 'DRAFT-0042']),
                    'backup_identical': bool(text) and copy.is_file() and copy.read_bytes() == (workspace / 'output/result.md').read_bytes(),
                    'one_tab': len(runtime.call('tabs')) == 1,
                    'keyboard_shortcut': any(t.name == 'interaction_hotkey' and t.success
                                             and _browser_hotkey(t.arguments.get('keys', [])) == 'Control+A'
                                             for t in agent.orchestrator.current.traces),
                }
                if options.scenario == 'typing':
                    try:
                        readback = runtime.call('page', operation='wait_state', target={'role': 'textbox', 'name': 'Review note'},
                                                state='text', text=NOTE, timeout_ms=0)
                    except TimeoutError:
                        readback = {'matched': False}
                    checks = {'unicode_note': state.get('typed_value') == NOTE and readback.get('matched') is True,
                              'select_all_observed': state.get('select_all_observed') is True,
                              'no_saves': len(state['saves']) == 0,
                              'one_tab': len(runtime.call('tabs')) == 1}
                elif options.scenario == 'five-stage':
                    checks['select_all_observed'] = state.get('select_all_observed') is True
                    checks['five_verified_plan_steps'] = (len(agent.orchestrator.current.plan) == 5
                        and all(step.status == 'completed' for step in agent.orchestrator.current.plan))
                outcome['independent_checks'] = checks
                outcome['trace_path'] = str(agent.orchestrator.trace_dir / (outcome['task_id'] + '.json'))
                outcome['independent_pass'] = all(checks.values()) and outcome.get('mission_completed') is True
                runtime.call('page', operation='screenshot', path=str(output / 'final-page.png'))
            finally:
                if timer:
                    timer.cancel()
                if runtime:
                    from core.process_control import set_cancellation
                    set_cancellation(lambda: False)  # Only cleanup/readback after the test has ended.
                    try:
                        runtime.call('shutdown')
                    except Exception as exc:
                        cleanup_errors.append(f'CDP shutdown: {type(exc).__name__}: {exc}')
                    finally:
                        chrome_cdp._RUNTIME = None
                try:
                    _stop_dashboard(process)
                except Exception as exc:
                    cleanup_errors.append(f'Owned browser shutdown: {type(exc).__name__}: {exc}')
    except Exception as exc:
        outcome.update(status='test_error', error=f'{type(exc).__name__}: {exc}', independent_pass=False)
    finally:
        if agent:
            try:
                agent.close()
            except Exception as exc:
                cleanup_errors.append(f'Agent shutdown: {type(exc).__name__}: {exc}')
        server.shutdown()
        server.server_close()
        server_thread.join(2)
        if cleanup_errors:
            outcome['cleanup_errors'] = cleanup_errors
            outcome['independent_pass'] = False
        outcome['elapsed_seconds'] = round(time.perf_counter() - started, 2)
        (output / 'result.json').write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding='utf-8')
        (output / 'events.json').write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding='utf-8')
        (output / 'fixture-state.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        print('LIVE_AUTONOMOUS_TEST_RESULT ' + json.dumps(outcome, ensure_ascii=False), flush=True)
        print('ARTIFACT_DIRECTORY ' + str(output), flush=True)
    return 0 if outcome.get('independent_pass') else 1


if __name__ == '__main__':
    raise SystemExit(main())
