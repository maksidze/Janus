# Janus — SD Card Mass Flasher

> ВНИМАНИЕ! На данный момент программа практически полностью написана нейросетью!
> Мне нужна была утилита для массовой заливки образов на SD/USB.  
> Тратить несколько дней на написание “ради принципов” — не планировал.  
> Если оно работает — меня всё устраивает. Мелкие огрехи интерфейса поправлю когда-нибудь (в далёком будущем), если мне станет совсем нечем заняться.

---

## Что это

**Janus** — веб-приложение для **параллельной записи** образов ОС на **несколько SD-карт / USB-накопителей одновременно**. Ориентировано на сценарии “втыкаю пачку кардридеров / флешек → выбираю образ → нажимаю Start → получаю готовые носители”.

Главная идея UI: **настраиваемая сетка портов** (ячейки), где каждая ячейка соответствует физическому USB-порту/ридеру и показывает статус, прогресс, скорость, этапы, логи и кнопки управления.

---

## Ключевые возможности

- Массовая запись одного образа на несколько устройств
- Ограничение параллельности: `Concurrent writes: N` (по умолчанию 2)
- Опции на партию:
  - `Verify` (проверка после записи)
  - `Expand partition` (growpart)
  - `Resize filesystem` (resize2fs, ext4)
  - `Eject after done` (eject / udisksctl power-off)
- Реактивный UI:
  - **SSE** `/api/events`
  - fallback на polling `/api/jobs` раз в 1–2 сек
- Безопасность:
  - фильтрация по `removable`
  - защита от записи на системные диски
  - проверка `mounted`
- Настраиваемый layout: импорт/экспорт `layout.json`, хранение на сервере

---

## Требования

### ОС / окружение
- Linux (Raspberry Pi OS / Ubuntu / любой адекватный Linux)
- Python 3.10+ (рекомендуется)

### Системные утилиты
Нужны (или их аналоги):
- `dd`
- `lsblk`
- `growpart`
- `resize2fs`
- `eject` и/или `udisksctl`

---

## Архитектура

### Frontend (SPA)
- **Vanilla JavaScript** (без фреймворков)
- **Spectre.css** + кастомный `dark.css`
- Одна HTML-страница, обновление по SSE/polling

### Backend
- **FastAPI + Uvicorn**
- Управление задачами: `asyncio` + subprocess
- Выполнение пайплайна: `dd → verify → growpart → resize2fs → eject`

---

## Быстрый старт

```bash
git clone https://github.com/maksidze/Janus.git
cd Janus

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt

sudo python -m uvicorn main:app
```

Откройте: `http://localhost:8000`

> Образы кладутся в `./images` (поддерживаются `.img`, `.img.xz`, `.img.gz`).

---

## Запуск через systemd (root) — да, небезопасно, мне всё равно

Нужно запускать от **root**, чтобы иметь возможность писать в `/dev/*` (и выполнять `growpart/resize2fs/eject` без плясок с правами). Это **небезопасно**, но предполагается отдельная “рабочая” машина/raspberry под эту задачу.

### Вариант 1: systemd unit (рекомендуется)

`/etc/systemd/system/janus.service`

```ini
[Unit]
Description=Janus SD Card Writer (root mode)
After=network.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/opt/janus
ExecStart=/opt/janus/venv/bin/python -m uvicorn main:app
Restart=always
RestartSec=2

# Если UI/сервису нужны внешние утилиты — явно задаём PATH (по желанию)
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

[Install]
WantedBy=multi-user.target
```

### Установка в /opt/janus (пример)

```bash
sudo mkdir -p /opt/janus
sudo chown -R root:root /opt/janus

# Клонируем (или копируем проект) в /opt/janus
sudo git clone https://github.com/maksidze/Janus.git /opt/janus

# Виртуальное окружение
sudo python3 -m venv /opt/janus/venv
sudo /opt/janus/venv/bin/pip install -r /opt/janus/requirements.txt
```

### Включение и запуск сервиса

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now janus.service

# Проверка статуса
sudo systemctl status janus.service

# Логи
sudo journalctl -u janus.service -f
```

---

## Как пользоваться

1. Откройте UI в браузере.
2. В верхней панели выберите **Image** из `./images`.
3. Включите нужные опции партии (Verify/Expand/Resize/Eject).
4. Выделите ячейки (клик / Shift+клик / “Select all connected”).
5. Нажмите **Start**.

Каждая ячейка покажет:

* устройство (`/dev/sdX`, размер, модель, serial)
* статус (готово / в процессе / done / failed / cancelled)
* прогресс, скорость, ETA
* текущий этап (write/verify/expand/resize)
* кнопки: Details (лог), Cancel, Retry, Eject

---

## Edit Layout (настройка сетки)

Режим редактирования позволяет задать:

* `rows × cols`
* размер ячеек (`compact / normal`)
* для каждой ячейки:

  * `label`
  * `portId` (по-path/topology)
  * `usbHint` (2.0 / 3.0 / unknown)
  * `enabled`

Конфиг сохраняется на сервер:

* `PUT /api/layout`
* импорт/экспорт через `/api/layout/import` и `/api/layout/export`

---

## Хранение данных

```
./data/layout.json          — конфигурация сетки
./images/                   — директория с образами
./config.json               — глобальные настройки (пути, дефолты)
```

---

## Layout schema (пример)

```json
{
  "schemaVersion": 1,
  "rows": 2,
  "cols": 4,
  "cellSize": "normal",
  "cells": [
    {
      "cellId": "A1",
      "label": "Reader 1",
      "portId": "/dev/disk/by-path/pci-0000:00:14.0-usb-0:1:1.0",
      "usbHint": "3.0",
      "enabled": true
    }
  ]
}
```

---

## REST API

### Layout

* `GET  /api/layout` — текущий layout
* `PUT  /api/layout` — сохранить layout (JSON)
* `POST /api/layout/import` — загрузить layout.json (multipart)
* `GET  /api/layout/export` — скачать layout.json

### Inventory

* `GET /api/ports` — доступные порты (layout + найденные)
* `GET /api/drives?removable=1` — подключённые накопители
* `GET /api/images` — образы из `./images`

### Jobs / Batch

* `POST /api/batch/start` — старт партии
  **body:** `{imageName, cellIds, options, concurrency}`
* `GET  /api/jobs` — список jobs (polling)
* `GET  /api/jobs/{jobId}` — детали + лог
* `POST /api/jobs/{jobId}/cancel` — отмена (убить процесс)
* `POST /api/jobs/{jobId}/retry` — повтор
* `POST /api/cells/{cellId}/eject` — извлечение / unmount

### Events (SSE)

* `GET /api/events`

События:

```
event: job_update
data: {"jobId":"...","state":"...","progress":0.5,...}

event: job_log
data: {"jobId":"...","lines":["...","..."]}

event: drive_change
data: {"drives":[{...}]}
```

Если SSE недоступен — фронтенд опрашивает `/api/jobs` раз в 1–2 секунды.

---

## Пайплайн записи (job)

1. **write_image**

   * `dd if=<image> of=/dev/sdX bs=4M status=progress`
   * парсинг stderr для прогресса
   * отмена через kill_event (проверка каждые ~200мс)
2. **verify_image** (если включено)

   * SHA256 устройства vs SHA256 образа
3. **expand_partition** (если включено)

   * `growpart /dev/sdX 1` (ошибки — warn, без жёсткого падения)
4. **resize_filesystem** (если включено)

   * `resize2fs /dev/sdX1` (ошибки — warn)
5. **eject** (если включено)

   * `eject /dev/sdX` или `udisksctl power-off -b /dev/sdX`

Параллельность:

* очередь + лимит `concurrency`
* каждый job — отдельный asyncio Task + OS-thread

---

## Файловая структура проекта

```
Janus/
├── backend/
│   ├── main.py
│   ├── app.py
│   ├── job_manager.py
│   ├── flash_runner.py
│   ├── inventory.py
│   ├── layout_manager.py
│   └── config.py
├── public/
│   ├── index.html
│   ├── app.js
│   ├── style.css
│   ├── dark.css
│   └── spectre.css
├── data/
│   ├── layout.json
│   └── ...
├── images/
│   ├── *.img
│   ├── *.img.xz
│   └── *.img.gz
├── config.json
├── requirements.txt
└── README.md
```

---

## Важные замечания по безопасности

* Это инструмент, который пишет в `/dev/sdX`. Ошибка выбора устройства = гарантированный плохой день.
* Root-режим делает это **ещё веселее** (то есть опаснее).
* Рекомендуется держать включённым `Only removable` и не отключать проверки.
* Если устройство смонтировано — корректно размонтируйте (или используйте eject endpoint).

---

## Roadmap (когда-нибудь)

* Улучшение UX в “Edit layout”
* Более подробные графики скорости/ETA и история запусков
