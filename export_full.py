
"""Full amoCRM export script with progress reporting."""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd
import numpy as _np

import config
from amo_client import AmoTokenStore


logger = logging.getLogger(__name__)

try:  # optional rich progress
    if config.USE_RICH_PROGRESS:
        from rich.console import Console
        from rich.progress import (
            BarColumn,
            MofNCompleteColumn,
            Progress,
            TextColumn,
            TimeElapsedColumn,
            TimeRemainingColumn,
        )
        from rich.status import Status

        RICH_AVAILABLE = True
    else:  # pragma: no cover - configuration gate
        RICH_AVAILABLE = False
        Console = None  # type: ignore[assignment]
except ImportError:  # pragma: no cover - optional dependency
    RICH_AVAILABLE = False
    Console = None  # type: ignore[assignment]
    Progress = None  # type: ignore[assignment]
    Status = None  # type: ignore[assignment]

try:  # optional tqdm fallback
    from tqdm import tqdm

    TQDM_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    TQDM_AVAILABLE = False
    tqdm = None  # type: ignore[assignment]


def format_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


class StageProgress:
    """Render progress for a single export stage."""

    def __init__(self, name: str, expected_total: Optional[int]) -> None:
        self.name = name
        self.expected_total = expected_total
        self._start: float = 0.0
        self._status: Optional[Any] = None
        self._console: Optional[Any] = None
        self._progress: Optional[Any] = None
        self._task_id: Optional[int] = None
        self._tqdm: Optional[Any] = None
        self._finalized = False

    def __enter__(self) -> "StageProgress":
        self._start = time.perf_counter()
        if RICH_AVAILABLE:
            self._console = Console()
            self._status = self._console.status(f"{self.name}…")
            self._status.start()
            columns = [
                TextColumn("{task.description}", justify="left"),
                BarColumn(),
                MofNCompleteColumn(),
                TextColumn("стр. {task.fields[page]}", justify="right"),
                TimeElapsedColumn(),
                TimeRemainingColumn(),
            ]
            self._progress = Progress(*columns, console=self._console, transient=True)
            self._progress.start()
            total = self.expected_total or 0
            self._task_id = self._progress.add_task(self.name, total=total, page=0)
        elif TQDM_AVAILABLE:
            total = self.expected_total or 0
            self._tqdm = tqdm(total=total, desc=self.name, unit="items", leave=False)
        else:
            logger.info("%s…", self.name)
        return self

    def update(self, page: int, items_accumulated: int, last_chunk_size: int) -> None:
        if self._progress is not None and self._task_id is not None:
            task = self._progress.tasks[self._task_id]
            current_total = task.total or 0
            if items_accumulated > current_total:
                self._progress.update(self._task_id, total=items_accumulated)
            completed = min(items_accumulated, self._progress.tasks[self._task_id].total or items_accumulated)
            self._progress.update(
                self._task_id,
                completed=completed,
                description=f"{self.name} · страница {page}",
                page=page,
            )
            if self._status:
                task_total = self._progress.tasks[self._task_id].total
                expected = task_total if task_total else self.expected_total or "?"
                if isinstance(expected, float) and expected.is_integer():
                    expected = int(expected)
                self._status.update(f"{self.name}: страница {page}, {items_accumulated}/{expected}")
        elif self._tqdm is not None:
            bar = self._tqdm
            if items_accumulated > bar.total:
                bar.total = items_accumulated
            bar.set_postfix(page=page)
            delta = items_accumulated - bar.n
            if delta > 0:
                bar.update(delta)
        else:
            print(f"{self.name}: {items_accumulated} (стр. {page})")

    def finalize(self, final_count: int) -> Tuple[float, str]:
        duration = time.perf_counter() - self._start
        expected = self.expected_total or final_count
        if final_count > expected:
            expected = final_count
        summary = f"✅ {self.name}: {final_count}/{expected} за {format_duration(duration)}"
        print(summary)
        logger.info(summary)
        self._finalized = True
        return duration, summary

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._progress is not None:
            self._progress.stop()
        if self._status is not None:
            self._status.stop()
        if self._tqdm is not None:
            self._tqdm.close()
        if exc_type:
            logger.exception("Stage %s failed", self.name)


def api_get_paged(
    client: AmoTokenStore,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    limit: Optional[int] = None,
    cap: Optional[int] = None,
    on_page: Optional[Callable[[int, int, int], None]] = None,
) -> List[Dict[str, Any]]:
    """Proxy to :meth:`AmoTokenStore.get_paged_cap` for backward compatibility."""

    return client.get_paged_cap(
        path,
        params=params,
        limit=limit or config.REQUEST_LIMIT,
        cap=cap,
        on_page=on_page,
    )



# --- карты кастомных полей ---
def fetch_custom_fields_maps(client: AmoTokenStore) -> Dict[str, Dict[int, str]]:
    result: Dict[str, Dict[int, str]] = {}
    for entity in ("leads", "contacts", "companies"):
        try:
            items = api_get_paged(
                client,
                f"/{entity}/custom_fields",
                limit=config.REQUEST_LIMIT,
            )
            result[entity] = {
                int(it["id"]): str(it.get("name") or it.get("code") or it["id"])
                for it in items if "id" in it
            }
        except Exception as e:
            print(f"[WARN] custom_fields для {entity}: {e}")
            result[entity] = {}
    return result

def flatten_custom_fields_row(row: Dict[str, Any], cf_map: Dict[int, str]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    cf_values = row.get("custom_fields_values") or []
    for cf in cf_values:
        fid = cf.get("field_id")
        name = cf_map.get(int(fid)) if fid is not None else None
        if not name:
            name = f"Пользовательское поле {fid}" if fid is not None else "Пользовательское поле"
        values = cf.get("values") or []
        vals = []
        for v in values:
            if isinstance(v, dict):
                if "value" in v:
                    vals.append(v["value"])
                else:
                    for k in ("enum_id","enum_code","code","subtype","file_id","phone"):
                        if k in v:
                            vals.append(v[k])
        result[name] = "; ".join(map(lambda x: str(x) if x is not None else "", vals)) if vals else None
    return result

def normalize_entities(entity: str, items: List[Dict[str, Any]], cf_map: Dict[int, str]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for it in items:
        base: Dict[str, Any] = {
            "id": it.get("id"),
            "name": it.get("name"),
            "price": it.get("price"),
            "status_id": it.get("status_id"),
            "pipeline_id": it.get("pipeline_id"),
            "responsible_user_id": it.get("responsible_user_id"),
            "account_id": it.get("account_id"),
            "created_at": it.get("created_at"),
            "updated_at": it.get("updated_at"),
            "closed_at": it.get("closed_at"),
            "is_deleted": it.get("is_deleted"),
            "loss_reason_id": it.get("loss_reason_id"),
        }
        if entity in ("contacts", "companies"):
            base["first_name"]  = it.get("first_name")
            base["last_name"]   = it.get("last_name")
            base["middle_name"] = it.get("middle_name")
        emb = it.get("_embedded")
        if isinstance(emb, dict):
            t = emb.get("tags")
            if isinstance(t, list):
                base["tags"] = ", ".join([str(x.get("name", "")) for x in t])
        rows.append({**base, **flatten_custom_fields_row(it, cf_map)})
    return pd.DataFrame(rows)



# === ДОБАВЛЕНО: статусы/причины потерь и человекочитаемые даты ===
import pandas as pd

def fetch_pipelines_map(client: AmoTokenStore) -> pd.DataFrame:
    """Возвращает маппинг статусов: status_id -> (status_name, pipeline_id, pipeline_name)."""
    items = api_get_paged(client, "/leads/pipelines", limit=config.REQUEST_LIMIT)
    rows = []
    for p in items:
        pipeline_id = p.get("id")
        pipeline_name = p.get("name")
        statuses = (p.get("_embedded") or {}).get("statuses", [])
        for s in statuses:
            rows.append({
                "status_id": s.get("id"),
                "status_name": s.get("name"),
                "pipeline_id": pipeline_id,
                "pipeline_name": pipeline_name,
            })
    return pd.DataFrame(rows)

def fetch_loss_reasons_map(client: AmoTokenStore) -> pd.DataFrame:
    """Возвращает маппинг причин потерь: loss_reason_id -> loss_reason_name."""
    items = api_get_paged(client, "/leads/loss_reasons", limit=config.REQUEST_LIMIT)
    return pd.DataFrame([
        {"loss_reason_id": x.get("id"), "loss_reason_name": x.get("name")}
        for x in items
    ])

def add_datetime_columns_ru(df: pd.DataFrame) -> pd.DataFrame:
    """Из *unix-колонок* создаёт человекопонятные даты/время (UTC).
    Ожидаемые входные (после rename_columns_ru): 'Создано (unix)', 'Обновлено (unix)', 'Закрыто (unix)'"""
    out = df.copy()
    def to_dt(x):
        try:
            return pd.to_datetime(int(x), unit="s")
        except Exception:
            return pd.NaT
    if "Создано (unix)" in out.columns:
        out["Создано (дата/время)"] = out["Создано (unix)"].apply(to_dt)
    if "Обновлено (unix)" in out.columns:
        out["Обновлено (дата/время)"] = out["Обновлено (unix)"].apply(to_dt)
    if "Закрыто (unix)" in out.columns:
        out["Закрыто (дата/время)"] = out["Закрыто (unix)"].apply(to_dt)
    return out

def enrich_leads_with_refs(df_leads_ru: pd.DataFrame, client: AmoTokenStore) -> pd.DataFrame:
    """Добавляет человекочитаемые 'Статус', 'Воронка', 'Причина потери' по ID-колонкам.
    На входе — уже переименованные русские поля (после rename_columns_ru)."""
    out = df_leads_ru.copy()
    # статусы / воронки
    try:
        st = fetch_pipelines_map(client)
    except Exception:
        st = pd.DataFrame()
    # причины потери
    try:
        lr = fetch_loss_reasons_map(client)
    except Exception:
        lr = pd.DataFrame()

    # подготовим ключи под русские названия
    if "ID статуса" in out.columns and not st.empty:
        out = out.merge(st[["status_id", "status_name", "pipeline_name"]],
                        left_on="ID статуса", right_on="status_id", how="left")
        out = out.drop(columns=["status_id"], errors="ignore")
        out = out.rename(columns={"status_name": "Статус", "pipeline_name": "Воронка"})

    if "ID причины потери" in out.columns and not lr.empty:
        out = out.merge(lr, left_on="ID причины потери", right_on="loss_reason_id", how="left")
        out = out.drop(columns=["loss_reason_id"], errors="ignore")
        out = out.rename(columns={"loss_reason_name": "Причина потери"})

    return out



# --- переименование колонок на русский ---
base_map_ru: Dict[str, str] = {
    "id": "ID",
    "name": "Название",
    "price": "Сумма сделки",
    "status_id": "ID статуса",
    "pipeline_id": "ID воронки",
    "responsible_user_id": "ID ответственного",
    "account_id": "ID аккаунта",
    "created_at": "Создано (unix)",
    "updated_at": "Обновлено (unix)",
    "closed_at": "Закрыто (unix)",
    "is_deleted": "Удалено",
    "loss_reason_id": "ID причины потери",
    "tags": "Теги",
    "first_name": "Имя",
    "last_name": "Фамилия",
    "middle_name": "Отчество",
}

def rename_columns_ru(df: pd.DataFrame):
    if df is None or df.empty:
        return df, []
    rename_map = {c: base_map_ru.get(c, c) for c in df.columns}
    df2 = df.rename(columns=rename_map)
    return df2, [(k,v) for k,v in rename_map.items() if k != v]



# --- пользователи, события, заметки ---
def fetch_users_map(client: AmoTokenStore) -> Dict[int, str]:
    try:
        items = api_get_paged(client, "/users", limit=config.REQUEST_LIMIT)
        mp: Dict[int, str] = {}
        for u in items:
            uid = u.get("id")
            name = u.get("name") or u.get("email") or str(uid)
            if isinstance(uid, int):
                mp[uid] = str(name)
        return mp
    except Exception as e:
        print(f"[WARN] users: {e}")
        return {}

def fetch_events(client: AmoTokenStore, days_back: int = 0):
    types = ("incoming_chat_message","outgoing_chat_message","incoming_mail","outgoing_mail")
    out = []
    for t in types:
        params = {"filter[type]": t}
        if days_back and days_back > 0:
            since = int(time.time()) - days_back*24*3600
            params["filter[created_at][from]"] = since
        try:
            out.extend(
                api_get_paged(
                    client,
                    "/events",
                    params=params,
                    limit=config.REQUEST_LIMIT,
                )
            )
        except Exception as e:
            print(f"[WARN] events {t}: {e}")
    return out

def fetch_notes_for_leads(client: AmoTokenStore, days_back: int = 0):
    params = {}
    if days_back and days_back > 0:
        since = int(time.time()) - days_back*24*3600
        params["filter[created_at][from]"] = since
    try:
        return api_get_paged(
            client,
            "/leads/notes",
            params=params,
            limit=config.REQUEST_LIMIT,
        )
    except Exception as e:
        print(f"[WARN] notes: {e}")
        return []



# --- разбор строк событий/заметок в единую таблицу ---
def _first_dict(x):
    if isinstance(x, dict):
        return x
    if isinstance(x, list):
        for it in x:
            if isinstance(it, dict):
                return it
    return {}

def _safe_pick_text(*objs):
    keys = ("text","body","message","description")
    for obj in objs:
        if obj is None:
            continue
        if isinstance(obj, dict):
            for k in keys:
                if k in obj and obj[k]:
                    return str(obj[k])
        if isinstance(obj, str) and obj:
            return obj
    return None

def extract_note_row(note: Dict[str, Any], users_map: Dict[int, str]) -> Dict[str, Any]:
    created_at   = note.get("created_at")
    text         = note.get("text") or _safe_pick_text(_first_dict(note.get("params")))
    entity_id    = note.get("entity_id")
    entity_type  = (note.get("entity_type") or "").lower()
    user_id      = note.get("created_by")
    user_name    = users_map.get(int(user_id)) if isinstance(user_id, int) else None
    return {
        "Тип": "Комментарий (сотр.)",
        "Дата/время (unix)": created_at,
        "Дата/время": pd.to_datetime(created_at, unit="s", errors="coerce"),
        "ID сделки": entity_id if entity_type in ("lead","leads") else None,
        "ID контакта": entity_id if entity_type in ("contact","contacts") else None,
        "ID компании": entity_id if entity_type in ("company","companies") else None,
        "Автор": user_name,
        "Текст": text
    }

def extract_event_row(ev: Dict[str, Any], users_map: Dict[int, str]) -> Dict[str, Any]:
    t = ev.get("type")
    created_at = ev.get("created_at")
    created_by = ev.get("created_by")
    user_name  = users_map.get(int(created_by)) if isinstance(created_by, int) else None

    lead_id, contact_id, company_id = None, None, None
    emb = ev.get("_embedded", {})
    if isinstance(emb, dict):
        for k in ("leads","contacts","companies"):
            arr = emb.get(k)
            if isinstance(arr, list) and arr:
                d = _first_dict(arr)
                if k == "leads":
                    lead_id = d.get("id")
                elif k == "contacts":
                    contact_id = d.get("id")
                elif k == "companies":
                    company_id = d.get("id")

    payload      = ev.get("payload") or {}
    extra        = payload.get("extra") or {}
    chat_text    = _safe_pick_text(payload, extra, payload.get("new"))
    email_subj   = extra.get("subject") if isinstance(extra, dict) else None
    email_text   = _safe_pick_text(extra, payload)

    return {
        "Тип": t,
        "Дата/время (unix)": created_at,
        "Дата/время": pd.to_datetime(created_at, unit="s", errors="coerce"),
        "ID сделки": lead_id,
        "ID контакта": contact_id,
        "ID компании": company_id,
        "Автор": user_name,
        "Тема письма": email_subj,
        "Текст письма": email_text,
        "Текст чата": chat_text,
    }

def build_conversation_sheet(users_map: Dict[int, str],
                             notes_list: List[Dict[str, Any]],
                             events_list: List[Dict[str, Any]]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for n in notes_list:
        rows.append(extract_note_row(n, users_map))
    for e in events_list:
        rows.append(extract_event_row(e, users_map))
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Дата/время", na_position="last").reset_index(drop=True)
    return df


# --- функции для Excel (с сохранением существующих листов, если они не перезаписываются) ---
def safe_sheet_name(name: str) -> str:
    bad = '[]:*?/\\'
    out = ''.join('_' if ch in bad else ch for ch in str(name))
    out = out.strip().strip("'")
    return out[:31] or "Sheet1"

def auto_column_widths(writer, df: pd.DataFrame, sheet_name: str,
                       min_w: int = 8, max_w: int = 60) -> None:
    ws = writer.sheets[sheet_name]
    for i, col in enumerate(df.columns):
        series = df[col].astype(str)
        max_len = max([len(str(col))] + [len(x) for x in series[:800]])
        ws.set_column(i, i, max(min(max_len + 2, max_w), min_w))

def export_to_excel(dfs: Dict[str, pd.DataFrame], out_path: str) -> None:
    """
    Сохраняет dfs в XLSX. Если out_path уже существует, сохраняет ЛЮБЫЕ
    существующие листы, которых нет в dfs (например, 'Задачи').
    Листы с совпадающими именами в dfs перезаписываются новыми данными.
    """
    # 1) Подготовим карту safe_name -> (orig_name, df)
    new_items = {}
    for name, df in dfs.items():
        safe = safe_sheet_name(name)
        new_items[safe] = (name, df)

    # 2) Если файл существует — подтянем старые листы, которых нет в новых
    preserved = {}
    if os.path.exists(out_path):
        try:
            xls = pd.ExcelFile(out_path)
            for sh in xls.sheet_names:
                if sh not in new_items:  # сохраняем
                    preserved[sh] = pd.read_excel(out_path, sheet_name=sh)
        except Exception as _e:
            print(f"[WARN] Не удалось прочитать существующий файл для сохранения листов: {_e}")

    # 3) Пишем новый файл: сначала новые листы, затем добавляем сохранённые
    used = set()
    with pd.ExcelWriter(out_path, engine="xlsxwriter") as writer:
        # новые
        for safe, (_, frame) in new_items.items():
            name = safe
            if name in used:
                base = name[:28]; n = 2
                while f"{base} ({n})" in used:
                    n += 1
                name = f"{base} ({n})"
            used.add(name)
            frame.to_excel(writer, sheet_name=name, index=False)
            try:
                auto_column_widths(writer, frame, name)
            except Exception:
                pass

        # сохранённые
        for safe, frame in preserved.items():
            name = safe
            if name in used:
                base = name[:28]; n = 2
                while f"{base} ({n})" in used:
                    n += 1
                name = f"{base} ({n})"
            used.add(name)
            frame.to_excel(writer, sheet_name=name, index=False)
            try:
                auto_column_widths(writer, frame, name)
            except Exception:
                pass

    print(f"[OK] Сохранено {len(used)} лист(ов) → {out_path}")


# === ДОБАВЛЕНО: телефон клиента (главный контакт сделки) ===
import re
from typing import Dict, List, Optional

def _chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i+size]

def _clean_phone(x: Optional[str]) -> Optional[str]:
    if not x:
        return x
    s = re.sub(r"[^\d+]", "", str(x))
    if len(s) == 11 and s.startswith("8"):
        s = "+7" + s[1:]
    if len(s) == 10 and not s.startswith("+"):
        s = "+7" + s
    return s

def get_main_contact_ids_for_leads(
    client: AmoTokenStore,
    lead_ids: List[int],
) -> Dict[int, int]:
    """
    Возвращает {lead_id: main_contact_id} для переданных ID сделок.
    Использует массивный фильтр filter[id][] (надежно для amo v4) и ручную пагинацию.
    """
    url = f"{client.base_url}/api/v4/leads"
    out: Dict[int, int] = {}

    for batch in _chunked(lead_ids, 200):
        page = 1
        while True:
            params = [("limit", config.REQUEST_LIMIT), ("page", page), ("with", "contacts")]
            for lid in batch:
                params.append(("filter[id][]", int(lid)))
            r = client.session.get(url, params=params, timeout=config.REQUEST_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            leads = (data.get("_embedded") or {}).get("leads", []) or []
            if not leads:
                break
            for lead in leads:
                lid = lead.get("id")
                if lid is None:
                    continue
                contacts = (lead.get("_embedded") or {}).get("contacts", []) or []
                if not contacts:
                    continue
                main = next((c for c in contacts if c.get("is_main")), contacts[0])
                if main and "id" in main:
                    out[int(lid)] = int(main["id"])
            if len(leads) < config.REQUEST_LIMIT:
                break
            page += 1
    print(f"[INFO] Найдены главные контакты для {len(out)} сделок из {len(set(lead_ids))}")
    return out

def get_phones_for_contacts(
    client: AmoTokenStore,
    contact_ids: List[int],
) -> Dict[int, str]:
    """
    Возвращает {contact_id: phone}.
    Запрос делает через массивные параметры filter[id][], чтобы amoCRM точно отдал нужные контакты.
    """
    url = f"{client.base_url}/api/v4/contacts"
    pref = ["WORK", "MOB", "WORKDD", "OTHER"]
    out: Dict[int, str] = {}

    for batch in _chunked(contact_ids, 200):
        page = 1
        while True:
            params = [("limit", config.REQUEST_LIMIT), ("page", page)]
            for cid in batch:
                params.append(("filter[id][]", cid))
            r = client.session.get(url, params=params, timeout=config.REQUEST_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            contacts = (data.get("_embedded") or {}).get("contacts", []) or []
            if not contacts:
                break
            for c in contacts:
                cid = c.get("id")
                if cid is None:
                    continue
                phone = None
                cfv = c.get("custom_fields_values") or []
                for field in cfv:
                    if field.get("field_code") == "PHONE" or str(field.get("field_name", "")).strip().lower() == "телефон":
                        values = field.get("values") or []
                        chosen = None
                        for code in pref:
                            for v in values:
                                if v.get("enum_code") == code and v.get("value"):
                                    chosen = v["value"]
                                    break
                            if chosen:
                                break
                        if not chosen and values:
                            chosen = values[0].get("value")
                        phone = _clean_phone(chosen)
                        break
                if phone:
                    out[int(cid)] = phone
            if len(contacts) < config.REQUEST_LIMIT:
                break
            page += 1
    print(f"[INFO] Получено телефонов по контактам: {len(out)} / {len(set(contact_ids))}")
    return out

def attach_client_phone_to_deals(
                                 client: AmoTokenStore,
                                 df_deals: pd.DataFrame,
                                 id_col: str = "ID",
                                 out_col: str = "Телефон клиента") -> pd.DataFrame:
    """Возвращает КОПИЮ df_deals с добавленной колонкой out_col. Ничего лишнего не трогаем."""
    if df_deals is None or df_deals.empty:
        return df_deals
    if id_col not in df_deals.columns:
        # не падаем — просто возвращаем как есть
        return df_deals
    lead_ids = (df_deals[id_col].dropna().astype(int).unique().tolist())
    if not lead_ids:
        if out_col not in df_deals.columns:
            df_out = df_deals.copy()
            df_out[out_col] = None
            return df_out
        return df_deals

    lead_to_contact = get_main_contact_ids_for_leads(client, lead_ids)
    if not lead_to_contact:
        return df_deals

    contact_ids = sorted(set(lead_to_contact.values()))
    contact_to_phone = get_phones_for_contacts(client, contact_ids)
    lead_to_phone = {lid: contact_to_phone.get(cid) for lid, cid in lead_to_contact.items()}

    df_out = df_deals.copy()
    if out_col in df_out.columns:
        mask = df_out[out_col].isna() | (df_out[out_col].astype(str).str.strip() == "")
        df_out.loc[mask, out_col] = df_out.loc[mask, id_col].map(lead_to_phone)
    else:
        df_out[out_col] = df_out[id_col].map(lead_to_phone)
    print(f"[INFO] Смогли сопоставить телефоны для {sum(df_out[out_col].notna())} сделок из {len(df_out)}")
    return df_out

# === ДОРАБОТАНО: корректный перенос полей сделки на лист "Контакты" ===
# Ключевые изменения:
# 1) Перед маппингом полностью очищаем ранее существующие столбцы "Сделка: ...",
#    чтобы исключить ложные значения, попавшие туда на предыдущих шагах/запусках.
# 2) Жёсткая стыковка только по валидному телефону: строки без нормализованного телефона не маппим.
# 3) Если на один телефон есть несколько сделок — берём самую свежую по времени обновления/создания.
# 4) Учитываем возможные суффиксы столбцов после merge (например, Название_y).
import pandas as pd
import re

def _resolve_first_existing(df, candidates):
    for c in candidates:
        if c in df.columns:
            return c
    return None

def attach_deal_fields_to_contacts(df_deals: pd.DataFrame,
                                   df_contacts: pd.DataFrame,
                                   phone_cols_deals = ("Телефон клиента","Телефон","phone","Телефон контакта"),
                                   phone_cols_contacts = ("Телефон","phone","Телефон контакта","Телефон клиента"),
                                   fields_map = (("Сделка: Статус", ("Статус","status_name","Этап воронки")),
                                                 ("Сделка: Причина потери", ("Причина потери","loss_reason_name")),
                                                 ("Сделка: Название", ("Название_y","Название","name"))),
                                   normalizer = None) -> pd.DataFrame:
    """
    К df_contacts добавляет столбцы 'Сделка: ...' с данными из df_deals
    через сопоставление по телефону. Если на один телефон несколько сделок — берём последнюю по времени.
    """
    # Копии, чтобы не менять исходники
    deals = df_deals.copy()
    contacts = df_contacts.copy()
    out = contacts.copy()

    # 0) Очистка уже существующих "Сделка: ..." в контактах
    deal_cols_existing = [c for c in out.columns if c.startswith("Сделка: ")]
    if deal_cols_existing:
        out.drop(columns=deal_cols_existing, inplace=True, errors="ignore")

    # 1) Определяем столбцы телефонов
    phone_d = _resolve_first_existing(deals, phone_cols_deals)
    phone_c = _resolve_first_existing(contacts, phone_cols_contacts)
    if not phone_d or not phone_c:
        return out  # нет телефонов — нечего переносить

    # 2) Нормализация телефонов
    def _clean_phone(x):
        if normalizer:
            return normalizer(x)
        if pd.isna(x): return None
        s = re.sub(r"[^\d+]", "", str(x))
        if len(s) == 11 and s.startswith("8"):
            s = "+7" + s[1:]
        if len(s) == 10 and not s.startswith("+"):
            s = "+7" + s
        return s or None

    deals["_phone_norm"] = deals[phone_d].map(_clean_phone)
    contacts["_phone_norm"] = contacts[phone_c].map(_clean_phone)
    out["_phone_norm"] = contacts["_phone_norm"]

    # 3) Готовим мап по телефону из сделок: берем самую свежую
    time_col = _resolve_first_existing(deals, ("Обновлено (unix)","Создано (unix)","updated_at","created_at"))
    take_cols = ["_phone_norm"] + [src for (_dst, srcs) in fields_map for src in srcs if src in deals.columns]
    if time_col and time_col not in take_cols:
        take_cols.append(time_col)
    deals_valid = deals.dropna(subset=["_phone_norm"])
    if time_col and time_col in deals_valid.columns:
        deals_valid = deals_valid.sort_values(by=time_col, ascending=False)
    map_df = deals_valid[take_cols].drop_duplicates(subset=["_phone_norm"], keep="first")

    # 4) left-join по нормализованному телефону
    merged = out.reset_index(drop=True).merge(map_df, on="_phone_norm", how="left")

    # 5) Переносим поля
    for out_name, src_candidates in fields_map:
        src_name = _resolve_first_existing(merged, src_candidates)
        if not src_name:
            continue
        out[out_name] = merged[src_name].to_numpy()

    # 6) Чистим служебное поле
    if "_phone_norm" in out.columns:
        out.drop(columns=["_phone_norm"], inplace=True)

    return out


# === ДОБАВЛЕНО: выгрузка задач amoCRM на отдельный лист "Задачи" ===
import pandas as _pd
import time as _time

def _dt(ts):
    try:
        ts = int(ts)
        return _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(ts))
    except Exception:
        return None

def fetch_task_types_map(client: AmoTokenStore):
    """{task_type_id: task_type_name}"""
    try:
        url = f"{client.base_url}/api/v4/tasks/types"
        r = client.session.get(
            url,
            params={"limit": config.REQUEST_LIMIT},
            timeout=config.REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        items = (data.get("_embedded") or {}).get("task_types", []) or []
        return {int(x["id"]): x.get("name") for x in items if "id" in x}
    except Exception as e:
        print(f"[WARN] Не удалось получить типы задач: {e}")
        return {}

def normalize_tasks(items, task_types=None):
    rows = []
    tt = task_types or {}
    for t in (items or []):
        rid = t.get("responsible_user_id")
        ttid = t.get("task_type_id")
        rows.append({
            "ID задачи": t.get("id"),
            "Тип сущности": t.get("entity_type"),
            "ID сущности": t.get("entity_id"),
            "Текст задачи": t.get("text"),
            "Тип задачи ID": ttid,
            "Тип задачи": tt.get(int(ttid)) if ttid is not None and int(ttid) in tt else None,
            "Ответственный (ID)": rid,
            "Выполнена": t.get("is_completed"),
            "Срок (ts)": t.get("complete_till"),
            "Срок": _dt(t.get("complete_till")),
            "Создано (ts)": t.get("created_at"),
            "Создано": _dt(t.get("created_at")),
            "Обновлено (ts)": t.get("updated_at"),
            "Обновлено": _dt(t.get("updated_at")),
            "Длительность (сек)": t.get("duration"),
            "Результат": (t.get("result") or {}).get("text"),
        })
    df = _pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["Выполнена","Срок (ts)","Обновлено (ts)"], ascending=[True, True, False], ignore_index=True)
    return df

def attach_entity_name_to_tasks(df_tasks, dfs):
    if df_tasks is None or df_tasks.empty:
        return df_tasks
    out = df_tasks.copy()

    def _name_map(sheet, id_candidates=("ID","id"), name_candidates=("Название","Имя","Name","name")):
        if sheet not in dfs or dfs[sheet] is None or dfs[sheet].empty:
            return {}
        df = dfs[sheet]
        id_col = next((c for c in id_candidates if c in df.columns), None)
        nm_col = next((c for c in name_candidates if c in df.columns), None)
        if not id_col or not nm_col:
            return {}
        try:
            m = _pd.Series(df[nm_col].values, index=_pd.to_numeric(df[id_col], errors="coerce").astype("Int64")).to_dict()
        except Exception:
            m = _pd.Series(df[nm_col].values, index=df[id_col]).to_dict()
        return m

    lead_map = _name_map("Сделки", ("ID","id"), ("Название","name","Name"))
    contact_map = _name_map("Контакты", ("ID","id"), ("Название","Имя","name","Name"))
    company_map = _name_map("Компании", ("ID","id"), ("Название","name","Name"))

    names = []
    for et, eid in zip(out.get("Тип сущности", []), out.get("ID сущности", [])):
        nm = None
        if _pd.notna(eid):
            try:
                eid_int = int(eid)
            except Exception:
                eid_int = None
        else:
            eid_int = None

        if et == "leads" and eid_int is not None:
            nm = lead_map.get(eid_int)
        elif et == "contacts" and eid_int is not None:
            nm = contact_map.get(eid_int)
        elif et == "companies" and eid_int is not None:
            nm = company_map.get(eid_int)
        names.append(nm)
    out["Название сущности"] = names
    return out



# --- основная логика ---
def run_export(period_days: int = config.PERIOD_DAYS_DEFAULT) -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    overall_start = time.perf_counter()

    auth_backup = config.BASE_DIR / "auth_code_backup.txt"
    if not auth_backup.exists():
        auth_backup = None

    client = AmoTokenStore(
        tokens_path=config.TOKENS_FILE,
        request_timeout=config.REQUEST_TIMEOUT,
        request_limit=config.REQUEST_LIMIT,
        auth_code_backup=auth_backup,
    )

    cf_maps = fetch_custom_fields_maps(client)

    dfs: Dict[str, pd.DataFrame] = {}
    sample_cap = config.SAMPLE_LIMIT or None

    if config.DOWNLOAD_LEADS:
        with StageProgress("Leads", sample_cap) as stage:
            ts_from = int(time.time() - period_days * 24 * 3600)
            leads_params = {"filter[updated_at][from]": ts_from}
            leads_raw = api_get_paged(
                client,
                "/leads",
                params=leads_params,
                cap=sample_cap,
                on_page=stage.update,
            )
            df_leads = normalize_entities("leads", leads_raw, cf_maps.get("leads", {}))
            df_leads, _ = rename_columns_ru(df_leads)
            df_leads = enrich_leads_with_refs(df_leads, client)
            df_leads = add_datetime_columns_ru(df_leads)
            dfs["Сделки"] = df_leads
            logger.info("[OK] Сделки: %s", len(df_leads))
            stage.finalize(len(df_leads))

    if config.DOWNLOAD_CONTACTS:
        with StageProgress("Contacts", sample_cap) as stage:
            contacts_raw = api_get_paged(
                client,
                "/contacts",
                cap=sample_cap,
                on_page=stage.update,
            )
            df_contacts = normalize_entities("contacts", contacts_raw, cf_maps.get("contacts", {}))
            df_contacts, _ = rename_columns_ru(df_contacts)
            dfs["Контакты"] = df_contacts
            logger.info("[OK] Контакты: %s", len(df_contacts))
            stage.finalize(len(df_contacts))

    if config.DOWNLOAD_COMPANIES:
        with StageProgress("Companies", sample_cap) as stage:
            companies_raw = api_get_paged(
                client,
                "/companies",
                cap=sample_cap,
                on_page=stage.update,
            )
            df_companies = normalize_entities("companies", companies_raw, cf_maps.get("companies", {}))
            df_companies, _ = rename_columns_ru(df_companies)
            dfs["Компании"] = df_companies
            logger.info("[OK] Компании: %s", len(df_companies))
            stage.finalize(len(df_companies))

    if config.DOWNLOAD_COMMENTS:
        users_map = fetch_users_map(client)
        notes = fetch_notes_for_leads(client, days_back=config.DAYS_BACK_NOTES)
        events = fetch_events(client, days_back=config.DAYS_BACK_EVENTS)
        df_conv = build_conversation_sheet(users_map, notes, events)
        if not df_conv.empty:
            dfs["Комментарии_Сотрудников"] = df_conv
            logger.info("[OK] Комментарии/события: %s", len(df_conv))
        else:
            logger.info("[INFO] Комментарии/события: пусто")

    if not dfs:
        raise RuntimeError("Нет данных для выгрузки. Проверьте download_* и доступ по API.")

    try:
        if "Сделки" in dfs and not dfs["Сделки"].empty:
            dfs["Сделки"] = attach_client_phone_to_deals(client, dfs["Сделки"], id_col="ID", out_col="Телефон клиента")
            logger.info("[OK] Лист 'Сделки': добавлена колонка 'Телефон клиента'")
        else:
            logger.info("[INFO] Лист 'Сделки' не найден или пуст — пропускаю добавление телефона")
    except Exception as exc:
        logger.warning("Не удалось добавить телефон клиента: %s", exc)

    try:
        if "Сделки" in dfs and "Контакты" in dfs and not dfs["Сделки"].empty and not dfs["Контакты"].empty:
            dfs["Контакты"] = attach_deal_fields_to_contacts(dfs["Сделки"], dfs["Контакты"])
            contacts_df = dfs["Контакты"]
            cols_candidates = [
                "Сделка: Статус",
                "Сделка: Причина потери",
                "Сделка: Название",
                "Статус",
                "Причина потери",
                "Название",
            ]
            present_cols = [c for c in cols_candidates if c in contacts_df.columns]
            filled_cnt = contacts_df[present_cols].notna().any(axis=1).sum() if present_cols else 0
            logger.info(
                "[OK] Лист 'Контакты': добавлены поля сделки по телефону (столбцы: %s, строк с данными: %s)",
                present_cols,
                filled_cnt,
            )
        else:
            logger.info("[INFO] Пропустил маппинг Сделки→Контакты: один из листов пуст/отсутствует")
    except Exception as exc:
        logger.warning("Не удалось дополнить 'Контакты' полями сделки: %s", exc)

    try:
        with StageProgress("Tasks", sample_cap) as stage:
            ts_from = int(_time.time() - 360 * 24 * 3600)
            task_types = fetch_task_types_map(client)
            tasks_params = {"filter[updated_at][from]": ts_from}
            tasks_raw = api_get_paged(
                client,
                "/tasks",
                params=tasks_params,
                cap=sample_cap,
                on_page=stage.update,
            )
            df_tasks = normalize_tasks(tasks_raw, task_types=task_types)

            if "Тип задачи" in df_tasks.columns and df_tasks["Тип задачи"].isna().all():
                _guess = {1: "Звонок", 2: "Встреча", 3: "Письмо"}
                if "Тип задачи ID" in df_tasks.columns:
                    df_tasks["Тип задачи"] = df_tasks["Тип задачи ID"].map(_guess).fillna(df_tasks["Тип задачи"])

            try:
                _shift = 6 * 3600
                if "Срок (ts)" in df_tasks.columns:
                    df_tasks["Срок (Алматы)"] = _pd.to_datetime(
                        _pd.to_numeric(df_tasks["Срок (ts)"], errors="coerce") + _shift,
                        unit="s",
                        errors="coerce",
                    )
                if "Создано (ts)" in df_tasks.columns:
                    df_tasks["Создано (Алматы)"] = _pd.to_datetime(
                        _pd.to_numeric(df_tasks["Создано (ts)"], errors="coerce") + _shift,
                        unit="s",
                        errors="coerce",
                    )
                if "Обновлено (ts)" in df_tasks.columns:
                    df_tasks["Обновлено (Алматы)"] = _pd.to_datetime(
                        _pd.to_numeric(df_tasks["Обновлено (ts)"], errors="coerce") + _shift,
                        unit="s",
                        errors="coerce",
                    )
            except Exception as exc3:
                logger.warning("Не удалось сконвертировать времена задач: %s", exc3)

            try:
                _now_ts = int(_time.time())
                _due = _pd.to_numeric(df_tasks.get("Срок (ts)"), errors="coerce")
                _open = ~df_tasks.get("Выполнена", _pd.Series([False] * len(df_tasks))).astype(bool)
                df_tasks["Просрочена?"] = _open & _due.notna() & (_due < _now_ts)
                df_tasks["Просрочено (часы)"] = _np.where(
                    df_tasks["Просрочена?"],
                    (_now_ts - _due) / 3600,
                    _np.nan,
                ).round(1)
                df_tasks["Осталось (часы)"] = _np.where(
                    _open & _due.notna() & (_due >= _now_ts),
                    (_due - _now_ts) / 3600,
                    _np.nan,
                ).round(1)
                df_tasks["Статус выполнения"] = _np.where(
                    df_tasks.get("Выполнена", False),
                    "Выполнена",
                    _np.where(df_tasks["Просрочена?"], "Просрочена", "В работе"),
                )
            except Exception as exc4:
                logger.warning("Не удалось проставить маркеры просрочки: %s", exc4)

            try:
                df_tasks = attach_entity_name_to_tasks(df_tasks, dfs)
            except Exception as exc2:
                logger.warning("Не удалось сопоставить названия сущностей для задач: %s", exc2)
            dfs["Задачи"] = df_tasks
            final_count = 0 if df_tasks is None else len(df_tasks)
            logger.info("[OK] Задачи: %s", final_count)
            stage.finalize(final_count)
    except Exception as exc:
        logger.warning("Не удалось выгрузить задачи: %s", exc)

    export_to_excel(dfs, config.OUT_XLSX)
    logger.info("[OK] Сохранено в %s", config.OUT_XLSX)

    overall_duration = time.perf_counter() - overall_start
    print(f"✅ Export total: {format_duration(overall_duration)}")


# Запуск экспорта
run_export()
