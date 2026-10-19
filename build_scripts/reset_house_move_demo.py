"""
Resets the House Move demo so every test customer can run a full journey from the start: no plans, drafts,
approvals or sends for the bank customers, no standalone cases, no waiting codes, uploads or saved details, and an
empty audit trail.

Nothing is lost: the case database, uploads and audit file are moved into case_data/backups/<time>/ first, so a
reset can be undone by moving them back. The encryption keys (in the key folder) are kept, so a backup still opens.
Synthetic data only.

Run from the repository root, with the portal and agent server running or not:
    python -m build_scripts.reset_house_move_demo --yes
"""

import argparse
import logging
import os
import shutil
from datetime import UTC
from datetime import datetime
from pathlib import Path

from coded_tools.hackathon.house_move.audit_log import AuditLog
from coded_tools.hackathon.house_move.case_store import CaseStore
from coded_tools.hackathon.house_move.import_statement import ImportStatement

logger = logging.getLogger(__name__)


class DemoReset:
    """
    Moves the demo's working data into a timestamped backup folder, then creates an empty case database.
    The demo customers themselves (customers.json and the other data files) are fixtures and are not touched.
    """

    def __init__(self, db_path: Path | None = None, upload_dir: Path | None = None, audit_file: Path | None = None):
        """
        :param db_path: the case database. Defaults to the one CaseStore uses.
        :param upload_dir: the uploads folder. Defaults to the one the portal and import_statement use.
        :param audit_file: the audit trail. Defaults to logs/house_move_audit.jsonl.
        """
        self._db_path: Path = db_path or Path(os.environ.get(CaseStore.DB_ENV) or CaseStore.DEFAULT_DB)
        self._upload_dir: Path = upload_dir or Path(
            os.environ.get(ImportStatement.UPLOAD_ENV) or ImportStatement.DEFAULT_UPLOAD_DIR
        )
        self._audit_file: Path = audit_file or AuditLog.DEFAULT_FILE

    def reset(self) -> Path:
        """
        Backs up and clears the case database, uploads and audit trail, then creates an empty database.
        :return: the backup folder.
        """
        backup: Path = self._db_path.parent / "backups" / datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        backup.mkdir(parents=True)
        backup.parent.chmod(0o700)
        backup.chmod(0o700)
        # SQLite may keep side files next to the database; they belong to it and move with it.
        for path in (self._db_path, *self._db_path.parent.glob(self._db_path.name + "-*")):
            self._move(path, backup)
        self._move(self._audit_file, backup)
        if self._upload_dir.is_dir():
            for folder in self._upload_dir.iterdir():
                self._move(folder, backup / "uploads")
        CaseStore(db_path=self._db_path)
        logger.info("Reset done. Backup: %s", backup)
        return backup

    @staticmethod
    def _move(path: Path, folder: Path) -> None:
        """
        Moves one file or folder into the backup, if it exists.
        """
        if not path.exists():
            return
        folder.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(folder / path.name))
        logger.info("Moved %s to the backup", path.name)

    @staticmethod
    def main() -> None:
        """
        Resets the demo after the --yes flag confirms it.
        """
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        parser = argparse.ArgumentParser(description="Reset the House Move demo data (backed up first).")
        parser.add_argument("--yes", action="store_true", help="confirm the reset")
        if not parser.parse_args().yes:
            parser.error("add --yes to reset the demo data (it is backed up to case_data/backups first)")
        DemoReset().reset()


if __name__ == "__main__":
    DemoReset.main()
