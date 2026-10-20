"""Isolated AI Intelligence foundation for the Serveone dashboard.

Phase 1 intentionally keeps AI work separate from purchasing routes. Tasks are
persisted before execution, provider secrets never enter the database, and the
pricing engine is deterministic.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import hashlib
import json
import os
import queue
import subprocess
import threading
import time
import uuid

import requests
from flask import Blueprint, jsonify, request
from sqlalchemy.exc import IntegrityError


TASK_STATES = {
    "CREATED", "QUEUED", "RUNNING", "COMPLETED", "RETRY_WAIT", "FAILED", "CANCELLED"
}
PROVIDER_TYPES = {"openrouter", "gemini", "ollama", "compatible"}
DEFAULT_SETTINGS = {
    "operating_mode": "FREE_ONLY",
    "max_retries": 2,
    "monthly_budget_usd": 0,
}

AIProviderConfig = None
AITask = None
AIResearchResult = None
AIUsageLog = None
AISetting = None
AIAuditLog = None

_APP = None
_DB = None
_ADMIN_CHECK = None
_TASK_QUEUE = queue.Queue(maxsize=500)
_WORKER = None
_WORKER_LOCK = threading.Lock()
_HERMES_CACHE = {"at": 0.0, "value": None}
_HERMES_INSTALLER_URL = "https://hermes-agent.nousresearch.com/install.ps1"
_HERMES_LOCAL_URL = "http://127.0.0.1:9119"
_HERMES_ACTION_LOCK = threading.Lock()
_HERMES_ACTION = {
    "status": "IDLE", "action": "", "message": "", "error": "",
    "started_at": None, "completed_at": None,
}
_HERMES_ACTION_PROCESS = None
_HERMES_SERVER_PROCESS = None


def _now():
    return datetime.utcnow()


def _iso(value):
    return value.isoformat(timespec="seconds") + "Z" if value else None


def _json_load(value, fallback):
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


def _json_dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _trim(value, limit=255):
    return str(value or "").strip()[:limit]


def _secret_root():
    base = os.environ.get("SERVEONE_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
    return Path(base) / "ai_secrets"


def _hermes_runtime_root():
    base = os.environ.get("SERVEONE_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
    path = Path(base) / "ai_hermes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _hermes_home():
    configured = _trim(os.environ.get("HERMES_HOME"), 1000)
    if configured:
        return Path(configured).expanduser()
    local = os.environ.get("LOCALAPPDATA")
    return Path(local) / "hermes" if local else Path.home() / "AppData" / "Local" / "hermes"


def _hermes_executable():
    bin_dir = _hermes_home() / "bin"
    for name in ("hermes.exe", "hermes.cmd", "hermes.ps1", "hermes"):
        candidate = bin_dir / name
        if candidate.is_file():
            return candidate
    return None


def _powershell_executable():
    system_root = Path(os.environ.get("SystemRoot", "C:/Windows"))
    candidate = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(candidate if candidate.is_file() else "powershell.exe")


def _write_hermes_wrapper(action, executable=None):
    runtime = _hermes_runtime_root()
    wrapper = runtime / f"{action.lower()}.ps1"
    if action in {"INSTALL", "REINSTALL"}:
        log_path = runtime / "install.log"
        source = f"""$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$installer = Join-Path $env:TEMP 'serveone-hermes-official-install.ps1'
function Get-FileHash {{
  param([string]$Path, [string]$Algorithm = 'SHA256')
  $hasher = [System.Security.Cryptography.HashAlgorithm]::Create($Algorithm)
  if (-not $hasher) {{ throw "Unsupported hash algorithm: $Algorithm" }}
  $stream = [System.IO.File]::OpenRead($Path)
  try {{
    $hash = [System.BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '')
    [PSCustomObject]@{{ Hash = $hash }}
  }} finally {{
    $stream.Dispose()
    $hasher.Dispose()
  }}
}}
Start-Transcript -Path '{str(log_path).replace("'", "''")}' -Append | Out-Null
try {{
  Write-Host 'Downloading official Hermes installer from Nous Research...'
  Invoke-WebRequest -Uri '{_HERMES_INSTALLER_URL}' -OutFile $installer -UseBasicParsing
  & $installer -NonInteractive -Verbose
  if ($LASTEXITCODE -ne 0) {{ exit $LASTEXITCODE }}
  Write-Host 'Hermes installation completed.'
}} catch {{
  Write-Error $_
  exit 1
}} finally {{
  Stop-Transcript | Out-Null
  Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
}}
"""
    elif action == "CONFIGURE":
        quoted = str(executable).replace("'", "''")
        source = f"""$ErrorActionPreference = 'Stop'
try {{
  & '{quoted}' setup
  if ($LASTEXITCODE -ne 0) {{ exit $LASTEXITCODE }}
}} catch {{
  Write-Error $_
  exit 1
}}
"""
    else:
        raise ValueError("Unsupported Hermes wrapper action")
    wrapper.write_text(source, encoding="utf-8")
    return wrapper


def _set_hermes_action(status, action=None, message=None, error=None, completed=False):
    with _HERMES_ACTION_LOCK:
        if action is not None:
            _HERMES_ACTION["action"] = action
        _HERMES_ACTION["status"] = status
        if message is not None:
            _HERMES_ACTION["message"] = message
        if error is not None:
            _HERMES_ACTION["error"] = error
        if status in {"INSTALLING", "REINSTALLING", "CONFIGURING", "STARTING", "STOPPING"}:
            _HERMES_ACTION["started_at"] = _iso(_now())
            _HERMES_ACTION["completed_at"] = None
        elif completed:
            _HERMES_ACTION["completed_at"] = _iso(_now())


def _run_visible_wrapper(action, wrapper):
    global _HERMES_ACTION_PROCESS
    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    process = subprocess.Popen([
        _powershell_executable(), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(wrapper),
    ], creationflags=creationflags)
    _HERMES_ACTION_PROCESS = process
    return process.wait()


def _run_hermes_hidden(arguments, wait=True):
    executable = _hermes_executable()
    if not executable:
        raise RuntimeError("Hermes is not installed.")
    command = [
        _powershell_executable(), "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-Command", "& '" + str(executable).replace("'", "''") + "' " +
        " ".join("'" + str(value).replace("'", "''") + "'" for value in arguments),
    ]
    log = (_hermes_runtime_root() / "server.log").open("a", encoding="utf-8")
    process = subprocess.Popen(
        command, stdout=log, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if not wait:
        return process
    try:
        return process.wait(timeout=45)
    finally:
        log.close()


def _wait_for_hermes(available, timeout=45):
    global _HERMES_CACHE
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _HERMES_CACHE = {"at": 0.0, "value": None}
        current = _hermes_health()["status"] == "AVAILABLE"
        if current is available:
            return True
        time.sleep(1)
    return False


def _hermes_action_worker(action):
    global _HERMES_ACTION_PROCESS, _HERMES_SERVER_PROCESS
    try:
        if action in {"INSTALL", "REINSTALL"}:
            wrapper = _write_hermes_wrapper(action)
            code = _run_visible_wrapper(action, wrapper)
            if code or not _hermes_executable():
                raise RuntimeError(f"Official Hermes installer exited with code {code}.")
            os.environ.setdefault("HERMES_BASE_URL", _HERMES_LOCAL_URL)
            message = "Hermes installed. Run configuration before starting research tasks."
        elif action == "CONFIGURE":
            code = _run_visible_wrapper(action, _write_hermes_wrapper(action, _hermes_executable()))
            if code:
                raise RuntimeError(f"Hermes setup exited with code {code}.")
            message = "Hermes configuration terminal completed."
        elif action == "START":
            os.environ["HERMES_BASE_URL"] = _HERMES_LOCAL_URL
            _HERMES_SERVER_PROCESS = _run_hermes_hidden(
                ["serve", "--host", "127.0.0.1", "--port", "9119"], wait=False)
            if not _wait_for_hermes(True):
                code = _HERMES_SERVER_PROCESS.poll()
                raise RuntimeError("Hermes did not become available." +
                                   (f" Process exited with code {code}." if code is not None else ""))
            message = "Hermes local service started."
        elif action == "STOP":
            code = _run_hermes_hidden(["dashboard", "--stop"])
            if code:
                raise RuntimeError(f"Hermes stop command exited with code {code}.")
            if _HERMES_SERVER_PROCESS and _HERMES_SERVER_PROCESS.poll() is None:
                _HERMES_SERVER_PROCESS.terminate()
            _wait_for_hermes(False, timeout=15)
            message = "Hermes local service stopped."
        else:
            raise RuntimeError("Unsupported Hermes action.")
        _set_hermes_action("SUCCESS", message=message, error="", completed=True)
    except Exception as exc:
        _set_hermes_action("FAILED", message="Hermes action failed.", error=str(exc)[:1000], completed=True)
    finally:
        _HERMES_ACTION_PROCESS = None


def _start_hermes_action(action):
    action = _trim(action, 20).upper()
    active = {"INSTALLING", "REINSTALLING", "CONFIGURING", "STARTING", "STOPPING"}
    with _HERMES_ACTION_LOCK:
        if _HERMES_ACTION["status"] in active:
            raise RuntimeError("Another Hermes action is still running.")
    installed = bool(_hermes_executable())
    running = _hermes_health()["status"] == "AVAILABLE"
    if action in {"REINSTALL", "CONFIGURE", "START", "STOP"} and not installed:
        raise ValueError("Hermes is not installed.")
    if action == "INSTALL" and installed:
        raise ValueError("Hermes is already installed. Use Reinstall instead.")
    if action == "REINSTALL" and running:
        raise ValueError("Stop Hermes before reinstalling it.")
    if action == "START" and running:
        raise ValueError("Hermes is already running.")
    if action == "STOP" and not running:
        raise ValueError("Hermes is already stopped.")
    status = {"INSTALL": "INSTALLING", "REINSTALL": "REINSTALLING", "CONFIGURE": "CONFIGURING",
              "START": "STARTING", "STOP": "STOPPING"}[action]
    with _HERMES_ACTION_LOCK:
        if _HERMES_ACTION["status"] in active:
            raise RuntimeError("Another Hermes action is still running.")
        _HERMES_ACTION.update({
            "status": status, "action": action, "message": f"{action.title()} started.",
            "error": "", "started_at": _iso(_now()), "completed_at": None,
        })
    threading.Thread(target=_hermes_action_worker, args=(action,),
                     name=f"serveone-hermes-{action.lower()}", daemon=True).start()
    return _hermes_installer_payload()


def _tail_text(path, max_lines=40, max_chars=12000):
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:]
        return "\n".join(lines)[-max_chars:]
    except OSError:
        return ""


def _hermes_installer_payload():
    installed = bool(_hermes_executable())
    health = _hermes_health()
    with _HERMES_ACTION_LOCK:
        action = dict(_HERMES_ACTION)
    return {
        "installed": installed,
        "running": health["status"] == "AVAILABLE",
        "service_status": health["status"],
        "base_url": health.get("base_url") or _HERMES_LOCAL_URL,
        "install_location": str(_hermes_home()) if installed else "",
        "action": action,
        "install_log": _tail_text(_hermes_runtime_root() / "install.log"),
        "official_installer": _HERMES_INSTALLER_URL,
    }


def _save_secret(secret_ref, value):
    value = str(value or "")
    if not value:
        return secret_ref
    if os.name != "nt":
        raise RuntimeError("Provider secrets require Windows DPAPI in this build")
    from desktop_secrets import _crypt
    ref = secret_ref or (uuid.uuid4().hex + ".bin")
    path = _secret_root() / Path(ref).name
    path.parent.mkdir(parents=True, exist_ok=True)
    encrypted = _crypt(value.encode("utf-8"), purpose=b"ServeoneDashboardDesktop.AIProvider.v1")
    temp = path.with_suffix(path.suffix + ".tmp")
    try:
        temp.write_bytes(encrypted)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return path.name


def _load_secret(secret_ref):
    if not secret_ref:
        return ""
    path = _secret_root() / Path(secret_ref).name
    if not path.is_file():
        return ""
    from desktop_secrets import _crypt
    return _crypt(path.read_bytes(), decrypt=True,
                  purpose=b"ServeoneDashboardDesktop.AIProvider.v1").decode("utf-8")


def _delete_secret(secret_ref):
    if secret_ref:
        (_secret_root() / Path(secret_ref).name).unlink(missing_ok=True)


def _admin_required():
    if _ADMIN_CHECK and _ADMIN_CHECK():
        return None
    return jsonify({"error": "Administrator access is required."}), 403


def _audit(action, entity_type, entity_id=None, metadata=None):
    row = AIAuditLog(
        actor=_trim(request.headers.get("X-Serveone-Device-Name") or request.remote_addr or "local", 120),
        action=_trim(action, 100), entity_type=_trim(entity_type, 80),
        entity_id=_trim(entity_id, 80) or None,
        metadata_json=_json_dump(metadata or {}), created_at=_now(),
    )
    _DB.session.add(row)


def _setting(key):
    row = AISetting.query.filter_by(setting_key=key).first()
    if row is None:
        return DEFAULT_SETTINGS[key]
    return _json_load(row.value_json, DEFAULT_SETTINGS[key])


def _settings_payload():
    return {key: _setting(key) for key in DEFAULT_SETTINGS}


def _provider_payload(row):
    return {
        "id": row.id, "name": row.name, "provider": row.provider,
        "connection_type": row.connection_type, "model_id": row.model_id or "",
        "endpoint": row.endpoint or "", "enabled": bool(row.enabled),
        "is_default": bool(row.is_default), "is_paid": bool(row.is_paid),
        "fallback_priority": row.fallback_priority,
        "connection_status": row.connection_status or "NOT_TESTED",
        "last_error": row.last_error or "", "last_tested_at": _iso(row.last_tested_at),
        "capabilities": _json_load(row.capability_json, {}),
        "usage_limit": _json_load(row.usage_limit_json, {}),
        "has_api_key": bool(row.secret_ref),
        "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at),
    }


def _task_payload(row, include_result=False):
    data = {
        "id": row.id, "task_type": row.task_type, "entity_type": row.entity_type or "",
        "entity_id": row.entity_id or "", "trigger_type": row.trigger_type,
        "status": row.status, "progress_stage": row.progress_stage or "",
        "provider_id": row.provider_id, "model_id": row.model_id or "",
        "attempt_count": row.attempt_count, "max_attempts": row.max_attempts,
        "error_code": row.error_code or "", "error_message": row.error_message or "",
        "error_stage": row.error_stage or "", "cancel_requested": bool(row.cancel_requested),
        "created_at": _iso(row.created_at), "started_at": _iso(row.started_at),
        "completed_at": _iso(row.completed_at),
    }
    if include_result:
        data["request"] = _json_load(row.payload_json, {})
        data["result"] = _json_load(row.result_json, None)
    return data


def calculate_pricing(payload):
    """Return transparent pricing scenarios without demand or LLM assumptions."""
    def number(name, required=True, minimum=None):
        raw = payload.get(name)
        if raw in (None, "") and not required:
            return None
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a valid number") from exc
        if not value.is_finite() or (minimum is not None and value < Decimal(str(minimum))):
            raise ValueError(f"{name} must be at least {minimum}")
        return value

    cost = number("purchase_cost", minimum=0)
    sales = number("sales_price", minimum=0)
    quantity = number("quantity", minimum=0)
    target_margin = number("target_gross_margin", minimum=0)
    minimum_margin = number("minimum_margin", required=False, minimum=0)
    market_purchase = number("market_purchase_benchmark", required=False, minimum=0)
    market_sales = number("market_selling_benchmark", required=False, minimum=0)
    if sales <= 0:
        raise ValueError("sales_price must be greater than 0")
    if target_margin >= 1:
        raise ValueError("target_gross_margin must be less than 1")
    if minimum_margin is not None and minimum_margin >= 1:
        raise ValueError("minimum_margin must be less than 1")

    gross_profit = sales - cost
    gross_margin = gross_profit / sales
    markup = (gross_profit / cost) if cost else None
    target_sales = cost / (Decimal("1") - target_margin)
    target_profit = (target_sales - cost) * quantity
    current_profit = gross_profit * quantity

    result = {
        "classification": "CALCULATED",
        "currency": _trim(payload.get("currency") or "IDR", 10).upper(),
        "inputs": {
            "purchase_cost": float(cost), "sales_price": float(sales),
            "quantity": float(quantity), "target_gross_margin": float(target_margin),
            "minimum_margin": float(minimum_margin) if minimum_margin is not None else None,
            "market_purchase_benchmark": float(market_purchase) if market_purchase is not None else None,
            "market_selling_benchmark": float(market_sales) if market_sales is not None else None,
        },
        "metrics": {
            "gross_profit_per_unit": float(gross_profit),
            "gross_margin": float(gross_margin),
            "markup": float(markup) if markup is not None else None,
            "target_selling_price": float(target_sales),
            "current_total_gross_profit": float(current_profit),
            "target_total_gross_profit": float(target_profit),
            "potential_gross_profit_improvement": float(target_profit - current_profit),
            "purchase_gap_vs_market": float(cost - market_purchase) if market_purchase is not None else None,
            "sales_gap_vs_market": float(sales - market_sales) if market_sales is not None else None,
        },
        "scenarios": [
            {"name": "Current", "selling_price": float(sales), "gross_margin": float(gross_margin),
             "gross_profit_total": float(current_profit)},
            {"name": "Target margin", "selling_price": float(target_sales),
             "gross_margin": float(target_margin), "gross_profit_total": float(target_profit)},
        ],
        "assumptions": [
            "Quantity remains unchanged in every scenario.",
            "Taxes, shipping, discounts, and demand elasticity are excluded unless already included in input prices.",
            "Output is a deterministic scenario, not automatic price optimization or approval.",
        ],
    }
    if minimum_margin is not None:
        result["metrics"]["minimum_margin_met"] = gross_margin >= minimum_margin
    return result


def _complete_task(task, result):
    if task.cancel_requested:
        task.status = "CANCELLED"
        task.progress_stage = "Cancelled"
        task.completed_at = _now()
        return
    task.status = "COMPLETED"
    task.progress_stage = "Completed"
    task.result_json = _json_dump(result)
    task.completed_at = _now()
    _DB.session.add(AIResearchResult(
        task_id=task.id, result_type=task.task_type, result_json=task.result_json,
        classification=result.get("classification", "VALIDATED"),
        verification_status="VALIDATED", analyzed_at=_now(),
    ))


def _run_task(task):
    if task.task_type == "PRICING_ANALYSIS":
        task.progress_stage = "Calculating pricing scenarios"
        _DB.session.commit()
        return calculate_pricing(_json_load(task.payload_json, {}))
    raise RuntimeError("Unsupported task type")


def _worker_loop():
    while True:
        task_id = _TASK_QUEUE.get()
        try:
            with _APP.app_context():
                task = _DB.session.get(AITask, task_id)
                if task is None or task.status not in {"QUEUED", "RETRY_WAIT"}:
                    continue
                if task.cancel_requested:
                    task.status = "CANCELLED"
                    task.progress_stage = "Cancelled"
                    task.completed_at = _now()
                    _DB.session.commit()
                    continue
                task.status = "RUNNING"
                task.progress_stage = "Starting"
                task.started_at = task.started_at or _now()
                task.attempt_count += 1
                task.error_code = task.error_message = task.error_stage = None
                _DB.session.commit()
                try:
                    result = _run_task(task)
                    _complete_task(task, result)
                    _DB.session.commit()
                except ValueError as exc:
                    task.status = "FAILED"
                    task.progress_stage = "Validation failed"
                    task.error_code = "INPUT_INVALID"
                    task.error_message = str(exc)[:1000]
                    task.error_stage = "VALIDATION"
                    task.completed_at = _now()
                    _DB.session.commit()
                except Exception as exc:
                    task.status = "FAILED"
                    task.progress_stage = "Failed"
                    task.error_code = "TASK_EXECUTION_FAILED"
                    task.error_message = str(exc)[:1000]
                    task.error_stage = "EXECUTION"
                    task.completed_at = _now()
                    _DB.session.commit()
        except Exception as exc:
            try:
                _DB.session.rollback()
                print(f"[ai-intelligence] worker error: {exc}", flush=True)
            except Exception:
                pass
        finally:
            _TASK_QUEUE.task_done()


def _ensure_worker():
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER and _WORKER.is_alive():
            return
        _WORKER = threading.Thread(target=_worker_loop, name="ai-intelligence-worker", daemon=True)
        _WORKER.start()


def _enqueue(task_id):
    _ensure_worker()
    try:
        _TASK_QUEUE.put_nowait(task_id)
    except queue.Full as exc:
        raise RuntimeError("AI task queue is full") from exc


def _provider_test_values(provider, endpoint, model_id, api_key):
    started = time.perf_counter()
    headers = {"Accept": "application/json"}
    provider = provider.lower()
    if provider == "openrouter":
        url = (endpoint or "https://openrouter.ai/api/v1").rstrip("/") + "/models"
        if not api_key:
            raise ValueError("API key is required for OpenRouter")
        headers["Authorization"] = "Bearer " + api_key
    elif provider == "gemini":
        if not api_key:
            raise ValueError("API key is required for Gemini")
        base = (endpoint or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        url = base + "/models?key=" + api_key
    elif provider == "ollama":
        url = (endpoint or "http://127.0.0.1:11434").rstrip("/") + "/api/tags"
    elif provider == "compatible":
        if not endpoint:
            raise ValueError("Endpoint is required for an OpenAI-compatible provider")
        url = endpoint.rstrip("/") + "/models"
        if api_key:
            headers["Authorization"] = "Bearer " + api_key
    else:
        raise ValueError("Unsupported provider type")
    response = requests.get(url, headers=headers, timeout=(3, 8))
    if response.status_code >= 400:
        raise RuntimeError(f"Provider returned HTTP {response.status_code}")
    body = response.json()
    raw_models = body.get("models", []) if provider == "ollama" else body.get("data", body.get("models", []))
    models = []
    for item in raw_models[:200] if isinstance(raw_models, list) else []:
        if isinstance(item, str):
            models.append(item)
        elif isinstance(item, dict):
            value = item.get("id") or item.get("name")
            if value:
                models.append(str(value).replace("models/", "", 1))
    return {
        "ok": True, "latency_ms": round((time.perf_counter() - started) * 1000),
        "models": models, "model_available": (not model_id or model_id in models),
        "capabilities": {"model_listing": True, "configured_model": model_id or ""},
    }


def _hermes_health():
    base = _trim(os.environ.get("HERMES_BASE_URL"), 500).rstrip("/")
    if not base and _hermes_executable():
        base = _HERMES_LOCAL_URL
    if not base:
        return {"status": "UNCONFIGURED", "base_url": "", "message": "HERMES_BASE_URL is not configured."}
    now = time.monotonic()
    if _HERMES_CACHE["value"] is not None and now - _HERMES_CACHE["at"] < 15:
        return _HERMES_CACHE["value"]
    started = time.perf_counter()
    try:
        response = requests.get(base + "/health", timeout=(2, 3))
        endpoint = "/health"
        if response.status_code == 404:
            response = requests.get(base + "/api/status", timeout=(2, 3))
            endpoint = "/api/status"
        value = {
            "status": "AVAILABLE" if response.ok else "UNAVAILABLE", "base_url": base,
            "latency_ms": round((time.perf_counter() - started) * 1000), "endpoint": endpoint,
            "message": "Connected." if response.ok else f"Health check returned HTTP {response.status_code}.",
        }
    except requests.RequestException as exc:
        status = "STOPPED" if _hermes_executable() and base == _HERMES_LOCAL_URL else "UNAVAILABLE"
        value = {"status": status, "base_url": base,
                 "message": "Installed, but local service is stopped." if status == "STOPPED" else str(exc)[:300]}
    _HERMES_CACHE.update(at=now, value=value)
    return value


def init_ai_intelligence(app, db, admin_check=None):
    global _APP, _DB, _ADMIN_CHECK
    global AIProviderConfig, AITask, AIResearchResult, AIUsageLog, AISetting, AIAuditLog
    if app.extensions.get("serveone_ai_intelligence"):
        return app.extensions["serveone_ai_intelligence"]
    _APP, _DB, _ADMIN_CHECK = app, db, admin_check

    class Provider(db.Model):
        __tablename__ = "ai_provider_configs"
        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(120), nullable=False)
        provider = db.Column(db.String(40), nullable=False, index=True)
        connection_type = db.Column(db.String(40), nullable=False, default="api")
        model_id = db.Column(db.String(200))
        endpoint = db.Column(db.String(700))
        enabled = db.Column(db.Boolean, nullable=False, default=True)
        is_default = db.Column(db.Boolean, nullable=False, default=False, index=True)
        is_paid = db.Column(db.Boolean, nullable=False, default=False)
        fallback_priority = db.Column(db.Integer, nullable=False, default=100)
        connection_status = db.Column(db.String(40), nullable=False, default="NOT_TESTED")
        capability_json = db.Column(db.Text, nullable=False, default="{}")
        usage_limit_json = db.Column(db.Text, nullable=False, default="{}")
        secret_ref = db.Column(db.String(180))
        last_error = db.Column(db.Text)
        last_tested_at = db.Column(db.DateTime)
        created_at = db.Column(db.DateTime, default=_now)
        updated_at = db.Column(db.DateTime, default=_now, onupdate=_now)

    class Task(db.Model):
        __tablename__ = "ai_tasks"
        id = db.Column(db.String(36), primary_key=True)
        task_type = db.Column(db.String(80), nullable=False, index=True)
        entity_type = db.Column(db.String(80))
        entity_id = db.Column(db.String(180), index=True)
        requested_by = db.Column(db.String(120))
        trigger_type = db.Column(db.String(40), nullable=False, default="MANUAL")
        status = db.Column(db.String(30), nullable=False, default="CREATED", index=True)
        progress_stage = db.Column(db.String(180))
        provider_id = db.Column(db.Integer, db.ForeignKey("ai_provider_configs.id"))
        model_id = db.Column(db.String(200))
        attempt_count = db.Column(db.Integer, nullable=False, default=0)
        max_attempts = db.Column(db.Integer, nullable=False, default=2)
        idempotency_key = db.Column(db.String(64), nullable=False, unique=True, index=True)
        payload_json = db.Column(db.Text, nullable=False, default="{}")
        result_json = db.Column(db.Text)
        error_code = db.Column(db.String(80))
        error_message = db.Column(db.Text)
        error_stage = db.Column(db.String(80))
        cancel_requested = db.Column(db.Boolean, nullable=False, default=False)
        created_at = db.Column(db.DateTime, default=_now, index=True)
        started_at = db.Column(db.DateTime)
        completed_at = db.Column(db.DateTime)

    class Result(db.Model):
        __tablename__ = "ai_research_results"
        id = db.Column(db.Integer, primary_key=True)
        task_id = db.Column(db.String(36), db.ForeignKey("ai_tasks.id"), nullable=False, index=True)
        result_type = db.Column(db.String(80), nullable=False)
        result_json = db.Column(db.Text, nullable=False)
        classification = db.Column(db.String(80), nullable=False, default="VALIDATED")
        verification_status = db.Column(db.String(80), nullable=False, default="VALIDATED")
        confidence = db.Column(db.Float)
        analyzed_at = db.Column(db.DateTime, default=_now)
        valid_until = db.Column(db.DateTime)

    class Usage(db.Model):
        __tablename__ = "ai_usage_logs"
        id = db.Column(db.Integer, primary_key=True)
        task_id = db.Column(db.String(36), db.ForeignKey("ai_tasks.id"), index=True)
        provider_id = db.Column(db.Integer, db.ForeignKey("ai_provider_configs.id"), index=True)
        model_id = db.Column(db.String(200))
        requests = db.Column(db.Integer, nullable=False, default=0)
        input_tokens = db.Column(db.Integer, nullable=False, default=0)
        output_tokens = db.Column(db.Integer, nullable=False, default=0)
        estimated_cost = db.Column(db.Float, nullable=False, default=0)
        duration_ms = db.Column(db.Integer, nullable=False, default=0)
        status = db.Column(db.String(40), nullable=False)
        created_at = db.Column(db.DateTime, default=_now, index=True)

    class Setting(db.Model):
        __tablename__ = "ai_settings"
        id = db.Column(db.Integer, primary_key=True)
        setting_key = db.Column(db.String(100), nullable=False, unique=True, index=True)
        value_json = db.Column(db.Text, nullable=False)
        updated_at = db.Column(db.DateTime, default=_now, onupdate=_now)

    class Audit(db.Model):
        __tablename__ = "ai_audit_logs"
        id = db.Column(db.Integer, primary_key=True)
        actor = db.Column(db.String(120))
        action = db.Column(db.String(100), nullable=False, index=True)
        entity_type = db.Column(db.String(80), nullable=False)
        entity_id = db.Column(db.String(80))
        metadata_json = db.Column(db.Text, nullable=False, default="{}")
        created_at = db.Column(db.DateTime, default=_now, index=True)

    AIProviderConfig, AITask, AIResearchResult = Provider, Task, Result
    AIUsageLog, AISetting, AIAuditLog = Usage, Setting, Audit

    bp = Blueprint("ai_intelligence", __name__)

    @bp.get("/api/ai/health")
    def health():
        counts = dict(_DB.session.query(AITask.status, db.func.count(AITask.id)).group_by(AITask.status).all())
        return jsonify({
            "status": "AVAILABLE", "worker": "RUNNING" if _WORKER and _WORKER.is_alive() else "STARTING",
            "queue_depth": _TASK_QUEUE.qsize(), "hermes": _hermes_health(),
            "providers": {"configured": AIProviderConfig.query.count(),
                          "connected": AIProviderConfig.query.filter_by(connection_status="CONNECTED").count()},
            "tasks": {"total": sum(counts.values()), **{key.lower(): counts.get(key, 0) for key in TASK_STATES}},
            "results": AIResearchResult.query.count(),
        })

    @bp.get("/api/ai/settings")
    def get_settings():
        return jsonify(_settings_payload())

    @bp.get("/api/ai/hermes/installer")
    def hermes_installer_status():
        return jsonify(_hermes_installer_payload())

    @bp.post("/api/ai/hermes/<action>")
    def hermes_installer_action(action):
        denied = _admin_required()
        if denied:
            return denied
        action = _trim(action, 20).upper()
        if action not in {"INSTALL", "REINSTALL", "CONFIGURE", "START", "STOP"}:
            return jsonify({"error": "Unsupported Hermes action."}), 404
        body = request.get_json(silent=True) or {}
        expected = {"INSTALL": "INSTALL_HERMES", "REINSTALL": "REINSTALL_HERMES"}.get(action)
        if expected and body.get("confirm") != expected:
            return jsonify({"error": f"Confirmation value {expected} is required."}), 400
        try:
            payload = _start_hermes_action(action)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 409
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        _audit(f"HERMES_{action}_STARTED", "hermes")
        _DB.session.commit()
        return jsonify(payload), 202

    @bp.patch("/api/ai/settings")
    def patch_settings():
        denied = _admin_required()
        if denied:
            return denied
        body = request.get_json(silent=True) or {}
        allowed = {}
        if "operating_mode" in body:
            mode = _trim(body["operating_mode"], 20).upper()
            if mode not in {"FREE_ONLY", "ALLOW_PAID"}:
                return jsonify({"error": "operating_mode must be FREE_ONLY or ALLOW_PAID"}), 400
            allowed["operating_mode"] = mode
        if "max_retries" in body:
            retries = body["max_retries"]
            if type(retries) is not int or not 0 <= retries <= 5:
                return jsonify({"error": "max_retries must be an integer from 0 to 5"}), 400
            allowed["max_retries"] = retries
        if "monthly_budget_usd" in body:
            try:
                budget = float(body["monthly_budget_usd"])
            except (TypeError, ValueError):
                return jsonify({"error": "monthly_budget_usd must be a number"}), 400
            if budget < 0 or budget > 1000000:
                return jsonify({"error": "monthly_budget_usd is outside the allowed range"}), 400
            allowed["monthly_budget_usd"] = budget
        for key, value in allowed.items():
            row = AISetting.query.filter_by(setting_key=key).first() or AISetting(setting_key=key)
            row.value_json = _json_dump(value)
            _DB.session.add(row)
        _audit("SETTINGS_UPDATED", "settings", metadata={"keys": sorted(allowed)})
        _DB.session.commit()
        return jsonify(_settings_payload())

    @bp.get("/api/ai/providers")
    def providers():
        rows = AIProviderConfig.query.order_by(AIProviderConfig.is_default.desc(), AIProviderConfig.fallback_priority).all()
        return jsonify({"providers": [_provider_payload(row) for row in rows]})

    def apply_provider(row, body, creating=False):
        provider = _trim(body.get("provider", row.provider if not creating else ""), 40).lower()
        if provider not in PROVIDER_TYPES:
            raise ValueError("provider must be openrouter, gemini, ollama, or compatible")
        name = _trim(body.get("name", row.name if not creating else ""), 120)
        if not name:
            raise ValueError("name is required")
        row.provider = provider
        row.name = name
        row.connection_type = _trim(body.get("connection_type", row.connection_type or "api"), 40) or "api"
        row.model_id = _trim(body.get("model_id", row.model_id), 200) or None
        row.endpoint = _trim(body.get("endpoint", row.endpoint), 700) or None
        row.enabled = bool(body.get("enabled", row.enabled if not creating else True))
        row.is_paid = bool(body.get("is_paid", row.is_paid if not creating else False))
        row.fallback_priority = int(body.get("fallback_priority", row.fallback_priority or 100))
        if not 0 <= row.fallback_priority <= 9999:
            raise ValueError("fallback_priority must be from 0 to 9999")
        desired_default = bool(body.get("is_default", row.is_default if not creating else False))
        if desired_default and row.is_paid and _setting("operating_mode") == "FREE_ONLY":
            raise ValueError("A paid provider cannot be default while operating mode is FREE_ONLY")
        row.is_default = desired_default
        key = body.get("api_key")
        if key:
            row.secret_ref = _save_secret(row.secret_ref, key)
            row.connection_status = "NOT_TESTED"
            row.last_error = None
        row.updated_at = _now()

    @bp.post("/api/ai/providers")
    def create_provider():
        denied = _admin_required()
        if denied:
            return denied
        body = request.get_json(silent=True) or {}
        row = AIProviderConfig()
        try:
            apply_provider(row, body, creating=True)
        except (ValueError, RuntimeError) as exc:
            return jsonify({"error": str(exc)}), 400
        if row.is_default:
            AIProviderConfig.query.update({"is_default": False})
        _DB.session.add(row)
        _DB.session.flush()
        _audit("PROVIDER_CREATED", "provider", row.id, {"provider": row.provider})
        _DB.session.commit()
        return jsonify(_provider_payload(row)), 201

    @bp.patch("/api/ai/providers/<int:provider_id>")
    def update_provider(provider_id):
        denied = _admin_required()
        if denied:
            return denied
        row = _DB.session.get(AIProviderConfig, provider_id)
        if row is None:
            return jsonify({"error": "Provider not found."}), 404
        try:
            apply_provider(row, request.get_json(silent=True) or {})
        except (ValueError, RuntimeError) as exc:
            return jsonify({"error": str(exc)}), 400
        if row.is_default:
            AIProviderConfig.query.filter(AIProviderConfig.id != row.id).update({"is_default": False})
        _audit("PROVIDER_UPDATED", "provider", row.id, {"provider": row.provider})
        _DB.session.commit()
        return jsonify(_provider_payload(row))

    @bp.delete("/api/ai/providers/<int:provider_id>")
    def delete_provider(provider_id):
        denied = _admin_required()
        if denied:
            return denied
        row = _DB.session.get(AIProviderConfig, provider_id)
        if row is None:
            return jsonify({"error": "Provider not found."}), 404
        ref = row.secret_ref
        _audit("PROVIDER_DELETED", "provider", row.id, {"provider": row.provider})
        _DB.session.delete(row)
        _DB.session.commit()
        _delete_secret(ref)
        return jsonify({"success": True})

    @bp.post("/api/ai/providers/test")
    def test_provider():
        denied = _admin_required()
        if denied:
            return denied
        body = request.get_json(silent=True) or {}
        row = None
        if body.get("id") is not None:
            row = _DB.session.get(AIProviderConfig, int(body["id"]))
            if row is None:
                return jsonify({"error": "Provider not found."}), 404
        provider = _trim(body.get("provider") or (row.provider if row else ""), 40)
        endpoint = _trim(body.get("endpoint") or (row.endpoint if row else ""), 700)
        model_id = _trim(body.get("model_id") or (row.model_id if row else ""), 200)
        api_key = str(body.get("api_key") or "") or (_load_secret(row.secret_ref) if row else "")
        try:
            result = _provider_test_values(provider, endpoint, model_id, api_key)
            status = "CONNECTED" if result["model_available"] else "MODEL_UNAVAILABLE"
            if row:
                row.connection_status = status
                row.capability_json = _json_dump(result["capabilities"] | {"models": result["models"]})
                row.last_error = None if status == "CONNECTED" else "Configured model was not found."
                row.last_tested_at = _now()
                _audit("PROVIDER_TESTED", "provider", row.id, {"status": status})
                _DB.session.commit()
            return jsonify(result | {"status": status})
        except Exception as exc:
            if row:
                row.connection_status = "FAILED"
                row.last_error = str(exc)[:1000]
                row.last_tested_at = _now()
                _audit("PROVIDER_TESTED", "provider", row.id, {"status": "FAILED"})
                _DB.session.commit()
            return jsonify({"ok": False, "status": "FAILED", "error": str(exc)}), 400

    @bp.get("/api/ai/models")
    def models():
        rows = AIProviderConfig.query.filter_by(enabled=True).all()
        return jsonify({"providers": [{"id": row.id, "name": row.name,
            "models": _json_load(row.capability_json, {}).get("models", [])} for row in rows]})

    @bp.get("/api/ai/usage")
    def usage():
        totals = _DB.session.query(
            db.func.coalesce(db.func.sum(AIUsageLog.requests), 0),
            db.func.coalesce(db.func.sum(AIUsageLog.input_tokens), 0),
            db.func.coalesce(db.func.sum(AIUsageLog.output_tokens), 0),
            db.func.coalesce(db.func.sum(AIUsageLog.estimated_cost), 0),
        ).first()
        return jsonify({"requests": int(totals[0]), "input_tokens": int(totals[1]),
                        "output_tokens": int(totals[2]), "estimated_cost_usd": float(totals[3])})

    @bp.get("/api/ai/tasks")
    def tasks():
        limit = min(max(request.args.get("limit", 50, type=int), 1), 200)
        status = _trim(request.args.get("status"), 30).upper()
        query = AITask.query
        if status:
            if status not in TASK_STATES:
                return jsonify({"error": "Invalid task status."}), 400
            query = query.filter_by(status=status)
        rows = query.order_by(AITask.created_at.desc()).limit(limit).all()
        return jsonify({"tasks": [_task_payload(row) for row in rows]})

    @bp.get("/api/ai/tasks/<task_id>")
    def task_detail(task_id):
        row = _DB.session.get(AITask, task_id)
        return (jsonify(_task_payload(row, True)) if row else
                (jsonify({"error": "Task not found."}), 404))

    @bp.post("/api/ai/tasks/<task_id>/cancel")
    def cancel_task(task_id):
        row = _DB.session.get(AITask, task_id)
        if row is None:
            return jsonify({"error": "Task not found."}), 404
        if row.status in {"COMPLETED", "FAILED", "CANCELLED"}:
            return jsonify({"error": "Task is already finished."}), 409
        row.cancel_requested = True
        if row.status in {"CREATED", "QUEUED", "RETRY_WAIT"}:
            row.status = "CANCELLED"
            row.progress_stage = "Cancelled"
            row.completed_at = _now()
        _DB.session.commit()
        return jsonify(_task_payload(row))

    @bp.post("/api/ai/tasks/<task_id>/retry")
    def retry_task(task_id):
        row = _DB.session.get(AITask, task_id)
        if row is None:
            return jsonify({"error": "Task not found."}), 404
        if row.status != "FAILED":
            return jsonify({"error": "Only failed tasks can be retried."}), 409
        if row.attempt_count >= row.max_attempts:
            return jsonify({"error": "Retry limit reached.", "error_code": "TASK_RETRY_EXHAUSTED"}), 409
        row.status = "QUEUED"
        row.progress_stage = "Queued for retry"
        row.cancel_requested = False
        row.completed_at = None
        _DB.session.commit()
        _enqueue(row.id)
        return jsonify(_task_payload(row))

    @bp.get("/api/ai/results/<int:result_id>")
    def result_detail(result_id):
        row = _DB.session.get(AIResearchResult, result_id)
        if row is None:
            return jsonify({"error": "Result not found."}), 404
        return jsonify({"id": row.id, "task_id": row.task_id, "result_type": row.result_type,
                        "classification": row.classification, "verification_status": row.verification_status,
                        "result": _json_load(row.result_json, {}), "analyzed_at": _iso(row.analyzed_at),
                        "valid_until": _iso(row.valid_until)})

    @bp.post("/api/ai/pricing-analysis")
    def pricing_analysis():
        body = request.get_json(silent=True) or {}
        try:
            calculate_pricing(body)
        except ValueError as exc:
            return jsonify({"error": str(exc), "error_code": "INPUT_INVALID"}), 400
        canonical = _json_dump(body)
        idem = _trim(request.headers.get("Idempotency-Key"), 64) or hashlib.sha256(
            ("PRICING_ANALYSIS|" + canonical).encode("utf-8")).hexdigest()
        existing = AITask.query.filter_by(idempotency_key=idem).first()
        if existing:
            return jsonify(_task_payload(existing, True) | {"duplicate": True}), 200
        row = AITask(
            id=str(uuid.uuid4()), task_type="PRICING_ANALYSIS", entity_type="PRICING_SCENARIO",
            entity_id=_trim(body.get("reference"), 180) or None,
            requested_by=_trim(request.remote_addr, 120), trigger_type="MANUAL",
            status="QUEUED", progress_stage="Queued", attempt_count=0,
            max_attempts=int(_setting("max_retries")) + 1,
            idempotency_key=idem, payload_json=canonical, created_at=_now(),
        )
        _DB.session.add(row)
        try:
            _DB.session.commit()
        except IntegrityError:
            _DB.session.rollback()
            existing = AITask.query.filter_by(idempotency_key=idem).first()
            return jsonify(_task_payload(existing, True) | {"duplicate": True}), 200
        try:
            _enqueue(row.id)
        except RuntimeError as exc:
            row.status = "FAILED"
            row.error_code = "QUEUE_UNAVAILABLE"
            row.error_message = str(exc)
            row.error_stage = "QUEUE"
            row.completed_at = _now()
            _DB.session.commit()
            return jsonify(_task_payload(row)), 503
        return jsonify(_task_payload(row)), 202

    app.register_blueprint(bp)
    with app.app_context():
        db.create_all()
        stale = AITask.query.filter(AITask.status.in_(["RUNNING", "QUEUED", "RETRY_WAIT"])).all()
        for row in stale:
            row.status = "QUEUED"
            row.progress_stage = "Recovered after restart"
            row.error_code = row.error_message = row.error_stage = None
        db.session.commit()
        for row in stale:
            try:
                _TASK_QUEUE.put_nowait(row.id)
            except queue.Full:
                break
    _ensure_worker()
    exports = {"Provider": Provider, "Task": Task, "Result": Result, "Usage": Usage,
               "Setting": Setting, "Audit": Audit, "calculate_pricing": calculate_pricing}
    app.extensions["serveone_ai_intelligence"] = exports
    return exports
