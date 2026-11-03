"""Regression contracts for independently operated competitions."""
import copy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from worldcup_mvp.console import model_registry as r, training as t
from worldcup_mvp.console.store import Store, fingerprint
from worldcup_mvp.console.training_jobs import TrainingJobs
from tests.test_console_football_model import history, dataset


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patch = patch.object(r,"REGISTRY",self.root/"models")
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.data = dataset(history(30,half=True)+history(30,league="kr.k1",half=True))
        for cid in ("jp.j1","kr.k1"): r.import_competition(self.data,cid)

    def report(self,data,*args,**kwargs):
        cid=data["manifest"]["competition_id"]
        self.assertEqual(kwargs["modes"],("separate",))
        return {"model_schema":t.MODEL_SCHEMA,"dataset_digest":fingerprint(data),
            "selected_by_competition":{cid:{"mode":"separate","half_life":180,"kind":"poisson"}},
            "test":{"count":1},"by_competition":{cid:{"count":1,"log_loss":1,"brier":.6}}}

    def run_model(self,cid):
        with patch.object(t,"evaluate",side_effect=self.report): return r.train(cid)

    def test_training_and_importing_korea_leave_japan_bytes_unchanged(self):
        first=self.run_model("jp.j1")
        r.select_observation("jp.j1",first["run_id"])
        snapshots={p:p.read_bytes() for p in r.directory("jp.j1").rglob('*') if p.is_file()}
        changed=copy.deepcopy(self.data)
        changed["matches"][-1]["away_goals"] += 1
        r.import_competition(changed,"kr.k1")
        self.run_model("kr.k1")
        self.assertEqual(snapshots,{p:p.read_bytes() for p in r.directory("jp.j1").rglob('*') if p.is_file()})
        self.assertFalse((r.directory("kr.k1")/"observation.json").exists())

    def test_import_during_training_preserves_frozen_input_and_prior_selection(self):
        old=self.run_model("jp.j1")
        r.select_observation("jp.j1",old["run_id"])
        original=fingerprint(r.dataset("jp.j1"))
        def evaluate(data,*args,**kwargs):
            changed=copy.deepcopy(self.data);changed["matches"][0]["home_goals"] += 1
            r.import_competition(changed,"jp.j1")
            return self.report(data,*args,**kwargs)
        with patch.object(t,"evaluate",side_effect=evaluate): new=r.train("jp.j1")
        manifest,*_=r.load_run("jp.j1",new["run_id"])
        self.assertEqual(manifest["dataset_digest"],original)
        self.assertNotEqual(fingerprint(r.dataset("jp.j1")),original)
        self.assertEqual(r.status("jp.j1")["model"]["run_id"],old["run_id"])
        self.assertTrue(r.status("jp.j1")["dataset_changed"])

    def test_cross_competition_selection_and_tampering_fail(self):
        run=self.run_model("jp.j1")
        with self.assertRaises(ValueError): r.select_observation("kr.k1",run["run_id"])
        model_path=r.directory("jp.j1")/"runs"/run["run_id"]/"model.json"
        value=r.read(model_path);value["models"]["jp.j1"]["mode"]="pooled"
        t.atomic_json(model_path,value)
        with self.assertRaisesRegex(ValueError,"校验失败"):r.select_observation("jp.j1",run["run_id"])

    def test_competition_scope_period_and_unconfigured_rules_are_enforced(self):
        value=r.dataset("jp.j1");value["matches"].append(self.data["matches"][-1])
        with self.assertRaisesRegex(ValueError,"其他赛事"):r.validate_scope(value,"jp.j1")
        value=copy.deepcopy(self.data);value["matches"][0]["period"]="extra_time"
        with self.assertRaisesRegex(ValueError,"90分钟"):r.import_competition(value,"jp.j1")
        value=copy.deepcopy(self.data);value["matches"][0]["stage"]="cup_final"
        with self.assertRaisesRegex(ValueError,"阶段未登记"):r.import_competition(value,"jp.j1")
        with self.assertRaises(ValueError):r.import_competition(self.data,"uefa.ucl")
        with self.assertRaises(ValueError):r.select_observation("jp.j1","../kr.k1")

    def test_bad_korean_record_does_not_block_japanese_import(self):
        value=copy.deepcopy(self.data);value["matches"][-1]["period"]="shootout"
        self.assertEqual(r.import_competition(value,"jp.j1")["rows"],30)
        with self.assertRaises(ValueError):r.import_competition(value,"kr.k1")

    def test_jobs_are_independent_and_no_scope_is_rejected(self):
        jobs=TrainingJobs(Store(self.root/"test.sqlite"))
        started={c:threading.Event() for c in ("jp.j1","kr.k1")};release=threading.Event()
        def train(cid,progress):
            started[cid].set();release.wait(5)
            if cid == "kr.k1": raise ValueError("Korean source failed")
            return {"model_id":"japan", "run_id":"japan"}
        with patch.object(r,"status",return_value={"ready":True}),patch.object(r,"train",side_effect=train) as mocked:
            for cid in started: jobs.start(cid)
            self.assertTrue(all(e.wait(1) for e in started.values()))
            jobs.start("jp.j1")
            self.assertEqual(mocked.call_count,2)
            release.set()
            for thread in jobs.threads.values():thread.join(3)
        self.assertEqual(jobs.status("jp.j1")["status"],"completed")
        self.assertEqual(jobs.status("kr.k1")["status"],"failed")
        with self.assertRaises(ValueError): jobs.start(None)

    def test_process_lock_is_per_competition(self):
        with r.training_lock("jp.j1"):
            with r.training_lock("kr.k1"): pass
            with self.assertRaises(ValueError):
                with r.training_lock("jp.j1"): pass


if __name__ == '__main__':unittest.main()
