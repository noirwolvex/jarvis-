"""Bounded semantic browser observations and exact actions on the CDP owner thread.

Only fixed application-owned scripts run here. Page content is data, and snapshot
node references expire on DOM/focus/layout changes rather than retargeting clicks.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable


_CHALLENGE_JS = r"""() => {
  if (!document.documentElement || !document.body) throw new Error('Page inspection unavailable');
  const text = (document.body.innerText || '').slice(0, 20000).toLowerCase();
  const patterns = ['captcha','recaptcha','hcaptcha','turnstile','verify you are human',
    'prove you are human','human verification','security check','bot verification','anti-bot','anti bot'];
  const evidence = patterns.filter(p => text.includes(p)).map(p => 'text:' + p);
  const selectors = ['captcha','recaptcha','hcaptcha','turnstile'].flatMap(p =>
    [`iframe[src*="${p}"]`, `[class*="${p}"]`, `[id*="${p}"]`]);
  if (document.querySelector(selectors.join(','))) evidence.push('challenge marker');
  return {challenge_detected: evidence.length > 0, inspection_available: true, evidence,
    url: location.href, title: document.title};
}"""

_SNAPSHOT_JS = r"""({force, max_nodes}) => {
  let s = window.__jarvisSemanticV1;
  if (!s) {
    s = {epoch: crypto.randomUUID(), version: 1, cache: null, nodes: new Map(), ids: new WeakMap(), roots: new WeakSet(), next: 1};
    s.invalidate = () => { s.version++; s.cache = null; };
    s.observer = new MutationObserver(s.invalidate);
    s.observer.observe(document, {subtree:true, childList:true, attributes:true, characterData:true});
    s.roots.add(document);
    for (const event of ['focusin','focusout','input','change','scroll','resize','hashchange','popstate'])
      window.addEventListener(event, s.invalidate, true);
    window.__jarvisSemanticV1 = s;
  }
  if (s.observer.takeRecords().length) s.invalidate();
  const version = `${s.epoch}:${s.version}`;
  if (!force && s.cache && s.cache.limit === max_nodes && s.cache.url === location.href
      && performance.now() - s.cache.created < 500)
    return {...s.cache.result, cached:true};
  const text = value => String(value || '').replace(/\s+/g,' ').trim().slice(0,180);
  const visible = el => {
    const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && st.display !== 'none' && st.visibility !== 'hidden'
      && !el.closest('[aria-hidden="true"],[inert]') && r.bottom > 0 && r.right > 0
      && r.top < innerHeight && r.left < innerWidth;
  };
  const role = el => el.getAttribute('role') || ({A:'link',BUTTON:'button',TEXTAREA:'textbox',
    SELECT:'combobox',NAV:'navigation',MAIN:'main',DIALOG:'dialog',FORM:'form',H1:'heading',
    H2:'heading',H3:'heading',IFRAME:'iframe'}[el.tagName]) || (el.tagName === 'INPUT'
      ? ({checkbox:'checkbox',radio:'radio',range:'slider',button:'button',submit:'button'}[el.type] || 'textbox')
      : (el.isContentEditable ? 'textbox' : 'generic'));
  const name = el => {
    const ids = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
    const labelled = ids.map(id => document.getElementById(id)?.textContent || '').join(' ');
    return text(el.getAttribute('aria-label') || labelled ||
      (el.labels ? [...el.labels].map(l => l.textContent).join(' ') : '') ||
      el.getAttribute('alt') || el.getAttribute('title') || el.getAttribute('placeholder') ||
      (el.matches('input,textarea,main,nav,form,dialog') || el.isContentEditable ? '' : safeText(el)));
  };
  const safeText = el => {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT); let value = '', visited = 0;
    for (let node = walker.nextNode(); node && visited++ < 200; node = walker.nextNode()) {
      if (!node.parentElement?.closest('input,textarea,[contenteditable]')) value += ' ' + node.textContent;
      if (value.length >= 180) break;
    }
    return value;
  };
  const selector = 'a[href],button,input:not([type="hidden"]),textarea,select,[role],[contenteditable]:not([contenteditable="false"]),nav,main,form,dialog,h1,h2,h3,iframe';
  const candidates = []; const roots = [document]; let scanned = 0;
  while (roots.length && scanned < 3000) {
    const root = roots.shift();
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    for (let el = walker.nextNode(); el; el = walker.nextNode()) {
      if (++scanned > 3000) break;
      if (el.shadowRoot) {
        if (!s.roots.has(el.shadowRoot)) {
          s.observer.observe(el.shadowRoot, {subtree:true, childList:true, attributes:true, characterData:true});
          s.roots.add(el.shadowRoot);
        }
        roots.push(el.shadowRoot);
      }
      if (el.matches(selector) && visible(el)) candidates.push(el);
    }
  }
  // Modal and focused controls come first so bounded snapshots retain immediate context.
  candidates.sort((a,b) => Number(!!b.closest('dialog,[role="dialog"],[aria-modal="true"]'))
    - Number(!!a.closest('dialog,[role="dialog"],[aria-modal="true"]')));
  let active = document.activeElement;
  while (active?.shadowRoot?.activeElement) active = active.shadowRoot.activeElement;
  const selected = candidates.slice(0,max_nodes); const ids = new Map(); s.nodes.clear();
  for (const el of selected) {
    const id = s.ids.get(el) || 'n' + s.next++; s.ids.set(el,id); ids.set(el,id); s.nodes.set(id,el);
  }
  const nodes = selected.map(el => {
    let parent = el.parentElement; while (parent && !ids.has(parent)) parent = parent.parentElement;
    const r = el.getBoundingClientRect();
    return {node_id:ids.get(el), parent:ids.get(parent) || null, role:role(el), name:name(el),
      tag:el.tagName.toLowerCase(), id:text(el.id), disabled:!!el.disabled || el.getAttribute('aria-disabled') === 'true',
      focused:el === active, expanded:el.getAttribute('aria-expanded'),
      selected:el.getAttribute('aria-selected'), checked:el.getAttribute('aria-checked') ?? (el.type === 'checkbox' ? !!el.checked : null),
      bounds:{x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),height:Math.round(r.height)},
      href:el.tagName === 'A' ? String(el.href).slice(0,1024) : undefined,
      frame_url:el.tagName === 'IFRAME' ? String(el.src).slice(0,1024) : undefined};
  });
  const result = {version,url:location.href,title:document.title,ready_state:document.readyState,
    viewport:{width:innerWidth,height:innerHeight,scroll_x:scrollX,scroll_y:scrollY},
    focused_node:ids.get(active) || null, nodes,
    truncated:candidates.length > selected.length || scanned > 3000,
    note:'Viewport controls only; field values omitted. Inspect a selected iframe separately.',cached:false};
  s.cache = {limit:max_nodes,url:location.href,created:performance.now(),result}; return result;
}"""

_NODE_JS = r"""({node_id, version}) => {
  const s = window.__jarvisSemanticV1;
  if (!s) throw new Error('No semantic snapshot; observe first');
  if (s.observer.takeRecords().length) s.invalidate();
  if (`${s.epoch}:${s.version}` !== version) throw new Error('STALE_UI: Snapshot changed; observe again');
  const el = s.nodes.get(node_id);
  if (!el || !el.isConnected) throw new Error('STALE_UI: Observed node no longer exists');
  return el;
}"""

_VERSION_JS = r"""() => {
  const s = window.__jarvisSemanticV1;
  if (!s) throw new Error('No semantic snapshot; observe first');
  if (s.observer.takeRecords().length) s.invalidate();
  return `${s.epoch}:${s.version}`;
}"""

_VIDEO_STATE_JS = r"""() => {
  const v = document.querySelector('video'); const p = document.querySelector('#movie_player');
  const selected = new URL(location.href).searchParams.get('v') || '';
  let loaded = '', inspectionError = '';
  try { loaded = p?.getVideoData?.().video_id || ''; } catch (error) { inspectionError = String(error).slice(0,200); }
  const request = window.__jarvisPlaybackV1;
  return {url:location.href, selected_video_id:selected, loaded_video_id:loaded, has_video:!!v,
    paused:v ? v.paused : null, ended:v ? v.ended : null, ready_state:v ? v.readyState : 0,
    current_time:v ? v.currentTime : 0, error:v?.error ? v.error.code : null,
    ad_playing:!!p?.classList.contains('ad-showing'), inspection_error:inspectionError,
    playback_error:request?.video === v ? request.error : null};
}"""

_VIDEO_ACTION_JS = r"""({action, expected_video_id}) => {
  const v = document.querySelector('video'); const p = document.querySelector('#movie_player');
  const selected = new URL(location.href).searchParams.get('v') || '';
  const loaded = p?.getVideoData?.().video_id || '';
  if (!v || selected !== expected_video_id || loaded !== selected)
    throw new Error('Selected video changed before playback action');
  if (p?.classList.contains('ad-showing')) throw new Error('Advertisement is active; content playback cannot be verified');
  const request = {video:v,error:null}; window.__jarvisPlaybackV1 = request;
  if (action === 'pause') v.pause();
  else {
    // Do not await an unbounded play promise. The caller verifies state/time with a deadline.
    const promise = v.play(); if (promise) promise.catch(error => { request.error = String(error).slice(0,200); });
  }
  return {dispatched:true};
}"""


class BrowserChallengeBlocked(RuntimeError):
    pass


def challenge_state(page: Any) -> dict[str, Any]:
    try:
        result = page.evaluate(_CHALLENGE_JS)
        if not isinstance(result, dict) or result.get("inspection_available") is not True or not isinstance(result.get("challenge_detected"), bool):
            raise ValueError("Invalid challenge inspection")
        return result
    except Exception as exc:
        raise BrowserChallengeBlocked("BROWSER_ACTION_BLOCKED: Challenge inspection unavailable; observe before continuing") from exc


def require_clear_page(page: Any) -> dict[str, Any]:
    result = challenge_state(page)
    if result["challenge_detected"]:
        raise BrowserChallengeBlocked("BROWSER_ACTION_BLOCKED: Human verification requires the user; " + json.dumps(result))
    return result


def _frame(page: Any, selector: str = "") -> Any:
    if not selector:
        return page
    locator = page.locator(selector)
    if locator.count() != 1:
        raise RuntimeError("Iframe selector is missing or ambiguous")
    handle = locator.element_handle(timeout=1000)
    try:
        frame = handle.content_frame() if handle else None
        if frame is None:
            raise RuntimeError("Selected element is not an available iframe")
        return frame
    finally:
        if handle:
            handle.dispose()


def _target(page: Any, target: dict[str, Any], expected_version: str = "") -> Any:
    if target.get("node_id"):
        if not expected_version:
            raise ValueError("Observed node targets require expected_version from their snapshot")
        handle = page.evaluate_handle(_NODE_JS, {"node_id": target["node_id"], "version": expected_version})
        element = handle.as_element()
        if element is None:
            handle.dispose()
            raise RuntimeError("Observed node no longer resolves to an element")
        return element
    if expected_version:
        if page.evaluate(_VERSION_JS) != expected_version:
            raise RuntimeError("STALE_UI: Snapshot changed; observe again")
    if target.get("role"):
        locator = page.get_by_role(target["role"], name=target.get("name", ""), exact=True)
    elif target.get("selector"):
        locator = page.locator(target["selector"])
    else:
        raise ValueError("Target requires node_id, exact role/name, or selector")
    count = locator.count()
    if count != 1:
        raise RuntimeError(f"Target must resolve uniquely; observed {count} matches")
    return locator


def _release_target(target: Any) -> None:
    # ElementHandles need disposal; Locators have no dispose method.
    if hasattr(target, "dispose"):
        target.dispose()


def _wait_state(page: Any, args: dict[str, Any], check: Callable[[], None]) -> dict[str, Any]:
    state = args.get("state", "visible")
    if state not in {"visible", "hidden", "enabled", "text"}:
        raise ValueError("Unsupported wait state")
    target = args["target"]
    if target.get("node_id"):
        raise ValueError("State waits require a stable semantic locator, not a snapshot node")
    deadline = time.monotonic() + max(0, min(int(args.get("timeout_ms", 5000)), 30000)) / 1000
    polls = 0
    while True:
        check()
        require_clear_page(page)
        polls += 1
        locator = page.get_by_role(target["role"], name=target.get("name", ""), exact=True) if target.get("role") else page.locator(target["selector"])
        count = locator.count()
        if count > 1:
            raise RuntimeError("Wait target is ambiguous; refine its exact locator")
        matched = state == "hidden" and count == 0
        if count == 1:
            if state == "visible":
                matched = locator.is_visible()
            elif state == "hidden":
                matched = not locator.is_visible()
            elif state == "enabled":
                matched = locator.is_visible() and locator.is_enabled()
            else:
                matched = locator.is_visible() and locator.inner_text(timeout=500).strip() == str(args.get("text", "")).strip()
        if matched:
            return {"matched": True, "state": state, "polls": polls}
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Browser state '{state}' did not become true after {polls} observations")
        time.sleep(min(0.05, max(0, deadline - time.monotonic())))


def run_browser_operation(page: Any, operation: str, args: dict[str, Any], check: Callable[[], None]) -> Any:
    check()
    if operation == "challenge_state":
        return challenge_state(page)
    require_clear_page(page)
    selected = _frame(page, str(args.get("frame_selector", "")))
    if selected is not page:
        require_clear_page(selected)
    if operation == "semantic_snapshot":
        result = selected.evaluate(_SNAPSHOT_JS, {"force": bool(args.get("force", False)), "max_nodes": max(1, min(int(args.get("max_nodes", 160)), 250))})
        result["frame_selector"] = args.get("frame_selector", "")
        return result
    if operation == "wait_state":
        return _wait_state(selected, args, check)
    if operation == "semantic_action":
        action = args["action"]
        if action not in {"click", "fill", "press", "select", "check", "uncheck", "focus"}:
            raise ValueError("Unsupported semantic action")
        target = _target(selected, args["target"], str(args.get("expected_version", "")))
        try:
            check()
            require_clear_page(page)
            if selected is not page:
                require_clear_page(selected)
            if args.get("expected_version"):
                if selected.evaluate(_VERSION_JS) != args["expected_version"]:
                    raise RuntimeError("STALE_UI: Interface changed during action preflight; observe again")
            if not target.is_visible() or not target.is_enabled():
                raise RuntimeError("Target is not currently visible and enabled; wait or re-observe")
            value = str(args.get("value", ""))
            if len(value) > 4096 or "\0" in value:
                raise ValueError("Semantic action value must be at most 4096 characters without NUL")
            # A single bounded action attempt. Timeouts may have side effects: never replay here.
            if action == "fill":
                target.fill(value, timeout=1500)
                verified = target.input_value(timeout=500) == value
            elif action == "select":
                target.select_option(value=value, timeout=1500)
                verified = target.input_value(timeout=500) == value
            elif action in {"check", "uncheck"}:
                getattr(target, action)(timeout=1500)
                verified = target.is_checked() == (action == "check")
            elif action == "press":
                target.press(value, timeout=1500)
                verified = False
            elif action == "focus":
                if args["target"].get("node_id"):
                    target.focus()  # ElementHandle.focus has no timeout parameter.
                else:
                    target.focus(timeout=1500)
                verified = bool(target.evaluate("el => el === el.getRootNode().activeElement"))
            else:
                target.click(timeout=1500)
                verified = False
            check()
            require_clear_page(page)
            if selected is not page:
                require_clear_page(selected)
            if action in {"fill", "select", "check", "uncheck", "focus"} and not verified:
                raise RuntimeError("Semantic action readback did not match; observe before any retry")
            return {"action": action, "executed": True, "verified": verified,
                    "requires_result_verification": not verified, "url": page.url}
        finally:
            _release_target(target)
    if operation == "youtube_state":
        return selected.evaluate(_VIDEO_STATE_JS)
    if operation == "youtube_results":
        return selected.evaluate("""() => [...document.querySelectorAll('ytd-video-renderer a#video-title[href*="/watch?"]')]
            .slice(0,40).map(a => ({text:(a.innerText || a.textContent || '').trim().slice(0,500),href:a.href}))""")
    if operation == "youtube_playback":
        action = args["action"]
        if action not in {"play", "pause"}:
            raise ValueError("Playback action must be play or pause")
        expected = str(args["expected_video_id"])
        deadline = time.monotonic() + max(100, min(int(args.get("timeout_ms", 5000)), 15000)) / 1000
        while True:
            check()
            require_clear_page(page)
            before = selected.evaluate(_VIDEO_STATE_JS)
            if before.get("selected_video_id") != expected:
                raise RuntimeError("Selected YouTube video changed before playback")
            if before.get("has_video") and before.get("loaded_video_id") == expected and not before.get("ad_playing"):
                _require_video(before, expected, check_playback_error=False)
                break
            if before.get("error") or before.get("inspection_error") or time.monotonic() >= deadline:
                raise RuntimeError("Requested YouTube content did not become ready; playback was not dispatched")
            time.sleep(0.05)
        check()
        selected.evaluate(_VIDEO_ACTION_JS, {"action": action, "expected_video_id": expected})
        while True:
            check()
            require_clear_page(page)
            state = selected.evaluate(_VIDEO_STATE_JS)
            _require_video(state, expected)
            paused = state.get("paused") is True
            if (action == "pause" and paused) or (action == "play" and not paused and not state.get("ended")
                    and state.get("ready_state", 0) >= 2 and state.get("current_time", 0) > before.get("current_time", 0) + 0.02):
                return {**state, "action": action, "verified": True}
            if time.monotonic() >= deadline:
                raise TimeoutError("YouTube playback did not reach the requested state; no action was replayed")
            time.sleep(0.05)
    raise ValueError(f"Unsupported semantic browser operation: {operation}")


def _require_video(state: dict[str, Any], expected: str, *, check_playback_error: bool = True) -> None:
    if not state.get("has_video") or state.get("selected_video_id") != expected or state.get("loaded_video_id") != expected:
        raise RuntimeError("Selected/loaded YouTube video does not match the requested video")
    if state.get("error") or state.get("ad_playing"):
        raise RuntimeError("Content playback is unavailable or an advertisement is active")
    if state.get("inspection_error") or (check_playback_error and state.get("playback_error")):
        raise RuntimeError("YouTube playback inspection/action failed: " + str(state.get("inspection_error") or state.get("playback_error")))


def browser_semantic_snapshot(force: bool = False, max_nodes: int = 160, frame_selector: str = "") -> str:
    from .chrome_cdp import chrome_current_tab, chrome_page_operation, chrome_tabs
    result = chrome_page_operation("semantic_snapshot", force=force, max_nodes=max_nodes, frame_selector=frame_selector)
    result["tab"] = json.loads(chrome_current_tab())
    result["tabs"] = json.loads(chrome_tabs())[:40]
    return json.dumps(result, ensure_ascii=False)


def browser_semantic_action(action: str, target: dict[str, Any], value: str = "", expected_version: str = "", frame_selector: str = "") -> str:
    from .chrome_cdp import chrome_page_operation
    result = chrome_page_operation("semantic_action", action=action, target=target, value=value, expected_version=expected_version, frame_selector=frame_selector)
    return ("VERIFIED: " if result.get("verified") else "ACTION_EXECUTED: ") + json.dumps(result, ensure_ascii=False)


def browser_wait_state(target: dict[str, Any], state: str = "visible", timeout_ms: int = 5000, text: str = "", frame_selector: str = "") -> str:
    from .chrome_cdp import chrome_page_operation
    result = chrome_page_operation("wait_state", target=target, state=state, timeout_ms=timeout_ms, text=text, frame_selector=frame_selector)
    return "VERIFIED: " + json.dumps(result, ensure_ascii=False)


def register_browser_semantic_tools(registry: Any) -> None:
    from .permissions import Risk
    from .tools import ToolSpec
    target = {"type": "object", "properties": {"node_id": {"type": "string", "maxLength": 32},
        "selector": {"type": "string", "maxLength": 500}, "role": {"type": "string", "maxLength": 40},
        "name": {"type": "string", "maxLength": 300}}, "additionalProperties": False,
        "oneOf": [{"required": ["node_id"], "maxProperties": 1}, {"required": ["selector"], "maxProperties": 1}, {"required": ["role", "name"], "maxProperties": 2}]}
    frame = {"type": "string", "maxLength": 500}
    registry.register(ToolSpec("browser_semantic_snapshot", "Observe bounded visible DOM controls, parent hierarchy, focus, dialogs, page and tabs with a change version. Prefer these exact controls over coordinates; inspect an iframe explicitly when needed. Values are omitted.", Risk.LOW,
        {"type": "object", "properties": {"force": {"type": "boolean"}, "max_nodes": {"type": "integer", "minimum": 1, "maximum": 250}, "frame_selector": frame}, "additionalProperties": False}, browser_semantic_snapshot))
    registry.register(ToolSpec("browser_semantic_action", "Execute one exact semantic action, refusing ambiguous or stale targets. Use snapshot node_id plus expected_version, or exact role/name. Fill/select/check/focus verify locally; click/press require important result verification. Never blindly repeat an uncertain click.", Risk.MEDIUM,
        {"type": "object", "properties": {"action": {"enum": ["click", "fill", "press", "select", "check", "uncheck", "focus"]}, "target": target, "value": {"type": "string", "maxLength": 4096}, "expected_version": {"type": "string", "maxLength": 100}, "frame_selector": frame}, "required": ["action", "target"], "additionalProperties": False}, browser_semantic_action))
    registry.register(ToolSpec("browser_wait_state", "Wait for an exact unique DOM target state with bounded 50ms polling and cancellation. Returns immediately when true; no fixed loading delay or action retries.", Risk.LOW,
        {"type": "object", "properties": {"target": target, "state": {"enum": ["visible", "hidden", "enabled", "text"]}, "timeout_ms": {"type": "integer", "minimum": 0, "maximum": 30000}, "text": {"type": "string", "maxLength": 1000}, "frame_selector": frame}, "required": ["target"], "additionalProperties": False}, browser_wait_state))
