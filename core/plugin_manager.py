"""插件系统：直接移植 Novel-Claude 的 EventBus + 容错隔离 + 管道处理。"""
import importlib, inspect, sys, threading
from pathlib import Path
from typing import Any, Callable, Optional


class EventBus:
    """中央事件总线（移植 Novel-Claude V3）。
    支持: 注册/注销、容错隔离、串行管道、收集模式。
    """
    def __init__(self, logger):
        self.logger = logger
        self._subscribers: list = []
        self._handlers: dict[str, list[Callable]] = {}
        self._lock = threading.RLock()

    def register(self, skill):
        """注册插件到总线。"""
        with self._lock:
            if skill not in self._subscribers:
                self._subscribers.append(skill)
                self.logger.debug("插件注册到 EventBus: %s", skill.name)

    def unregister(self, skill):
        with self._lock:
            if skill in self._subscribers:
                self._subscribers.remove(skill)

    def clear(self):
        with self._lock:
            self._subscribers.clear()
            self._handlers.clear()

    def on(self, event: str, handler: Callable, priority: int = 0):
        """注册事件监听器（用于非插件模式）。"""
        with self._lock:
            if event not in self._handlers:
                self._handlers[event] = []
            self._handlers[event].append((priority, handler))
            self._handlers[event].sort(key=lambda x: x[0], reverse=True)

    def emit(self, event_name: str, *args, **kwargs) -> list:
        """触发事件，容错隔离：单个插件崩溃不影响其他插件。"""
        results = []
        with self._lock:
            subscribers = list(self._subscribers)
            handlers = list(self._handlers.get(event_name, []))
        for skill in subscribers:
            if not getattr(skill, "enabled", True):
                continue
            method = getattr(skill, event_name, None)
            if method:
                try:
                    res = method(*args, **kwargs)
                    results.append(res)
                except Exception as e:
                    self.logger.warning("[EventBus] 插件 %s 在 %s 崩溃: %s", skill.name, event_name, e)
        # 也触发非插件 handler
        for priority, handler in handlers:
            try:
                results.append(handler(*args, **kwargs))
            except Exception as e:
                self.logger.warning("[EventBus] handler 在 %s 崩溃: %s", event_name, e)
        return results

    def emit_pipeline(self, event_name: str, initial_data: Any, *args, **kwargs) -> Any:
        """串行管道处理：上一个插件的结果传给下一个。"""
        data = initial_data
        with self._lock:
            subscribers = list(self._subscribers)
            handlers = list(self._handlers.get(event_name, []))
        for skill in subscribers:
            if not getattr(skill, "enabled", True):
                continue
            method = getattr(skill, event_name, None)
            if method:
                try:
                    result = method(data, *args, **kwargs)
                    if result is not None:
                        data = result
                except Exception as e:
                    self.logger.warning("[EventBus] 管道中断 %s: %s", skill.name, e)
        for priority, handler in handlers:
            try:
                result = handler(data, *args, **kwargs)
                if result is not None:
                    data = result
            except Exception as e:
                self.logger.warning("[EventBus] handler 管道中断: %s", e)
        return data

    def collect(self, method_name: str, *args, **kwargs) -> list:
        """收集所有插件的方法返回值。"""
        collected = []
        with self._lock:
            subscribers = list(self._subscribers)
            handlers = list(self._handlers.get(method_name, []))
        for skill in subscribers:
            if not getattr(skill, "enabled", True):
                continue
            method = getattr(skill, method_name, None)
            if method:
                try:
                    res = method(*args, **kwargs)
                    if isinstance(res, list):
                        collected.extend(res)
                    else:
                        collected.append(res)
                except Exception as e:
                    self.logger.warning("[EventBus] collect 失败 %s: %s", skill.name, e)
        for priority, handler in handlers:
            try:
                collected.append(handler(*args, **kwargs))
            except Exception as e:
                self.logger.warning("[EventBus] handler collect 失败: %s", e)
        return collected


class BasePlugin:
    """插件基类（移植 Novel-Claude BaseSkill 生命周期）。"""
    def __init__(self, name: str, event_bus: EventBus, logger):
        self.name = name
        self.event_bus = event_bus
        self.logger = logger
        self.enabled = True

    def on_init(self):
        """插件初始化时触发。"""
        pass

    def on_unload(self):
        """插件卸载时触发。"""
        pass

    def on_before_scene_write(self, prompt_payload: list, beat_data: dict) -> list:
        """AI 写作前，可修改 prompt。"""
        return prompt_payload

    def on_after_scene_write(self, beat_data: dict, raw_text: str):
        """AI 写作后，用于更新角色/数据库。"""
        pass


class PluginManager:
    """插件管理器：扫描、加载、容错隔离。"""
    def __init__(self, plugins_dir: Path, event_bus: EventBus, logger):
        self.plugins_dir = plugins_dir
        self.event_bus = event_bus
        self.logger = logger
        self._plugins: dict[str, BasePlugin] = {}
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self._load_plugins()

    def _load_plugins(self):
        for plugin_file in sorted(self.plugins_dir.glob("*.py")):
            if plugin_file.name.startswith("_"): continue
            try:
                spec = importlib.util.spec_from_file_location(plugin_file.stem, plugin_file)
                if not spec or not spec.loader: continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                for name, obj in inspect.getmembers(module):
                    if inspect.isclass(obj) and issubclass(obj, BasePlugin) and obj is not BasePlugin:
                        plugin = obj(name, self.event_bus, self.logger)
                        plugin.on_init()
                        self.event_bus.register(plugin)
                        self._plugins[name] = plugin
                        self.logger.info("插件加载: %s", name)
            except Exception as e:
                self.logger.warning("插件加载失败 %s: %s", plugin_file.name, e)

    def list_plugins(self) -> list[dict]:
        return [{"name": p.name, "enabled": p.enabled, "type": type(p).__name__} for p in self._plugins.values()]

    def reload(self):
        for p in self._plugins.values():
            try: p.on_unload()
            except Exception as e: self.logger.warning("插件卸载失败 %s: %s", p.name, e)
        self._plugins.clear()
        self.event_bus.clear()
        # 清除 sys.modules 缓存，确保重新加载模块
        for name in list(sys.modules.keys()):
            if hasattr(sys.modules[name], '__file__') and sys.modules[name].__file__:
                if self.plugins_dir in Path(sys.modules[name].__file__).parents:
                    del sys.modules[name]
        self._load_plugins()
        self.logger.info("插件热重载完成")

    def get(self, name: str) -> Optional[BasePlugin]:
        return self._plugins.get(name)

    def disable(self, name: str):
        p = self._plugins.get(name)
        if p: p.enabled = False; self.logger.info("插件禁用: %s", name)

    def enable(self, name: str):
        p = self._plugins.get(name)
        if p: p.enabled = True; self.logger.info("插件启用: %s", name)
