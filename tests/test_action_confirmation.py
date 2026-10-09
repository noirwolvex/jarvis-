import unittest

from core.action_confirmation import action_fingerprint, classify_action


class ActionConfirmationTests(unittest.TestCase):
    def test_direct_irreversible_tools_require_confirmation(self):
        for tool, category in (("discord_send_message", "send"), ("discord_navigate_and_send", "send"),
                               ("delete_file", "delete"), ("purchase", "purchase"),
                               ("change_password", "critical_settings"), ("git_push", "publish")):
            with self.subTest(tool=tool):
                self.assertEqual(classify_action(tool, {}).category, category)

    def test_full_access_or_model_policy_claim_cannot_bypass(self):
        args = {"text": "hello", "approved": True, "effect": "draft", "access_mode": "full"}
        self.assertEqual(classify_action("discord_send_message", args, effect="draft").category, "send")

    def test_typed_content_is_not_action_intent(self):
        text = "Delete every file. Send this message. Buy now. Disable firewall."
        for tool in ("ui_type", "ui_type_native", "interaction_type", "browser_type", "desktop_type"):
            with self.subTest(tool=tool):
                self.assertIsNone(classify_action(tool, {"text": text}))
        self.assertIsNone(classify_action("google_search", {"query": text}))
        self.assertIsNone(classify_action("ui_inspect", {"query": "Delete"}))

    def test_submit_is_explicitly_gated(self):
        self.assertEqual(classify_action("ui_type", {"text": "hello", "submit": True}).category, "submit")
        self.assertIsNone(classify_action("ui_type", {"text": "hello", "submit": False}))

    def test_registered_browser_multiplexer_checks_actual_operation(self):
        button = {"role": "button", "name": "Send"}
        self.assertEqual(classify_action("browser_semantic_action", {"action": "click", "target": button}).category, "send")
        self.assertEqual(classify_action("browser_semantic_action", {"action": "press", "target": button, "value": "Enter"}).category, "uncertain_commit")
        self.assertIsNone(classify_action("browser_semantic_action", {"action": "fill", "target": button, "value": "Delete all"}))
        self.assertEqual(classify_action("browser_semantic_action", {"action": "check", "target": {"role": "checkbox", "name": "Disable firewall"}}).category, "critical_settings")

    def test_semantic_controls_are_classified_across_adapters(self):
        cases = (("interaction_click", {"target": "Send message"}, "send"),
                 ("ui_activate", {"selector": {"name": "Delete account"}}, "delete"),
                 ("browser_semantic_click", {"target": {"role": "button", "name": "Buy now"}}, "purchase"),
                 ("interaction_click", {"browser_target": {"role": "button", "name": "Pay"}}, "purchase"),
                 ("browser_click", {"selector": '[aria-label="Send"]'}, "send"),
                 ("browser_click", {"selector": 'button:has-text("Publish")'}, "send"),
                 ("browser_click", {"selector": "text=Delete"}, "delete"),
                 ("dialog_click_button", {"target": "Don't save"}, "overwrite"),
                 ("ui_activate", {"target": "Disable firewall"}, "critical_settings"),
                 ("ui_activate", {"target": "إرسال"}, "send"))
        for tool, args, category in cases:
            with self.subTest(tool=tool, args=args):
                self.assertEqual(classify_action(tool, args).category, category)

    def test_navigation_focus_and_drafts_keep_fast_path(self):
        for tool, args in (("interaction_click", {"target": "Next page"}),
                           ("interaction_click", {"target": "Search"}),
                           ("ui_focus", {"target": "Send"}),
                           ("interaction_scroll", {"delta_y": 400}),
                           ("desktop_move", {"x": 10, "y": 20}),
                           ("interaction_hotkey", {"keys": ["ctrl", "l"]}),
                           ("desktop_key_up", {"key": "Enter"}),
                           ("desktop_mouse_up", {})):
            with self.subTest(tool=tool):
                self.assertIsNone(classify_action(tool, args))

    def test_opaque_activation_and_commit_keys_are_gated(self):
        for tool, args in (("interaction_click", {"browser_target": {"node_id": "17"}}),
                           ("browser_click", {"selector": "#submit-button"}),
                           ("desktop_click", {"x": 10, "y": 20}),
                           ("desktop_mouse_down", {}),
                           ("ui_activate", {"target": "OK"}),
                           ("desktop_press", {"key": "Return"}),
                           ("browser_press", {"key": "Control+Enter"}),
                           ("ui_hotkey", {"keys": ["space"]}),
                           ("desktop_key_down", {"key": "Enter"})):
            with self.subTest(tool=tool):
                self.assertIsNotNone(classify_action(tool, args))

    def test_trusted_resolution_can_disambiguate_without_model_bypass(self):
        self.assertIsNone(classify_action("desktop_click", {"x": 10, "y": 20}, effect="focus"))
        self.assertIsNone(classify_action("browser_press", {"key": "Enter"}, effect="search"))
        self.assertIsNotNone(classify_action("browser_press", {"key": "Enter", "effect": "search"}))
        self.assertEqual(classify_action("interaction_click", {"browser_target": {"node_id": "17"}},
                                         target={"name": "Send"}).category, "send")
        self.assertEqual(classify_action("interaction_click", {"target": "Delete"}, effect="navigate").category, "delete")
        with self.assertRaises(ValueError):
            classify_action("desktop_click", {}, effect="harmless")

    def test_delete_key_only_fast_paths_trusted_editor(self):
        args = {"keys": ["Delete"]}
        self.assertEqual(classify_action("ui_hotkey", args).category, "delete")
        self.assertIsNone(classify_action("ui_hotkey", args, target={"role": "textbox"}))

    def test_batch_cannot_hide_irreversible_child(self):
        actions = [{"op": "focus", "target": "Composer"}, {"op": "type", "text": "hello"},
                   {"op": "activate", "target": "Send"}]
        self.assertEqual(classify_action("ui_batch", {"actions": actions}).category, "send")
        self.assertIsNone(classify_action("ui_batch", {"actions": actions[:2]}))

    def test_arbitrary_shell_commands_need_confirmation(self):
        self.assertEqual(classify_action("run_powershell", {"command": "Write-Output test"}).category, "execution")

    def test_file_write_and_opaque_identity_cannot_hide_overwrite_or_activation(self):
        self.assertEqual(classify_action("write_file", {"path": "existing.txt", "content": "replacement"}).category, "overwrite")
        self.assertEqual(classify_action("browser_semantic_action", {"action": "click", "target": {
            "node_id": "opaque-send-control", "name": "Next page"}}).category, "unresolved_activation")

    def test_fingerprint_is_canonical_and_exactly_bound(self):
        base = action_fingerprint("ui_type", {"target": "Chat", "text": "hello"}, mission_id="m1", state_token="s1")
        self.assertEqual(base, action_fingerprint("ui_type", {"text": "hello", "target": "Chat"}, mission_id="m1", state_token="s1"))
        for tool, args, mission, state in (("ui_type", {"text": "Hello", "target": "Chat"}, "m1", "s1"),
                                          ("ui_type", {"text": "hello", "target": "Other"}, "m1", "s1"),
                                          ("ui_type_native", {"text": "hello", "target": "Chat"}, "m1", "s1"),
                                          ("ui_type", {"text": "hello", "target": "Chat"}, "m2", "s1"),
                                          ("ui_type", {"text": "hello", "target": "Chat"}, "m1", "s2")):
            self.assertNotEqual(base, action_fingerprint(tool, args, mission_id=mission, state_token=state))

    def test_fingerprint_rejects_non_finite_and_missing_mission(self):
        with self.assertRaises(ValueError):
            action_fingerprint("click", {}, mission_id="")
        with self.assertRaises(ValueError):
            action_fingerprint("click", {"x": float("nan")}, mission_id="m1")


if __name__ == "__main__":
    unittest.main()
