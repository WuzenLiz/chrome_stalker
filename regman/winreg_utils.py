# type: ignore
import winreg

def read_value(key, name, default):
    try:
        val, _ = winreg.QueryValueEx(key, name)
        return val
    except FileNotFoundError:
        return default

def write_value(key, name, value):
    if isinstance(value, bool):
        winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(value))
    elif isinstance(value, int):
        winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)
    elif isinstance(value, float):
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value))
    else:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value))
