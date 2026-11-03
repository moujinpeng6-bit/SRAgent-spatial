"""Run a fixed 10x cache batch with durable per-sample checkpoints."""
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

from .local_validation import TenxCacheSession, run_validation_agent
from .tenx_cache import CacheInputError


@contextmanager
def batch_lock(out):
    # The OS releases the lock if the process is interrupted.
    with (out / ".batch.lock").open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("This output already has an active batch") from exc
        yield


def checkpoint(path, report):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    for attempt in range(10):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if os.name != "nt" or attempt == 9:
                raise
            time.sleep(0.05)


async def run_batch(root, sample_ids, out, model, *, timeout, resume=False, local_tools=False):
    root, out = Path(root).resolve(), Path(out).resolve()
    if not root.is_dir() or out.is_relative_to(root):
        raise ValueError("Cache must exist and output must be outside it")
    if (not 1 <= len(sample_ids) <= 20
            or len({item.casefold() for item in sample_ids}) != len(sample_ids)
            or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", item) for item in sample_ids)):
        raise ValueError("Supply 1..20 unique, path-safe sample IDs")
    request = {"cache_root": str(root), "sample_ids": list(sample_ids), "model": model}
    if local_tools:
        request["local_tools"] = True
    out.mkdir(parents=True, exist_ok=True)
    path = out / "batch.json"
    with batch_lock(out):
        if resume:
            report = json.loads(path.read_text(encoding="utf8"))
            if report.get("schema_version") != 1 or report.get("request") != request:
                raise ValueError("Resume requires the original cache, sample list and model")
        else:
            if path.exists():
                raise ValueError("Batch exists. Supply --resume or choose a new output.")
            report = {"schema_version": 1, "request": request, "resumes": 0,
                      "samples": {key: {"status": "pending", "attempts": 0} for key in sample_ids}}
        report["resumes"] += int(resume)
        report["status"] = "running"
        checkpoint(path, report)
        for key in sample_ids:
            row = report["samples"][key]
            if row["status"] == "correspondence_passed":
                continue
            row.update(status="running", attempts=row["attempts"] + 1)
            checkpoint(path, report)
            sample_out = out / key
            try:
                if not sample_out.resolve().is_relative_to(out):
                    raise ValueError("Sample output must stay inside the batch")
                session = TenxCacheSession(root, key, sample_out, model)
                if local_tools:
                    session.catalog()
                    prepared = await session.prepare(key)
                    if prepared["status"] == "prepared":
                        await session.validate(key)
                    result = session.report()
                    result["agent_tool_loop_verified"] = False
                else:
                    result = await run_validation_agent(session, timeout=timeout)
            except (ValueError, KeyError, TypeError, OSError) as exc:
                status = exc.status if isinstance(exc, CacheInputError) else "sample_input_error"
                row.update(status=status, error_type=type(exc).__name__)
            else:
                errors = [event for event in result["events"] if event["event"] == "10x_preparation_error"]
                sample = next(iter(result["samples"]), {})
                status = (errors[-1]["status"] if not result["preparation"] and errors else
                          sample["status"] if sample and (local_tools or result["agent_tool_loop_verified"])
                          else "agent_incomplete")
                row.pop("error_type", None)
                row.update(status=status, preparation=result["preparation"],
                           agent_tool_loop_verified=result["agent_tool_loop_verified"],
                           validation={field: sample[field] for field in ("cells", "checks", "centroids_inside_image",
                                       "feature_type_counts") if field in sample})
            checkpoint(path, report)
        report["status"] = ("completed" if all(row["status"] == "correspondence_passed"
                                               for row in report["samples"].values()) else "completed_with_failures")
        checkpoint(path, report)
        return report
