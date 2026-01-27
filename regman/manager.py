# type: ignore
import time
import threading
import winreg
from typing import Type
from dataclasses import fields

from .schema import RegistryCfg
from .winreg_utils import read_value, write_value


class ConfigManager:
    def __init__(
        self,
        reg_path: str,
        schema: Type[RegistryCfg] = RegistryCfg,
        ttl: float = 2.0,
    ):
        self._reg_path = reg_path
        self._schema = schema
        self._ttl = ttl
        self._lock = threading.Lock()
        self._last_load = 0.0
        self._config = schema()

    def get(self) -> RegistryCfg:
        with self._lock:
            if time.time() - self._last_load > self._ttl:
                self._config = self._read_registry()
                self._last_load = time.time()
            return self._config

    def write(self, key: str, value):
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self._reg_path) as reg_key:
            write_value(reg_key, key, value)

    # ---------- internal ----------

    def _read_registry(self) -> RegistryCfg:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._reg_path) as key:
                kwargs = {}
                for f in fields(self._schema):
                    kwargs[f.name] = read_value(key, f.name, f.default)
                return self._schema(**kwargs)
        except FileNotFoundError:
            return self._schema()
        except Exception as e:
            return self._schema()
