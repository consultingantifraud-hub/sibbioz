"""Full amoCRM export script using the AmoTokenStore helper."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
import requests

import config
from amo_client import AmoTokenStore


# === Helpers: CF maps, flatteners, phones/emails, dates ===

def to_df(items: Iterable[Dict[str, Any]]) -> pd.DataFrame:
    items = list(items)
    if not items:
        return pd.DataFrame()
    return pd.json_normalize(items, sep="__")


def make_cf_map(response: requests.Response) -> Dict[Any, str]:
    if response.status_code != 200:
        return {}
    items = response.json().get("_embedded", {}).get("custom_fields", [])
    mapping: Dict[Any, str] = {}
    for item in items:
        field_id = item.get("id") or item.get("field_id")
        name = item.get("name")
        if field_id is None or not name:
            continue
        try:
            mapping[int(field_id)] = str(name)
        except Exception:
            mapping[str(field_id)] = str(name)
    return mapping


def flatten_cfs(cf_list: Any, cf_map: Dict[Any, str]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    if not isinstance(cf_list, list):
        return result
    for item in cf_list:
        if not isinstance(item, dict):
            continue
        field_id = item.get("field_id") or item.get("id")
        try:
            ru_name = cf_map.get(int(field_id)) if field_id is not None else None
        except Exception:
            ru_name = cf_map.get(str(field_id))
        key = "CF:" + (ru_name if ru_name else (str(field_id) if field_id is not None else "unknown"))
        values = item.get("values")
        bucket: List[str] = []
        if isinstance(values, list):
            for value in values:
                if isinstance(value, dict):
                    val = value.get("value")
                    if isinstance(val, dict):
                        val = json.dumps(val, ensure_ascii=False)
                    bucket.append("" if val is None else str(val))
                else:
                    bucket.append(str(value))
        elif values is not None:
            bucket.append(str(values))
        result[key] = ", ".join(filter(None, bucket))
    return result


def tag_names(items: Any) -> str:
    if not isinstance(items, list):
        return ""
    return ", ".join(
        str(entry.get("name"))
        for entry in items
        if isinstance(entry, dict) and entry.get("name")
    )


def company_names_ids(items: Any) -> Tuple[str, str]:
    if not isinstance(items, list):
        return "", ""
    names: List[str] = []
    ids: List[str] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        if entry.get("name"):
            names.append(str(entry["name"]))
        if entry.get("id") is not None:
            ids.append(str(entry["id"]))
    return ", ".join(names), ", ".join(ids)


def epoch_to_local(timestamp: Any) -> Optional[str]:
    try:
        value = int(timestamp)
    except Exception:
        return None
    converted = datetime.fromtimestamp(value, tz=timezone.utc) + timedelta(hours=6)
    return converted.strftime("%Y-%m-%d %H:%M")


def extract_multival(items: Any, key: str, take: int = 3) -> List[str]:
    results: List[str] = []
    if not isinstance(items, list):
        return results
    for entry in items:
        if isinstance(entry, dict) and entry.get("field_code") == key:
            for value in entry.get("values") or []:
                if isinstance(value, dict):
                    val = value.get("value")
                else:
                    val = value
                if val:
                    results.append(str(val))
    return results[:take]


# === Reference data ===

def fetch_reference_data(store: AmoTokenStore) -> Dict[str, Dict[Any, str]]:
    cf_leads_r = store.request_with_refresh(
        lambda: requests.get(
            store.base + "/api/v4/leads/custom_fields",
            headers=store.headers,
            timeout=config.REQUEST_TIMEOUT,
        )
    )
    cf_conts_r = store.request_with_refresh(
        lambda: requests.get(
            store.base + "/api/v4/contacts/custom_fields",
            headers=store.headers,
            timeout=config.REQUEST_TIMEOUT,
        )
    )
    cf_comps_r = store.request_with_refresh(
        lambda: requests.get(
            store.base + "/api/v4/companies/custom_fields",
            headers=store.headers,
            timeout=config.REQUEST_TIMEOUT,
        )
    )

    pipelines = store.request_with_refresh(
        lambda: requests.get(
            store.base + "/api/v4/leads/pipelines",
            headers=store.headers,
            timeout=config.REQUEST_TIMEOUT,
        )
    ).json().get("_embedded", {}).get("pipelines", [])

    pipeline_to_name: Dict[Any, str] = {}
    status_to_name: Dict[Any, str] = {}
    for pipeline in pipelines:
        pid = pipeline.get("id")
        name = pipeline.get("name")
        if pid is not None:
            try:
                pipeline_to_name[int(pid)] = name
            except Exception:
                pipeline_to_name[str(pid)] = name
        for status in (pipeline.get("_embedded", {}) or {}).get("statuses", []):
            sid = status.get("id")
            sname = status.get("name")
            if sid is not None:
                try:
                    status_to_name[int(sid)] = sname
                except Exception:
                    status_to_name[str(sid)] = sname

    users = store.request_with_refresh(
        lambda: requests.get(
            store.base + "/api/v4/users",
            headers=store.headers,
            timeout=config.REQUEST_TIMEOUT,
        )
    ).json().get("_embedded", {}).get("users", [])
    user_to_name: Dict[Any, str] = {}
    for user in users:
        uid = user.get("id")
        if uid is None:
            continue
        try:
            key = int(uid)
        except Exception:
            key = str(uid)
        user_to_name[key] = user.get("name") or user.get("login") or str(key)

    loss_reasons = store.request_with_refresh(
        lambda: requests.get(
            store.base + "/api/v4/leads/loss_reasons",
            headers=store.headers,
            timeout=config.REQUEST_TIMEOUT,
        )
    ).json().get("_embedded", {}).get("loss_reasons", [])
    loss_to_name: Dict[Any, str] = {}
    for reason in loss_reasons:
        rid = reason.get("id")
        if rid is None:
            continue
        try:
            loss_to_name[int(rid)] = reason.get("name")
        except Exception:
            loss_to_name[str(rid)] = reason.get("name")

    task_types = store.request_with_refresh(
        lambda: requests.get(
            store.base + "/api/v4/tasks/types",
            headers=store.headers,
            timeout=config.REQUEST_TIMEOUT,
        )
    ).json().get("_embedded", {}).get("types", [])
    task_type_to_name: Dict[Any, str] = {}
    for task_type in task_types:
        tid = task_type.get("id")
        if tid is None:
            continue
        try:
            task_type_to_name[int(tid)] = task_type.get("name")
        except Exception:
            task_type_to_name[str(tid)] = task_type.get("name")

    print(
        "Dicts ready:",
        len(pipeline_to_name),
        "pipelines,",
        len(status_to_name),
        "statuses,",
        len(user_to_name),
        "users,",
        len(loss_to_name),
        "loss reasons,",
        len(task_type_to_name),
        "task types",
    )

    return {
        "cf_leads": make_cf_map(cf_leads_r),
        "cf_contacts": make_cf_map(cf_conts_r),
        "cf_companies": make_cf_map(cf_comps_r),
        "pipelines": pipeline_to_name,
        "statuses": status_to_name,
        "users": user_to_name,
        "loss_reasons": loss_to_name,
        "task_types": task_type_to_name,
    }


# === Export helpers ===

def enrich_leads(
    store: AmoTokenStore,
    leads: List[Dict[str, Any]],
    cf_map: Dict[Any, str],
) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for record in leads:
        base = record.copy()
        base["Ссылка"] = f"{store.base}/leads/detail/{record.get('id')}"
        base.update(flatten_cfs(record.get("custom_fields_values"), cf_map))
        embedded = record.get("_embedded", {}) or {}
        base["Теги"] = tag_names(embedded.get("tags"))
        names, ids = company_names_ids(embedded.get("companies"))
        base["Компании (названия)"] = names
        base["Компании (ID)"] = ids
        rows.append(base)
    return to_df(rows)


def enrich_contacts(
    store: AmoTokenStore,
    contacts: List[Dict[str, Any]],
    cf_map: Dict[Any, str],
) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for record in contacts:
        base = record.copy()
        base["Ссылка"] = f"{store.base}/contacts/detail/{record.get('id')}"
        base.update(flatten_cfs(record.get("custom_fields_values"), cf_map))
        embedded = record.get("_embedded", {}) or {}
        base["Теги"] = tag_names(embedded.get("tags"))
        phones = extract_multival(record.get("custom_fields_values"), "PHONE", take=3)
        emails = extract_multival(record.get("custom_fields_values"), "EMAIL", take=3)
        for idx, phone in enumerate(phones, 1):
            base[f"Телефон {idx}"] = phone
        for idx, email in enumerate(emails, 1):
            base[f"Email {idx}"] = email
        rows.append(base)
    return to_df(rows)


def enrich_companies(
    store: AmoTokenStore,
    companies: List[Dict[str, Any]],
    cf_map: Dict[Any, str],
) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for record in companies:
        base = record.copy()
        base["Ссылка"] = f"{store.base}/companies/detail/{record.get('id')}"
        base.update(flatten_cfs(record.get("custom_fields_values"), cf_map))
        embedded = record.get("_embedded", {}) or {}
        base["Теги"] = tag_names(embedded.get("tags"))
        phones = extract_multival(record.get("custom_fields_values"), "PHONE", take=3)
        emails = extract_multival(record.get("custom_fields_values"), "EMAIL", take=3)
        for idx, phone in enumerate(phones, 1):
            base[f"Телефон {idx}"] = phone
        for idx, email in enumerate(emails, 1):
            base[f"Email {idx}"] = email
        rows.append(base)
    return to_df(rows)


def enrich_tasks(
    store: AmoTokenStore,
    tasks: List[Dict[str, Any]],
    references: Dict[str, Dict[Any, str]],
) -> pd.DataFrame:
    task_type_to_name = references["task_types"]
    user_to_name = references["users"]
    rows: List[Dict[str, Any]] = []
    for task in tasks:
        base = task.copy()
        task_type_id = task.get("task_type_id")
        if task_type_id is not None:
            try:
                base["Тип задачи"] = task_type_to_name.get(int(task_type_id))
            except Exception:
                base["Тип задачи"] = task_type_to_name.get(str(task_type_id))
        responsible_id = task.get("responsible_user_id")
        if responsible_id is not None:
            try:
                base["Ответственный (задача)"] = user_to_name.get(int(responsible_id))
            except Exception:
                base["Ответственный (задача)"] = user_to_name.get(str(responsible_id))
        for column, nice in [
            ("created_at", "Создано"),
            ("updated_at", "Обновлено"),
            ("complete_till", "Срок"),
        ]:
            if task.get(column) is not None:
                base[nice] = epoch_to_local(task.get(column))
        rows.append(base)
    return to_df(rows)


# === Final shaping ===

def rename_base_ru(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    rename_map = {
        "id": "ID",
        "name": "Название",
        "price": "Бюджет",
        "status_id": "ID статуса",
        "pipeline_id": "ID воронки",
        "loss_reason_id": "ID причины потери",
        "is_deleted": "Удалено",
        "created_at": "Создано (unix)",
        "updated_at": "Обновлено (unix)",
        "closed_at": "Закрыто (unix)",
        "responsible_user_id": "ID ответственного",
        "group_id": "ID группы",
        "account_id": "ID аккаунта",
        "score": "Скоринг",
        "company_id": "ID компании",
        "closest_task_at": "Ближайшая задача (unix)",
    }
    return df.rename(columns={col: rename_map.get(col, col) for col in df.columns})


def map_ids_and_dates_leads(
    df: pd.DataFrame,
    references: Dict[str, Dict[Any, str]],
) -> pd.DataFrame:
    df = rename_base_ru(df)

    def get_col(*names: str) -> Optional[str]:
        for name in names:
            if name in df.columns:
                return name
        return None

    col_pipeline = get_col("ID воронки", "pipeline_id")
    col_status = get_col("ID статуса", "status_id")
    col_resp = get_col("ID ответственного", "responsible_user_id")
    col_loss = get_col("ID причины потери", "loss_reason_id")

    if col_pipeline:
        df["Воронка"] = df[col_pipeline].map(
            lambda x: references["pipelines"].get(int(x)) if pd.notna(x) else None
        )
    if col_status:
        df["Статус"] = df[col_status].map(
            lambda x: references["statuses"].get(int(x)) if pd.notna(x) else None
        )
    if col_resp:
        def _user(value: Any) -> Optional[str]:
            try:
                return references["users"].get(int(value)) if pd.notna(value) else None
            except Exception:
                return references["users"].get(str(value))

        df["Ответственный"] = df[col_resp].map(_user)
    if col_loss:
        def _loss(value: Any) -> Optional[str]:
            try:
                return references["loss_reasons"].get(int(value)) if pd.notna(value) else None
            except Exception:
                return references["loss_reasons"].get(str(value))

        df["Причина потери"] = df[col_loss].map(_loss)

    for raw_col, nice_col in [
        ("Создано (unix)", "Создано"),
        ("Обновлено (unix)", "Обновлено"),
        ("Закрыто (unix)", "Закрыто"),
        ("Ближайшая задача (unix)", "Ближайшая задача"),
    ]:
        if raw_col in df.columns:
            df[nice_col] = df[raw_col].map(epoch_to_local)

    front = [
        col
        for col in [
            "ID",
            "Название",
            "Ссылка",
            "Бюджет",
            "Воронка",
            "Статус",
            "Ответственный",
            "Причина потери",
            "Создано",
            "Обновлено",
            "Закрыто",
            "Теги",
            "Компании (названия)",
            "Компании (ID)",
        ]
        if col in df.columns
    ]
    other = [col for col in df.columns if col not in front]
    return df[front + other]


def map_dates_common(df: pd.DataFrame) -> pd.DataFrame:
    df = rename_base_ru(df)
    for raw_col, nice_col in [
        ("Создано (unix)", "Создано"),
        ("Обновлено (unix)", "Обновлено"),
        ("Ближайшая задача (unix)", "Ближайшая задача"),
    ]:
        if raw_col in df.columns:
            df[nice_col] = df[raw_col].map(epoch_to_local)
    return df


def tasks_final(store: AmoTokenStore, df: pd.DataFrame, references: Dict[str, Dict[Any, str]]) -> pd.DataFrame:
    df = df.copy()
    rename_map = {
        "id": "ID задачи",
        "entity_id": "ID сущности",
        "entity_type": "Тип сущности",
        "task_type_id": "ID типа задачи",
        "text": "Текст",
        "result__text": "Результат",
        "is_completed": "Завершена",
        "responsible_user_id": "ID ответственного",
    }
    for raw, nice in rename_map.items():
        if raw in df.columns:
            df.rename(columns={raw: nice}, inplace=True)

    if "ID ответственного" in df.columns:
        def _user(value: Any) -> Optional[str]:
            try:
                return references["users"].get(int(value)) if pd.notna(value) else None
            except Exception:
                return references["users"].get(str(value))

        df["Ответственный"] = df["ID ответственного"].map(_user)

    if "ID типа задачи" in df.columns:
        def _task_type(value: Any) -> Optional[str]:
            try:
                return references["task_types"].get(int(value)) if pd.notna(value) else None
            except Exception:
                return references["task_types"].get(str(value))

        df["Тип задачи"] = df["ID типа задачи"].map(_task_type)

    def build_link(row: pd.Series) -> Optional[str]:
        entity_type = row.get("Тип сущности") or row.get("entity_type")
        entity_id = row.get("ID сущности") or row.get("entity_id")
        if not entity_type or pd.isna(entity_id):
            return None
        try:
            entity_id = int(entity_id)
        except Exception:
            pass
        if entity_type == "leads":
            return f"{store.base}/leads/detail/{entity_id}"
        if entity_type == "contacts":
            return f"{store.base}/contacts/detail/{entity_id}"
        if entity_type == "companies":
            return f"{store.base}/companies/detail/{entity_id}"
        return None

    df["Ссылка на сущность"] = df.apply(build_link, axis=1)

    front = [
        col
        for col in [
            "ID задачи",
            "Тип задачи",
            "Текст",
            "Срок",
            "Завершена",
            "Ответственный",
            "Тип сущности",
            "ID сущности",
            "Ссылка на сущность",
            "Создано",
            "Обновлено",
        ]
        if col in df.columns
    ]
    other = [col for col in df.columns if col not in front]
    return df[front + other]


# === Main export flow ===

def run_export() -> None:
    store = AmoTokenStore(config.TOKENS_PATH)
    store.ensure_valid()

    references = fetch_reference_data(store)

    ts_from = int(time.time() - config.PERIOD_DAYS * 24 * 3600)

    leads_raw = store.get_paged_cap(
        "/api/v4/leads",
        params={"filter[updated_at][from]": ts_from},
        limit=config.REQUEST_LIMIT,
        cap=config.SAMPLE_LIMIT,
    )
    leads_df = enrich_leads(store, leads_raw, references["cf_leads"])

    contacts_raw = store.get_paged_cap(
        "/api/v4/contacts",
        params={"order[updated_at]": "desc"},
        limit=config.REQUEST_LIMIT,
        cap=config.SAMPLE_LIMIT,
    )
    contacts_df = enrich_contacts(store, contacts_raw, references["cf_contacts"])

    companies_raw = store.get_paged_cap(
        "/api/v4/companies",
        params={"order[updated_at]": "desc"},
        limit=config.REQUEST_LIMIT,
        cap=config.SAMPLE_LIMIT,
    )
    companies_df = enrich_companies(store, companies_raw, references["cf_companies"])

    print("Shapes raw:", leads_df.shape, contacts_df.shape, companies_df.shape)

    tasks_raw = store.get_paged_cap(
        "/api/v4/tasks",
        params={"order[created_at]": "desc"},
        limit=config.REQUEST_LIMIT,
        cap=config.SAMPLE_LIMIT,
    )
    tasks_df = enrich_tasks(store, tasks_raw, references)
    print("Tasks:", tasks_df.shape)

    leads_final = map_ids_and_dates_leads(leads_df, references)
    contacts_final = map_dates_common(contacts_df)
    companies_final = map_dates_common(companies_df)

    print(
        "Final shapes:",
        leads_final.shape,
        contacts_final.shape,
        companies_final.shape,
    )

    tasks_final_df = tasks_final(store, tasks_df, references)
    print("Tasks final:", tasks_final_df.shape)

    with pd.ExcelWriter(config.OUT_XLSX, engine="xlsxwriter") as writer:
        leads_final.to_excel(writer, index=False, sheet_name="Leads_500")
        contacts_final.to_excel(writer, index=False, sheet_name="Contacts_500")
        companies_final.to_excel(writer, index=False, sheet_name="Companies_500")
        tasks_final_df.to_excel(writer, index=False, sheet_name="Tasks_500")
    leads_final.to_csv(config.OUT_CSV_LEADS, index=False, encoding="utf-8-sig")
    print("OK: saved", config.OUT_XLSX, "и", config.OUT_CSV_LEADS)


if __name__ == "__main__":
    run_export()
