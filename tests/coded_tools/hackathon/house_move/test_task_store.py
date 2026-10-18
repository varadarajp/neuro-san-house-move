from unittest import TestCase

from coded_tools.hackathon.house_move.list_tasks import ListTasks
from coded_tools.hackathon.house_move.update_tasks import UpdateTasks
from tests.coded_tools.hackathon.house_move.house_move_test_state import HouseMoveTestState


class TestTaskStore(TestCase):
    """
    Unit tests for ListTasks and UpdateTasks.
    """

    def test_list_filters_by_agent_and_status(self):
        """
        Filtering by agent returns only that agent's tasks; an unknown status returns none.
        """
        sly_data: dict = HouseMoveTestState.planned()
        government = ListTasks().invoke({"agent": "government_agent"}, sly_data)
        self.assertEqual(
            {"dvla_licence", "dvla_v5c", "council_tax_old", "council_tax_new", "electoral_register"},
            {task.get("obligation_id") for task in government.get("tasks")},
        )
        self.assertEqual([], ListTasks().invoke({"status": "sent"}, sly_data).get("tasks"))
        self.assertTrue(ListTasks().invoke({}, {}).startswith("Error:"))

    def test_update_records_details_but_not_dates_or_status(self):
        """
        Handling details are stored; due date and status cannot be changed; bad channels are rejected.
        """
        sly_data: dict = HouseMoveTestState.planned()
        task: dict = HouseMoveTestState.task_for(sly_data, "broadband")
        due_date: str = task.get("due_date")
        result = UpdateTasks().invoke(
            {
                "updates": [
                    {
                        "task_id": task.get("task_id"),
                        "channel": "phone",
                        "action": "Book the move",
                        "due_date": "2027-01-01",
                        "status": "approved",
                    },
                    {"task_id": "T-99", "channel": "phone"},
                    {"task_id": task.get("task_id"), "channel": "carrier pigeon"},
                ]
            },
            sly_data,
        )
        self.assertEqual([task.get("task_id")], result.get("updated"))
        self.assertEqual(2, len(result.get("rejected")))
        self.assertEqual("phone", task.get("channel"))
        self.assertEqual("Book the move", task.get("action"))
        self.assertEqual(due_date, task.get("due_date"))
        self.assertEqual("planned", task.get("status"))
