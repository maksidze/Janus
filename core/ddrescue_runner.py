import re
import subprocess
import shlex
from typing import Callable

# Паттерны для парсинга вывода ddrescue (stderr)
# Пример строки ddrescue:
#   rescued:   1234 MB,  errsize:       0 B,  current rate:   45 MB/s
#   ipos:    1234 MB,   errors:       0,    average rate:   40 MB/s
#   opos:    1234 MB,   time since last successful read:          0 s
#   Finished
RE_RESCUED = re.compile(r"rescued:\s+([\d.]+ \w+)", re.IGNORECASE)
RE_ERRSIZE = re.compile(r"errsize:\s+([\d.]+ \w+)", re.IGNORECASE)
RE_ERRORS  = re.compile(r"errors:\s+(\d+)", re.IGNORECASE)
RE_RATE    = re.compile(r"current rate:\s+([\d.]+ \w+/s)", re.IGNORECASE)
RE_ELAPSED = re.compile(r"(\d+:\d{2}:\d{2})")

# Паттерн прогресса (ddrescue выводит размер в процентах через --log-rates или парсим rescued/total)
# Для определения % нужно знать общий размер устройства — получим его через lsblk/blockdev
RE_IPOS = re.compile(r"ipos:\s+([\d.]+ \w+)", re.IGNORECASE)


def parse_ddrescue_line(line: str) -> dict:
    """Парсит одну строку вывода ddrescue и возвращает словарь с найденными полями."""
    result = {}

    m = RE_RESCUED.search(line)
    if m:
        result["rescued"] = m.group(1)

    m = RE_ERRORS.search(line)
    if m:
        result["errors"] = m.group(1)

    m = RE_RATE.search(line)
    if m:
        result["rate"] = m.group(1)

    m = RE_ELAPSED.search(line)
    if m:
        result["elapsed"] = m.group(1)

    return result


def get_device_size_bytes(device: str) -> int:
    """Возвращает размер устройства/файла в байтах. 0 — если не удалось определить."""
    try:
        result = subprocess.run(
            ["blockdev", "--getsize64", device],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except Exception:
        pass
    return 0


def parse_size_to_bytes(size_str: str) -> int:
    """Преобразует строку вида '1.23 GB' в байты."""
    units = {
        "B": 1,
        "KB": 1024, "KiB": 1024,
        "MB": 1024**2, "MiB": 1024**2,
        "GB": 1024**3, "GiB": 1024**3,
        "TB": 1024**4, "TiB": 1024**4,
    }
    parts = size_str.strip().split()
    if len(parts) == 2:
        try:
            value = float(parts[0])
            unit = parts[1]
            return int(value * units.get(unit, 1))
        except (ValueError, KeyError):
            pass
    return 0


def run_ddrescue(
    job_id: str,
    source: str,
    destination: str,
    log_file: str | None,
    extra_args: str,
    on_update: Callable[[str, dict], None],
    on_finish: Callable[[str, int, str | None], None],
) -> subprocess.Popen:
    """
    Запускает ddrescue в subprocess.Popen и возвращает объект процесса.
    Эта функция предназначена для вызова в отдельном потоке.

    Колбэки:
      on_update(job_id, fields)   — вызывается при каждом обновлении прогресса
      on_finish(job_id, returncode, error_message) — вызывается по завершении
    """
    cmd = ["ddrescue", "--force", "-v"]

    if log_file:
        cmd += [source, destination, log_file]
    else:
        cmd += [source, destination]

    if extra_args:
        cmd += shlex.split(extra_args)

    total_bytes = get_device_size_bytes(source)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        on_update(job_id, {"pid": proc.pid})

        for line in proc.stdout:
            line = line.rstrip()
            fields = parse_ddrescue_line(line)

            # Вычисляем % прогресса по объёму rescued
            if "rescued" in fields and total_bytes > 0:
                rescued_bytes = parse_size_to_bytes(fields["rescued"])
                fields["progress_pct"] = round(rescued_bytes / total_bytes * 100, 1)

            if fields:
                on_update(job_id, fields)

        proc.wait()
        error_msg = None if proc.returncode == 0 else f"ddrescue завершился с кодом {proc.returncode}"
        on_finish(job_id, proc.returncode, error_msg)
        return proc

    except FileNotFoundError:
        on_finish(job_id, -1, "ddrescue не найден. Установите пакет gddrescue.")
        raise
    except Exception as e:
        on_finish(job_id, -1, str(e))
        raise

