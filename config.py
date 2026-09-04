"""Global configuration for Novel Workspace MCP."""
import os
import sys, logging
from pathlib import Path
from typing import Final

from version import __version__


MODULE_ROOT = Path(__file__).parent.resolve()


def _load_dotenv() -> None:
    """读取项目根目录的 .env，不覆盖宿主进程已注入的环境变量。"""
    candidates = [Path.cwd() / ".env", Path(__file__).parent.resolve() / ".env"]
    for dotenv in dict.fromkeys(candidates):
        if not dotenv.is_file():
            continue
        try:
            lines = dotenv.read_text("utf-8").splitlines()
        except OSError:
            continue
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key or key.startswith("#"):
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            os.environ.setdefault(key, value)
        break


_load_dotenv()

PROJECT_ROOT: Final[Path] = MODULE_ROOT


def _default_runtime_root(project_root: Path = PROJECT_ROOT) -> Path:
    override = os.getenv("NOVEL_WORKSPACE_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if (project_root / "pyproject.toml").is_file():
        return project_root
    if os.name == "nt":
        base = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return base / "NovelWorkspaceMCP"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "NovelWorkspaceMCP"
    base = Path(os.getenv("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / "novel-workspace-mcp"


RUNTIME_ROOT: Final[Path] = _default_runtime_root()
STORAGE_ROOT: Final[Path] = RUNTIME_ROOT / "storage"
NOVELS_ROOT: Final[Path] = STORAGE_ROOT / "novels"
WORKSPACE_FILE: Final[Path] = STORAGE_ROOT / "workspace.json"
LOG_DIR: Final[Path] = RUNTIME_ROOT / "logs"

NOVEL_DIRS: Final[list[str]] = ["bible","outline","chapters","summaries","characters","timeline"]
BIBLE_FILES: Final[dict[str,str]] = {
    "world.md": '''# 世界观

## 世界名称
（填写你的世界名称）

## 时代背景
（描述故事发生的时代）

## 地理
（主要大陆、国家、城市）

## 势力分布
（主要阵营、组织、门派）

## 文明水平
（科技/魔法/修炼水平）

## 特殊规则
（这个世界特有的物理/魔法/修炼规则）
''',
    "rules.md": '''# 规则设定

## 力量体系
（修炼/魔法/科技体系说明）

## 等级划分
（1. 等级名称 2. 突破条件 3. 能力特征）

## 特殊限制
（世界内的禁忌、无法做到的事）

## 经济系统
（货币、资源、交易方式）
''',
    "style.md": '''# 写作风格

## 叙事视角
（第一人称/第三人称/多视角）

## 语言风格
（简洁/华丽/幽默/严肃）

## 节奏偏好
（快节奏/慢热/张弛有度）

## 参考作品
（风格上参考的作品）
''',
}
OUTLINE_FILES: Final[dict[str,str]] = {"main.md":"# 大纲\n\n"}
CHAPTER_FILE_PATTERN: Final[str] = "{:06d}.txt"
SUMMARY_FILE_PATTERN: Final[str] = "{:06d}.json"

# ── 模型配置 ──
MODEL_CONFIG: Final[dict] = {
    # 默认采用 Ornith 保存预设的128K上下文；连接后仍以 LMS 实际加载值为准。
    "context_window": 131072,
    "max_output_tokens": 14000,
    "max_input_tokens": 116572,
    "system_prompt_tokens": 500,
    "available_context": 96000,
    # Qwen3.5 MoE 系中文正文保守按 1.4 字/token 估算。
    "chars_per_token": 1.4,
    "model_name": "ornith-1.0-35b-aeon-ultimate-uncensored-mtp-apex-i-compact",
    "embed_model": "text-embedding-nomic-embed-text-v1.5",
    "tokens_per_second": 53,
    "default_target_words": 5000,
    # 32GB 内存 + 单实例 35B Q4：所有生成严格串行，短分析限制输出。
    "max_concurrent_generations": 1,
    "analysis_max_tokens": 1536,
    "summary_max_tokens": 2400,
    "planning_max_tokens": 4096,
    "reasoning_effort": "none",
    "context_profiles": {
        "brief": 24000,
        "planning": 48000,
        "prose": 96000,
    },
}

# 模型服务配置。默认保持原来的本地 LM Studio 行为；设置
# NOVEL_LLM_PROVIDER=api 后即可连接任意 OpenAI-compatible API。
LLM_PROVIDER: Final[str] = (os.getenv("NOVEL_LLM_PROVIDER", "local").strip().lower() or "local")
LLM_BASE_URL: Final[str] = (os.getenv("NOVEL_LLM_BASE_URL", "http://127.0.0.1:1234").strip() or "http://127.0.0.1:1234")
LLM_API_KEY: Final[str] = os.getenv("NOVEL_LLM_API_KEY", "")
LLM_MODEL: Final[str] = (os.getenv("NOVEL_LLM_MODEL", MODEL_CONFIG["model_name"]).strip() or MODEL_CONFIG["model_name"])
LLM_EMBED_MODEL: Final[str] = (os.getenv("NOVEL_LLM_EMBED_MODEL", MODEL_CONFIG["embed_model"]).strip() or MODEL_CONFIG["embed_model"])
try:
    LLM_TIMEOUT: Final[int] = max(10, int(os.getenv("NOVEL_LLM_TIMEOUT", "600")))
except ValueError:
    LLM_TIMEOUT: Final[int] = 600

MODEL_CONFIG["model_name"] = LLM_MODEL
MODEL_CONFIG["embed_model"] = LLM_EMBED_MODEL

# 不同任务不能共用同一套采样参数。结构化任务优先稳定，正文任务保留创造力。
MODEL_TASK_PROFILES: Final[dict[str, dict[str, float | int]]] = {
    "general": {
        "temperature": 0.7, "top_p": 0.8, "top_k": 20,
        "repeat_penalty": 1.06, "presence_penalty": 0.0, "min_p": 0.0,
    },
    "structured": {
        "temperature": 0.2, "top_p": 0.7, "top_k": 20,
        "repeat_penalty": 1.03, "presence_penalty": 0.0, "min_p": 0.0,
    },
    "planning": {
        "temperature": 0.6, "top_p": 0.95, "top_k": 20,
        "repeat_penalty": 1.05, "presence_penalty": 0.0, "min_p": 0.0,
    },
    "prose": {
        "temperature": 0.7, "top_p": 0.8, "top_k": 20,
        "repeat_penalty": 1.08, "presence_penalty": 0.0, "min_p": 0.0,
    },
    "revision": {
        "temperature": 0.6, "top_p": 0.85, "top_k": 20,
        "repeat_penalty": 1.06, "presence_penalty": 0.0, "min_p": 0.0,
    },
}

MODEL_RUNTIME_CONFIG: Final[dict] = {
    "backend": "LM Studio managed",
    "parameter_source": "LM Studio 当前模型实例",
    "override_load_parameters": False,
    "parallel": 1,
    "cpu_affinity_physical_cores": 8,
    "cpu_affinity_logical_processors": 16,
    "chat_template": "LM Studio 模型实例配置",
    "system_prompt_reference": "",
    "use_reference_system_prompt": False,
}

_context = MODEL_CONFIG["context_window"]
_output = MODEL_CONFIG["max_output_tokens"]
_system = MODEL_CONFIG["system_prompt_tokens"]
MODEL_CONFIG["max_input_tokens"] = _context - _output - _system
MODEL_CONFIG["available_context"] = min(MODEL_CONFIG["max_input_tokens"] - 1000, 96000)

MAX_CONTEXT_TOKENS: Final[int] = MODEL_CONFIG["available_context"]
RECENT_SUMMARIES_COUNT: Final[int] = 8

LOG_LEVEL: Final[int] = logging.DEBUG
LOG_FORMAT: Final[str] = "[%(asctime)s] %(levelname)-8s %(name)-24s %(message)s"
LOG_DATE_FORMAT: Final[str] = "%Y-%m-%d %H:%M:%S"

SERVER_NAME: Final[str] = "novel-workspace"
SERVER_VERSION: Final[str] = __version__
SERVER_DESCRIPTION: Final[str] = "AI 长篇小说工作空间管理系统"

LM_STUDIO_MCP_CONFIG: Final[dict] = {
    "novel-workspace": {
        "command": "uv",
        "args": ["run", "python", "novel_server.py"],
        "cwd": str(PROJECT_ROOT),
    }
}


def estimate_tokens(text: str) -> int:
    if not text: return 0
    chinese = sum(1 for c in text if '\u4e00' <= c <= '\u9fff' or '\u3400' <= c <= '\u4dbf' or '\u3000' <= c <= '\u303f')
    others = len(text) - chinese
    return max(1, int(chinese / MODEL_CONFIG["chars_per_token"] + others / 3.0))


def estimate_target_tokens(target_words: int) -> dict:
    token_est = int(target_words / MODEL_CONFIG["chars_per_token"])
    speed = MODEL_CONFIG["tokens_per_second"]
    seconds = token_est / speed
    return {
        "target_words": target_words,
        "estimated_tokens": token_est,
        "estimated_seconds": round(seconds, 1),
        "estimated_minutes": round(seconds / 60, 1),
        "display": f"约{target_words}字 ≈ {token_est} tokens ≈ {round(seconds)}秒",
    }


def estimate_generation_time(tokens: int) -> dict:
    speed = MODEL_CONFIG["tokens_per_second"]
    seconds = tokens / speed
    return {
        "tokens": tokens,
        "speed": speed,
        "seconds": round(seconds, 1),
        "minutes": round(seconds / 60, 1),
        "display": f"约{round(seconds)}秒 ({round(seconds/60,1)}分钟)",
    }


def trim_to_token_limit(text: str, max_tokens: int) -> str:
    """按 token 预算截断文本，使用二分法精确截断。"""
    if not text or max_tokens <= 0:
        return ""
    if estimate_tokens(text) <= max_tokens:
        return text
    marker = "\n\n[已截断...]"
    if estimate_tokens(marker) >= max_tokens:
        marker = ""
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_tokens(text[:mid] + marker) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + marker


def get_model_config_report() -> dict:
    c = MODEL_CONFIG
    runtime = dict(MODEL_RUNTIME_CONFIG)
    if LLM_PROVIDER == "api":
        runtime.update({"backend": "OpenAI-compatible API", "parameter_source": "环境变量"})
    return {
        "provider": LLM_PROVIDER,
        "base_url": LLM_BASE_URL,
        "api_key_configured": bool(LLM_API_KEY),
        "model": c["model_name"],
        "context_window": c["context_window"],
        "reserved_for_output": c["max_output_tokens"],
        "available_context": c["available_context"],
        "speed_tokens_per_sec": c["tokens_per_second"],
        "chars_per_token": c["chars_per_token"],
        "default_target_words": c["default_target_words"],
        "output_estimate_3k": estimate_target_tokens(3000)["display"],
        "output_estimate_5k": estimate_target_tokens(5000)["display"],
        "output_estimate_8k": estimate_target_tokens(8000)["display"],
        "sampling_profiles": MODEL_TASK_PROFILES,
        "runtime": runtime,
        "note": "项目连接 LMS 托管的 Ornith，不覆盖 LM Studio 中的上下文、GPU、CPU专家层、KV、Flash Attention、MTP或聊天模板参数；仅把模型进程绑定到8个物理核心（16逻辑线程）。"
    }


def ensure_dirs() -> None:
    for d in [STORAGE_ROOT, NOVELS_ROOT, LOG_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def setup_logging() -> logging.Logger:
    ensure_dirs()
    log_file = LOG_DIR / "novel_server.log"
    logger = logging.getLogger("novel-workspace")
    logger.setLevel(LOG_LEVEL)
    for handler in logger.handlers:
        handler.close()
    logger.handlers.clear()
    from logging.handlers import RotatingFileHandler
    fh = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5, encoding="utf-8")
    fh.setLevel(LOG_LEVEL)
    fh.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    logger.addHandler(fh)
    # MCP stdio reserves stdout for JSON-RPC messages. All human-readable
    # diagnostics must go to stderr so clients never receive corrupted frames.
    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(ch)
    r = get_model_config_report()
    logger.info("Starting | context:%d | %d t/s | default chapter:%d words",
                r["context_window"], r["speed_tokens_per_sec"],
                r["default_target_words"])
    return logger
