"""Where the House Move demo keeps its local key files: apart from the data they protect."""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class KeyFolder:  # pylint: disable=too-few-public-methods  # one lookup
    """
    Local key files (the vault's encryption key, the session-signing secret) live in their own owner-only folder,
    outside the repository and away from case_data/, so a copy of the data folder or database alone cannot be
    decrypted. HOUSE_MOVE_KEY_DIR overrides the folder. Environment variables (HOUSE_MOVE_VAULT_KEY,
    HOUSE_MOVE_SESSION_SECRET) take precedence over any file; production would use a managed key service instead.
    """

    DIR_ENV: str = "HOUSE_MOVE_KEY_DIR"
    DEFAULT_DIR: Path = Path.home() / ".house_move" / "keys"

    @staticmethod
    def path(name: str, legacy: Path | None = None) -> Path:
        """
        :param name: the key file's name, for example "vault.key".
        :param legacy: where an older version kept this key (next to the data). If a key is still there and none is
                       in the key folder yet, it is moved, so existing cases keep opening.
        :return: the key file's path in the owner-only key folder (which is created if needed).
        """
        folder: Path = Path(os.environ.get(KeyFolder.DIR_ENV) or KeyFolder.DEFAULT_DIR)
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o700)
        target: Path = folder / name
        if legacy is not None and legacy.is_file() and not target.exists():
            legacy.replace(target)
            target.chmod(0o600)
            logger.warning("Moved the key %s out of the data folder into %s", legacy.name, folder)
        return target
