from coded_tools.hackathon.house_move.draft_renderer import DraftRenderer
from coded_tools.hackathon.house_move.letter_templates import LetterTemplates
from coded_tools.hackathon.house_move.save_drafts import SaveDrafts
from tests.coded_tools.hackathon.house_move.house_move_test_case import HouseMoveTestCase
from tests.coded_tools.hackathon.house_move.house_move_test_state import HouseMoveTestState


class TestDrafts(HouseMoveTestCase):
    """
    Unit tests for LetterTemplates, SaveDrafts and the DraftRenderer they use.
    """

    def test_templates_by_channel(self):
        """
        Requested channels come back with the placeholder lists; an unknown channel is an error.
        """
        result = LetterTemplates().invoke({"channels": ["email"]}, {})
        self.assertEqual(["email"], list(result.get("templates")))
        self.assertIn("new_address", result.get("profile_placeholders"))
        self.assertTrue(LetterTemplates().invoke({"channels": ["fax"]}, {}).startswith("Error:"))

    def test_save_renders_personal_data_and_awaits_approval(self):
        """
        The stored draft keeps its placeholders and no filled-in copy; rendering fills them from the profile; the LLM
        gets IDs and gaps back.
        """
        sly_data: dict = HouseMoveTestState.planned()
        task: dict = HouseMoveTestState.task_for(sly_data, "energy_supplier")
        body: str = "Please close our account. We move on {{move_date}} to {{new_address}}. Ref {{account_number}}."
        result = SaveDrafts().run(
            {"drafts": [{"task_id": task.get("task_id"), "subject": "Moving: {{full_names}}", "body": body}]},
            sly_data,
        )

        saved: dict = result.get("saved")[0]
        self.assertEqual(["account number"], saved.get("gaps_for_customer"))
        self.assertNotIn("Mayfield", str(result))
        draft: dict = sly_data.get("drafts").get(saved.get("draft_id"))
        self.assertNotIn("Mayfield", str(draft))
        renderer: DraftRenderer = DraftRenderer(["account_number"])
        subject, _, _ = renderer.render(draft.get("subject"), task, sly_data)
        rendered, gaps, _ = renderer.render(draft.get("body"), task, sly_data)
        self.assertEqual("Moving: Sam Morgan and Alex Morgan", subject)
        self.assertIn("27 November 2026 to 7 Mayfield Grove, Harrogate, HG1 1SY", rendered)
        self.assertIn("[add your account number]", rendered)
        self.assertEqual(["account number"], gaps)
        self.assertEqual(body, draft.get("body"))
        self.assertEqual("awaiting_approval", task.get("status"))

    def test_org_name_uses_provider_name(self):
        """
        {{org_name}} is the provider's own name, not the task label.
        """
        sly_data: dict = HouseMoveTestState.planned()
        task: dict = HouseMoveTestState.task_for(sly_data, "energy_supplier")
        SaveDrafts().run(
            {"drafts": [{"task_id": task.get("task_id"), "subject": "x", "body": "Dear {{org_name}}"}]}, sly_data
        )
        draft: dict = sly_data.get("drafts").get(task.get("draft_id"))
        self.assertEqual("Dear Brightwatt Energy", DraftRenderer([]).render(draft.get("body"), task, sly_data)[0])

    def test_unknown_placeholder_is_rejected(self):
        """
        A draft with an invented placeholder is not saved, and the task stays planned.
        """
        sly_data: dict = HouseMoveTestState.planned()
        task: dict = HouseMoveTestState.task_for(sly_data, "broadband")
        result = SaveDrafts().run(
            {"drafts": [{"task_id": task.get("task_id"), "subject": "Hi", "body": "{{bank_sort_code}}"}]}, sly_data
        )
        self.assertEqual([], result.get("saved"))
        self.assertIn("bank_sort_code", result.get("rejected")[0])
        self.assertEqual("planned", task.get("status"))
