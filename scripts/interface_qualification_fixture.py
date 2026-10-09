"""Owned interface matrix for live recognition/action qualification, without AI calls."""
from __future__ import annotations

import json
import time

from core.execution_telemetry import input_not_dispatched


INTERFACE_HTML = '''
<style>body{font:14px sans-serif;margin:12px}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
textarea,[contenteditable]{height:35px;width:180px;border:1px solid #444}fieldset{margin:0}</style>
<h1>Interface qualification</h1><div class="grid">
<div><span id="label">Primary editor</span><textarea aria-labelledby="label" aria-label="Wrong label">seed</textarea></div>
<div role="textbox" contenteditable="true" aria-label="Rich editor"></div>
<div id="host"></div>
<button title="Wrong tooltip" onclick="log('visible')">Visible action</button>
<button aria-label="Help icon" onclick="log('icon')"><svg width="16" height="16"><circle cx="8" cy="8" r="5"/></svg></button>
<label><input type="checkbox" aria-label="Preference" onchange="log('check',this.checked)">Preference</label>
<label>Color<select aria-label="Color" onchange="log('color',this.value)"><option value="red">Red</option><option value="blue">Blue</option></select></label>
<div><button onclick="log('first')">Repeat</button><button onclick="log('second')">Repeat</button></div>
<fieldset disabled><button onclick="log('disabled')">Disabled action</button></fieldset>
<div hidden><button onclick="log('hidden')">Hidden action</button></div>
<button onclick="document.querySelector('dialog').showModal();log('open-dialog')">Open dialog</button>
<button onclick="document.querySelector('#load').disabled=false;setTimeout(()=>{document.querySelector('#load').textContent='Ready action'},100);log('load-start')">Start load</button>
<button id="load" disabled onclick="log('ready')">Loading</button>
<div><button onclick="log('stale-first')">Bound</button><button onclick="log('stale-second')">Bound</button></div>
<button onclick="document.querySelectorAll('button').forEach(b=>{if(b.textContent==='Bound')b.remove()});log('replace')">Replace controls</button>
<a href="/next">Matrix next</a>
<iframe id="matrix-frame" src="/frame" title="Matrix frame" style="height:70px;width:220px"></iframe>
<canvas width="80" height="40" aria-label="Visual chart"></canvas>
</div>
<dialog aria-label="Edit dialog"><label>Modal note<input aria-label="Modal note"></label>
<button onclick="this.closest('dialog').close();log('close-dialog')">Close dialog</button></dialog>
<script>
function log(kind,value){fetch('/interface-event',{method:'POST',body:JSON.stringify({kind,value})})}
const shadow=document.querySelector('#host').attachShadow({mode:'open'});
shadow.innerHTML='<label>Shadow editor<input aria-label="Shadow editor"></label><button>Shadow action</button>';
shadow.querySelector('button').onclick=()=>log('shadow');
shadow.querySelector('input').oninput=e=>log('shadow-text',e.target.value);
document.querySelector('textarea').oninput=e=>log('primary',e.target.value);
document.querySelector('[contenteditable]').oninput=e=>log('rich',e.target.innerText);
document.querySelector('[aria-label="Modal note"]').oninput=e=>log('modal',e.target.value);
</script>'''


def qualify_interface(registry, runtime, base, event_log):
    """Use production discovery and actions; confirm effects from fixture events."""
    runtime.call('page', operation='goto', url=base + '/interface')
    started = time.perf_counter()
    checks = {}

    def call(name, **arguments):
        result = registry.execute(name, arguments, approved=True)
        if str(result).startswith(('ERROR', 'PERMISSION_DENIED', 'BROWSER_ACTION_BLOCKED')):
            raise AssertionError(f'{name}: {result}')
        return str(result)

    def payload(result):
        return json.loads(result.removeprefix('VERIFIED: '))

    def resolve(query, role='', **extra):
        return payload(call('interaction_resolve', surface='browser', query=query, role=role,
                            force_refresh=True, **extra))['action_target']

    def wait_text(target, value):
        assert call('interaction_wait', state='text', text=value, timeout_ms=1000, **target).startswith('VERIFIED:')

    observed = payload(call('computer_observe', surface='browser', visual='never'))
    names = {node['name'] for node in observed['semantic']['nodes']}
    required = {'Primary editor', 'Rich editor', 'Shadow editor', 'Shadow action', 'Visible action',
                'Help icon', 'Preference', 'Color', 'Repeat', 'Open dialog', 'Matrix next'}
    assert required <= names, f'Undiscovered labels: {required - names}'
    assert not {'Wrong label', 'Wrong tooltip', 'Hidden action'} & names
    checks['accessible_names_and_shadow_discovery'] = True
    assert {'uninspected_embedded_content', 'visual_only_regions'} <= set(observed['coverage_gaps'])
    checks['incomplete_frame_and_canvas_coverage_reported'] = True
    limited = payload(call('computer_observe', surface='browser', visual='never', max_controls=1))
    assert limited['semantic']['truncated'] and 'semantic_map_truncated' in limited['coverage_gaps']
    checks['bounded_inspection_reports_truncation'] = True

    text = 'Precision — مرحبا\nSecond line'
    primary = resolve('Primary editor')
    call('interaction_type', text=text, replace=True, **primary)
    wait_text(primary, text)
    call('interaction_hotkey', keys=['ctrl', 'a'], **primary)
    call('interaction_type', text='Final draft — مرحبا', replace=True, **primary)
    call('interaction_type', text=' + append', **primary)
    wait_text(primary, 'Final draft — مرحبا + append')
    checks['unicode_multiline_selection_replace_append'] = True
    call('interaction_type', surface='browser', text=' + focused')
    wait_text(primary, 'Final draft — مرحبا + append + focused')
    checks['focused_editor_typing_without_model'] = True

    for name, value in [('Rich editor', 'Rich مرحبا'), ('Shadow editor', 'Shadow مرحبا')]:
        target = resolve(name)
        call('interaction_type', text=value, replace=True, **target)
        wait_text(target, value)
    checks['contenteditable_and_shadow_typing'] = True

    for name in ('Visible action', 'Help icon', 'Shadow action'):
        call('interaction_click', **resolve(name))
    ambiguous = registry.execute('interaction_resolve', {'surface': 'browser', 'query': 'Repeat', 'role': 'button'}, True)
    assert 'ambiguous' in ambiguous, ambiguous
    call('interaction_click', **resolve('Repeat', 'button', ordinal=2))
    checks['buttons_icons_and_exact_ordinal'] = True

    for query, action, value in [('Preference', 'check', ''), ('Color', 'select', 'blue')]:
        target = resolve(query)
        args = {'target': target['browser_target'], 'action': action, 'value': value}
        if 'expected_version' in target:
            args['expected_version'] = target['expected_version']
        assert call('browser_semantic_action', **args).startswith('VERIFIED:')
    checks['checkbox_and_select'] = True

    for name in ('Disabled action', 'Hidden action'):
        result = registry.execute('interaction_click', {'surface': 'browser',
                                  'browser_target': {'role': 'button', 'name': name}}, True)
        assert str(result).startswith('ERROR') and input_not_dispatched(result), result
    checks['hidden_and_disabled_rejected_before_input'] = True

    call('interaction_click', **resolve('Open dialog'))
    dialog = payload(call('computer_observe', surface='browser', visual='never'))
    assert any(node['name'] == 'Edit dialog' for node in dialog['signals']['dialogs'])
    target = resolve('Modal note')
    call('interaction_type', text='Unsent modal draft', replace=True, **target)
    wait_text(target, 'Unsent modal draft')
    call('interaction_click', **resolve('Close dialog'))
    checks['dialog_detection_typing_and_close'] = True

    call('interaction_click', **resolve('Start load'))
    call('interaction_wait', surface='browser', browser_target={'role': 'button', 'name': 'Ready action'}, state='enabled', timeout_ms=1000)
    call('interaction_click', **resolve('Ready action'))
    checks['loading_state_adaptation'] = True

    stale = resolve('Bound', 'button', ordinal=2)
    call('interaction_click', **resolve('Replace controls'))
    rejected = registry.execute('interaction_click', stale, True)
    assert 'STALE_UI' in rejected and input_not_dispatched(rejected), rejected
    checks['stale_target_rejected_before_input'] = True

    frame = resolve('Frame draft', frame_selector='#matrix-frame')
    call('interaction_type', text='Frame مرحبا', replace=True, **frame)
    wait_text(frame, 'Frame مرحبا')
    checks['iframe_discovery_typing_readback'] = True

    expected = {'visible': None, 'icon': None, 'shadow': None, 'second': None, 'check': True,
                'color': 'blue', 'open-dialog': None, 'close-dialog': None, 'load-start': None,
                'ready': None, 'replace': None, 'rich': 'Rich مرحبا', 'shadow-text': 'Shadow مرحبا',
                'modal': 'Unsent modal draft', 'primary': 'Final draft — مرحبا + append + focused'}
    deadline = time.monotonic() + 2
    while not all(any(row.get('kind') == key and row.get('value') == value for row in event_log)
                  for key, value in expected.items()):
        if time.monotonic() >= deadline:
            raise AssertionError('Independent fixture events did not confirm every action')
        time.sleep(0.01)
    assert not any(row['kind'] in {'first', 'disabled', 'hidden', 'stale-first', 'stale-second'} for row in event_log)
    checks['independent_effects_and_no_wrong_target'] = True

    call('interaction_click', **resolve('Matrix next'))
    call('interaction_wait', surface='browser', browser_target={'role': 'heading', 'name': 'Navigation verified'}, state='visible')
    assert runtime.call('page', operation='url') == base + '/next'
    assert len(runtime.call('tabs')) == 1
    checks['navigation_and_single_tab'] = True
    return {'checks': checks, 'duration_ms': round((time.perf_counter() - started) * 1000, 2),
            'fixture_events': len(event_log), 'model_calls': 0}


def qualify_challenge_stop(registry, runtime, base, event_log):
    """Last test: land on the owned challenge marker and perform no further input."""
    from core.browser_semantic import BrowserChallengeBlocked
    try:
        runtime.call('page', operation='goto', url=base + '/challenge')
    except BrowserChallengeBlocked:
        pass  # Navigation's postcondition guard must stop on arrival.
    else:
        raise AssertionError('Navigation failed to report the owned human-verification marker')
    for tool, arguments in (
        ('computer_observe', {'surface': 'browser', 'visual': 'never'}),
        ('interaction_click', {'surface': 'browser', 'browser_target': {'role': 'button', 'name': 'Verify'}}),
    ):
        blocked = registry.execute(tool, arguments, True)
        assert 'BROWSER_ACTION_BLOCKED' in blocked, blocked
    assert runtime.call('current')['url'] == base + '/challenge'
    assert not any(row['kind'] == 'challenge-click' for row in event_log)
    return True
