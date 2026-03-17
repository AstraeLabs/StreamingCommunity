# 29.01.26

import os

try:
    import sqlite3

    SQLITE3_AVAILABLE = True
except Exception:
    SQLITE3_AVAILABLE = False
from typing import List, Optional
from urllib.parse import urlparse

from rich.console import Console

from VibraVid.setup import binary_paths
from VibraVid.utils import config_manager


console = Console()
CREATE_DB_ON_STARTUP = config_manager.config.get("DRM", "create_local_db")


class LocalDBVault:
    def __init__(self, db_path):
        self.db_path = db_path
        self._init_database()

    def _init_database(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Main table for storing DRM cache entries
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS drm_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    base_url_license TEXT NOT NULL,
                    pssh TEXT NOT NULL,
                    drm_type TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    access_count INTEGER DEFAULT 1,
                    UNIQUE(base_url_license, pssh, drm_type)
                )
            """)

            # Separate table for keys (one-to-many relationship)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS drm_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cache_id INTEGER NOT NULL,
                    kid TEXT NOT NULL,
                    key TEXT NOT NULL,
                    label TEXT,
                    is_valid BOOLEAN DEFAULT 1,
                    FOREIGN KEY (cache_id) REFERENCES drm_cache(id) ON DELETE CASCADE,
                    UNIQUE(cache_id, kid)
                )
            """)

            # Indexes for performance
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_cache_lookup 
                ON drm_cache(base_url_license, pssh, drm_type)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_keys_cache 
                ON drm_keys(cache_id)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_keys_kid 
                ON drm_keys(kid)
            """)

            conn.commit()

    def _clean_license_url(self, license_url: str) -> str:
        """Extract base URL from license URL (remove query parameters and fragments)"""
        parsed = urlparse(license_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        return base_url.rstrip("/")

    # --------- SET
    def set_key(self, kid: str, key: str, drm_type: str, license_url: str, pssh: str = None, label: str = None) -> bool:
        """Add a single DRM key to the database"""
        kid = kid.replace("-", "").strip().lower()
        key = key.replace("-", "").strip().lower()
        drm_type = drm_type.lower()
        base_url = self._clean_license_url(license_url)

        if drm_type not in ["widevine", "playready"]:
            console.print(f"[red]Invalid DRM type: {drm_type}. Must be 'widevine' or 'playready'.")
            return False

        if not pssh:
            console.print(f"[yellow]Warning: No PSSH provided for KID: {kid}")
            return False

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            try:
                # Check if cache entry exists
                cursor.execute(
                    """
                    SELECT id FROM drm_cache 
                    WHERE base_url_license = ? AND pssh = ? AND drm_type = ?
                """,
                    (base_url, pssh, drm_type),
                )

                result = cursor.fetchone()

                if result:
                    cache_id = result[0]

                    # Update access statistics
                    cursor.execute(
                        """
                        UPDATE drm_cache 
                        SET last_accessed = CURRENT_TIMESTAMP, 
                            access_count = access_count + 1
                        WHERE id = ?
                    """,
                        (cache_id,),
                    )

                    # Check if key already exists
                    cursor.execute(
                        """
                        SELECT id FROM drm_keys 
                        WHERE cache_id = ? AND kid = ?
                    """,
                        (cache_id, kid),
                    )

                    if cursor.fetchone():
                        console.print(f"\n[yellow]Key already exists for KID: {kid}")
                        conn.commit()
                        return False
                else:
                    # Create new cache entry
                    cursor.execute(
                        """
                        INSERT INTO drm_cache (base_url_license, pssh, drm_type)
                        VALUES (?, ?, ?)
                    """,
                        (base_url, pssh, drm_type),
                    )
                    cache_id = cursor.lastrowid

                # Insert key
                cursor.execute(
                    """
                    INSERT INTO drm_keys (cache_id, kid, key, label)
                    VALUES (?, ?, ?, ?)
                """,
                    (cache_id, kid, key, label),
                )

                conn.commit()
                return True

            except sqlite3.IntegrityError as e:
                console.print(f"[yellow]Key already exists: {e}")
                return False
            except Exception as e:
                console.print(f"[red]Error adding key: {e}")
                conn.rollback()
                return False

    def set_keys(self, keys_list: List[str], drm_type: str, license_url: str, pssh: str = None) -> int:
        """Add multiple keys to the database at once."""
        if not keys_list:
            console.print("[yellow]No keys provided to add.")
            return 0

        added_count = 0
        for key_str in keys_list:
            if ":" in key_str:
                kid, key = key_str.split(":", 1)
                label = None

                if self.set_key(kid, key, drm_type, license_url, pssh, label):
                    added_count += 1

        return added_count

    # --------- GET
    def get_keys_by_pssh(self, license_url: str, pssh: str, drm_type: str) -> List[str]:
        """
        Retrieve all keys for a given license URL, PSSH, and DRM type.

        Args:
            license_url (str): License URL.
            pssh (str): PSSH value.
            drm_type (str): Either 'widevine' or 'playready'.

        Returns:
            list: List of "KID:KEY" strings found in database.
        """
        base_url = self._clean_license_url(license_url)
        drm_type = drm_type.lower()

        if drm_type not in ["widevine", "playready"]:
            console.print(f"[red]Invalid DRM type: {drm_type}")
            return []

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Find cache entry
            cursor.execute(
                """
                SELECT id FROM drm_cache 
                WHERE base_url_license = ? AND pssh = ? AND drm_type = ?
            """,
                (base_url, pssh, drm_type),
            )

            result = cursor.fetchone()

            if not result:
                return []

            cache_id = result[0]

            # Update access statistics
            cursor.execute(
                """
                UPDATE drm_cache 
                SET last_accessed = CURRENT_TIMESTAMP, 
                    access_count = access_count + 1
                WHERE id = ?
            """,
                (cache_id,),
            )

            # Retrieve all keys
            cursor.execute(
                """
                SELECT kid, key, label 
                FROM drm_keys 
                WHERE cache_id = ? AND is_valid = 1
            """,
                (cache_id,),
            )

            rows = cursor.fetchall()
            conn.commit()

            if not rows:
                return []

            pssh_display = f"{pssh[:30]}..." if len(pssh) > 30 else pssh
            console.print(f"\n[red]{drm_type} [cyan](PSSH: [yellow]{pssh_display}[cyan])")
            keys = []
            for row in rows:
                kid, key, label = row
                keys.append(f"{kid}:{key}")
                console.print(f"    - [red]{kid}[white]:[green]{key} [cyan]| [#a855f7]local")

            return keys

    def get_keys_by_kids(self, license_url: Optional[str], kids: List[str], drm_type: str, pssh: str = None) -> List[str]:
        """
        Retrieve keys for one or more KIDs in a single SQL query.

        Args:
            license_url (Optional[str]): License URL. If None, search globally by KID.
            kids (List[str]): List of KID values to look up.
            drm_type (str): Either 'widevine' or 'playready'.
            pssh (str): Optional PSSH value for proper display context

        Returns:
            List[str]: List of "KID:KEY" strings found.
        """
        if not kids:
            return []

        base_url = self._clean_license_url(license_url) if license_url else None
        normalized_kids = [k.replace("-", "").strip().lower() for k in kids]
        drm_type = drm_type.lower()

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            placeholders = ",".join("?" * len(normalized_kids))

            if base_url:
                cursor.execute(
                    f"""
                    SELECT k.kid, k.key
                    FROM drm_keys k
                    JOIN drm_cache c ON k.cache_id = c.id
                    WHERE c.base_url_license = ?
                    AND c.drm_type = ?
                    AND k.kid IN ({placeholders})
                    AND k.is_valid = 1
                """,
                    [base_url, drm_type] + normalized_kids,
                )
            else:
                cursor.execute(
                    f"""
                    SELECT k.kid, k.key
                    FROM drm_keys k
                    JOIN drm_cache c ON k.cache_id = c.id
                    WHERE c.drm_type = ?
                    AND k.kid IN ({placeholders})
                    AND k.is_valid = 1
                """,
                    [drm_type] + normalized_kids,
                )

            found = cursor.fetchall()
            if found and base_url:
                cursor.execute(
                    """
                    UPDATE drm_cache
                    SET last_accessed = CURRENT_TIMESTAMP,
                        access_count = access_count + 1
                    WHERE base_url_license = ? AND drm_type = ?
                """,
                    (base_url, drm_type),
                )
                conn.commit()

            if not found:
                return []

            if pssh:
                pssh_display = f"{pssh[:30]}..." if len(pssh) > 30 else pssh
            else:
                pssh_display = f"{normalized_kids[0][:30]}..." if normalized_kids else "..."
            
            console.print(f"\n[red]{drm_type} [cyan](PSSH: [yellow]{pssh_display}[cyan])")
            result_keys = []
            for row in found:
                kid, key = row
                result_keys.append(f"{kid}:{key}")
                console.print(f"    - [red]{kid}[white]:[green]{key} [cyan]| [#a855f7]local")

            return result_keys

    def get_keys_by_kid(self, license_url: Optional[str], kid: str, drm_type: str) -> List[str]:
        """Convenience wrapper for a single KID lookup."""
        return self.get_keys_by_kids(license_url, [kid], drm_type)


# Initialize
if SQLITE3_AVAILABLE:
    try:
        if CREATE_DB_ON_STARTUP:
            obj_localDbValut = LocalDBVault(os.path.join(binary_paths.get_binary_directory(), "drm_keys.db"))
        else:
            obj_localDbValut = None
    except Exception:
        obj_localDbValut = None
else:
    obj_localDbValut = None