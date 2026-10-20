import os
import secrets
import shutil
import tempfile
from pathlib import Path
from unittest import TestCase

import pytest
from cryptography.fernet import Fernet
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
    issues fresh sessions into the environment variables the fixtures read, so no real case data is touched.
    """

    DYNAMIC = DynamicHoconUnitTests(__file__, path_to_basis="../fixtures")
    ENV_KEYS: tuple[str, ...] = (
        "AGENT_MANIFEST_FILE",
        CaseDatabase.DB_ENV,
        PersonalDataVault.KEY_ENV,
        SessionTokens.SECRET_ENV,
        KeyFolder.DIR_ENV,
        "HOUSE_MOVE_TEST_SESSION",
        "HOUSE_MOVE_TEST_GUEST_SESSION",
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
        os.environ["HOUSE_MOVE_TEST_GUEST_SESSION"] = tokens.issue(SessionTokens.new_guest_id(), [])
        # What `python -m neuro_san_studio run` sets for the server: where the coded tools and toolbox live.
        ProjectEnvironment(Path(__file__).resolve().parents[2]).apply()

    @classmethod
    def tearDownClass(cls):
        """
        Restores the environment and removes the temporary folder.
        """
        for key, value in cls.saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(cls.folder)

    @parameterized.expand(
        DynamicHoconUnitTests.from_hocon_list(
            [
                "hackathon/house_move/sign_in_required.hocon",
                "hackathon/house_move/guest_starts_on_the_form.hocon",
                "hackathon/house_move/bank_customer_discovery.hocon",
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
