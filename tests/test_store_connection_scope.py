import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tests.test_console_domain import fixture
from worldcup_mvp.console import sources
from worldcup_mvp.console.service import ConsoleService
from worldcup_mvp.console.store import Store


class ConnectionScopeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = Store(Path(folder.name) / 'scope.sqlite')

    def test_reuse_closes_on_failure_and_next_scope_is_fresh(self):
        with patch.object(self.store, '_open_connection', wraps=self.store._open_connection) as opened:
            with self.assertRaisesRegex(ValueError, 'stop'):
                with self.store.connection_scope():
                    with self.store.connect() as first:
                        first.execute('SELECT 1')
                    with self.store.connection_scope(), self.store.connect() as second:
                        self.assertIs(first, second)
                    raise ValueError('stop')
            with self.assertRaises(sqlite3.ProgrammingError):
                first.execute('SELECT 1')
            with self.store.connection_scope(), self.store.connect() as third:
                self.assertIsNot(first, third)
            self.assertEqual(opened.call_count, 2)

    def test_commits_remain_per_operation_and_failed_write_rolls_back(self):
        with self.store.connection_scope():
            with self.store.connect() as db:
                db.execute('CREATE TABLE probe(value INTEGER)')
                db.execute('INSERT INTO probe VALUES(1)')
            # Other connections can read and write while the scope is open.
            with closing(sqlite3.connect(self.store.path, timeout=.1)) as other, other:
                self.assertEqual(other.execute('SELECT value FROM probe').fetchone()[0], 1)
                other.execute('INSERT INTO probe VALUES(2)')
            with self.assertRaisesRegex(ValueError, 'rollback'):
                with self.store.connect() as db:
                    db.execute('INSERT INTO probe VALUES(3)')
                    raise ValueError('rollback')
            with self.store.connect() as db:
                self.assertEqual([r[0] for r in db.execute('SELECT value FROM probe')], [1, 2])

    def test_nested_connect_does_not_commit_outer_failed_transaction(self):
        with self.store.connection_scope():
            with self.assertRaises(ValueError):
                with self.store.connect() as db:
                    db.execute('INSERT INTO schema_versions VALUES(99)')
                    with self.store.connect() as same:
                        same.execute('INSERT INTO schema_versions VALUES(100)')
                    raise ValueError()
            with self.store.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM schema_versions WHERE version>=99').fetchone()[0], 0)

    def test_worker_thread_never_inherits_request_connection(self):
        connections, errors = [], []
        def worker():
            try:
                with self.store.connection_scope(), self.store.connect() as db:
                    connections.append(db)
                    db.execute('SELECT 1')
            except Exception as exc:
                errors.append(exc)
        with self.store.connection_scope(), self.store.connect() as main:
            thread = threading.Thread(target=worker)
            thread.start()
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertFalse(errors)
            self.assertIsNot(main, connections[0])

    def test_review_result_unchanged_with_one_connection_for_whole_list(self):
        now = datetime.now(timezone.utc)
        matches = []
        for i in range(20):
            match = sources.normalize_match(fixture())
            match.update(id=f'sporttery:scope-{i}', provider_id=f'scope-{i}',
                         kickoff=(now-timedelta(hours=3)).isoformat())
            matches.append(match)
        self.store.upsert_matches(matches, now.isoformat())
        for match in matches:
            self.store.save_detail(match['id'], {'official_score':'2-1', 'results':[]})
        app = ConsoleService(self.store)
        with patch.object(self.store, 'connection_scope', side_effect=nullcontext):
            expected = app.reviews()
        with patch.object(self.store, '_open_connection', wraps=self.store._open_connection) as opened:
            actual = app.reviews()
            self.assertEqual(opened.call_count, 1)
        self.assertEqual(len(actual), 20)
        self.assertEqual(actual, expected)
        self.assertEqual(self.store.versions(), [])


if __name__ == '__main__':
    unittest.main()
