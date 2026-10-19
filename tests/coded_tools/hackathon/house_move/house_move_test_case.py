import os
import shutil
import tempfile
from pathlib import Path
from unittest import TestCase

from cryptography.fernet import Fernet

from coded_tools.hackathon.house_move.case_store import CaseStore
from coded_tools.hackathon.house_move.import_statement import ImportStatement
from coded_tools.hackathon.house_move.key_folder import KeyFolder
from coded_tools.hackathon.house_move.personal_data_vault import PersonalDataVault
from coded_tools.hackathon.house_move.session_tokens import SessionTokens


class HouseMoveTestCase(TestCase):
    """
    Base class for House Move tests: every test gets its own temporary case database, vault key, session secret, key
    folder and uploads folder, so nothing touches real data and tests cannot see each other's cases.
    """

    ENV_KEYS: tuple[str, ...] = (
        CaseStore.DB_ENV,
        PersonalDataVault.KEY_ENV,
        SessionTokens.SECRET_ENV,
        ImportStatement.UPLOAD_ENV,
        KeyFolder.DIR_ENV,
    )

    def setUp(self):
        """
        Points the case store, vault, session secret and uploads at a temporary folder.
        """
        self.folder: Path = Path(tempfile.mkdtemp())
        self.saved_env: dict = {key: os.environ.get(key) for key in HouseMoveTestCase.ENV_KEYS}
        os.environ[CaseStore.DB_ENV] = str(self.folder / "cases.sqlite")
        os.environ[PersonalDataVault.KEY_ENV] = Fernet.generate_key().decode()
        os.environ[SessionTokens.SECRET_ENV] = "test-secret-" + self.folder.name
        self.uploads: Path = self.folder / "uploads"
        self.uploads.mkdir()
        os.environ[ImportStatement.UPLOAD_ENV] = str(self.uploads)
        os.environ[KeyFolder.DIR_ENV] = str(self.folder / "keys")

    def tearDown(self):
        """
        Restores the environment and removes the temporary folder.
        """
        for key, value in self.saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.folder)
