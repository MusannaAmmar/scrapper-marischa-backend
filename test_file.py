import subprocess
import sys
import time
import os
from pathlib import Path


EXCLUDED_MODULES = {
	"scrapping.lintberg_scrapper",
	"scrapping.linkedin_scrapper",
	"scrapping.indeed_scrapper",
}

# Default timeout per scraper process (seconds).
DEFAULT_TIMEOUT_SECONDS = int(os.getenv("SCRAPER_TIMEOUT_SECONDS", "1800"))

# Optional per-scraper timeout overrides.
MODULE_TIMEOUT_OVERRIDES = {
	"scrapping.changed_job_scrapper": int(os.getenv("CHANGED_JOB_TIMEOUT_SECONDS", "3600")),
	"database.upsert_vectors": int(os.getenv("UPSERT_VECTORS_TIMEOUT_SECONDS", "7200")),
	"database.upsert_vectors_two": int(os.getenv("UPSERT_VECTORS_TWO_TIMEOUT_SECONDS", "7200")),
	"analyzer.agent": int(os.getenv("AGENT_MATCHING_TIMEOUT_SECONDS", "10800")),
}

# Stop the workflow immediately when a stage fails unless explicitly overridden.
CONTINUE_ON_STAGE_FAILURE = os.getenv("CONTINUE_ON_STAGE_FAILURE", "true").lower() == "true"


def discover_scraper_modules(scrapping_dir: Path) -> list[str]:
	modules = []

	for file_path in sorted(scrapping_dir.glob("*_scrapper.py")):
		stem = file_path.stem
		if stem.startswith("__"):
			continue

		module_name = f"scrapping.{stem}"
		if module_name in EXCLUDED_MODULES:
			continue

		modules.append(module_name)

	return modules


def run_module(module_name: str, timeout_seconds: int) -> tuple[bool, str]:
	cmd = [sys.executable, "-m", module_name]
	env = os.environ.copy()
	env["PYTHONUNBUFFERED"] = "1"
	env["PYTHONIOENCODING"] = "utf-8"
	env["PYTHONUTF8"] = "1"

	process = subprocess.Popen(
		cmd,
		stdout=subprocess.PIPE,
		stderr=subprocess.STDOUT,
		text=True,
		encoding="utf-8",
		errors="replace",
		bufsize=1,
		env=env,
	)

	start_time = time.time()
	collected_output = []

	try:
		while True:
			line = process.stdout.readline() if process.stdout else ""
			if line:
				line = line.rstrip("\n")
				collected_output.append(line)
				print(f"    {line}")

			if process.poll() is not None:
				break

			if time.time() - start_time > timeout_seconds:
				process.kill()
				collected_output.append(f"Timed out after {timeout_seconds}s")
				return False, "\n".join(collected_output).strip()

			time.sleep(0.1)

		remaining = process.stdout.read() if process.stdout else ""
		if remaining:
			for tail_line in remaining.splitlines():
				collected_output.append(tail_line)
				print(f"    {tail_line}")

		success = process.returncode == 0
		return success, "\n".join(collected_output).strip()
	except Exception as exc:
		try:
			process.kill()
		except Exception:
			pass
		collected_output.append(str(exc))
		return False, "\n".join(collected_output).strip()


def run_all_scrapers_except_excluded() -> dict:
	project_root = Path(__file__).resolve().parent
	scrapping_dir = project_root / "scrapping"

	modules = discover_scraper_modules(scrapping_dir)
	if not modules:
		print("No scraper modules discovered.")
		return {"total": 0, "success": 0, "failed": 0, "failures": []}

	print("=" * 70)
	print("Workflow started")
	print(f"Total scraper modules to run: {len(modules)}")
	print("Excluded modules: scrapping.lintberg_scrapper, scrapping.linkedin_scrapper, scrapping.indeed_scrapper")
	print("=" * 70)

	success_count = 0
	failures = []

	for idx, module_name in enumerate(modules, start=1):
		timeout_seconds = MODULE_TIMEOUT_OVERRIDES.get(module_name, DEFAULT_TIMEOUT_SECONDS)
		print(f"\n[{idx}/{len(modules)}] Running {module_name} (timeout={timeout_seconds}s)")
		ok, output = run_module(module_name, timeout_seconds=timeout_seconds)

		if ok:
			success_count += 1
			print(f"  ✓ Success: {module_name}")
		else:
			failures.append({"module": module_name, "error": output})
			print(f"  ✗ Failed: {module_name}")
			if output:
				preview = output[:600]
				print("  Error preview:")
				print(f"{preview}")

	failed_count = len(failures)

	print("\n" + "=" * 70)
	print("Workflow completed")
	print(f"Successful: {success_count}")
	print(f"Failed: {failed_count}")
	print(f"Total: {len(modules)}")
	print("=" * 70)

	return {
		"total": len(modules),
		"success": success_count,
		"failed": failed_count,
		"failures": failures,
	}


def run_full_workflow() -> dict:
	"""
	Run the full pipeline in this order:
	1) All scrapers
	2) Upsert vectors (phase 1)
	3) Upsert vectors (phase 2)
	4) AI matching agent
	"""
	print("\n" + "=" * 70)
	print("FULL WORKFLOW STARTED")
	print("Stages: scrapers -> upsert_vectors -> upsert_vectors_two -> analyzer.agent")
	print("=" * 70)

	workflow_summary = {
		"scrapers": None,
		"upsert_vectors": {"ok": False, "output": ""},
		"upsert_vectors_two": {"ok": False, "output": ""},
		"agent_matching": {"ok": False, "output": ""},
	}

	# Stage 1: Run all scraper modules
	print("\n[STAGE 1/4] Running all scrapers...")
	scraper_result = run_all_scrapers_except_excluded()
	workflow_summary["scrapers"] = scraper_result

	if scraper_result["failed"] > 0 and not CONTINUE_ON_STAGE_FAILURE:
		print("\n[WORKFLOW STOPPED] One or more scrapers failed.")
		print("Set CONTINUE_ON_STAGE_FAILURE=true to continue anyway.")
		return workflow_summary

	# Stage 2: Upsert vectors (phase 1)
	print("\n[STAGE 2/4] Running database.upsert_vectors...")
	ok, output = run_module(
		"database.upsert_vectors",
		timeout_seconds=MODULE_TIMEOUT_OVERRIDES.get("database.upsert_vectors", DEFAULT_TIMEOUT_SECONDS),
	)
	workflow_summary["upsert_vectors"] = {"ok": ok, "output": output}

	if not ok and not CONTINUE_ON_STAGE_FAILURE:
		print("\n[WORKFLOW STOPPED] database.upsert_vectors failed.")
		print("Set CONTINUE_ON_STAGE_FAILURE=true to continue anyway.")
		return workflow_summary

	# Stage 3: Upsert vectors (phase 2)
	print("\n[STAGE 3/4] Running database.upsert_vectors_two...")
	ok, output = run_module(
		"database.upsert_vectors_two",
		timeout_seconds=MODULE_TIMEOUT_OVERRIDES.get("database.upsert_vectors_two", DEFAULT_TIMEOUT_SECONDS),
	)
	workflow_summary["upsert_vectors_two"] = {"ok": ok, "output": output}

	if not ok and not CONTINUE_ON_STAGE_FAILURE:
		print("\n[WORKFLOW STOPPED] database.upsert_vectors_two failed.")
		print("Set CONTINUE_ON_STAGE_FAILURE=true to continue anyway.")
		return workflow_summary

	# Stage 4: Run AI matching agent
	print("\n[STAGE 4/4] Running analyzer.agent (matching)...")
	ok, output = run_module(
		"analyzer.agent",
		timeout_seconds=MODULE_TIMEOUT_OVERRIDES.get("analyzer.agent", DEFAULT_TIMEOUT_SECONDS),
	)
	workflow_summary["agent_matching"] = {"ok": ok, "output": output}

	print("\n" + "=" * 70)
	print("FULL WORKFLOW COMPLETED")
	print(f"Scrapers failed: {workflow_summary['scrapers']['failed'] if workflow_summary['scrapers'] else 'N/A'}")
	print(f"Upsert vectors: {'OK' if workflow_summary['upsert_vectors']['ok'] else 'FAILED'}")
	print(f"Upsert vectors two: {'OK' if workflow_summary['upsert_vectors_two']['ok'] else 'FAILED'}")
	print(f"Agent matching: {'OK' if workflow_summary['agent_matching']['ok'] else 'FAILED'}")
	print("=" * 70)

	return workflow_summary


if __name__ == "__main__":
	run_full_workflow()



