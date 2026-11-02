"""Competition-scoped job orchestration; it cannot select a model."""
import threading
from . import model_registry
from .store import now_iso


class TrainingJobs:
    def __init__(self, store):
        self.store = store
        self.threads = {}
        self.guard = threading.Lock()

    def status(self, competition_id):
        model_registry.competition_spec(competition_id)
        job = next((s for s in self.store.statuses() if s["id"] == "training-job:" + competition_id), None)
        thread = self.threads.get(competition_id)
        if job and job["status"] == "running" and not (thread and thread.is_alive()):
            job = {**job, "status": "interrupted", "error": "训练被中断，请重新运行"}
        return job

    def start(self, competition_id, target='full_time'):
        if target not in ('full_time','had'):
            raise ValueError('训练目标无效')
        model_registry.competition_spec(competition_id)
        with self.guard:
            thread = self.threads.get(competition_id)
            if thread and thread.is_alive():
                return self.status(competition_id)
            ready = model_registry.status(competition_id)
            if not ready["ready"]:
                raise ValueError(ready["reason"])
            job = {"competition_id":competition_id, "target":target, "status":"running", "phase":"validation",
                   "completed":0, "total":9, "started_at":now_iso()}
            self.store.status("training-job:" + competition_id, job)
            def work():
                def progress(update):
                    job.update(update)
                    self.store.status("training-job:" + competition_id, job)
                try:
                    report = (model_registry.train(competition_id, progress) if target == 'full_time'
                              else model_registry.train(competition_id, progress, target=target))
                    progress({"status":"completed", "phase":"finished", "run_id":report["run_id"],
                              "model_id":report["model_id"], "finished_at":now_iso()})
                except Exception as exc:
                    progress({"status":"failed", "error":str(exc), "finished_at":now_iso()})
            thread = threading.Thread(target=work, name="football-training:" + competition_id, daemon=True)
            self.threads[competition_id] = thread
            thread.start()
            return dict(job)
