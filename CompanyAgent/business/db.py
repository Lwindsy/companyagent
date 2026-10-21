"""One file, short transactions, one connection per operation/thread."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class Database:
    # A Database stores a file address, not an open connection. Different calls can
    # therefore open independent connections to the same persistent database file.
    def __init__(self, path: str):
        self.path = path

    # @contextmanager lets the caller write "with db.connect() as conn:".
    # Code before yield prepares the resource; code after it cleans the resource up.
    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        # Under this project's Python 3.12 legacy transaction mode, None prevents
        # the driver from adding BEGIN automatically. transaction() adds it explicitly.
        # timeout is a lock-wait setting in seconds, not a deadline for the whole task.
        conn = sqlite3.connect(self.path, isolation_level=None, timeout=5)
        # A Row supports both row[0] and row["column_name"], making SQL results readable.
        conn.row_factory = sqlite3.Row
        # SQLite settings beginning with PRAGMA configure the database or connection.
        # Enable relationship checks on EVERY connection, before beginning a transaction.
        conn.execute("PRAGMA foreign_keys=ON")
        # This sets the same connection's busy wait in milliseconds; it does not add
        # another five seconds to timeout=5 or guarantee that lock contention succeeds.
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            # Pause here while the caller runs the statements inside its with block.
            yield conn
        finally:
            # finally runs on normal exit and Python exceptions. Closing releases
            # resources; connect() itself does not commit a group of business changes.
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as conn:
            # Acquire the database's write slot BEFORE checking facts such as whether
            # a charge was refunded. Another writer must wait or receive a busy error.
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Every query and update using this conn belongs to this transaction.
                # Keep network/model calls outside it so other writers are not held up.
                yield conn
                # Reaching here means the caller completed without an exception.
                # A return inside the caller's with block also passes through this commit.
                conn.commit()
            except BaseException:
                # Undo this transaction's uncommitted changes, then preserve the failure.
                # This covers Python interruption paths, not a hard process kill; it also
                # cannot undo a payment already sent to an external payment provider.
                conn.rollback()
                raise

    def initialize(self) -> None:
        # parents=True creates missing directories; exist_ok=True allows an existing one.
        # This prepares storage without deleting any database that is already there.
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            # WAL appends changes to a log and permits better reader/writer overlap.
            # SQLite still permits only one write transaction at a time per database.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
            # MAX finds the last applied version; COALESCE substitutes zero for an empty table.
            current = conn.execute("SELECT COALESCE(MAX(version),0) FROM schema_version").fetchone()[0]
            # Migration files are numbered 001, 002, ... and applied in filename order.
            # The startup design assumes one migration owner, not concurrent migrators.
            migrations = sorted(Path(__file__).with_name("migrations").glob("[0-9][0-9][0-9]_*.sql"))
            if current > len(migrations):
                # Older code must not silently operate on a newer, unknown table layout.
                raise RuntimeError("Unsupported business database schema")
            for migration in migrations:
                version = int(migration.name.split("_", 1)[0])
                if version > current:
                    sql = migration.read_text(encoding="utf-8")
                    # Execute the table changes and version marker in ONE explicit transaction.
                    # executescript accepts several SQL statements. The interpolated version
                    # is an integer parsed from a local filename, never customer-supplied SQL.
                    conn.executescript("BEGIN IMMEDIATE;\n" + sql + f"\nINSERT INTO schema_version VALUES ({version});\nCOMMIT;")
