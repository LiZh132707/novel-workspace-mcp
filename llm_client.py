"""统一模型客户端：本地 LM Studio 或远程 OpenAI-compatible API。

支持流式生成（SSE）、文本嵌入和按后端筛选采样参数。
"""
import json, os, time, subprocess, threading, secrets, socket
import re
from pathlib import Path
from typing import Optional
import httpx

from config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_EMBED_MODEL,
    LLM_MODEL,
    LLM_PROVIDER,
    LLM_TIMEOUT,
    MODEL_CONFIG,
    MODEL_RUNTIME_CONFIG,
    MODEL_TASK_PROFILES,
    STORAGE_ROOT,
)

for _key in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "all_proxy", "ALL_PROXY"]:
    os.environ.pop(_key, None)

LMS_PATH = os.getenv("NOVEL_LMS_PATH", r"C:\Program Files\LM Studio\resources\app\.webpack\lms.exe")
MODEL_KEY = LLM_MODEL or "ornith-1.0-35b-aeon-ultimate-uncensored-mtp-apex-i-compact"
EMBED_MODEL_KEY = LLM_EMBED_MODEL or "text-embedding-nomic-embed-text-v1.5"
DEFAULT_PORT = 1234
CONTEXT_LENGTH = int(MODEL_CONFIG["context_window"])
LLAMA_SERVER_PATH = os.getenv(
    "NOVEL_LLAMA_SERVER_PATH",
    str(Path.home() / ".lmstudio" / "extensions" / "backends" / "llama.cpp-win-x86_64-vulkan-avx2-2.24.0" / "llama-server.exe"),
)
GENESIS_MODEL_FILENAME = "Ornith-1.0-35B-AEON-Ultimate-Uncensored-MTP-APEX-I-Compact.gguf"
LM_STUDIO_MODELS_ROOT = Path.home() / ".lmstudio" / "models"
CHAT_TEMPLATE_PATH = Path(MODEL_RUNTIME_CONFIG["chat_template"])
SYSTEM_PROMPT_REFERENCE_PATH = Path(MODEL_RUNTIME_CONFIG["system_prompt_reference"])
PID_FILE = STORAGE_ROOT / "llama-server.pid"


def _creation_flags() -> int:
    """Return the Windows no-console flag when available, else a POSIX-safe 0."""
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def resolve_model_path() -> tuple[Path, bool]:
    matches = list(LM_STUDIO_MODELS_ROOT.glob(f"**/{GENESIS_MODEL_FILENAME}"))
    complete = [path for path in matches if path.is_file() and path.stat().st_size > 10_000_000_000]
    if complete:
        return max(complete, key=lambda path: path.stat().st_mtime), True
    expected = LM_STUDIO_MODELS_ROOT / "Phil2Sat" / "Ornith-1.0-35B-AEON-Ultimate-Uncensored-MTP-APEX-I-Compact-GGUF" / GENESIS_MODEL_FILENAME
    return expected, True


def detect_generation_loop(text: str) -> str:
    """返回检测到的重复片段；空字符串表示正常。"""
    sample = (text or "")[-12000:]
    if len(sample) < 64:
        return ""
    compact = "".join(sample.split())
    if compact and len(set(compact[-100:])) <= 2:
        return compact[-20:]
    for size in range(8, min(401, len(compact) // 4 + 1)):
        if len(compact) < size * 4:
            continue
        unit = compact[-size:]
        if len(set(unit)) >= 4 and compact.endswith(unit * 4):
            return unit[:120]
    paragraphs = [item.strip() for item in sample.splitlines() if len(item.strip()) >= 30]
    if len(paragraphs) >= 4 and len(set(paragraphs[-4:])) == 1:
        return paragraphs[-1][:120]
    return ""


class LMStudioClient:
    """本地 LM Studio 或远程 OpenAI-compatible API 客户端。

    通过 ``NOVEL_LLM_PROVIDER=local|api`` 选择后端。保留这个类名是为了
    兼容现有调用方；API 模式不会执行任何 LM Studio/Windows 管理命令。
    """

    def __init__(self, base_url: str = None, timeout: int = None, provider: str = None,
                 api_key: str = None, model: str = None, embed_model: str = None):
        self.provider = (provider or LLM_PROVIDER or "local").strip().lower()
        if self.provider not in {"local", "api"}:
            raise ValueError("NOVEL_LLM_PROVIDER 只能是 local 或 api")
        raw_base_url = base_url or (f"http://127.0.0.1:{DEFAULT_PORT}" if self.provider == "local" else LLM_BASE_URL)
        raw_base_url = raw_base_url.rstrip("/")
        self.base_url = raw_base_url[:-3] if raw_base_url.lower().endswith("/v1") else raw_base_url
        self.timeout = timeout or LLM_TIMEOUT
        self.api_key = LLM_API_KEY if api_key is None else api_key
        self.model_key = model or MODEL_KEY
        self.embed_model = embed_model or EMBED_MODEL_KEY
        self._client: Optional[httpx.Client] = None
        self._model_loaded = False
        self._server_started = False
        self._generation_lock = threading.Lock()
        self._server_process = None
        self._server_log = None
        self.last_metrics = {"completion_tokens": 0, "elapsed_seconds": 0.0, "tokens_per_second": 0.0}
        self._warmed_up = False
        if self.provider == "local":
            self.model_path, self.mtp_capable = resolve_model_path()
        else:
            self.model_path, self.mtp_capable = None, False

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            self._client = httpx.Client(
                base_url=self.base_url,
                timeout=self.timeout,
                headers=headers,
                trust_env=False,
            )
        return self._client

    def _set_server_port(self, port: int):
        base_url = f"http://127.0.0.1:{port}"
        if self.base_url == base_url:
            return
        self.close()
        self.base_url = base_url

    def _discover_server_port(self) -> int | None:
        try:
            result = subprocess.run(
                [LMS_PATH, "server", "status", "--json"], capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=10,
                creationflags=_creation_flags(),
            )
            data = json.loads(result.stdout or "{}")
            if data.get("running") and 1 <= int(data.get("port", 0)) <= 65535:
                return int(data["port"])
        except Exception:
            pass
        try:
            result = subprocess.run(
                [LMS_PATH, "server", "status"], capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=10,
                creationflags=_creation_flags(),
            )
            match = re.search(r"\bport\s+(\d{1,5})\b", result.stdout or "", re.IGNORECASE)
            if match and 1 <= int(match.group(1)) <= 65535:
                return int(match.group(1))
        except Exception:
            pass
        return None

    @property
    def generation_busy(self) -> bool:
        return self._generation_lock.locked()

    def _request_seed(self) -> int:
        try:
            settings = json.loads((STORAGE_ROOT / "settings.json").read_text("utf-8"))
            if settings.get("seed_mode") == "fixed":
                return max(0, min(2147483647, int(settings.get("fixed_seed", 42))))
        except Exception:
            pass
        return secrets.randbelow(2147483647)

    @staticmethod
    def _task_parameters(task_type: str, values: tuple) -> tuple:
        profile = MODEL_TASK_PROFILES.get(task_type)
        if not profile:
            return values
        return tuple(profile[key] for key in (
            "temperature", "top_p", "top_k", "repeat_penalty", "presence_penalty", "min_p",
        ))

    @staticmethod
    def _system_message(system: str) -> str:
        if not MODEL_RUNTIME_CONFIG.get("use_reference_system_prompt"):
            return system
        try:
            reference = SYSTEM_PROMPT_REFERENCE_PATH.read_text("utf-8").strip()
        except Exception:
            reference = ""
        return reference + ("\n\n" if reference and system else "") + system

    # ── 生命周期 ──
    def start(self, wait_ready: bool = True, max_wait: int = 120) -> bool:
        if self.provider == "api":
            self._server_started = True
            ready = self.is_available()
            self._model_loaded = ready
            return ready
        self._start_server()
        ready = self._wait_ready(min(max_wait, 10)) if wait_ready else self.is_available()
        if ready:
            self._model_loaded = True
            self._sync_context_from_lms()
            self._apply_cpu_affinity()
        return ready

    def _apply_cpu_affinity(self) -> bool:
        if self.provider != "local":
            return False
        logical_processors = int(MODEL_RUNTIME_CONFIG.get("cpu_affinity_logical_processors", 0) or 0)
        if os.name != "nt" or logical_processors <= 0:
            return False
        mask = (1 << min(logical_processors, 63)) - 1
        script = (
            "$targets=Get-CimInstance Win32_Process | Where-Object {"
            "$_.Name -eq 'llama-server.exe' -and $_.CommandLine -match 'Ornith-1\\.0-35B'"
            "}; foreach($item in $targets){"
            f"(Get-Process -Id $item.ProcessId).ProcessorAffinity=[intptr]{mask}"
            "}; if($targets){exit 0}else{exit 1}"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script], capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=10,
                creationflags=_creation_flags(),
            )
            return result.returncode == 0
        except Exception:
            return False

    def _is_direct_server(self) -> bool:
        try:
            response = httpx.get(self.base_url + "/props", timeout=3, trust_env=False)
            return response.status_code == 200
        except Exception:
            return False

    @staticmethod
    def _port_in_use(port: int = DEFAULT_PORT) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            return False

    def _wait_port_released(self, max_wait: int = 20):
        for attempt in range(max_wait * 2):
            if not self._port_in_use():
                return
            if attempt == 8:
                try:
                    subprocess.run([LMS_PATH, "server", "stop"], capture_output=True, timeout=15, creationflags=_creation_flags())
                except Exception:
                    pass
            time.sleep(0.5)
        raise RuntimeError(f"端口{DEFAULT_PORT}仍被LM Studio转发服务占用，无法启动Vulkan直连模型")

    def _start_direct_server(self):
        raise RuntimeError("当前已切换为 LMS 托管模式，禁止项目覆盖 LM Studio 加载参数")
        self.model_path, self.mtp_capable = resolve_model_path()
        if not os.path.exists(LLAMA_SERVER_PATH):
            raise RuntimeError("找不到Vulkan llama-server.exe")
        if not self.model_path.exists():
            raise RuntimeError(f"Genesis Hermes MTP模型尚未下载完成：{self.model_path}")
        if not CHAT_TEMPLATE_PATH.exists():
            raise RuntimeError(f"找不到Genesis Hermes聊天模板：{CHAT_TEMPLATE_PATH}")
        if self._is_direct_server():
            self._reuse_direct_server()
            return
        try:
            subprocess.run([LMS_PATH, "server", "stop"], capture_output=True, timeout=15, creationflags=_creation_flags())
            subprocess.run([LMS_PATH, "unload", "--all"], capture_output=True, timeout=60, creationflags=_creation_flags())
        except Exception:
            pass
        self._wait_port_released()
        log_dir = os.path.join(os.path.dirname(__file__), "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "llama-server.log")
        command = [
            LLAMA_SERVER_PATH, "--model", str(self.model_path), "--alias", self.model_key,
            "--host", "127.0.0.1", "--port", str(DEFAULT_PORT),
            "--ctx-size", str(CONTEXT_LENGTH),
            "--threads", str(MODEL_RUNTIME_CONFIG["threads"]),
            "--threads-batch", str(MODEL_RUNTIME_CONFIG["threads_batch"]),
            "--batch-size", str(MODEL_RUNTIME_CONFIG["batch_size"]),
            "--ubatch-size", str(MODEL_RUNTIME_CONFIG["ubatch_size"]), "--gpu-layers", "all",
            "--n-cpu-moe", str(MODEL_RUNTIME_CONFIG["cpu_moe_experts"]),
            "--flash-attn", "on",
            "--cache-type-k", str(MODEL_RUNTIME_CONFIG["kv_cache_type_k"]),
            "--cache-type-v", str(MODEL_RUNTIME_CONFIG["kv_cache_type_v"]),
            "--parallel", str(MODEL_RUNTIME_CONFIG["parallel"]), "--kv-unified",
            "--jinja", "--chat-template-file", str(CHAT_TEMPLATE_PATH),
            "--no-webui", "--no-mmap",
        ]
        if self.mtp_capable and MODEL_RUNTIME_CONFIG.get("mtp_enabled"):
            command.extend([
                "--spec-type", "draft-mtp",
                "--spec-draft-n-max", str(MODEL_RUNTIME_CONFIG["mtp_draft_tokens"]),
            ])
        server_env = os.environ.copy()
        try:
            self._server_log = open(log_path, "a", encoding="utf-8")
            self._server_process = subprocess.Popen(
                command, cwd=os.path.dirname(LLAMA_SERVER_PATH), stdout=self._server_log,
                stderr=subprocess.STDOUT, creationflags=_creation_flags(), env=server_env,
            )
            PID_FILE.parent.mkdir(parents=True, exist_ok=True)
            PID_FILE.write_text(str(self._server_process.pid), "utf-8")
        except Exception:
            if self._server_process and self._server_process.poll() is None:
                self._server_process.terminate()
            if self._server_log:
                self._server_log.close()
            self._server_process = None
            self._server_log = None
            PID_FILE.unlink(missing_ok=True)
            raise
        self._server_started = True
        self._model_loaded = True

    def _load_model(self):
        raise RuntimeError("请在 LM Studio 中使用已保存参数加载 Ornith")

    def _start_server(self):
        if self.provider != "local":
            self._server_started = True
            return
        if self._server_started:
            return
        running_port = self._discover_server_port()
        if running_port is not None:
            self._set_server_port(running_port)
            self._server_started = True
            return
        cmd = [LMS_PATH, "server", "start", "--port", str(DEFAULT_PORT), "--cors"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, creationflags=_creation_flags())
            if r.returncode == 0 or "already" in r.stderr.lower():
                self._set_server_port(self._discover_server_port() or DEFAULT_PORT)
                self._server_started = True
        except Exception: pass

    def _wait_ready(self, max_wait: int = 120) -> bool:
        if self.provider == "api":
            return self.is_available()
        for attempt in range(max_wait):
            model_ids = self._loaded_model_ids()
            if any(self.model_key in model_id for model_id in model_ids):
                return True
            unexpected = [model_id for model_id in model_ids if model_id and "embedding" not in model_id]
            if unexpected and attempt >= 2:
                raise RuntimeError(
                    f"LMS 当前加载的是其他模型：{', '.join(unexpected[:3])}；请在 LM Studio 中切换到 Ornith",
                )
            time.sleep(1)
        return False

    def _sync_context_from_lms(self):
        for item in self.loaded_models():
            identifier = str(item.get("identifier") or item.get("modelKey") or item.get("id") or "")
            if self.model_key not in identifier:
                continue
            try:
                context = int(item.get("contextLength") or item.get("context_length") or 0)
            except (TypeError, ValueError):
                context = 0
            if context < 4096:
                return
            MODEL_CONFIG["context_window"] = context
            MODEL_CONFIG["max_input_tokens"] = context - MODEL_CONFIG["max_output_tokens"] - MODEL_CONFIG["system_prompt_tokens"]
            MODEL_CONFIG["available_context"] = max(
                1024, min(MODEL_CONFIG["max_input_tokens"] - 1000, 96000),
            )
            return

    @staticmethod
    def _is_expected_server_path(executable: str) -> bool:
        try:
            return Path(executable).resolve() == Path(LLAMA_SERVER_PATH).resolve()
        except Exception:
            return False

    def _managed_pid(self) -> int | None:
        if not PID_FILE.exists():
            return None
        try:
            pid = int(PID_FILE.read_text("utf-8").strip())
            script = f'(Get-CimInstance Win32_Process -Filter "ProcessId = {pid}").ExecutablePath'
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script], capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=10,
                creationflags=_creation_flags(),
            )
            executable = result.stdout.strip().strip('"')
            return pid if result.returncode == 0 and self._is_expected_server_path(executable) else None
        except Exception:
            return None

    def stop(self):
        """项目关闭只断开连接，保留 LMS 模型和界面加载参数。"""
        self._model_loaded = False
        self._server_started = False
        self._warmed_up = False
        self._server_process = None
        self.close()

    def loaded_models(self) -> list[dict]:
        if self.provider == "api":
            try:
                response = self.client.get("/v1/models", timeout=10)
                response.raise_for_status()
                data = response.json()
                return data.get("data", []) if isinstance(data, dict) else []
            except Exception:
                return []
        try:
            result = subprocess.run([LMS_PATH, "ps", "--json"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15, creationflags=_creation_flags())
            data = json.loads(result.stdout or "[]")
            return data if isinstance(data, list) else data.get("models", data.get("data", []))
        except Exception:
            return []

    def _loaded_model_ids(self) -> list[str]:
        try:
            response = self.client.get("/v1/models", timeout=5)
            if response.status_code != 200:
                return []
            return [str(item.get("id", "")) for item in response.json().get("data", []) if item.get("id")]
        except Exception:
            return []

    def _reuse_direct_server(self):
        model_ids = self._loaded_model_ids()
        if any(self.model_key in model_id for model_id in model_ids):
            self._server_started = True
            self._model_loaded = True
            return
        if model_ids:
            raise RuntimeError(
                f"端口{DEFAULT_PORT}正在运行其他模型：{', '.join(model_ids[:3])}；请先卸载，避免等待假死",
            )
        if self._managed_pid() is not None:
            self._server_started = True
            self._model_loaded = False
            return
        raise RuntimeError(
            f"端口{DEFAULT_PORT}已被未知模型服务占用；请先停止该服务，避免等待假死",
        )

    def unload_all(self) -> bool:
        if self.generation_busy:
            raise RuntimeError("模型正在生成，需先停止当前任务")
        if self.provider == "api":
            self.stop()
            return True
        try:
            result = subprocess.run(
                [LMS_PATH, "unload", self.model_key], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60,
                creationflags=_creation_flags(),
            )
            combined = ((result.stdout or "") + "\n" + (result.stderr or "")).lower()
            if result.returncode != 0 and "not loaded" not in combined and "未加载" not in combined:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "LMS 卸载失败")
        finally:
            self.stop()
        return True

    def reload(self) -> bool:
        self.close()
        self._model_loaded = False
        return self.start(wait_ready=True, max_wait=10)

    def warmup(self) -> dict:
        """执行极短、可复现的推理，让首次正式创作不承担冷启动成本。"""
        if self._warmed_up and self.is_available():
            return {"already_warm": True, **self.last_metrics}
        if not self.start(wait_ready=True, max_wait=10):
            raise RuntimeError("模型服务尚未就绪，请检查本地服务或 API 配置")
        self.chat("你是响应测试器。", "只回复：就绪", max_tokens=16, temperature=0.0, top_p=1.0)
        self._warmed_up = True
        return {"already_warm": False, **self.last_metrics}

    # ── 非流式调用 ──
    def chat(self, system: str, prompt: str, max_tokens: int = 4096,
             temperature: float = 0.7, top_p: float = 0.8, top_k: int = 20,
             repeat_penalty: float = 1.06, presence_penalty: float = 0.0, min_p: float = 0.0,
             reasoning_effort: str = "none", seed: int = None, task_type: str = "") -> str:
        """非流式调用，返回完整文本。带指数退避重试。"""
        last_err = None
        temperature, top_p, top_k, repeat_penalty, presence_penalty, min_p = self._task_parameters(
            task_type, (temperature, top_p, top_k, repeat_penalty, presence_penalty, min_p),
        )
        _max_tk = max(16, min(MODEL_CONFIG["max_output_tokens"], int(max_tokens)))
        request_seed = self._request_seed() if seed is None else max(0, min(2147483647, int(seed)))
        try:
            from core.prompt_snapshot_manager import PromptSnapshotManager
            PromptSnapshotManager(STORAGE_ROOT).record(task_type or "general", self._system_message(system), prompt, {
                "max_tokens": _max_tk, "temperature": temperature, "top_p": top_p, "top_k": top_k,
                "repeat_penalty": repeat_penalty, "presence_penalty": presence_penalty, "min_p": min_p, "seed": request_seed,
            })
        except Exception:
            pass
        queued_at = time.perf_counter()
        with self._generation_lock:
            queue_wait = max(0.0, time.perf_counter() - queued_at)
            for attempt in range(3):
                attempt_started = time.perf_counter()
                try:
                    body = {"model": self.model_key, "messages": [{"role": "system", "content": self._system_message(system)}, {"role": "user", "content": prompt}], "max_tokens": _max_tk, "temperature": temperature, "top_p": top_p, "seed": request_seed}
                    if presence_penalty != 0:
                        body["presence_penalty"] = presence_penalty
                    if self.provider == "local":
                        body["reasoning_effort"] = reasoning_effort
                        body["chat_template_kwargs"] = {"enable_thinking": False}
                        if top_k > 0: body["top_k"] = top_k
                        if repeat_penalty > 0: body["repeat_penalty"] = repeat_penalty
                        if min_p > 0: body["min_p"] = min_p
                    resp = self.client.post("/v1/chat/completions", json=body)
                    resp.raise_for_status()
                    data = resp.json()
                    content = data["choices"][0]["message"].get("content", "").strip()
                    loop_fragment = detect_generation_loop(content)
                    if loop_fragment:
                        raise RuntimeError(f"检测到模型重复循环：{loop_fragment}")
                    usage = data.get("usage", {})
                    completion_tokens = int(usage.get("completion_tokens", 0) or max(1, len(content) / 1.8))
                    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
                    timings = data.get("timings", {})
                    cached_prompt_tokens = int(
                        timings.get("cache_n", 0)
                        or usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
                        or 0
                    )
                    request_elapsed = max(0.001, time.perf_counter() - attempt_started)
                    total_elapsed = max(0.001, time.perf_counter() - queued_at)
                    self.last_metrics = {
                        "prompt_tokens": prompt_tokens,
                        "cached_prompt_tokens": cached_prompt_tokens,
                        "prompt_eval_tokens": int(timings.get("prompt_n", prompt_tokens) or prompt_tokens),
                        "prompt_tokens_per_second": round(float(timings.get("prompt_per_second", 0) or 0), 2),
                        "completion_tokens": completion_tokens,
                        "elapsed_seconds": round(total_elapsed, 3),
                        "request_seconds": round(request_elapsed, 3),
                        "queue_wait_seconds": round(queue_wait, 3),
                        "retry_count": attempt,
                        "tokens_per_second": round(float(timings.get("predicted_per_second", 0) or completion_tokens / request_elapsed), 2),
                        "end_to_end_tokens_per_second": round(completion_tokens / total_elapsed, 2),
                        "seed": request_seed,
                    }
                    if not content:
                        usage = data.get("usage", {})
                        reasoning = usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0)
                        total = usage.get("completion_tokens", 0)
                        if reasoning > 0 and total >= _max_tk * 0.8 and attempt < 2:
                            _max_tk = min(MODEL_CONFIG["max_output_tokens"], _max_tk * 2)
                            if _max_tk > total:
                                continue
                    return content if content else ""
                except Exception as e:
                    last_err = e
                    if "重复循环" in str(e):
                        request_seed = secrets.randbelow(2147483647)
                        repeat_penalty = max(repeat_penalty, 1.18 + attempt * 0.04)
                    if isinstance(e, httpx.HTTPStatusError) and 400 <= e.response.status_code < 500:
                        break
                    if attempt < 2: time.sleep(1.5 ** attempt)
        raise RuntimeError(f"API 调用失败: {last_err}")

    # ── 流式调用 ──
    def chat_stream(self, system: str, prompt: str, max_tokens: int = 4096,
                    temperature: float = 0.7, top_p: float = 0.8, top_k: int = 20,
                    repeat_penalty: float = 1.06, presence_penalty: float = 0.0, min_p: float = 0.0,
                    task_type: str = ""):
        """流式调用，逐个 token yield。适用于实时显示生成进度。"""
        temperature, top_p, top_k, repeat_penalty, presence_penalty, min_p = self._task_parameters(
            task_type, (temperature, top_p, top_k, repeat_penalty, presence_penalty, min_p),
        )
        request_seed = self._request_seed()
        try:
            from core.prompt_snapshot_manager import PromptSnapshotManager
            PromptSnapshotManager(STORAGE_ROOT).record(task_type or "general_stream", self._system_message(system), prompt, {
                "max_tokens": max(16, min(MODEL_CONFIG["max_output_tokens"], int(max_tokens))), "temperature": temperature, "top_p": top_p, "top_k": top_k,
                "repeat_penalty": repeat_penalty, "presence_penalty": presence_penalty, "min_p": min_p, "seed": request_seed,
                "stream": True,
            })
        except Exception:
            pass
        body = {"model": self.model_key, "messages": [{"role": "system", "content": self._system_message(system)}, {"role": "user", "content": prompt}], "max_tokens": max(16, min(MODEL_CONFIG["max_output_tokens"], int(max_tokens))), "temperature": temperature, "top_p": top_p, "stream": True, "seed": request_seed}
        if presence_penalty != 0:
            body["presence_penalty"] = presence_penalty
        if self.provider == "local":
            body["stream_options"] = {"include_usage": True}
            body["reasoning_effort"] = "none"
            body["chat_template_kwargs"] = {"enable_thinking": False}
            if top_k > 0: body["top_k"] = top_k
            if repeat_penalty > 0: body["repeat_penalty"] = repeat_penalty
            if min_p > 0: body["min_p"] = min_p
        with self._generation_lock:
            started = time.perf_counter()
            completion_tokens = 0
            prompt_tokens = 0
            output_chars = 0
            generated_text = ""
            first_token_at = None
            cached_prompt_tokens = 0
            prompt_eval_tokens = 0
            prompt_tokens_per_second = 0.0
            with self.client.stream("POST", "/v1/chat/completions", json=body) as resp:
                    if resp.status_code != 200:
                        raise RuntimeError(f"API 错误: {resp.status_code}")
                    for line in resp.iter_lines():
                        if not line or line.startswith(":") or line == "data: [DONE]": continue
                        if line.startswith("data: "):
                            try:
                                d = json.loads(line[6:])
                                if d.get("usage"):
                                    completion_tokens = int(d["usage"].get("completion_tokens", 0) or 0)
                                    prompt_tokens = int(d["usage"].get("prompt_tokens", 0) or 0)
                                    cached_prompt_tokens = int(
                                        d["usage"].get("prompt_tokens_details", {}).get("cached_tokens", 0) or 0
                                    )
                                if d.get("timings"):
                                    timings = d["timings"]
                                    cached_prompt_tokens = int(timings.get("cache_n", cached_prompt_tokens) or cached_prompt_tokens)
                                    prompt_eval_tokens = int(timings.get("prompt_n", prompt_tokens) or prompt_tokens)
                                    prompt_tokens_per_second = float(timings.get("prompt_per_second", 0) or 0)
                                c = (d.get("choices") or [{}])[0].get("delta", {}).get("content", "")
                                if c:
                                    if first_token_at is None:
                                        first_token_at = time.perf_counter()
                                    output_chars += len(c)
                                    generated_text = (generated_text + c)[-12000:]
                                    if output_chars % 200 < len(c):
                                        loop_fragment = detect_generation_loop(generated_text)
                                        if loop_fragment:
                                            raise RuntimeError(f"检测到模型重复循环，已强制停止：{loop_fragment}")
                                    yield c
                            except json.JSONDecodeError: continue
            elapsed = max(0.001, time.perf_counter() - started)
            generation_elapsed = max(0.001, time.perf_counter() - (first_token_at or started))
            completion_tokens = completion_tokens or max(1, int(output_chars / 1.8))
            self.last_metrics = {
                "completion_tokens": completion_tokens,
                "prompt_tokens": prompt_tokens,
                "cached_prompt_tokens": cached_prompt_tokens,
                "prompt_eval_tokens": prompt_eval_tokens or prompt_tokens,
                "prompt_tokens_per_second": round(prompt_tokens_per_second, 2),
                "elapsed_seconds": round(elapsed, 3),
                "time_to_first_token": round((first_token_at or started) - started, 3),
                "tokens_per_second": round(completion_tokens / generation_elapsed, 2),
                "end_to_end_tokens_per_second": round(completion_tokens / elapsed, 2),
                "seed": request_seed,
            }

    # ── 嵌入 ──
    def embed(self, text: str) -> list[float]:
        last_err = None
        for attempt in range(3):
            try:
                resp = self.client.post("/v1/embeddings", json={"model": self.embed_model, "input": text})
                resp.raise_for_status(); return resp.json()["data"][0]["embedding"]
            except Exception as e:
                last_err = e
                if attempt < 2: time.sleep(1.5 ** attempt)
        raise RuntimeError(f"嵌入 API 失败: {last_err}") from last_err

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        try:
            resp = self.client.post("/v1/embeddings", json={"model": self.embed_model, "input": texts})
            resp.raise_for_status(); data = resp.json()
            results = [None] * len(texts)
            for item in data["data"]: results[item["index"]] = item["embedding"]
            return results
        except Exception as e:
            raise RuntimeError(f"批量嵌入失败: {e}") from e

    def is_available(self) -> bool:
        model_ids = self._loaded_model_ids()
        if self.provider == "api":
            return any(self.model_key == model_id or self.model_key in model_id for model_id in model_ids)
        return any(self.model_key in model_id for model_id in model_ids)

    def __del__(self):
        try: self.close()
        except Exception: pass

    def close(self):
        if self._client: self._client.close(); self._client = None


# 语义别名：新集成可使用更通用的名称，旧调用方继续使用 LMStudioClient。
OpenAICompatibleClient = LMStudioClient
