import os
import secrets
import shutil
import tempfile
from pathlib import Path
from typing import Any
from unittest import TestCase
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from neuro_san.test.evaluators.gist_agent_evaluator import GistAgentEvaluator
from neuro_san.test.unittest.dynamic_hocon_unit_tests import DynamicHoconUnitTests
from parameterized import parameterized

from coded_tools.hackathon.house_move.case_database import CaseDatabase
from coded_tools.hackathon.house_move.key_folder import KeyFolder
from coded_tools.hackathon.house_move.personal_data_vault import PersonalDataVault
from coded_tools.hackathon.house_move.session_tokens import SessionTokens
from neuro_san_studio.commands.project_environment import ProjectEnvironment


class TestHouseMoveHocons(TestCase):
    """
    Data-driven integration tests for the hackathon/house_move network (fixtures under
    tests/fixtures/hackathon/house_move). They call the LLM, so they need an API key.

    The network is off in the standard manifest (it has optional dependencies), so these tests switch it on with
    registries/house_move_demo_manifest.hocon. Each run uses a throwaway case database, keys and session secret, and
    issues fresh sessions into the environment variables the fixtures read, so no real case data is touched. Each
    fixture gets its own empty case database, so a plan saved by one fixture never changes what the next one sees.

    `gist` checks are judged by tests/fixtures/hackathon/house_move/gist_judge.hocon, which uses the project's LLM
    config, instead of neuro-san's stock judge, which needs an OpenAI key.
    """

    DYNAMIC = DynamicHoconUnitTests(__file__, path_to_basis="../fixtures")
    GIST_JUDGE: Path = Path(__file__).resolve().parents[1] / "fixtures/hackathon/house_move/gist_judge.hocon"
    # The synthetic bank customers in coded_tools/hackathon/house_move/data/customers.json. Each gets a session in
    # HOUSE_MOVE_TEST_SESSION_<ID>, for example HOUSE_MOVE_TEST_SESSION_C_20001.
    BANK_CUSTOMERS: tuple[str, ...] = ("C-10042", "C-20001", "C-20002", "C-20003", "C-20004")
    ENV_KEYS: tuple[str, ...] = (
        "AGENT_MANIFEST_FILE",
        CaseDatabase.DB_ENV,
        PersonalDataVault.KEY_ENV,
        SessionTokens.SECRET_ENV,
        KeyFolder.DIR_ENV,
        "HOUSE_MOVE_TEST_SESSION",
        "HOUSE_MOVE_TEST_GUEST_SESSION",
        *(f"HOUSE_MOVE_TEST_SESSION_{customer.replace('-', '_')}" for customer in BANK_CUSTOMERS),
        "AGENT_TOOL_PATH",
        "MCP_SERVERS_INFO_FILE",
        "AGENT_TOOLBOX_INFO_FILE",
        "AGENT_NETWORK_DESIGNER_TOOLBOX_INFO_FILE",
    )

    @classmethod
    def setUpClass(cls):
        """
        Serves the network and points every House Move store at a temporary folder, then issues the sessions.
        """
        cls.saved_env = {key: os.environ.get(key) for key in TestHouseMoveHocons.ENV_KEYS}
        cls.folder = Path(tempfile.mkdtemp())
        os.environ["AGENT_MANIFEST_FILE"] = "registries/manifest.hocon:registries/house_move_demo_manifest.hocon"
        os.environ[CaseDatabase.DB_ENV] = str(cls.folder / "cases.sqlite")
        os.environ[PersonalDataVault.KEY_ENV] = Fernet.generate_key().decode()
        os.environ[SessionTokens.SECRET_ENV] = secrets.token_hex(32)
        os.environ[KeyFolder.DIR_ENV] = str(cls.folder / "keys")
        tokens: SessionTokens = SessionTokens()
        os.environ["HOUSE_MOVE_TEST_SESSION"] = tokens.issue(SessionTokens.bank_owner_id("C-10042"), ["C-10042"])
        for customer in TestHouseMoveHocons.BANK_CUSTOMERS:
            os.environ[f"HOUSE_MOVE_TEST_SESSION_{customer.replace('-', '_')}"] = tokens.issue(
                SessionTokens.bank_owner_id(customer), [customer]
            )
        os.environ["HOUSE_MOVE_TEST_GUEST_SESSION"] = tokens.issue(SessionTokens.new_guest_id(), [])
        # The stock judge's model is a constructor default, and the evaluator factory passes no judge, so the default
        # is swapped for the whole class: (negate, discriminator_agent, connection_type).
        defaults: tuple[Any, ...] = (False, str(TestHouseMoveHocons.GIST_JUDGE), "direct")
        cls.judge_patch = patch.object(GistAgentEvaluator.__init__, "__defaults__", defaults)
        cls.judge_patch.start()
        # What `python -m neuro_san_studio run` sets for the server: where the coded tools and toolbox live.
        ProjectEnvironment(Path(__file__).resolve().parents[2]).apply()

    @classmethod
    def tearDownClass(cls):
        """
        Restores the environment and the stock judge, and removes the temporary folder.
        """
        cls.judge_patch.stop()
        for key, value in cls.saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(cls.folder)

    def setUp(self):
        """
        Gives each fixture an empty case database in the temporary folder. Every House Move tool opens the database
        named in the environment when it runs, so the sessions issued for the class stay valid.
        """
        os.environ[CaseDatabase.DB_ENV] = str(TestHouseMoveHocons.folder / f"{self.id().rsplit('.', 1)[-1]}.sqlite")

    @parameterized.expand(
        DynamicHoconUnitTests.from_hocon_list(
            [
                "hackathon/house_move/sign_in_required.hocon",
                "hackathon/house_move/guest_starts_on_the_form.hocon",
                "hackathon/house_move/bank_customer_discovery.hocon",
                "hackathon/house_move/discovery_c_10042.hocon",
                "hackathon/house_move/discovery_c_20001.hocon",
                "hackathon/house_move/discovery_c_20002.hocon",
                "hackathon/house_move/discovery_c_20003.hocon",
                "hackathon/house_move/discovery_c_20004.hocon",
                "hackathon/house_move/plan_c_20004.hocon",
                "hackathon/house_move/plan_and_drafts_c_10042.hocon",
            ]
        ),
        skip_on_empty=True,
    )
    @pytest.mark.integration
    def test_hocon_house_move(self, test_name: str, test_hocon: str):
        """
        Runs one House Move fixture.
        """
        self.DYNAMIC.one_test_hocon(self, test_name, test_hocon)
